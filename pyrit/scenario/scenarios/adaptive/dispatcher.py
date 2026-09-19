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
``SequenceCompletionPolicy.FIRST_SUCCESS``. Each child result carries the
stable registered-factory identity and friendly technique name in labels so
different configured techniques remain attributable even when their inner
attack implementations are execution-equivalent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialChildAttack,
)

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_strategy import AttackStrategy
    from pyrit.models import AttackResult, AttackSeedGroup, AttackTechniqueSeedGroup
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


# Memory-label key stamped onto persisted prompt rows so adaptive attempts
# can be filtered/grouped after a run.
ADAPTIVE_ATTEMPT_LABEL: str = "_adaptive_attempt"
"""1-based attempt index within the per-objective loop."""

ADAPTIVE_TECHNIQUE_ID_LABEL: str = "_adaptive_technique_id"
"""Joined registered-factory and behavioral-history identity for the selected arm."""

ADAPTIVE_TECHNIQUE_NAME_LABEL: str = "_adaptive_technique_name"
"""Registered technique name for human-readable result attribution."""


@dataclass(frozen=True)
class TechniqueBundle:
    """
    Per-technique bundle consumed by the dispatcher.

    Carries the inner attack strategy alongside the factory-supplied
    ``seed_technique`` (if any) and ``adversarial_chat`` (required when the
    seed technique contains a simulated-conversation config). ``name`` is the
    factory-registration key and is persisted on every selected child result.
    """

    attack: AttackStrategy[Any, AttackResult]
    name: str = ""
    seed_technique: AttackTechniqueSeedGroup | None = None
    adversarial_chat: PromptTarget | None = None


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
    ) -> None:
        """
        Args:
            objective_target (PromptTarget): The target inner attacks run against.
            techniques (dict[str, TechniqueBundle]): Mapping from
                joined Adaptive technique identity to its bundle. Must be non-empty.
            selector (TechniqueSelector): Stateless technique selector.
            objective_scorer (TrueFalseScorer | None): Scorer forwarded
                to inner attacks that generate simulated conversations.
            max_attempts_per_objective (int): Maximum attempts per
                objective; must be >= 1. Defaults to 3.
            scenario_result_id (str | None): Passed to the selector to
                scope memory queries to this scenario run. Defaults to
                ``None``.

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

    def compatible_techniques(self, *, seed_group: AttackSeedGroup) -> list[str]:
        """
        Return technique identifiers whose ``seed_technique`` is compatible with ``seed_group``.

        Techniques with no ``seed_technique`` are universally compatible.
        Used by ``AdaptiveScenario`` to drop seed groups with no usable
        techniques before building atomic attacks.

        Returns:
            list[str]: Joined Adaptive technique identities in declaration order.
        """
        return [
            name
            for name, bundle in self._techniques.items()
            if bundle.seed_technique is None or seed_group.is_compatible_with_technique(technique=bundle.seed_technique)
        ]

    async def build_attack_async(
        self,
        *,
        seed_group: AttackSeedGroup,
        compatible: list[str] | None = None,
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

        Returns:
            SequentialAttack: The ready-to-run attack. Each child carries its
                canonical factory identity and registered name in result labels.

        Raises:
            ValueError: If ``seed_group.objective`` is not initialized,
                or if no techniques in the pool are compatible with the
                seed group.
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

        chosen_identifiers = await self._selector.select_async(
            technique_identifiers=compatible,
            objective=seed_group.objective.value,
            num_top_techniques=self._max_attempts,
            scenario_result_id=self._scenario_result_id,
        )

        child_attacks: list[SequentialChildAttack] = []
        for attempt_idx, chosen in enumerate(chosen_identifiers):
            bundle = self._techniques[chosen]
            execution_group = (
                seed_group.with_technique(technique=bundle.seed_technique)
                if bundle.seed_technique is not None
                else seed_group
            )
            child_attacks.append(
                SequentialChildAttack(
                    strategy=bundle.attack,
                    seed_group=execution_group,
                    adversarial_chat=bundle.adversarial_chat,
                    objective_scorer=self._objective_scorer,
                    memory_labels={
                        ADAPTIVE_ATTEMPT_LABEL: str(attempt_idx + 1),
                        ADAPTIVE_TECHNIQUE_ID_LABEL: chosen,
                        ADAPTIVE_TECHNIQUE_NAME_LABEL: bundle.name,
                    },
                )
            )

        return SequentialAttack(
            objective_target=self._objective_target,
            child_attacks=child_attacks,
            completion_policy=SequenceCompletionPolicy.FIRST_SUCCESS,
        )
