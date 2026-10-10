# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``AdaptiveTechniqueDispatcher`` — selects inner techniques per objective via a
``TechniqueSelector`` and builds a ``SequentialAttack`` to run them.

The dispatcher is a plain class, not an ``AttackStrategy``. It does not
execute anything and does not persist anything. ``AdaptiveScenario`` calls
``build_attack_async`` once per ``AttackSeedGroup`` during scenario
initialization, wraps each returned attack in its own ``AtomicAttack``, and
hands them to the scenario base for execution.

The returned attack is a plain ``SequentialAttack`` with
``SequenceCompletionPolicy.FIRST_SUCCESS``. The per-attempt dispatch trail
(which technique ran, with what outcome, in what order) is not stamped onto
the envelope — every child ``AttackResult`` in
``SequentialAttackResult.child_attack_results`` already carries its own
``outcome`` and its own ``atomic_attack_identifier.eval_hash``. Callers that
want a human-readable technique label per child read it directly from the
child via ``child.get_attack_strategy_identifier().unique_name`` (the
executor auto-stamps ``class_name`` and ``unique_name`` on every persisted
row), so there is no separate ``{eval_hash: name}`` map to consult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialChildAttack,
)
from pyrit.models import AtomicAttackIdentifier
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    TechniqueRequirements,
    prepare_seed_group,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.executor.attack.core.attack_strategy import AttackStrategy
    from pyrit.models import AttackResult, AttackSeedGroup, AttackTechniqueSeedGroup, ComponentIdentifier
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
    from pyrit.score import TrueFalseScorer

# Memory-label key stamped onto persisted prompt rows so adaptive attempts
# can be filtered/grouped after a run.
ADAPTIVE_ATTEMPT_LABEL: str = "_adaptive_attempt"
"""1-based attempt index within the per-objective loop."""


@dataclass(frozen=True)
class TechniqueBundle:
    """
    Per-technique bundle consumed by the dispatcher.

    Carries the inner attack strategy alongside the factory-supplied
    ``seed_technique`` (if any) and ``adversarial_chat`` (required when the
    seed_technique contains a simulated-conversation config). ``name`` is the
    factory-registration key; the dispatcher does not consume it, but it is
    convenient for diagnostics and is preserved here so callers/tests can
    cross-check which factory each bundle came from.

    Adaptation-permitting bundles require a technique identifier so their
    persisted child results use the same behavioral identity as selection.

    Notebook/report code that wants a human-readable label for a persisted
    child ``AttackResult`` should read it from the child itself via
    ``child.get_attack_strategy_identifier()`` — the executor already stamps
    ``class_name`` and ``unique_name`` on every row, so there is no need to
    publish a separate ``{eval_hash: name}`` map.
    """

    attack: AttackStrategy[Any, AttackResult]
    name: str = ""
    seed_technique: AttackTechniqueSeedGroup | None = None
    adversarial_chat: PromptTarget | None = None
    requirements: TechniqueRequirements = field(default_factory=TechniqueRequirements)
    technique_identifier: ComponentIdentifier | None = None

    def __post_init__(self) -> None:
        """
        Require behavioral identity when input adaptation is permitted.

        Raises:
            ValueError: If an adaptation-permitting bundle has no technique identifier.
        """
        if self.requirements.adaptation is not None and self.technique_identifier is None:
            raise ValueError("Adaptation-permitting techniques require a technique_identifier")


