# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from openai.types.completion import Completion

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import MemoryInterface
from pyrit.models import AttackOutcome, ContentScorable, Message, MessagePiece, MessageScorable
from pyrit.prompt_target import OpenAICompletionTarget
from pyrit.registry import ScorerRegistry
from pyrit.score import (
    TrueFalseScoreAggregator,
    TrueFalseScoreSelector,
    WildGuardLabel,
    WildGuardMultiLabelScorer,
    WildGuardScorer,
    render_wildguard_prompt,
)
from tests.unit.mocks import MockPromptTarget

FIRST = "Harmful request: yes\nResponse refusal: no\nHarmful response: yes"
SECOND = "Harmful request: no\nResponse refusal: no\nHarmful response: no"


def _target():
    return OpenAICompletionTarget(
        model_name="allenai/wildguard", endpoint="http://localhost:8000/v1", api_key="unused", max_tokens=128
    )


def _completion(text):
    return Completion(
        id="wildguard-test",
        object="text_completion",
        created=0,
        model="allenai/wildguard",
        choices=[{"index": 0, "text": text, "finish_reason": "stop", "logprobs": None}],
    )


def _store_response(memory, values=("response",), *, prompt="question", **kwargs):
    conversation_id = str(uuid.uuid4())
    memory.add_message_to_memory(
        request=MessagePiece(role="user", original_value=prompt, conversation_id=conversation_id).to_message()
    )
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value=value, conversation_id=conversation_id, **kwargs)
            for value in values
        ]
    )
    memory.add_message_to_memory(request=response)
    return response


@pytest.mark.parametrize("retry", [False, True])
async def test_one_inference_persists_three_verdicts_and_shared_observation(sqlite_instance: MemoryInterface, retry):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    response = _store_response(sqlite_instance)
    replies = ["malformed", FIRST] if retry else [FIRST]
    with patch.object(
        target._client.completions, "create", new=AsyncMock(side_effect=[_completion(text) for text in replies])
    ) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(response))

    assert create.call_count == len(replies)
    assert [score.score_category for score in scores] == [[label] for label in scorer.labels]
    assert [score.get_value() for score in scores] == [True, False, True]
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 3
    assert len({score.id for score in scores}) == 3
    assert len(scores[0].observation_ids) == 1
    assert all(score.observation_ids == scores[0].observation_ids for score in scores)
    [observation] = sqlite_instance.get_observations(observation_ids=scores[0].observation_ids)
    assert observation.scorable == MessageScorable.from_message(response)
    for score, label in zip(scores, WildGuardLabel, strict=True):
        [stored] = sqlite_instance.get_scores(score_category=label.metadata_key)
        assert stored.id == score.id
        assert stored.score_metadata["selected_label"] == label.value


@pytest.mark.parametrize(
    ("aggregator", "expected"),
    [(TrueFalseScoreAggregator.OR, [True, False, True]), (TrueFalseScoreAggregator.AND, [False, False, False])],
)
async def test_multipart_response_is_aggregated_within_labels(sqlite_instance, aggregator, expected):
    target = _target()
    response = _store_response(sqlite_instance, values=("first", "second"))
    scorer = WildGuardMultiLabelScorer(chat_target=target, score_aggregator=aggregator)
    with patch.object(
        target._client.completions, "create", new=AsyncMock(side_effect=[_completion(FIRST), _completion(SECOND)])
    ) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(response))
    assert create.call_count == 2
    assert [score.get_value() for score in scores] == expected
    assert all(len(score.observation_ids) == 2 for score in scores)
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 3


async def test_not_applicable_label_does_not_discard_other_verdicts_or_retry(sqlite_instance):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    response = _store_response(sqlite_instance, values=("",))
    reply = "Harmful request: yes\nResponse refusal: N/A\nHarmful response: N/A"
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(reply))) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(response))
    assert create.call_count == 1
    assert scores[0].get_value() is True
    assert all(score.is_undetermined for score in scores[1:])
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 3


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Harmful request: N/A\nResponse refusal: no\nHarmful response: yes", [None, "false", "true"]),
        ("Harmful request: N/A\nResponse refusal: N/A\nHarmful response: N/A", [None, None, None]),
    ],
)
async def test_any_label_can_abstain_without_losing_the_other_judgments(sqlite_instance, reply, expected):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    response = _store_response(sqlite_instance)
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(reply))) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(response))
    create.assert_called_once()
    assert [score.score_value for score in scores] == expected
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 3


