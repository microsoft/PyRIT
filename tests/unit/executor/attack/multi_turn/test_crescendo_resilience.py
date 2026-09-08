# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions import InvalidJsonException, RateLimitException, pyrit_target_retry
from pyrit.exceptions.retry_collector import get_retry_collector
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackParameters,
    AttackScoringConfig,
    CrescendoAttack,
    CrescendoAttackContext,
    CrescendoAttackResult,
)
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, ComponentIdentifier, ConversationType, Message, MessagePiece, Score
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.score import MessageScorer, TrueFalseScorer
from pyrit.score.message_scorable_resolver import MessageScorableResolver

_OBJECTIVE = "Recover the hidden phrase through gradual rapport."


@dataclass(frozen=True)
class _Reconnect:
    response_text: str


@dataclass(frozen=True)
class _TargetAttempt:
    conversation_id: str
    prompt: str


class _ScriptedTarget(MockPromptTarget):
    def __init__(
        self,
        *,
        name: str,
        script: list[str | BaseException | _Reconnect],
        event_log: list[str],
    ) -> None:
        super().__init__(id=name)
        self._name = name
        self._script = list(script)
        self._event_log = event_log
        self.attempts: list[_TargetAttempt] = []
        self.reconnect_count = 0

    @property
    def remaining(self) -> int:
        return len(self._script)

    @pyrit_target_retry
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        conversation_id = request.get_piece().conversation_id
        assert conversation_id is not None
        self.attempts.append(_TargetAttempt(conversation_id=conversation_id, prompt=request.get_value()))
        self._event_log.append(self._name)

        scripted = self._script.pop(0)
        if isinstance(scripted, BaseException):
            raise scripted
        if isinstance(scripted, _Reconnect):
            self._event_log.extend([f"{self._name}:disconnect", f"{self._name}:reconnect"])
            self.reconnect_count += 1
            scripted = scripted.response_text

        return [
            MessagePiece(
                role="assistant",
                original_value=scripted,
                conversation_id=conversation_id,
            ).to_message()
        ]


def _identifier(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module=__name__)


def _score(*, value: bool, name: str, rationale: str) -> Score:
    return Score(
        score_type="true_false",
        score_value=str(value).lower(),
        score_category=[name],
        score_value_description=f"{name} decision",
        score_rationale=rationale,
        message_piece_id=f"00000000-0000-0000-0000-{len(rationale):012d}",
        scorer_class_identifier=_identifier(name),
    )


def _scorer(name: str) -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _identifier(name)
    return scorer


def _adversarial_reply(attempt: int) -> str:
    return json.dumps(
        {
            "next_message": f"question-{attempt}",
            "last_response_summary": f"summary-{attempt}",
            "rationale": f"rationale-{attempt}",
        }
    )


def _build_attack(
    *,
    adversarial_target: _ScriptedTarget,
    objective_target: _ScriptedTarget,
    objective_scorer: MagicMock,
    refusal_scorer: MagicMock,
    max_backtracks: int = 2,
    max_turns: int = 10,
) -> CrescendoAttack:
    return CrescendoAttack(
        objective_target=objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_target),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        ),
        prompt_normalizer=PromptNormalizer(),
        max_backtracks=max_backtracks,
        max_turns=max_turns,
    )


def _context() -> CrescendoAttackContext:
    return CrescendoAttackContext(
        params=AttackParameters(
            objective=_OBJECTIVE,
            memory_labels={"suite": "crescendo-resilience"},
        )
    )


def _false_objective_score(turn: int) -> Score:
    return _score(value=False, name="ObjectiveScorer", rationale=f"objective false at turn {turn}")


def _objective_scoring_result(score: Score) -> dict[str, list[Score]]:
    return {"objective_scores": [score], "auxiliary_scores": []}


