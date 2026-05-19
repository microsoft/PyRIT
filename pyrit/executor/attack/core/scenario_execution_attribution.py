# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Typed attribution metadata used to link a persisted ``AttackResult`` to the
``Scenario`` that produced it.

Lives in the ``executor`` layer (rather than ``scenario``) so the attack
persistence path — the consumer — does not introduce a dependency on
``pyrit.scenario``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioExecutionAttribution:
    """
    Scenario-linkage metadata stamped onto an ``AttackContext`` by the
    ``AttackExecutor`` and copied onto the resulting ``AttackResult`` by the
    attack persistence path so the row carries the scenario FK + scenario_data.

    Attributes:
        scenario_result_id (str): The ID of the scenario result that produced
            this attack execution. Persisted to
            ``AttackResultEntry.scenario_result_id`` so per-scenario hydration
            and resume can locate the row directly without relying on a JSON
            manifest written at the end of an atomic attack.
        atomic_attack_name (str): The unique key of the atomic attack within
            the scenario (matches ``AtomicAttack.atomic_attack_name``).
            Persisted into ``AttackResultEntry.scenario_data``.
        objective_index (int): The 0-based original seed-group index (the
            ``input_indices`` value from ``AttackExecutorResult``). Assigned
            **before** task execution so it is deterministic and parallel-safe.
            Persisted into ``AttackResultEntry.scenario_data`` and used as the
            stable resume key (instead of the easily-duplicated objective text).
    """

    scenario_result_id: str
    atomic_attack_name: str
    objective_index: int
