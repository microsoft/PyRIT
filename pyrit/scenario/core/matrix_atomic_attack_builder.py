# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Reusable matrix builder for scenario atomic attacks.

``MatrixAtomicAttackBuilder`` turns a technique × dataset (× optional adversarial
target) grid into a flat list of ``AtomicAttack`` instances. It centralizes the
seed-technique compatibility filtering, ``factory.create`` wiring, ``AtomicAttack``
construction, and baseline emission for scenarios whose attacks form such a
cross-product.

This is one member of a *family* of construction helpers named by shape
(``Matrix...``); scenarios whose construction is composite or per-objective build
``AtomicAttack`` lists differently. There is intentionally no shared builder
interface yet — each scenario calls the builder it needs directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pyrit.executor.attack import AttackScoringConfig
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import SeedAttackGroup
from pyrit.prompt_normalizer import PromptConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pyrit.prompt_converter import PromptConverter
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import Scorer
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatrixCombo:
    """
    One cell of the build matrix, passed to the ``name_fn``/``display_group_fn`` callbacks.

    Attributes:
        technique_name (str): The technique (technique enum value) for this cell.
        dataset_name (str): The dataset key from ``DatasetAttackConfiguration.get_attack_groups_by_dataset_async()``.
        target_name (str | None): The adversarial-target registry name when an
            adversarial-target axis is in play, else ``None``.
    """

    technique_name: str
    dataset_name: str
    target_name: str | None = None


def _default_atomic_attack_name(combo: MatrixCombo) -> str:
    """
    Default ``atomic_attack_name`` builder; target-aware so names stay unique.

    Args:
        combo (MatrixCombo): The matrix cell being named.

    Returns:
        str: The atomic attack name for the cell.
    """
    if combo.target_name is None:
        return f"{combo.technique_name}_{combo.dataset_name}"
    return f"{combo.technique_name}__{combo.target_name}_{combo.dataset_name}"


def _default_display_group(combo: MatrixCombo) -> str:
    """
    Build the default display group: aggregate results by technique.

    Args:
        combo (MatrixCombo): The matrix cell being grouped.

    Returns:
        str: The display group for the cell.
    """
    return combo.technique_name


def build_baseline_atomic_attack(
    *,
    objective_target: PromptTarget,
    objective_scorer: Scorer,
    seed_groups: list[SeedAttackGroup],
    memory_labels: dict[str, str] | None = None,
) -> AtomicAttack:
    """
    Build the baseline ``AtomicAttack`` that sends each objective unmodified.

    The baseline is a plain ``PromptSendingAttack`` used as a comparison point against
    a scenario's technique attacks. Pass the *same* ``seed_groups`` used to build the
    technique attacks so both populations match — re-resolving under ``max_dataset_size``
    would draw a fresh random sample and diverge from the technique population.

    Args:
        objective_target (PromptTarget): The target to attack.
        objective_scorer (Scorer): The scorer used to evaluate the baseline.
        seed_groups (list[SeedAttackGroup]): Seed groups to attack. Used as-is.
        memory_labels (dict[str, str] | None): Labels applied to the baseline's prompts.

    Returns:
        AtomicAttack: The baseline atomic attack named ``"baseline"``.
    """
    attack = PromptSendingAttack(
        objective_target=objective_target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", objective_scorer)),
    )
    return AtomicAttack(
        atomic_attack_name="baseline",
        attack_technique=AttackTechnique(attack=attack),
        seed_groups=seed_groups,
        memory_labels=memory_labels or {},
    )


