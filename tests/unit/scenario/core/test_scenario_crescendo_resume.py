# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APITimeoutError, RateLimitError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, CrescendoAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    ConversationRetryReason,
    ConversationType,
    ScenarioRunState,
    SeedObjective,
)
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import AtomicAttack, AttackTechnique, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SelfAskRefusalScorer, SubStringScorer, TrueFalseScorer

_ATOMIC_ATTACK_NAME = "crescendo"
_OBJECTIVE_A = "Recover objective A"
_OBJECTIVE_B = "Recover objective B"
_MALFORMED_REPLY = "not valid JSON"
_REFUSAL = "I refuse to help."


class _CrescendoScenarioTechnique(ScenarioTechnique):
    CRESCENDO = ("crescendo", {"crescendo"})
    ALL = ("all", {"all"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _CrescendoResumeScenario(Scenario):
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget,
        objective_scorer: TrueFalseScorer,
        refusal_scorer: TrueFalseScorer,
        max_turns: int = 1,
        scenario_result_id: str | None = None,
    ) -> None:
        super().__init__(
            name="Crescendo Resume Scenario",
            version=1,
            technique_class=_CrescendoScenarioTechnique,
            default_dataset_config=DatasetConfiguration(),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )
        self._adversarial_chat = adversarial_chat
        self._refusal_scorer = refusal_scorer
        self._max_turns = max_turns

    async def _resolve_seed_groups_by_dataset_async(
        self,
        *,
        apply_sampling: bool = True,
    ) -> dict[str, list[AttackSeedGroup]]:
        return {
            "resume": [
                AttackSeedGroup(seeds=[SeedObjective(value=_OBJECTIVE_A)]),
                AttackSeedGroup(seeds=[SeedObjective(value=_OBJECTIVE_B)]),
            ]
        }

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        attack = CrescendoAttack(
            objective_target=context.objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=self._adversarial_chat),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=self._objective_scorer,
                refusal_scorer=self._refusal_scorer,
            ),
            max_backtracks=1,
            max_turns=self._max_turns,
        )
        return [
            AtomicAttack(
                atomic_attack_name=_ATOMIC_ATTACK_NAME,
                attack_technique=AttackTechnique(attack=attack),
                seed_groups=list(context.seed_groups),
                memory_labels=context.memory_labels,
            )
        ]


def _build_chat_completion(*, text: str, completion_id: str) -> ChatCompletion:
    return ChatCompletion(
        id=completion_id,
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                logprobs=None,
                message=ChatCompletionMessage(content=text, refusal=None, role="assistant"),
            )
        ],
        created=0,
        model="gpt-test",
        object="chat.completion",
    )


def _adversarial_reply(*, prompt: str) -> str:
    return json.dumps(
        {
            "next_message": prompt,
            "last_response_summary": "summary",
            "rationale": "rationale",
        }
    )


def _refusal_score_reply(*, refused: bool) -> str:
    return json.dumps(
        {
            "score_value": str(refused).lower(),
            "description": "refusal result",
            "rationale": "controlled test score",
        }
    )


def _build_target(*, model_name: str) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        model_name=model_name,
        endpoint="https://api.openai.com/v1",
        api_key="test-api-key",
    )


def _build_rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, text="rate limited", request=request)
    return RateLimitError("rate limited", response=response, body=None)


def _build_timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return APITimeoutError(request=request)


def _build_boundary_side_effect(
    *,
    schedule: list[tuple[str, ChatCompletion | BaseException]],
    events: list[str],
    requests: list[dict[str, Any]],
) -> Callable[..., Awaitable[ChatCompletion]]:
    async def _side_effect_async(**kwargs: Any) -> ChatCompletion:
        requests.append(kwargs)
        label, outcome = schedule.pop(0)
        events.append(label)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _side_effect_async


def _build_scenario(
    *,
    objective_target: OpenAIChatTarget,
    adversarial_target: OpenAIChatTarget,
    objective_scorer: SubStringScorer,
    refusal_scorer: SelfAskRefusalScorer,
    max_retries: int,
    max_turns: int = 1,
    scenario_result_id: str | None = None,
) -> _CrescendoResumeScenario:
    scenario = _CrescendoResumeScenario(
        adversarial_chat=adversarial_target,
        objective_scorer=objective_scorer,
        refusal_scorer=refusal_scorer,
        max_turns=max_turns,
        scenario_result_id=scenario_result_id,
    )
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "max_concurrency": 1,
            "max_retries": max_retries,
            "memory_labels": {"test": "crescendo-resume"},
        }
    )
    return scenario


