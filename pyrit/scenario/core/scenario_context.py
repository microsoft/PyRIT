# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Resolved runtime inputs for building a scenario's atomic attacks.

``ScenarioContext`` is the single bundle of values a scenario needs to construct
its ``AtomicAttack`` list. The base ``Scenario`` resolves these during
``initialize_async`` and passes them to ``_build_atomic_attacks_async``, so a
scenario builds its attacks from the context it is given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models import SeedAttackGroup
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy


@dataclass(frozen=True)
class ScenarioContext:
    """
    Immutable snapshot of the inputs needed to build a scenario's atomic attacks.

    Constructed by ``Scenario._build_scenario_context`` from the values resolved in
    ``initialize_async`` and handed to ``_build_atomic_attacks_async``. A scenario
    builds its attacks from this context, independent of instance-attribute
    initialization order.

    Attributes:
        objective_target (PromptTarget): The target system the scenario attacks.
        scenario_strategies (Sequence[ScenarioStrategy]): The resolved, concrete
            strategies selected for this run (aggregates already expanded).
        dataset_config (DatasetAttackConfiguration): The effective dataset configuration
            (caller-supplied or the scenario's default).
        memory_labels (dict[str, str]): Labels applied to every attack run.
        include_baseline (bool): Whether a baseline atomic attack should be emitted
            for this run, already resolved against the scenario's
            ``BASELINE_ATTACK_POLICY``.
        seed_groups (Sequence[SeedAttackGroup]): The scenario's seed groups, resolved
            and sampled once by the base ``Scenario`` (flattened across datasets). Use
            these to build attacks so every atomic attack — and the baseline — draws from
            the same population.
        seed_groups_by_dataset (Mapping[str, list[SeedAttackGroup]]): The same resolved
            seed groups keyed by originating dataset name, for scenarios that map datasets
            onto separate attacks or display groups.
    """

    objective_target: PromptTarget
    scenario_strategies: Sequence[ScenarioStrategy]
    dataset_config: DatasetAttackConfiguration
    memory_labels: dict[str, str] = field(default_factory=dict)
    include_baseline: bool = False
    seed_groups: Sequence[SeedAttackGroup] = field(default_factory=tuple)
    seed_groups_by_dataset: Mapping[str, list[SeedAttackGroup]] = field(default_factory=dict)