class AdaptiveTechniqueDispatcher:
    """
    Selects inner techniques per objective and builds a ``SequentialAttack``.

    Not an ``AttackStrategy``: the dispatcher does not execute anything
    and does not persist anything. It is a small factory used by
    ``AdaptiveScenario`` at initialization to translate one
    ``AttackSeedGroup`` (one objective) into one ready-to-run attack.

    For each call: query the selector for the top
    ``max_attempts_per_objective`` techniques compatible with the seed
    group, then construct a ``SequentialAttack`` (with
    ``SequenceCompletionPolicy.FIRST_SUCCESS``) whose children are the
    chosen techniques in priority order. The selector is shared by
    reference across all calls in a scenario so learning accumulates
    across objectives — though all selections are committed up-front
    during scenario initialization (see
    ``AdaptiveScenario._build_atomic_attacks_async``).
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        techniques: dict[str, TechniqueBundle],
        selector: TechniqueSelector,
        objective_scorer: TrueFalseScorer | None = None,
        max_attempts_per_objective: int = 3,
        scenario_result_id: str | None = None,
        incompatible_technique_policy: IncompatibleTechniquePolicy = IncompatibleTechniquePolicy.SKIP,
    ) -> None:
        """
        Args:
            objective_target (PromptTarget): The target inner attacks run against.
            techniques (dict[str, TechniqueBundle]): Mapping from
                technique eval hash to its bundle. Must be non-empty.
            selector (TechniqueSelector): Stateless technique selector.
            objective_scorer (TrueFalseScorer | None): Scorer forwarded
                to inner attacks that generate simulated conversations.
            max_attempts_per_objective (int): Maximum attempts per
                objective; must be >= 1. Defaults to 3.
            scenario_result_id (str | None): Passed to the selector to
                scope memory queries to this scenario run. Defaults to
                ``None``.
            incompatible_technique_policy (IncompatibleTechniquePolicy): Action for
                known dataset incompatibility. Candidate checks do not log warnings.

        Raises:
            ValueError: If ``techniques`` is empty or
                ``max_attempts_per_objective`` < 1.
        """
        if not techniques:
            raise ValueError("techniques must contain at least one attack technique")
        if max_attempts_per_objective < 1:
            raise ValueError(f"max_attempts_per_objective must be >= 1, got {max_attempts_per_objective}")
        self._objective_target = objective_target
        self._techniques = techniques
        self._selector = selector
        self._objective_scorer = objective_scorer
        self._max_attempts = max_attempts_per_objective
        self._scenario_result_id = scenario_result_id
        self._incompatible_technique_policy = incompatible_technique_policy
        self._selected_adaptations: dict[str, str] = {}
        self._selected_technique_eval_hashes: list[str] = []

    @property
    def selected_technique_eval_hashes(self) -> list[str]:
        """The ordered technique choices for the last built sequence."""
        return list(self._selected_technique_eval_hashes)

    @property
    def selected_adaptations(self) -> dict[str, str]:
        """Applied adaptations by selected technique hash for the last built sequence."""
        return dict(self._selected_adaptations)

    def compatible_techniques(self, *, seed_group: AttackSeedGroup) -> list[str]:
        """
        Return technique hashes whose dataset requirements and full seed merge pass.

        Used by ``AdaptiveScenario`` to drop seed groups with no usable
        techniques before building atomic attacks.

        Returns:
            list[str]: Technique eval hashes in declaration order.

        Raises:
            IncompatibleTechniqueError: If dataset input is incompatible under RAISE.
        """
        compatible: list[str] = []
        for name, bundle in self._techniques.items():
            try:
                prepare_seed_group(
                    seed_group=seed_group,
                    requirements=bundle.requirements.seed_group,
                    seed_technique=bundle.seed_technique,
                )
            except IncompatibleTechniqueError:
                if self._incompatible_technique_policy is IncompatibleTechniquePolicy.RAISE:
                    raise
                continue
            compatible.append(name)
        return compatible

    async def build_attack_async(
        self,
        *,
        seed_group: AttackSeedGroup,
        compatible: list[str] | None = None,
        selected_technique_eval_hashes: Sequence[str] | None = None,
    ) -> SequentialAttack:
        """
        Build a ``SequentialAttack`` for one ``AttackSeedGroup``.

        Queries the selector for the top
        ``max_attempts_per_objective`` techniques (filtered by per-call
        seed-group compatibility) and wraps them in a
        ``SequentialAttack`` with
        ``SequenceCompletionPolicy.FIRST_SUCCESS``.

        Args:
            seed_group (AttackSeedGroup): The seed group for the
                objective this attack will run against. Must carry a
                non-None objective.
            compatible (list[str] | None): Precomputed result of
                ``compatible_techniques(seed_group=...)``. When ``None``
                (default) the dispatcher computes it itself. Callers that
                already filter empty pools out via ``compatible_techniques``
                should pass the result through to avoid re-scanning the
                technique map.
            selected_technique_eval_hashes (Sequence[str] | None): Saved ordered choices
                on resume. When supplied, the selector is not called.

        Returns:
            SequentialAttack: The ready-to-run attack. Each child's
                identity is captured by its own
                ``atomic_attack_identifier.eval_hash`` after execution;
                callers wanting the friendly technique name read it
                directly from the child via
                ``child.get_attack_strategy_identifier().unique_name``.

        Raises:
            ValueError: If ``seed_group.objective`` is not initialized,
                or if no techniques in the pool are compatible with the
                seed group, or a selected technique is unavailable or incompatible.
        """
        if seed_group.objective is None:
            raise ValueError("seed_group.objective is not initialized")

        if compatible is None:
            compatible = self.compatible_techniques(seed_group=seed_group)
        if not compatible:
            raise ValueError(
                f"AdaptiveTechniqueDispatcher: no compatible techniques for seed group "
                f"(objective={seed_group.objective.value!r})."
            )

        chosen_hashes = list(
            selected_technique_eval_hashes
            if selected_technique_eval_hashes is not None
            else await self._selector.select_async(
                technique_identifiers=compatible,
                objective=seed_group.objective.value,
                num_top_techniques=self._max_attempts,
                scenario_result_id=self._scenario_result_id,
            )
        )
        if not chosen_hashes or len(chosen_hashes) > self._max_attempts:
            raise ValueError(f"Adaptive selection must contain between 1 and {self._max_attempts} technique hashes.")
        unavailable = set(chosen_hashes) - set(compatible)
        if unavailable:
            raise ValueError(
                f"Selected adaptive techniques are unavailable or incompatible with seed group "
                f"'{seed_group.logical_id}': {sorted(unavailable)}."
            )

        child_attacks: list[SequentialChildAttack] = []
        self._selected_technique_eval_hashes = chosen_hashes
        self._selected_adaptations = {}
        for attempt_idx, chosen in enumerate(chosen_hashes):
            bundle = self._techniques[chosen]
            prepared = prepare_seed_group(
                seed_group=seed_group,
                requirements=bundle.requirements.seed_group,
                seed_technique=bundle.seed_technique,
            )
            identifier = (
                AtomicAttackIdentifier.from_component_identifier(
                    AtomicAttackIdentifier.build(
                        technique_identifier=bundle.technique_identifier,
                        seed_group=seed_group,
                    ).with_eval_hash(chosen)
                )
                if bundle.technique_identifier is not None
                else None
            )
            metadata: dict[str, Any] = {}
            if prepared.adaptation is not None:
                self._selected_adaptations[chosen] = prepared.adaptation
                metadata["seed_group_adaptation"] = {
                    "source_seed_group_id": seed_group.logical_id,
                    "technique_eval_hash": chosen,
                    "adaptation": prepared.adaptation,
                }
            child_attacks.append(
                SequentialChildAttack(
                    strategy=bundle.attack,
                    seed_group=prepared.seed_group,
                    adversarial_chat=bundle.adversarial_chat,
                    objective_scorer=self._objective_scorer,
                    memory_labels={ADAPTIVE_ATTEMPT_LABEL: str(attempt_idx + 1)},
                    atomic_attack_identifier=identifier,
                    result_metadata=metadata,
                )
            )

        return SequentialAttack(
            objective_target=self._objective_target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_SUCCESS,
        )