def _message_contents(requests: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [request["messages"] for request in requests]


@pytest.mark.usefixtures("patch_central_database")
async def test_crescendo_scenario_retry_and_resume_use_real_components_without_stale_state() -> None:
    objective_target = _build_target(model_name="objective-model")
    adversarial_target = _build_target(model_name="adversarial-model")
    scorer_target = _build_target(model_name="scorer-model")
    objective_scorer = SubStringScorer(substring="RECOVERED")
    refusal_scorer = SelfAskRefusalScorer(chat_target=scorer_target)

    events: list[str] = []
    adversarial_requests: list[dict[str, Any]] = []
    objective_requests: list[dict[str, Any]] = []
    scorer_requests: list[dict[str, Any]] = []
    adversarial_schedule = [
        ("adversarial:a:rate-limit", _build_rate_limit_error()),
        (
            "adversarial:a:success",
            _build_chat_completion(text=_adversarial_reply(prompt="A prompt"), completion_id="adv-a"),
        ),
        (
            "adversarial:b:timeout-attempt",
            _build_chat_completion(text=_adversarial_reply(prompt="B timeout prompt"), completion_id="adv-b-timeout"),
        ),
        (
            "adversarial:b:malformed-1",
            _build_chat_completion(text=_MALFORMED_REPLY, completion_id="adv-b-malformed-1"),
        ),
        (
            "adversarial:b:malformed-2",
            _build_chat_completion(text=_MALFORMED_REPLY, completion_id="adv-b-malformed-2"),
        ),
        (
            "adversarial:b:refusal",
            _build_chat_completion(text=_adversarial_reply(prompt="B refused prompt"), completion_id="adv-b-refusal"),
        ),
        (
            "adversarial:b:recovery",
            _build_chat_completion(text=_adversarial_reply(prompt="B recovery prompt"), completion_id="adv-b-recovery"),
        ),
    ]
    objective_schedule = [
        ("objective:a:success", _build_chat_completion(text="RECOVERED A", completion_id="objective-a")),
        ("objective:b:timeout", _build_timeout_error()),
        ("objective:b:refusal", _build_chat_completion(text=_REFUSAL, completion_id="objective-b-refusal")),
        ("objective:b:recovery", _build_chat_completion(text="RECOVERED B", completion_id="objective-b-recovery")),
    ]
    scorer_schedule = [
        (
            "scorer:a:not-refusal",
            _build_chat_completion(text=_refusal_score_reply(refused=False), completion_id="score-a"),
        ),
        (
            "scorer:b:refusal",
            _build_chat_completion(text=_refusal_score_reply(refused=True), completion_id="score-b-1"),
        ),
        (
            "scorer:b:not-refusal",
            _build_chat_completion(text=_refusal_score_reply(refused=False), completion_id="score-b-2"),
        ),
    ]

    first_scenario = _build_scenario(
        objective_target=objective_target,
        adversarial_target=adversarial_target,
        objective_scorer=objective_scorer,
        refusal_scorer=refusal_scorer,
        max_retries=1,
    )
    memory = CentralMemory.get_memory_instance()

    with (
        patch.object(
            adversarial_target._async_client.chat.completions,
            "create",
            new=AsyncMock(
                side_effect=_build_boundary_side_effect(
                    schedule=adversarial_schedule,
                    events=events,
                    requests=adversarial_requests,
                )
            ),
        ),
        patch.object(
            objective_target._async_client.chat.completions,
            "create",
            new=AsyncMock(
                side_effect=_build_boundary_side_effect(
                    schedule=objective_schedule,
                    events=events,
                    requests=objective_requests,
                )
            ),
        ),
        patch.object(
            scorer_target._async_client.chat.completions,
            "create",
            new=AsyncMock(
                side_effect=_build_boundary_side_effect(
                    schedule=scorer_schedule,
                    events=events,
                    requests=scorer_requests,
                )
            ),
        ),
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        patch.object(
            memory,
            "update_scenario_run_state",
            wraps=memory.update_scenario_run_state,
        ) as update_state,
    ):
        await first_scenario.initialize_async()
        with pytest.raises(ScenarioPartialFailureException, match="1 of 1 objectives incomplete"):
            await first_scenario.run_async()

        scenario_result_id = first_scenario._scenario_result_id
        assert scenario_result_id is not None
        failed_result = memory.get_scenario_results(scenario_result_ids=[scenario_result_id])[0]
        assert failed_result.scenario_run_state == ScenarioRunState.FAILED
        assert failed_result.number_tries == 2
        assert [result.outcome for result in failed_result.attack_results[_ATOMIC_ATTACK_NAME]] == [
            AttackOutcome.SUCCESS,
            AttackOutcome.ERROR,
            AttackOutcome.ERROR,
        ]

        resumed_scenario = _build_scenario(
            objective_target=objective_target,
            adversarial_target=adversarial_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
            max_retries=0,
            scenario_result_id=scenario_result_id,
        )
        await resumed_scenario.initialize_async()
        assert resumed_scenario._atomic_attacks[0].objectives == [_OBJECTIVE_A, _OBJECTIVE_B]

        completed_result = await resumed_scenario.run_async()

    attack_results = completed_result.attack_results[_ATOMIC_ATTACK_NAME]
    objective_a_results = [result for result in attack_results if result.objective == _OBJECTIVE_A]
    objective_b_results = [result for result in attack_results if result.objective == _OBJECTIVE_B]
    error_results = [result for result in attack_results if result.outcome == AttackOutcome.ERROR]
    timeout_result = next(result for result in error_results if result.error_type == "Exception")
    malformed_result = next(result for result in error_results if result.error_type == "InvalidJsonException")
    recovered_result = next(result for result in objective_b_results if result.outcome == AttackOutcome.SUCCESS)

    assert completed_result.scenario_run_state == ScenarioRunState.COMPLETED
    assert completed_result.number_tries == 3
    assert completed_result.error_message is None
    assert completed_result.error_type is None
    assert len(objective_a_results) == 1
    assert len(objective_b_results) == 3
    assert [result.outcome for result in objective_b_results] == [
        AttackOutcome.ERROR,
        AttackOutcome.ERROR,
        AttackOutcome.SUCCESS,
    ]
    assert len({result.attack_result_id for result in attack_results}) == len(attack_results)
    assert len({result.conversation_id for result in attack_results}) == len(attack_results)
    assert all(result.labels == {"test": "crescendo-resume"} for result in attack_results)
    assert recovered_result.executed_turns == 1
    assert recovered_result.metadata["backtrack_count"] == 1
    assert recovered_result.last_response and recovered_result.last_response.converted_value == "RECOVERED B"
    assert recovered_result.last_score and recovered_result.last_score.get_value() is True
    assert recovered_result.last_score.objective == _OBJECTIVE_B
    assert recovered_result.last_score.message_piece_id == recovered_result.last_response.id

    timeout_messages = memory.get_conversation_messages(conversation_id=timeout_result.conversation_id)
    assert [message.api_role for message in timeout_messages] == ["user", "assistant"]
    assert timeout_messages[0].get_value() == "B timeout prompt"
    assert timeout_messages[1].get_piece().response_error == "processing"
    assert "APITimeoutError" in timeout_messages[1].get_value()

    pruned_conversations = recovered_result.get_conversations_by_type(ConversationType.PRUNED)
    adversarial_conversations = recovered_result.get_conversations_by_type(ConversationType.ADVERSARIAL)
    assert len(pruned_conversations) == 1
    assert len(adversarial_conversations) == 1
    pruned_messages = memory.get_conversation_messages(conversation_id=pruned_conversations[0].conversation_id)
    recovered_messages = memory.get_conversation_messages(conversation_id=recovered_result.conversation_id)
    assert [message.get_value() for message in pruned_messages] == ["B refused prompt", _REFUSAL]
    assert [message.get_value() for message in recovered_messages] == ["B recovery prompt", "RECOVERED B"]

    malformed_adversarial = malformed_result.get_conversations_by_type(ConversationType.ADVERSARIAL)
    assert len(malformed_adversarial) == 1
    malformed_conversation = memory._get_conversation(
        conversation_id=malformed_adversarial[0].conversation_id,
    )
    assert malformed_conversation is not None
    assert len(malformed_conversation.retries) == 1
    assert malformed_conversation.retries[0].reason == ConversationRetryReason.JSON_PARSING
    malformed_messages = memory.get_conversation_messages(
        conversation_id=malformed_adversarial[0].conversation_id,
    )
    assert [message.api_role for message in malformed_messages] == ["system", "user", "assistant"]
    assert malformed_messages[-1].get_value() == _MALFORMED_REPLY

    assert events == [
        "adversarial:a:rate-limit",
        "adversarial:a:success",
        "objective:a:success",
        "scorer:a:not-refusal",
        "adversarial:b:timeout-attempt",
        "objective:b:timeout",
        "adversarial:b:malformed-1",
        "adversarial:b:malformed-2",
        "adversarial:b:refusal",
        "objective:b:refusal",
        "scorer:b:refusal",
        "adversarial:b:recovery",
        "objective:b:recovery",
        "scorer:b:not-refusal",
    ]
    assert not adversarial_schedule
    assert not objective_schedule
    assert not scorer_schedule
    assert sleep_mock.await_count == 2
    assert all(call.args[0] >= 0 for call in sleep_mock.await_args_list)
    observed_states = [call.kwargs["scenario_run_state"] for call in update_state.call_args_list]
    assert observed_states == [
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.FAILED,
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.COMPLETED,
    ]

    adversarial_messages = _message_contents(adversarial_requests)
    assert adversarial_messages[0] == adversarial_messages[1]
    assert adversarial_messages[3] == adversarial_messages[4]
    objective_messages = _message_contents(objective_requests)
    assert [messages[-1]["content"] for messages in objective_messages] == [
        "A prompt",
        "B timeout prompt",
        "B refused prompt",
        "B recovery prompt",
    ]
    assert all(_REFUSAL not in json.dumps(message) for message in objective_messages[-1])
    scorer_messages = _message_contents(scorer_requests)
    scorer_inputs = [messages[-1]["content"] for messages in scorer_messages]
    assert "conversation_objective: A prompt" in scorer_inputs[0]
    assert "response_to_evaluate_input: RECOVERED A" in scorer_inputs[0]
    assert "conversation_objective: B refused prompt" in scorer_inputs[1]
    assert f"response_to_evaluate_input: {_REFUSAL}" in scorer_inputs[1]
    assert "conversation_objective: B recovery prompt" in scorer_inputs[2]
    assert "response_to_evaluate_input: RECOVERED B" in scorer_inputs[2]

    all_pieces = memory.get_message_pieces()
    assert len({piece.id for piece in all_pieces}) == len(all_pieces)
    assert len({piece.conversation_id for piece in recovered_messages}) == 1
    assert len({piece.conversation_id for piece in pruned_messages}) == 1
    assert timeout_result.total_retries == 0
    assert malformed_result.total_retries == 2
    assert [event.attempt_number for event in malformed_result.retry_events] == [1, 2]
    assert all(event.exception_type == "InvalidJsonException" for event in malformed_result.retry_events)
    assert all(event.component_role == "adversarial_chat" for event in malformed_result.retry_events)


@pytest.mark.usefixtures("patch_central_database")
async def test_crescendo_scenario_cancellation_preserves_progress_and_resumes_only_incomplete_objective() -> None:
    objective_target = _build_target(model_name="objective-model")
    adversarial_target = _build_target(model_name="adversarial-model")
    scorer_target = _build_target(model_name="scorer-model")
    objective_scorer = SubStringScorer(substring="RECOVERED")
    refusal_scorer = SelfAskRefusalScorer(chat_target=scorer_target)

    events: list[str] = []
    adversarial_requests: list[dict[str, Any]] = []
    objective_requests: list[dict[str, Any]] = []
    scorer_requests: list[dict[str, Any]] = []
    cancellation_started = asyncio.Event()
    block_until_cancelled = asyncio.Event()
    adversarial_schedule = [
        (
            "adversarial:a:success",
            _build_chat_completion(text=_adversarial_reply(prompt="A prompt"), completion_id="adv-a"),
        ),
        ("adversarial:b:rate-limit", _build_rate_limit_error()),
        (
            "adversarial:b:turn-1",
            _build_chat_completion(text=_adversarial_reply(prompt="B prompt 1"), completion_id="adv-b-1"),
        ),
        ("adversarial:b:malformed", _build_chat_completion(text=_MALFORMED_REPLY, completion_id="adv-b-malformed")),
        (
            "adversarial:b:refusal",
            _build_chat_completion(text=_adversarial_reply(prompt="B refused prompt"), completion_id="adv-b-refusal"),
        ),
        (
            "adversarial:b:recovery",
            _build_chat_completion(text=_adversarial_reply(prompt="B recovery prompt"), completion_id="adv-b-recovery"),
        ),
        (
            "adversarial:b:turn-3",
            _build_chat_completion(text=_adversarial_reply(prompt="B prompt 3"), completion_id="adv-b-3"),
        ),
        (
            "adversarial:b:turn-4",
            _build_chat_completion(text=_adversarial_reply(prompt="B prompt 4"), completion_id="adv-b-4"),
        ),
        (
            "adversarial:b:cancel",
            _build_chat_completion(text=_adversarial_reply(prompt="B prompt 5"), completion_id="adv-b-5"),
        ),
        (
            "adversarial:b:resumed",
            _build_chat_completion(text=_adversarial_reply(prompt="B resumed prompt"), completion_id="adv-b-resumed"),
        ),
    ]
    objective_schedule: list[tuple[str, ChatCompletion | BaseException | None]] = [
        ("objective:a:success", _build_chat_completion(text="RECOVERED A", completion_id="objective-a")),
        ("objective:b:turn-1", _build_chat_completion(text="B progress 1", completion_id="objective-b-1")),
        ("objective:b:refusal", _build_chat_completion(text=_REFUSAL, completion_id="objective-b-refusal")),
        ("objective:b:recovery", _build_chat_completion(text="B progress 2", completion_id="objective-b-2")),
        ("objective:b:turn-3", _build_chat_completion(text="B progress 3", completion_id="objective-b-3")),
        ("objective:b:turn-4", _build_chat_completion(text="B progress 4", completion_id="objective-b-4")),
        ("objective:b:cancel", None),
        ("objective:b:resumed", _build_chat_completion(text="RECOVERED B", completion_id="objective-b-resumed")),
    ]
    scorer_schedule = [
        (
            "scorer:a:not-refusal",
            _build_chat_completion(text=_refusal_score_reply(refused=False), completion_id="score-a"),
        ),
        *[
            (
                f"scorer:b:not-refusal-{turn}",
                _build_chat_completion(
                    text=_refusal_score_reply(refused=False),
                    completion_id=f"score-b-{turn}",
                ),
            )
            for turn in [1]
        ],
        (
            "scorer:b:refusal",
            _build_chat_completion(text=_refusal_score_reply(refused=True), completion_id="score-b-refusal"),
        ),
        *[
            (
                f"scorer:b:not-refusal-{turn}",
                _build_chat_completion(
                    text=_refusal_score_reply(refused=False),
                    completion_id=f"score-b-{turn}",
                ),
            )
            for turn in [2, 3, 4]
        ],
        (
            "scorer:b:resumed",
            _build_chat_completion(text=_refusal_score_reply(refused=False), completion_id="score-b-resumed"),
        ),
    ]

    async def objective_side_effect_async(**kwargs: Any) -> ChatCompletion:
        objective_requests.append(kwargs)
        label, outcome = objective_schedule.pop(0)
        events.append(label)
        if label == "objective:b:cancel":
            cancellation_started.set()
            await block_until_cancelled.wait()
            raise AssertionError("Cancellation should interrupt the blocked target request")
        if isinstance(outcome, BaseException):
            raise outcome
        assert outcome is not None
        return outcome

    first_scenario = _build_scenario(
        objective_target=objective_target,
        adversarial_target=adversarial_target,
        objective_scorer=objective_scorer,
        refusal_scorer=refusal_scorer,
        max_retries=0,
        max_turns=10,
    )
    memory = CentralMemory.get_memory_instance()

    with (
        patch.object(
            adversarial_target._async_client.chat.completions,
            "create",
            new=AsyncMock(
                side_effect=_build_boundary_side_effect(
                    schedule=adversarial_schedule,
                    events=events,
                    requests=adversarial_requests,
                )
            ),
        ),
        patch.object(
            objective_target._async_client.chat.completions,
            "create",
            new=AsyncMock(side_effect=objective_side_effect_async),
        ),
        patch.object(
            scorer_target._async_client.chat.completions,
            "create",
            new=AsyncMock(
                side_effect=_build_boundary_side_effect(
                    schedule=scorer_schedule,
                    events=events,
                    requests=scorer_requests,
                )
            ),
        ),
        patch("asyncio.sleep", new_callable=AsyncMock) as sleep_mock,
        patch.object(
            memory,
            "update_scenario_run_state",
            wraps=memory.update_scenario_run_state,
        ) as update_state,
    ):
        await first_scenario.initialize_async()
        first_attack = first_scenario._atomic_attacks[0].attack_technique.attack
        with patch.object(
            first_attack,
            "_teardown_async",
            new_callable=AsyncMock,
            wraps=first_attack._teardown_async,
        ) as first_teardown:
            scenario_task = asyncio.create_task(first_scenario.run_async())
            await asyncio.wait_for(cancellation_started.wait(), timeout=5)
            scenario_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await scenario_task

        # The shared strategy tears down after objective A and again when objective B is cancelled.
        assert first_teardown.await_count == 2
        scenario_result_id = first_scenario._scenario_result_id
        assert scenario_result_id is not None
        cancelled_result = memory.get_scenario_results(scenario_result_ids=[scenario_result_id])[0]
        assert cancelled_result.scenario_run_state == ScenarioRunState.CANCELLED
        assert cancelled_result.number_tries == 1
        assert [result.objective for result in cancelled_result.attack_results[_ATOMIC_ATTACK_NAME]] == [_OBJECTIVE_A]
        assert memory.get_attack_results(objective=_OBJECTIVE_B) == []

        partial_piece = next(piece for piece in memory.get_message_pieces() if piece.original_value == "B progress 4")
        partial_conversation_id = partial_piece.conversation_id
        partial_piece_ids = {piece.id for piece in memory.get_message_pieces(conversation_id=partial_conversation_id)}

        resumed_scenario = _build_scenario(
            objective_target=objective_target,
            adversarial_target=adversarial_target,
            objective_scorer=objective_scorer,
            refusal_scorer=refusal_scorer,
            max_retries=0,
            max_turns=10,
            scenario_result_id=scenario_result_id,
        )
        await resumed_scenario.initialize_async()
        resumed_attack = resumed_scenario._atomic_attacks[0].attack_technique.attack
        with patch.object(
            resumed_attack,
            "_teardown_async",
            new_callable=AsyncMock,
            wraps=resumed_attack._teardown_async,
        ) as resumed_teardown:
            completed_result = await resumed_scenario.run_async()

    # Resume executes only the incomplete objective B.
    resumed_teardown.assert_awaited_once()
    attack_results = completed_result.attack_results[_ATOMIC_ATTACK_NAME]
    assert completed_result.scenario_run_state == ScenarioRunState.COMPLETED
    assert completed_result.number_tries == 2
    assert [result.objective for result in attack_results] == [_OBJECTIVE_A, _OBJECTIVE_B]
    assert len({result.attack_result_id for result in attack_results}) == 2
    assert len({result.conversation_id for result in attack_results}) == 2

    objective_a_result, objective_b_result = attack_results
    assert objective_a_result.executed_turns == 1
    assert objective_b_result.executed_turns == 1
    assert objective_b_result.conversation_id != partial_conversation_id
    assert objective_b_result.last_response and objective_b_result.last_response.converted_value == "RECOVERED B"
    assert partial_piece_ids <= {
        piece.id for piece in memory.get_message_pieces(conversation_id=partial_conversation_id)
    }
    resumed_messages = memory.get_conversation_messages(conversation_id=objective_b_result.conversation_id)
    assert [message.get_value() for message in resumed_messages] == ["B resumed prompt", "RECOVERED B"]
    assert [
        message.get_value() for message in memory.get_conversation_messages(conversation_id=partial_conversation_id)
    ] == [
        "B prompt 1",
        "B progress 1",
        "B recovery prompt",
        "B progress 2",
        "B prompt 3",
        "B progress 3",
        "B prompt 4",
        "B progress 4",
    ]

    assert events == [
        "adversarial:a:success",
        "objective:a:success",
        "scorer:a:not-refusal",
        "adversarial:b:rate-limit",
        "adversarial:b:turn-1",
        "objective:b:turn-1",
        "scorer:b:not-refusal-1",
        "adversarial:b:malformed",
        "adversarial:b:refusal",
        "objective:b:refusal",
        "scorer:b:refusal",
        "adversarial:b:recovery",
        "objective:b:recovery",
        "scorer:b:not-refusal-2",
        "adversarial:b:turn-3",
        "objective:b:turn-3",
        "scorer:b:not-refusal-3",
        "adversarial:b:turn-4",
        "objective:b:turn-4",
        "scorer:b:not-refusal-4",
        "adversarial:b:cancel",
        "objective:b:cancel",
        "adversarial:b:resumed",
        "objective:b:resumed",
        "scorer:b:resumed",
    ]
    assert not adversarial_schedule
    assert not objective_schedule
    assert not scorer_schedule
    assert sleep_mock.await_count == 2
    adversarial_messages = _message_contents(adversarial_requests)
    assert adversarial_messages[1] == adversarial_messages[2]
    assert adversarial_messages[3] == adversarial_messages[4]
    assert [call.kwargs["scenario_run_state"] for call in update_state.call_args_list] == [
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.CANCELLED,
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.COMPLETED,
    ]