@pytest.mark.usefixtures("patch_central_database")
class TestCrescendoMixedFailureRecovery:
    async def test_ten_executed_turns_preserve_state_across_mixed_failures(self):
        event_log: list[str] = []
        partial_adversarial_reply = json.dumps({"next_message": "missing required fields"})
        adversarial_script = [_adversarial_reply(1), partial_adversarial_reply]
        adversarial_script.extend(_adversarial_reply(attempt) for attempt in range(2, 13))

        objective_script: list[str | BaseException | _Reconnect] = [
            "response-1",
            "response-2",
            RateLimitException(message="transient rate limit"),
            "response-3",
            "refusal-backtrack-1",
            "refusal-backtrack-2",
            "response-4-recovered",
            "response-5",
            _Reconnect(response_text="response-6-after-reconnect"),
            "refusal-at-backtrack-limit",
            "response-8-recovered",
            "response-9",
            "response-10-final",
        ]
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=adversarial_script,
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(
            name="objective",
            script=objective_script,
            event_log=event_log,
        )
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        refusal_scores = [
            [_score(value=attempt in {4, 5, 9}, name="RefusalScorer", rationale=f"refusal attempt {attempt}")]
            for attempt in range(1, 13)
        ]
        objective_scores = [_false_objective_score(turn) for turn in range(1, 10)]
        objective_scores.append(_score(value=True, name="ObjectiveScorer", rationale="objective achieved at turn 10"))
        refusal_score_iter = iter(refusal_scores)
        objective_score_iter = iter(objective_scores)

        async def score_refusal(**_kwargs):
            event_log.append("refusal-scorer")
            return next(refusal_score_iter)

        async def score_objective(**_kwargs):
            event_log.append("objective-scorer")
            return _objective_scoring_result(next(objective_score_iter))

        refusal_scorer.score_async.side_effect = score_refusal

        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()

        with (
            patch.object(
                MessageScorer,
                "score_response_async",
                new_callable=AsyncMock,
                side_effect=score_objective,
            ) as score_response,
            patch(
                "pyrit.exceptions.exception_classes._DynamicWaitRandomExponential.__call__",
                return_value=0,
            ),
        ):
            result = await attack.execute_with_context_async(context=context)

        assert isinstance(result, CrescendoAttackResult)
        assert result.outcome is AttackOutcome.SUCCESS
        assert result.executed_turns == 10
        assert result.backtrack_count == 2
        assert result.outcome_reason == "Objective achieved in 10 turns"
        assert result.last_response is not None
        assert result.last_response.converted_value == "response-10-final"
        assert result.last_score == objective_scores[-1]
        assert result.labels == {"suite": "crescendo-resilience"}
        assert context.last_response_was_refusal is False
        assert context.last_accepted_response is not None
        assert context.last_accepted_response.get_value() == "response-10-final"
        assert context.refused_text is None

        expected_events: list[str] = []
        for attempt in range(1, 13):
            expected_events.append("adversarial")
            if attempt == 2:
                expected_events.append("adversarial")
            expected_events.append("objective")
            if attempt == 3:
                expected_events.append("objective")
            if attempt == 8:
                expected_events.extend(["objective:disconnect", "objective:reconnect"])
            expected_events.append("refusal-scorer")
            if attempt not in {4, 5}:
                expected_events.append("objective-scorer")
        assert event_log == expected_events
        assert adversarial_target.remaining == 0
        assert objective_target.remaining == 0
        assert objective_target.reconnect_count == 1

        expected_objective_prompts = [
            "question-1",
            "question-2",
            "question-3",
            "question-3",
            *[f"question-{attempt}" for attempt in range(4, 13)],
        ]
        assert [attempt.prompt for attempt in objective_target.attempts] == expected_objective_prompts
        initial_conversation_id = objective_target.attempts[0].conversation_id
        first_branch_id = objective_target.attempts[5].conversation_id
        final_conversation_id = objective_target.attempts[6].conversation_id
        assert len({initial_conversation_id, first_branch_id, final_conversation_id}) == 3
        assert all(attempt.conversation_id == initial_conversation_id for attempt in objective_target.attempts[:5])
        assert objective_target.attempts[5].conversation_id == first_branch_id
        assert all(attempt.conversation_id == final_conversation_id for attempt in objective_target.attempts[6:])
        assert result.conversation_id == final_conversation_id
        assert len({attempt.conversation_id for attempt in adversarial_target.attempts}) == 1

        # A scorable names piece ids rather than carrying the message, so read them back.
        memory = CentralMemory.get_memory_instance()
        refusal_inputs = [
            MessageScorableResolver().resolve(scorable=call.kwargs["scorable"], memory=memory).get_value()
            for call in refusal_scorer.score_async.await_args_list
        ]
        assert refusal_inputs == [
            "response-1",
            "response-2",
            "response-3",
            "refusal-backtrack-1",
            "refusal-backtrack-2",
            "response-4-recovered",
            "response-5",
            "response-6-after-reconnect",
            "refusal-at-backtrack-limit",
            "response-8-recovered",
            "response-9",
            "response-10-final",
        ]
        assert [call.kwargs["expectation"].objective for call in refusal_scorer.score_async.await_args_list] == [
            f"question-{attempt}" for attempt in range(1, 13)
        ]
        objective_inputs = [call.kwargs["response"].get_value() for call in score_response.await_args_list]
        assert refusal_scorer.score_async.await_count == 12
        assert score_response.await_count == 10
        assert objective_inputs == [
            "response-1",
            "response-2",
            "response-3",
            "response-4-recovered",
            "response-5",
            "response-6-after-reconnect",
            "refusal-at-backtrack-limit",
            "response-8-recovered",
            "response-9",
            "response-10-final",
        ]

        related_by_type = {
            conversation_type: {
                reference.conversation_id
                for reference in result.related_conversations
                if reference.conversation_type is conversation_type
            }
            for conversation_type in ConversationType
        }
        assert related_by_type[ConversationType.PRUNED] == {initial_conversation_id, first_branch_id}
        assert related_by_type[ConversationType.ADVERSARIAL] == {adversarial_target.attempts[0].conversation_id}

        memory = attack._memory
        final_pieces = memory.get_message_pieces(conversation_id=final_conversation_id)
        assert len(final_pieces) == 20
        assert len({piece.id for piece in final_pieces}) == 20
        assert [piece.original_value for piece in final_pieces if piece.api_role == "user"] == [
            "question-1",
            "question-2",
            "question-3",
            *[f"question-{attempt}" for attempt in range(6, 13)],
        ]
        assert [piece.original_value for piece in final_pieces if piece.api_role == "assistant"] == [
            "response-1",
            "response-2",
            "response-3",
            "response-4-recovered",
            "response-5",
            "response-6-after-reconnect",
            "refusal-at-backtrack-limit",
            "response-8-recovered",
            "response-9",
            "response-10-final",
        ]

        adversarial_conversation_id = adversarial_target.attempts[0].conversation_id
        adversarial_pieces = memory.get_message_pieces(conversation_id=adversarial_conversation_id)
        assert len(adversarial_pieces) == 25
        assert partial_adversarial_reply not in {piece.original_value for piece in adversarial_pieces}
        adversarial_conversation = memory._get_conversation(conversation_id=adversarial_conversation_id)
        assert adversarial_conversation is not None
        assert len(adversarial_conversation.retries) == 1

        assert result.total_retries == 2
        assert {event.exception_type for event in result.retry_events} == {
            "InvalidJsonException",
            "RateLimitException",
        }
        stored_results = memory.get_attack_results(objective=_OBJECTIVE)
        assert len(stored_results) == 1
        assert stored_results[0].outcome is AttackOutcome.SUCCESS
        assert stored_results[0].executed_turns == 10
        assert stored_results[0].metadata["backtrack_count"] == 2


