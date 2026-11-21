# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Independent, fixed-count trials of a packaged attack against one objective."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialAttackResult,
    SequentialChildAttack,
)
from pyrit.models import AttackOutcome, AttackResult, AttackResultSelection

if TYPE_CHECKING:
    import uuid

    from pyrit.executor.attack.core import AttackContext, AttackParameters
    from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
    from pyrit.models import ComponentIdentifier, ScoringExpectation
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class RepeatedAttack(SequentialAttack):
    """
    Run every independent trial and summarize success for one objective.

    Each trial uses the child's normal execution lifecycle, including a fresh conversation.
    The parent succeeds when any trial succeeds. Errored trials are ignored for the group
    outcome unless every trial errors. Trial results remain individually persisted; their
    outcomes and evidence references are also recorded in parent metadata.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        child_attack: SequentialChildAttack,
        num_trials: int = 5,
    ) -> None:
        """Configure a positive, fixed number of independent child executions."""
        self.validate_constructor_parameters({"num_trials": num_trials})
        self._num_trials = num_trials
        super().__init__(
            objective_target=objective_target,
            child_attacks=[
                replace(
                    child_attack,
                    result_metadata={**child_attack.result_metadata, "trial_index": index},
                )
                for index in range(1, num_trials + 1)
            ],
            completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
        )

    @classmethod
    def validate_constructor_parameters(cls, params: dict[str, Any]) -> None:
        """
        Reject invalid trial counts before execution.

        Raises:
            ValueError: If num_trials is not a positive integer.
        """
        super().validate_constructor_parameters(params)
        count = params.get("num_trials", 5)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("num_trials must be a positive integer")

    def _build_identifier(self) -> ComponentIdentifier:
        child = self._child_attacks[0]
        return self._create_identifier(
            params={"num_trials": self._num_trials},
            children={"trial": child.technique_identifier or child.strategy.get_identifier()},
        )

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> SequentialAttackResult:
        result = await super()._perform_async(context=context)
        trials = result.child_attack_results
        successes = sum(trial.outcome is AttackOutcome.SUCCESS for trial in trials)
        valid = sum(trial.outcome in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE) for trial in trials)
        result.metadata["independent_trials"] = {
            "num_trials": self._num_trials,
            "successful_trials": successes,
            "valid_trials": valid,
            "attack_success_rate": successes / self._num_trials if valid == self._num_trials else None,
            "trials": [self._trial_metadata(index=index, result=trial) for index, trial in enumerate(trials, start=1)],
        }
        return result

    async def _run_child_attack_async(
        self,
        *,
        child_attack: SequentialChildAttack,
        memory_labels: dict[str, str],
        child_result_ids: list[str],
        attribution: AttackResultAttribution | None = None,
        expectation: ScoringExpectation | None = None,
        operation_id: uuid.UUID | None = None,
    ) -> AttackResult:
        previous_count = len(child_result_ids)
        try:
            return await super()._run_child_attack_async(
                child_attack=child_attack,
                memory_labels=memory_labels,
                child_result_ids=child_result_ids,
                attribution=attribution,
                expectation=expectation,
                operation_id=operation_id,
            )
        except Exception:
            # Continue only when the executor has durably recorded this trial's error.
            if len(child_result_ids) != previous_count + 1:
                raise
            stored = await self._memory.get_attack_results_async(
                attack_result_ids=[child_result_ids[-1]],
                result_selection=AttackResultSelection.ALL_RESULTS,
            )
            if not stored or stored[0].outcome is not AttackOutcome.ERROR:
                raise
            logger.warning("Independent trial failed; retaining its error and continuing.", exc_info=True)
            child_result_ids.pop()
            return stored[0]

    def _compute_outcome(self, *, results: list[AttackResult]) -> AttackOutcome:
        for outcome in (AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED, AttackOutcome.FAILURE):
            if any(result.outcome is outcome for result in results):
                return outcome
        return AttackOutcome.ERROR

    @staticmethod
    def _trial_metadata(*, index: int, result: AttackResult) -> dict[str, Any]:
        score = result.automated_score
        return {
            "trial_index": index,
            "attack_result_id": result.attack_result_id,
            "conversation_id": result.conversation_id,
            "outcome": result.outcome.value,
            "outcome_reason": result.outcome_reason,
            "score_id": str(score.id) if score is not None else None,
            "score_value": score.get_value() if score is not None and not score.is_undetermined else None,
            "score_rationale": score.score_rationale if score is not None else None,
        }