@pytest.mark.parametrize("error", ["blocked", "processing"])
async def test_unavailable_evidence_does_not_invent_three_negative_verdicts(sqlite_instance, error):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    response = _store_response(sqlite_instance, original_value_data_type="error", response_error=error)
    with patch.object(target._client.completions, "create", new=AsyncMock()) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(response))
    create.assert_not_called()
    assert len(scores) == 3
    assert all(score.is_undetermined for score in scores)


async def test_loose_content_keeps_all_scores_on_one_managed_content(sqlite_instance):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target, user_prompt="question")
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(FIRST))) as create:
        scores = await scorer.score_async(scorable=ContentScorable(value="response", data_type="text"))
    create.assert_called_once()
    assert len(scores) == 3
    assert len({score.scorable.content_id for score in scores}) == 1
    assert all(score.message_piece_id is None for score in scores)


async def test_batch_keeps_each_conversations_context_and_all_labels(sqlite_instance):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    responses = [_store_response(sqlite_instance, values=(f"response {i}",), prompt=f"question {i}") for i in range(8)]
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(FIRST))) as create:
        scores = await scorer.score_batch_async(
            scorables=[MessageScorable.from_message(response) for response in responses], batch_size=4
        )
    assert create.call_count == 8
    assert len(scores) == 24
    assert len(sqlite_instance.get_scores(score_type="true_false")) == 24
    assert {call.kwargs["prompt"] for call in create.call_args_list} == {
        render_wildguard_prompt(user_prompt=f"question {i}", response=f"response {i}").value for i in range(8)
    }
    for i, response in enumerate(responses):
        assert all(score.scorable == MessageScorable.from_message(response) for score in scores[i * 3 : i * 3 + 3])


async def test_selector_batch_respects_the_classifier_rate_limit(sqlite_instance):
    target = OpenAICompletionTarget(
        model_name="allenai/wildguard", endpoint="http://localhost:8000/v1", api_key="unused", max_requests_per_minute=1
    )
    selector = TrueFalseScoreSelector(scorer=WildGuardMultiLabelScorer(chat_target=target), label="harmful_response")
    responses = [_store_response(sqlite_instance) for _ in range(2)]
    with patch.object(target._client.completions, "create", new=AsyncMock()) as create:
        with pytest.raises(ValueError, match="Batch size must be configured to 1"):
            await selector.score_batch_async(
                scorables=[MessageScorable.from_message(response) for response in responses], batch_size=2
            )
    create.assert_not_called()


async def test_selector_drives_real_attack_outcome_from_named_label(sqlite_instance):
    target = _target()
    scorer = WildGuardMultiLabelScorer(chat_target=target)
    selector = TrueFalseScoreSelector(scorer=scorer, label="response_refusal")
    attack = PromptSendingAttack(
        objective_target=MockPromptTarget(), attack_scoring_config=AttackScoringConfig(objective_scorer=selector)
    )
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(FIRST))) as create:
        result = await attack.execute_async(objective="test objective")
    create.assert_called_once()
    assert result.outcome is AttackOutcome.FAILURE  # The first classifier label is True, the selected refusal is False.
    [stored] = sqlite_instance.get_scores(score_type="true_false")
    assert stored.score_category == ["response_refusal"]
    assert stored.scorer_class_identifier == selector.get_identifier()


async def test_existing_single_label_api_still_returns_its_selected_score(sqlite_instance):
    target = _target()
    scorer = WildGuardScorer(chat_target=target, label=WildGuardLabel.RESPONSE_REFUSAL)
    with patch.object(target._client.completions, "create", new=AsyncMock(return_value=_completion(FIRST))) as create:
        scores = await scorer.score_async(scorable=MessageScorable.from_message(_store_response(sqlite_instance)))
    create.assert_called_once()
    assert len(scores) == 1 and scores[0].get_value() is False
    assert scores[0].score_category == ["wildguard"]


@pytest.mark.usefixtures("patch_central_database")
def test_registry_can_construct_the_classifier_and_projection():
    registry = ScorerRegistry(lazy_discovery=True)
    scorer = registry.create_instance("WildGuardMultiLabelScorer", chat_target=_target(), user_prompt="question")
    selector = registry.create_instance("TrueFalseScoreSelector", scorer=scorer, label="harmful_response")
    assert isinstance(selector, TrueFalseScoreSelector)
    assert selector.get_chat_target() is scorer.get_chat_target()
