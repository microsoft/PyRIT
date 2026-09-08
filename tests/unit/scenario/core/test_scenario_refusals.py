# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputRefusal, ResponseOutputText

from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    ComponentIdentifier,
    MessagePiece,
    ScenarioRunState,
    Score,
    SeedObjective,
)
from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget, PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import MessageTrueFalseScorer, ScorerPromptValidator, TrueFalseScorer

_ATOMIC_ATTACK_NAME = "structured_refusal"
_REFUSAL = "I cannot assist with that request."
_TEXT_RESPONSE = "This is a benign response."
_TRANSPORT_ERROR = "Simulated transport failure."


class _RefusalScenarioTechnique(ScenarioTechnique):
    PROMPT_SENDING = ("prompt_sending", {"prompt_sending"})
    ALL = ("all", {"all"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _RecordingObjectiveScorer(MessageTrueFalseScorer):
    def __init__(self) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))
        self.scored_pieces: list[MessagePiece] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(
        self,
        message_piece: MessagePiece,
        *,
        objective: str | None = None,
    ) -> list[Score]:
        self.scored_pieces.append(message_piece)
        return [
            Score(
                score_value="false",
                score_value_description="Objective not achieved",
                score_type="true_false",
                score_rationale="The response did not achieve the objective.",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]


class _RefusalScenario(Scenario):
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(
        self,
        *,
        atomic_attacks: list[AtomicAttack] | None = None,
        objective_scorer: TrueFalseScorer | None = None,
    ) -> None:
        resolved_scorer = objective_scorer or _RecordingObjectiveScorer()
        super().__init__(
            name="Structured Refusal Scenario",
            version=1,
            technique_class=_RefusalScenarioTechnique,
            default_dataset_config=DatasetConfiguration(),
            objective_scorer=resolved_scorer,
        )
        self._test_atomic_attacks = atomic_attacks or []

    async def _resolve_seed_groups_by_dataset_async(
        self,
        *,
        apply_sampling: bool = True,
    ) -> dict[str, list[AttackSeedGroup]]:
        return {}

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return self._test_atomic_attacks


@pytest.fixture(params=["responses", "chat_completions"], ids=["responses-api", "chat-completions"])
def openai_target(
    request: pytest.FixtureRequest,
    patch_central_database: MagicMock,
) -> OpenAIResponseTarget | OpenAIChatTarget:
    if request.param == "responses":
        return OpenAIResponseTarget(
            model_name="gpt-test",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )
    return OpenAIChatTarget(
        model_name="gpt-test",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )


def _build_responses_api_response(*, response_kind: str) -> MagicMock:
    if response_kind == "refusal":
        content = [ResponseOutputRefusal(refusal=_REFUSAL, type="refusal")]
    elif response_kind == "text":
        content = [ResponseOutputText(annotations=[], text=_TEXT_RESPONSE, type="output_text")]
    else:
        raise ValueError(f"Unsupported response kind: {response_kind}")

    output = ResponseOutputMessage(
        id=f"{response_kind}-message",
        content=content,
        role="assistant",
        status="completed",
        type="message",
    )
    response = MagicMock(spec=Response)
    response.error = None
    response.status = "completed"
    response.output = [output]
    return response


def _build_chat_completion(*, response_kind: str) -> ChatCompletion:
    if response_kind == "refusal":
        message = ChatCompletionMessage(content=None, refusal=_REFUSAL, role="assistant")
    elif response_kind == "text":
        message = ChatCompletionMessage(content=_TEXT_RESPONSE, refusal=None, role="assistant")
    else:
        raise ValueError(f"Unsupported response kind: {response_kind}")

    return ChatCompletion(
        id=f"{response_kind}-completion",
        choices=[Choice(finish_reason="stop", index=0, logprobs=None, message=message)],
        created=0,
        model="gpt-test",
        object="chat.completion",
    )


def _configure_target_responses(
    *,
    target: OpenAIResponseTarget | OpenAIChatTarget,
    response_kinds: Sequence[str],
) -> AsyncMock:
    side_effects: list[object | BaseException] = []
    for response_kind in response_kinds:
        if response_kind == "error":
            side_effects.append(RuntimeError(_TRANSPORT_ERROR))
        elif isinstance(target, OpenAIResponseTarget):
            side_effects.append(_build_responses_api_response(response_kind=response_kind))
        else:
            side_effects.append(_build_chat_completion(response_kind=response_kind))

    create_mock = AsyncMock(side_effect=side_effects)
    if isinstance(target, OpenAIResponseTarget):
        target._async_client.responses.create = create_mock  # type: ignore[method-assign]
    else:
        target._async_client.chat.completions.create = create_mock  # type: ignore[method-assign]
    return create_mock


def _build_scenario(
    *,
    target: PromptTarget,
    response_kinds: Sequence[str],
) -> tuple[_RefusalScenario, _RecordingObjectiveScorer, list[str]]:
    scorer = _RecordingObjectiveScorer()
    objectives = [f"{response_kind} objective {index}" for index, response_kind in enumerate(response_kinds)]
    seed_groups = [AttackSeedGroup(seeds=[SeedObjective(value=objective)]) for objective in objectives]
    atomic_attack = build_baseline_atomic_attack(
        objective_target=target,
        objective_scorer=scorer,
        seed_groups=seed_groups,
        atomic_attack_name=_ATOMIC_ATTACK_NAME,
    )
    scenario = _RefusalScenario(atomic_attacks=[atomic_attack], objective_scorer=scorer)
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "max_concurrency": 1,
            "max_retries": 0,
        }
    )
    return scenario, scorer, objectives


