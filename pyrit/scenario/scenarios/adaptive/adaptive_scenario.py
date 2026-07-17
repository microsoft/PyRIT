# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``AdaptiveScenario`` — modality-agnostic base for scenarios that pick attack
techniques per-objective using a ``TechniqueSelector``.

Owns selector wiring, dispatcher construction, and per-dataset atomic-attack
emission. Concrete subclasses (``TextAdaptive``, future ``ImageAdaptive`` /
``AudioAdaptive``) only declare technique class, default datasets, version,
and atomic-attack prefix.

Baseline policy is ``Enabled``: prompt_sending runs as a separate baseline
comparison and is excluded from the adaptive technique pool.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, ClassVar

from pyrit.common.utils import to_sha256
from pyrit.executor.attack import AttackScoringConfig
from pyrit.models.identifiers import compute_inner_attack_eval_hash
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target
from pyrit.scenario.scenarios.adaptive.dispatcher import AdaptiveTechniqueDispatcher, TechniqueBundle
from pyrit.scenario.scenarios.adaptive.selectors import EpsilonGreedyTechniqueSelector, TechniqueSelector

if TYPE_CHECKING:
    from pyrit.models import AttackSeedGroup
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class AdaptiveScenario(Scenario):
    """
    Abstract base for adaptive (epsilon-greedy) scenarios.

    Subclasses must implement the standard ``Scenario`` class-method overrides
    and implement ``_atomic_attack_prefix``. Selector wiring
    and dispatcher construction are handled here.
    """

    VERSION: ClassVar[int]

    @classmethod
    @abstractmethod
    def _atomic_attack_prefix(cls) -> str:
        """
        Return the prefix for per-objective atomic-attack names (e.g. ``"adaptive_text"``).

        Must be unique across adaptive subclasses — different modalities
        emitting the same prefix would collide on ``atomic_attack_name`` and
        merge their resume bookkeeping silently.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_technique_class(cls) -> type[ScenarioTechnique]:
        """Return the scenario's technique enum (subclasses must override)."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_default_technique(cls) -> ScenarioTechnique:
        """Return the scenario's default technique aggregate (subclasses must override)."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def default_dataset_config(cls) -> DatasetAttackConfiguration:
        """Return the scenario's default ``DatasetAttackConfiguration`` (subclasses must override)."""
        raise NotImplementedError

    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        selector: TechniqueSelector | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Args:
            objective_scorer (TrueFalseScorer | None): Scorer used to judge each
                response. Defaults to the composite scorer from the base class.
            selector (TechniqueSelector | None): Pre-built selector. When ``None``
                (default) an ``EpsilonGreedyTechniqueSelector`` is created
                with default settings.
            scenario_result_id (str | None): ID of an existing ``ScenarioResult`` to resume.
        """
        if not objective_scorer:
            objective_scorer = self._get_default_objective_scorer()
        self._objective_scorer: TrueFalseScorer = objective_scorer

        self._selector: TechniqueSelector = selector if selector is not None else EpsilonGreedyTechniqueSelector()

        super().__init__(
            version=self.VERSION,
            technique_class=self.get_technique_class(),
            default_technique=self.get_default_technique(),
            default_dataset_config=self.default_dataset_config(),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _get_attack_technique_factories(self) -> dict[str, AttackTechniqueFactory]:
        """
        Build factories from the canonical scenario-techniques catalog,
        augmented with any factory currently in the global
        ``AttackTechniqueRegistry``.

        The catalog defines the deterministic baseline pool — it is also the
        source of truth for the technique enum's valid values, so iteration
        order and presence of techniques do not depend on registry
        initialization order. Registry-registered factories whose name
        matches a catalog entry **override** the catalog default, letting
        operators swap in tuned configurations (custom adversarial chat,
        different converter chain, etc.) without editing core. Factories
        registered only in the registry (no matching technique enum value)
        are returned too but the scenario will only consume those whose
        names appear in ``self._scenario_techniques``. When the registry
        has not been initialized yet, the catalog alone is used.

        Subclasses may override to further customize the pool.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        # Local import: ``techniques`` imports ``pyrit.scenario.core``,
        # which transitively re-imports this module, so a top-level import
        # would form a cycle during ``pyrit.scenario`` package initialization.
        from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
        from pyrit.setup.initializers.techniques import build_technique_factories

        catalog = {factory.name: factory for factory in build_technique_factories()}
        try:
            registry_overrides = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()
        except RuntimeError:
            # Registry not initialized yet (e.g. bare CLI parse before
            # TechniqueInitializer has run). Catalog alone is the
            # safe fallback and matches the technique enum's value set.
            registry_overrides = {}
        return {**catalog, **registry_overrides}

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one ``AtomicAttack`` per (dataset, compatible seed group) pair.

        For each dataset, construct a single ``AdaptiveTechniqueDispatcher``
        shared across that dataset's seed groups. For each seed group, ask
        the dispatcher to build its per-objective ``SequentialAttack`` and
        wrap it in its own ``AtomicAttack``.         All dispatchers across all
        datasets share one ``TechniqueSelector`` instance so learning
        accumulates globally; selection is committed up-front during
        scenario initialization, before any execution starts.

        The base ``Scenario`` prepends the baseline ``AtomicAttack`` (named
        ``"baseline"``) at index 0 when ``context.include_baseline`` is true (the
        default under ``BASELINE_ATTACK_POLICY = Enabled``).

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: One ``AtomicAttack`` per compatible
                seed group across all datasets.

        Raises:
            ValueError: If ``_build_techniques_dict`` finds no usable techniques.
        """
        techniques = self._build_techniques_dict(objective_target=context.objective_target)

        atomic_attacks: list[AtomicAttack] = []
        for dataset_name, seed_groups in context.seed_groups_by_dataset.items():
            atomic_attacks.extend(
                await self._build_atomics_for_dataset_async(
                    dataset_name=dataset_name,
                    seed_groups=seed_groups,
                    techniques=techniques,
                    selector=self._selector,
                )
            )

        return atomic_attacks

    def _build_techniques_dict(
        self,
        *,
        objective_target: PromptTarget,
    ) -> dict[str, TechniqueBundle]:
        """
        Resolve selected techniques into a ``{eval_hash: TechniqueBundle}`` map.

        Each bundle carries the inner attack technique along with the factory's
        ``seed_technique`` and ``adversarial_chat`` so the dispatcher can
        reproduce the static ``AtomicAttack`` execution path per attempt.

        Technique keys are eval hashes derived from the inner attack technique's
        identifier (run through ``AtomicAttackEvaluationIdentifier`` so seeds,
        scorers, and operational target params are excluded). The same hash is
        auto-stamped on every persisted ``AttackResultEntry.atomic_attack_identifier``
        by the executor, which lets the selector aggregate historical success
        rates by behavioral configuration via
        ``MemoryInterface.get_attack_results(atomic_attack_eval_hashes=...)``.

        For factories whose attack class narrows ``attack_scoring_config`` to a
        specific subtype (e.g. ``TAPAttackScoringConfig`` for TAP), this method
        builds the matching subtype using the scenario's objective scorer.
        Techniques whose factory rejects the scenario scorer at construction
        time (e.g. TAP also requires a ``FloatScaleThresholdScorer`` at runtime)
        are dropped with a warning so the rest of the pool continues to run.

        Returns:
            dict[str, TechniqueBundle]: Mapping from technique eval hash to its
                bundle, in the order selected techniques were resolved.

        Raises:
            ValueError: If no techniques remain after filtering. Includes the
                requested techniques and skip reasons.
        """
        selected_techniques = sorted({s.value for s in self._scenario_techniques})
        factories = self._get_attack_technique_factories()

        techniques: dict[str, TechniqueBundle] = {}
        skipped_no_factory: list[str] = []
        skipped_incompatible: dict[str, str] = {}
        for technique_name in selected_techniques:
            factory = factories.get(technique_name)
            if factory is None:
                skipped_no_factory.append(technique_name)
                logger.warning(f"No factory for technique '{technique_name}', skipping.")
                continue
            scoring_config = self._build_scoring_config_for_factory(factory=factory)
            if scoring_config is None:
                required_type = factory.scoring_config_type
                required_name = required_type.__name__ if required_type is not None else "AttackScoringConfig"
                reason = f"scenario scorer is incompatible with required {required_name}"
                skipped_incompatible[technique_name] = reason
                logger.warning(f"Skipping technique '{technique_name}': {reason}")
                continue
            try:
                technique = factory.create(
                    objective_target=objective_target,
                    attack_scoring_config=scoring_config,
                )
            except (TypeError, ValueError) as exc:
                skipped_incompatible[technique_name] = str(exc)
                logger.warning(f"Skipping technique '{technique_name}': {type(exc).__name__}: {exc}")
                continue
            eval_hash = compute_inner_attack_eval_hash(attack=technique.attack)
            adversarial_chat = factory.adversarial_chat
            if adversarial_chat is None and factory.uses_adversarial:
                adversarial_chat = get_default_adversarial_target()
            techniques[eval_hash] = TechniqueBundle(
                attack=technique.attack,
                name=technique_name,
                seed_technique=technique.seed_technique,
                adversarial_chat=adversarial_chat,
            )

        if not techniques:
            details: list[str] = []
            if skipped_no_factory:
                details.append(f"no factory registered: {sorted(skipped_no_factory)}")
            if skipped_incompatible:
                details.append(f"incompatible with scenario scorer: {sorted(skipped_incompatible)}")
            suffix = f" ({'; '.join(details)})" if details else ""
            raise ValueError(
                f"{type(self).__name__}: no usable techniques after resolving techniques. "
                f"Check the --techniques selection.{suffix}"
            )

        return techniques

    def _build_scoring_config_for_factory(self, *, factory: AttackTechniqueFactory) -> AttackScoringConfig | None:
        """
        Build the most specific scoring config the factory's attack class accepts.

        When the attack's constructor narrows ``attack_scoring_config`` to a
        subtype of ``AttackScoringConfig`` (e.g. TAP requires
        ``TAPAttackScoringConfig``), construct that subtype directly so the
        factory does not have to fall back to its WARN policy and silently
        substitute an internal default scorer.

        Returns ``None`` when the required subtype itself rejects the
        scenario's ``objective_scorer`` (e.g. TAP requires a
        ``FloatScaleThresholdScorer`` while the scenario provides a
        ``TrueFalseScorer``). ``None`` signals that no scenario-scorer-
        preserving config exists for this technique — the caller drops the
        technique rather than relying on the factory's override policy to
        react (under WARN/SKIP the factory would silently substitute its
        internal default scorer, masking the incompatibility).

        Returns:
            AttackScoringConfig | None: The most specific config that could
                be built, or ``None`` if the technique is incompatible with
                the scenario scorer.
        """
        required = factory.scoring_config_type
        if required is None or required is AttackScoringConfig:
            return AttackScoringConfig(objective_scorer=self._objective_scorer)
        try:
            return required(objective_scorer=self._objective_scorer)
        except (TypeError, ValueError):
            return None

    async def _build_atomics_for_dataset_async(
        self,
        *,
        dataset_name: str,
        seed_groups: list[AttackSeedGroup],
        techniques: dict[str, TechniqueBundle],
        selector: TechniqueSelector,
    ) -> list[AtomicAttack]:
        """
        Build one ``AtomicAttack`` per seed group with at least one
        compatible technique.

        A single ``AdaptiveTechniqueDispatcher`` is constructed for this
        dataset and used to build a fresh ``SequentialAttack`` per seed
        group. Each returned atomic carries one seed group and one
        pre-built attack whose children were selected up-front via the
        dispatcher.

        Seed groups for which no technique in the pool is compatible are
        dropped here with a warning.

        Returns:
            list[AtomicAttack]: One atomic per compatible seed group.
                Empty list when every seed group is incompatible with
                every technique.

        Raises:
            ValueError: If ``self._objective_target`` is not set
                (defensive guard; ``_build_atomic_attacks_async`` enforces
                this earlier).
        """
        if self._objective_target is None:  # pragma: no cover - defensive
            raise ValueError("objective_target must be set before creating attacks")

        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=self._objective_target,
            techniques=techniques,
            selector=selector,
            objective_scorer=self._objective_scorer,
            max_attempts_per_objective=self.params.get("max_attempts_per_objective", 3),
            scenario_result_id=self._scenario_result_id,
        )

        atomics: list[AtomicAttack] = []
        for seed_group in seed_groups:
            compatible = dispatcher.compatible_techniques(seed_group=seed_group)
            if not compatible:
                logger.warning(
                    "AdaptiveScenario: no compatible techniques for seed group in dataset '%s' "
                    "(objective=%r); skipping.",
                    dataset_name,
                    seed_group.objective.value,
                )
                continue

            attack = await dispatcher.build_attack_async(seed_group=seed_group, compatible=compatible)
            objective_sha = to_sha256(seed_group.objective.value)
            atomic_attack_name = f"{self._atomic_attack_prefix()}_{dataset_name}::{objective_sha}"
            atomics.append(
                AtomicAttack(
                    atomic_attack_name=atomic_attack_name,
                    attack_technique=AttackTechnique(attack=attack),
                    seed_groups=[seed_group],
                    objective_scorer=self._objective_scorer,
                    memory_labels=dict(self._memory_labels),
                    display_group=dataset_name,
                )
            )

        return atomics