@pytest.mark.usefixtures("patch_central_database")
class TestCrescendoTerminalBoundaries:
    async def test_refusal_scorer_failure_aborts_before_objective_scoring_and_persists_error(self):
        event_log: list[str] = []
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=[_adversarial_reply(1)],
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(
            name="objective",
            script=["response-1"],
            event_log=event_log,
        )
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        refusal_scorer.score_async.side_effect = RuntimeError("refusal scorer unavailable")
        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()

        with (
            patch.object(MessageScorer, "score_response_async", new_callable=AsyncMock) as score_response,
            patch.object(attack, "_teardown_async", new_callable=AsyncMock, wraps=attack._teardown_async) as teardown,
        ):
            with pytest.raises(RuntimeError, match="Strategy execution failed") as exc_info:
                await attack.execute_with_context_async(context=context)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == "refusal scorer unavailable"
        teardown.assert_awaited_once_with(context=context)
        assert event_log == ["adversarial", "objective"]
        assert context.executed_turns == 0
        assert context.last_response is not None
        assert context.last_response.get_value() == "response-1"
        refusal_scorer.score_async.assert_awaited_once()
        score_response.assert_not_awaited()

        pieces = attack._memory.get_message_pieces(conversation_id=context.session.conversation_id)
        assert [piece.api_role for piece in pieces] == ["user", "assistant"]
        assert [piece.original_value for piece in pieces] == ["question-1", "response-1"]
        assert len({piece.id for piece in pieces}) == 2

        stored_results = attack._memory.get_attack_results(objective=_OBJECTIVE)
        assert len(stored_results) == 1
        assert stored_results[0].outcome is AttackOutcome.ERROR
        assert stored_results[0].error_type == "RuntimeError"
        assert stored_results[0].error_message == "refusal scorer unavailable"
        assert stored_results[0].executed_turns == 0
        assert not any(result.outcome is AttackOutcome.SUCCESS for result in stored_results)

    async def test_objective_scorer_failure_persists_partial_history_and_one_error_result(self):
        event_log: list[str] = []
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=[_adversarial_reply(attempt) for attempt in range(1, 6)],
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(
            name="objective",
            script=[f"response-{attempt}" for attempt in range(1, 6)],
            event_log=event_log,
        )
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        refusal_scorer.score_async.side_effect = [
            [_score(value=False, name="RefusalScorer", rationale=f"accepted attempt {attempt}")]
            for attempt in range(1, 6)
        ]
        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()
        scorer_side_effect: list[dict[str, list[Score]] | BaseException] = [
            _objective_scoring_result(_false_objective_score(turn)) for turn in range(1, 5)
        ]
        scorer_side_effect.append(RuntimeError("scorer unavailable"))

        with (
            patch.object(
                MessageScorer,
                "score_response_async",
                new_callable=AsyncMock,
                side_effect=scorer_side_effect,
            ) as score_response,
            patch.object(attack, "_teardown_async", new_callable=AsyncMock, wraps=attack._teardown_async) as teardown,
        ):
            with pytest.raises(RuntimeError, match="Strategy execution failed") as exc_info:
                await attack.execute_with_context_async(context=context)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert str(exc_info.value.__cause__) == "scorer unavailable"
        teardown.assert_awaited_once_with(context=context)
        assert context.executed_turns == 4
        assert score_response.await_count == 5
        assert refusal_scorer.score_async.await_count == 5
        partial_pieces = attack._memory.get_message_pieces(conversation_id=context.session.conversation_id)
        assert len(partial_pieces) == 10
        assert [piece.original_value for piece in partial_pieces if piece.api_role == "assistant"] == [
            "response-1",
            "response-2",
            "response-3",
            "response-4",
            "response-5",
        ]
        stored_results = attack._memory.get_attack_results(objective=_OBJECTIVE)
        assert len(stored_results) == 1
        assert stored_results[0].outcome is AttackOutcome.ERROR
        assert stored_results[0].error_type == "RuntimeError"
        assert stored_results[0].error_message == "scorer unavailable"
        assert not any(result.outcome is AttackOutcome.SUCCESS for result in stored_results)

    async def test_malformed_adversarial_reply_exhaustion_rolls_back_only_retryable_turn(self):
        event_log: list[str] = []
        malformed = "not json"
        partial = json.dumps({"next_message": "missing required fields"})
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=[malformed, partial],
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(name="objective", script=[], event_log=event_log)
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()

        with pytest.raises(RuntimeError, match="Strategy execution failed") as exc_info:
            await attack.execute_with_context_async(context=context)

        root_cause: BaseException | None = exc_info.value
        while root_cause is not None and root_cause.__cause__ is not None:
            root_cause = root_cause.__cause__
        assert isinstance(root_cause, InvalidJsonException)
        assert event_log == ["adversarial", "adversarial"]
        assert objective_target.attempts == []
        refusal_scorer.score_async.assert_not_awaited()

        adversarial_conversation_id = adversarial_target.attempts[0].conversation_id
        pieces = attack._memory.get_message_pieces(conversation_id=adversarial_conversation_id)
        values = [piece.original_value for piece in pieces]
        assert malformed not in values
        assert partial in values
        conversation = attack._memory._get_conversation(conversation_id=adversarial_conversation_id)
        assert conversation is not None
        assert len(conversation.retries) == 1

        stored_results = attack._memory.get_attack_results(objective=_OBJECTIVE)
        assert len(stored_results) == 1
        assert stored_results[0].outcome is AttackOutcome.ERROR
        assert stored_results[0].total_retries == 2
        assert all(event.exception_type == "InvalidJsonException" for event in stored_results[0].retry_events)

    async def test_target_retry_exhaustion_persists_one_failed_send_and_propagates(self):
        event_log: list[str] = []
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=[_adversarial_reply(1)],
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(
            name="objective",
            script=[
                RateLimitException(message="rate limit attempt 1"),
                RateLimitException(message="rate limit attempt 2"),
            ],
            event_log=event_log,
        )
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()

        with (
            patch(
                "pyrit.exceptions.exception_classes._DynamicWaitRandomExponential.__call__",
                return_value=0,
            ),
            pytest.raises(RuntimeError, match="Strategy execution failed") as exc_info,
        ):
            await attack.execute_with_context_async(context=context)

        assert isinstance(exc_info.value.__cause__, Exception)
        assert event_log == ["adversarial", "objective", "objective"]
        assert [attempt.prompt for attempt in objective_target.attempts] == ["question-1", "question-1"]
        refusal_scorer.score_async.assert_not_awaited()

        pieces = attack._memory.get_message_pieces(conversation_id=context.session.conversation_id)
        assert len(pieces) == 2
        assert pieces[0].original_value == "question-1"
        assert pieces[1].response_error == "processing"
        stored_results = attack._memory.get_attack_results(objective=_OBJECTIVE)
        assert len(stored_results) == 1
        assert stored_results[0].outcome is AttackOutcome.ERROR
        assert stored_results[0].total_retries == 2
        assert all(event.exception_type == "RateLimitException" for event in stored_results[0].retry_events)

    async def test_cancellation_runs_teardown_preserves_completed_turns_and_clears_retry_state(self):
        event_log: list[str] = []
        adversarial_target = _ScriptedTarget(
            name="adversarial",
            script=[_adversarial_reply(attempt) for attempt in range(1, 6)],
            event_log=event_log,
        )
        objective_target = _ScriptedTarget(
            name="objective",
            script=[
                "response-1",
                "response-2",
                "response-3",
                "response-4",
                asyncio.CancelledError(),
            ],
            event_log=event_log,
        )
        objective_scorer = _scorer("ObjectiveScorer")
        refusal_scorer = _scorer("RefusalScorer")
        refusal_scorer.score_async.side_effect = [
            [_score(value=False, name="RefusalScorer", rationale=f"accepted attempt {attempt}")]
            for attempt in range(1, 5)
        ]
        attack = _build_attack(
            adversarial_target=adversarial_target,
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
        )
        context = _context()

        with (
            patch.object(
                MessageScorer,
                "score_response_async",
                new_callable=AsyncMock,
                side_effect=[_objective_scoring_result(_false_objective_score(turn)) for turn in range(1, 5)],
            ),
            patch.object(attack, "_teardown_async", new_callable=AsyncMock, wraps=attack._teardown_async) as teardown,
            pytest.raises(asyncio.CancelledError),
        ):
            await attack.execute_with_context_async(context=context)

        teardown.assert_awaited_once_with(context=context)
        assert context.executed_turns == 4
        assert event_log == [
            "adversarial",
            "objective",
            "adversarial",
            "objective",
            "adversarial",
            "objective",
            "adversarial",
            "objective",
            "adversarial",
            "objective",
        ]
        pieces = attack._memory.get_message_pieces(conversation_id=context.session.conversation_id)
        assert len(pieces) == 8
        assert [piece.original_value for piece in pieces if piece.api_role == "assistant"] == [
            "response-1",
            "response-2",
            "response-3",
            "response-4",
        ]
        assert attack._memory.get_attack_results(objective=_OBJECTIVE) == []
        assert get_retry_collector() is None