@pytest.mark.parametrize(
    "response_kinds",
    [
        pytest.param(("refusal", "refusal"), id="all-objectives-refused"),
        pytest.param(("refusal", "text"), id="some-objectives-refused"),
    ],
)
async def test_scenario_structured_refusals_are_completed_and_scored(
    openai_target: OpenAIResponseTarget | OpenAIChatTarget,
    response_kinds: tuple[str, str],
) -> None:
    create_mock = _configure_target_responses(target=openai_target, response_kinds=response_kinds)
    scenario, scorer, _ = _build_scenario(target=openai_target, response_kinds=response_kinds)

    await scenario.initialize_async()
    result = await scenario.run_async()

    attack_results = result.attack_results[_ATOMIC_ATTACK_NAME]
    expected_refusals = response_kinds.count("refusal")
    refusal_results = [
        item for item in attack_results if item.last_response and item.last_response.structured_refusal is not None
    ]
    scored_refusals = [piece for piece in scorer.scored_pieces if piece.structured_refusal is not None]

    assert result.scenario_run_state == ScenarioRunState.COMPLETED
    assert result.error_type is None
    assert len(attack_results) == len(response_kinds)
    assert len(refusal_results) == expected_refusals
    assert all(item.outcome == AttackOutcome.FAILURE for item in attack_results)
    assert all(item.last_score and item.last_score.get_value() is False for item in attack_results)
    assert all(item.last_response.converted_value_data_type == "error" for item in refusal_results)
    assert all(item.last_response.structured_refusal == _REFUSAL for item in refusal_results)
    assert len(scored_refusals) == expected_refusals
    assert all(piece.converted_value_data_type == "text" for piece in scored_refusals)
    assert all(piece.converted_value == _REFUSAL for piece in scored_refusals)
    assert create_mock.await_count == len(response_kinds)


async def test_scenario_counts_refusal_as_completed_when_another_objective_errors(
    openai_target: OpenAIResponseTarget | OpenAIChatTarget,
) -> None:
    response_kinds = ("refusal", "error")
    _configure_target_responses(target=openai_target, response_kinds=response_kinds)
    scenario, scorer, objectives = _build_scenario(target=openai_target, response_kinds=response_kinds)
    await scenario.initialize_async()

    with pytest.raises(ScenarioPartialFailureException, match="1 of 2 objectives incomplete") as exc_info:
        await scenario.run_async()

    error = exc_info.value
    assert error.completed_count == 1
    assert error.incomplete_count == 1
    assert error.incomplete_objectives[0][0] == objectives[1]
    assert _TRANSPORT_ERROR in str(error.incomplete_objectives[0][1])
    assert error.__cause__ is error.incomplete_objectives[0][1]
    assert len(scorer.scored_pieces) == 1
    assert scorer.scored_pieces[0].converted_value == _REFUSAL

    stored_result = CentralMemory.get_memory_instance().get_scenario_results(
        scenario_result_ids=[scenario._scenario_result_id]
    )[0]
    stored_attack_results = stored_result.attack_results[_ATOMIC_ATTACK_NAME]
    refusal_results = [
        item
        for item in stored_attack_results
        if item.last_response and item.last_response.structured_refusal is not None
    ]

    assert stored_result.scenario_run_state == ScenarioRunState.FAILED
    assert stored_result.error_type == "ScenarioPartialFailureException"
    assert len(refusal_results) == 1
    assert refusal_results[0].outcome == AttackOutcome.FAILURE
    assert refusal_results[0].last_score and refusal_results[0].last_score.get_value() is False