def resolve_technique_factories(
    *,
    context: ScenarioContext,
    extra_factories: dict[str, AttackTechniqueFactory] | None = None,
) -> dict[str, AttackTechniqueFactory]:
    """
    Resolve a run's selected techniques to their registered ``AttackTechniqueFactory`` instances.

    Reads the ``AttackTechniqueRegistry`` singleton and keeps only the factories whose name
    matches a selected technique, preserving selection order. Techniques with no registered
    factory are silently dropped so the caller can proceed with whatever techniques exist.

    Args:
        context (ScenarioContext): The resolved runtime inputs for this run.
        extra_factories (dict[str, AttackTechniqueFactory] | None): Scenario-local factories
            merged on top of the registry before filtering, so a scenario can offer techniques
            without registering them globally. Entries override registry factories of the same
            name.

    Returns:
        dict[str, AttackTechniqueFactory]: Mapping of technique name to factory, ordered by
        the selected techniques.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

    all_factories = dict(AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise())
    if extra_factories:
        all_factories.update(extra_factories)
    return {
        technique.value: all_factories[technique.value]
        for technique in context.scenario_techniques
        if technique.value in all_factories
    }


def build_matrix_atomic_attacks(
    *,
    context: ScenarioContext,
    objective_scorer: Scorer,
    display_group_fn: Callable[[MatrixCombo], str] | None = None,
    technique_converters: dict[str, list[PromptConverter]] | None = None,
    extra_factories: dict[str, AttackTechniqueFactory] | None = None,
) -> list[AtomicAttack]:
    """
    Build a matrix-shaped scenario's atomic attacks from its resolved context in one call.

    This is the zero-boilerplate path for scenarios whose construction is the plain
    technique × dataset cross-product: it resolves the selected techniques to factories
    (``resolve_technique_factories``) and hands them to ``MatrixAtomicAttackBuilder``
    with the context's target, labels, and per-dataset seed groups. The baseline is emitted
    centrally by ``Scenario.initialize_async``, so this never prepends one.

    Scenarios needing extra axes (adversarial targets, caching, converter stacks) call
    ``MatrixAtomicAttackBuilder`` directly instead.

    Args:
        context (ScenarioContext): The resolved runtime inputs for this run.
        objective_scorer (Scorer): The scorer applied to each produced atomic attack.
        display_group_fn (Callable[[MatrixCombo], str] | None): Builds each ``display_group``.
            Defaults to grouping by technique name.
        technique_converters (dict[str, list[PromptConverter]] | None): Optional mapping from
            technique name to converters appended after that technique's converters. Pass a
            scenario's ``self._technique_converters`` so per-technique converter overrides are
            preserved.
        extra_factories (dict[str, AttackTechniqueFactory] | None): Scenario-local factories
            merged on top of the registry (see ``resolve_technique_factories``), so a scenario
            can offer techniques without registering them globally.

    Returns:
        list[AtomicAttack]: The generated atomic attacks (no baseline).
    """
    builder = MatrixAtomicAttackBuilder(
        objective_target=context.objective_target,
        objective_scorer=objective_scorer,
        memory_labels=context.memory_labels,
    )
    return builder.build(
        technique_factories=resolve_technique_factories(context=context, extra_factories=extra_factories),
        dataset_groups=context.seed_groups_by_dataset,
        display_group_fn=display_group_fn,
        technique_converters=technique_converters,
        include_baseline=False,
    )


class MatrixAtomicAttackBuilder:
    """
    Build ``AtomicAttack`` instances from a technique × dataset (× target) cross-product.

    Construct once with the shared run inputs (target, scorer, labels), then call
    ``build`` with the per-run grid. The builder owns:

    - seed-technique compatibility filtering (``SeedAttackGroup.filter_compatible``),
    - the ``factory.create(...)`` call, forwarding an adversarial target when the
      adversarial-target axis is active,
    - ``AtomicAttack`` construction with naming and display-group stamping, and
    - optional baseline emission using the same resolved seed groups.

    Example:
        >>> builder = MatrixAtomicAttackBuilder(
        ...     objective_target=target,
        ...     objective_scorer=scorer,
        ...     memory_labels=labels,
        ... )
        >>> attacks = builder.build(
        ...     technique_factories=factories,
        ...     dataset_groups=groups,
        ...     include_baseline=True,
        ... )
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        objective_scorer: Scorer,
        memory_labels: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the builder with inputs shared across every atomic attack it produces.

        Args:
            objective_target (PromptTarget): The target system to attack.
            objective_scorer (Scorer): The scorer applied to each produced atomic attack
                and to the baseline.
            memory_labels (dict[str, str] | None): Labels applied to every produced
                atomic attack.
        """
        self._objective_target = objective_target
        self._objective_scorer = objective_scorer
        self._memory_labels = memory_labels or {}

    def build(
        self,
        *,
        technique_factories: dict[str, AttackTechniqueFactory],
        dataset_groups: Mapping[str, list[SeedAttackGroup]],
        adversarial_targets: Sequence[tuple[str, PromptTarget]] | None = None,
        name_fn: Callable[[MatrixCombo], str] | None = None,
        display_group_fn: Callable[[MatrixCombo], str] | None = None,
        technique_converters: dict[str, list[PromptConverter]] | None = None,
        include_baseline: bool = False,
    ) -> list[AtomicAttack]:
        """
        Build the atomic attacks for the given grid.

        Iterates technique → (adversarial target) → dataset. The caller pre-resolves
        ``technique_factories`` to exactly the techniques to build (and, by dict
        insertion order, the order to build them in), so the builder does not need the
        full registry or the selected-technique set.

        Args:
            technique_factories (dict[str, AttackTechniqueFactory]): Mapping of technique
                name to the factory that produces it. Only these techniques are built.
            dataset_groups (Mapping[str, list[SeedAttackGroup]]): Mapping of dataset name to
                its seed groups (e.g. ``await DatasetAttackConfiguration.get_attack_groups_by_dataset_async()``).
            adversarial_targets (Sequence[tuple[str, PromptTarget]] | None): Optional
                ``(name, instance)`` pairs adding an adversarial-target axis. When set,
                each technique is swept across every target and the target instance is
                forwarded to ``factory.create(adversarial_chat=...)``. When ``None``, the
                axis is collapsed and each factory uses its own (possibly lazy)
                adversarial target.
            name_fn (Callable[[MatrixCombo], str] | None): Builds each ``atomic_attack_name``.
                Defaults to ``"{technique}_{dataset}"`` (or ``"{technique}__{target}_{dataset}"``
                when an adversarial-target axis is active).
            display_group_fn (Callable[[MatrixCombo], str] | None): Builds each
                ``display_group``. Defaults to grouping by technique name.
            technique_converters (dict[str, list[PromptConverter]] | None): Optional mapping
                from technique name to request converters appended on top of that technique's
                built-in converters (via ``factory.create(extra_request_converters=...)``).
                Techniques absent from the mapping are built unchanged.
            include_baseline (bool): When ``True``, prepend a baseline atomic attack built
                from the flattened seed groups across all datasets.

        Returns:
            list[AtomicAttack]: The generated atomic attacks, baseline first when requested.
        """
        name_fn = name_fn or _default_atomic_attack_name
        display_group_fn = display_group_fn or _default_display_group

        scoring_config = AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer))

        target_axis: Sequence[tuple[str | None, PromptTarget | None]] = (
            list(adversarial_targets) if adversarial_targets else [(None, None)]
        )

        atomic_attacks: list[AtomicAttack] = []
        technique_converters = technique_converters or {}
        for technique_name, factory in technique_factories.items():
            extra_converters = technique_converters.get(technique_name)
            extra_request_converters = (
                PromptConverterConfiguration.from_converters(converters=extra_converters) if extra_converters else None
            )
            for target_name, target_instance in target_axis:
                for dataset_name, seed_groups in dataset_groups.items():
                    compatible_groups = self._filter_compatible_groups(
                        factory=factory,
                        seed_groups=seed_groups,
                        technique_name=technique_name,
                        dataset_name=dataset_name,
                    )
                    if compatible_groups is None:
                        continue

                    create_adversarial = {"adversarial_chat": target_instance} if target_instance is not None else {}
                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_scoring_config=scoring_config,
                        extra_request_converters=extra_request_converters,
                        **create_adversarial,
                    )

                    combo = MatrixCombo(
                        technique_name=technique_name,
                        dataset_name=dataset_name,
                        target_name=target_name,
                    )
                    atomic_attacks.append(
                        AtomicAttack(
                            atomic_attack_name=name_fn(combo),
                            attack_technique=attack_technique,
                            seed_groups=compatible_groups,
                            adversarial_chat=(
                                target_instance if target_instance is not None else factory.adversarial_chat
                            ),
                            objective_scorer=cast("TrueFalseScorer", self._objective_scorer),
                            memory_labels=self._memory_labels,
                            display_group=display_group_fn(combo),
                        )
                    )

        if include_baseline:
            all_seed_groups = [group for groups in dataset_groups.values() for group in groups]
            atomic_attacks.insert(
                0,
                build_baseline_atomic_attack(
                    objective_target=self._objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=all_seed_groups,
                    memory_labels=self._memory_labels,
                ),
            )

        return atomic_attacks

    def _filter_compatible_groups(
        self,
        *,
        factory: AttackTechniqueFactory,
        seed_groups: list[SeedAttackGroup],
        technique_name: str,
        dataset_name: str,
    ) -> list[SeedAttackGroup] | None:
        """
        Filter seed groups to those compatible with the factory's seed technique.

        Args:
            factory (AttackTechniqueFactory): The factory whose ``seed_technique`` gates
                compatibility.
            seed_groups (list[SeedAttackGroup]): Candidate seed groups for one dataset.
            technique_name (str): Technique name, used only for log messages.
            dataset_name (str): Dataset name, used only for log messages.

        Returns:
            list[SeedAttackGroup] | None: The compatible groups, or ``None`` when the
            ``(technique, dataset)`` pair has no compatible groups and should be skipped.
        """
        if factory.seed_technique is None:
            return list(seed_groups)

        compatible_groups = SeedAttackGroup.filter_compatible(
            seed_groups=seed_groups,
            technique=factory.seed_technique,
        )
        skipped = len(seed_groups) - len(compatible_groups)
        if skipped:
            logger.info(
                f"Skipped {skipped} seed group(s) from '{dataset_name}' for technique "
                f"'{technique_name}' (prompt sequences overlap with simulated conversation)."
            )
        if not compatible_groups:
            logger.warning(
                f"No compatible seed groups in '{dataset_name}' for technique "
                f"'{technique_name}', skipping this (technique, dataset) pair."
            )
            return None
        return compatible_groups
