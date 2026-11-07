# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Judge-supplied original_float_value must not override the stored score.

normalize_score_to_float prefers an original_float_value metadata key over the
validated score_value. The threshold wrapper writes that key itself after
parsing; a raw float-scale scorer left the judge-supplied key authoritative,
so a judge body of score_value 0.0 + metadata original_float_value 1.0 stored
0.0 while every consumer computed 1.0.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.memory import MemoryInterface
from pyrit.models import (
    ComponentIdentifier,
    ContentScorable,
    Message,
    MessagePiece,
    ScoringExpectation,
    UnvalidatedScore,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    CallableResponseHandler,
    FloatScaleThresholdScorer,
    JsonSchemaResponseHandler,
    NonReplayableObservationError,
    NumericRubric,
    SelfAskScaleScorer,
)
from pyrit.score.response_handler import _build_unvalidated_score
from pyrit.score.score_utils import normalize_score_to_float

_SCORER = ComponentIdentifier(class_name="TestScorer", class_module=__name__)


def _build(metadata: dict[str, Any]) -> UnvalidatedScore:
    return _build_unvalidated_score(
        parsed_response={"score_value": "0.0", "rationale": "r", "metadata": metadata},
        score_value_output_key="score_value",
        rationale_output_key="rationale",
        description_output_key="description",
        metadata_output_key="metadata",
        category_output_key="category",
        scorer_identifier=_SCORER,
        scored_prompt_id="00000000-0000-0000-0000-000000000000",
        category=None,
        objective=None,
    )


def test_judge_supplied_original_float_value_is_stripped():
    score = _build({"original_float_value": "1.0"})
    assert score.score_metadata is not None
    assert "original_float_value" not in score.score_metadata


def test_out_of_range_override_is_stripped():
    score = _build({"original_float_value": 7.5})
    assert score.score_metadata is not None
    assert "original_float_value" not in score.score_metadata


def test_innocent_metadata_keys_are_retained():
    score = _build({"judge_confidence": 0.9, "notes": "ok"})
    assert score.score_metadata == {"judge_confidence": 0.9, "notes": "ok"}


def test_escaped_key_variant_is_not_consulted():
    score = _build({"original\\u005ffloat_value": "1.0"})
    # A different literal key: retained, and never consulted by
    # normalize_score_to_float, which reads the exact constant.
    assert "original_float_value" not in (score.score_metadata or {})


def _parse_response(response_text: str) -> dict[str, Any]:
    return json.loads(response_text)


def _target(*, metadata_key: str, override: str | float) -> MagicMock:
    body = {
        "score_value": "2",
        "rationale": "The response only partially matches.",
        "description": "Low score",
        metadata_key: {"original_float_value": override, "judge_confidence": 0.9},
    }
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    target.set_system_prompt_async = AsyncMock()
    target.send_prompt_async = AsyncMock(
        return_value=[Message(message_pieces=[MessagePiece(role="assistant", original_value=json.dumps(body))])]
    )
    return target


def _scale_scorer(
    *,
    target: MagicMock,
    handler_type: type[JsonSchemaResponseHandler] | type[CallableResponseHandler],
    metadata_key: str,
) -> SelfAskScaleScorer:
    handler = (
        CallableResponseHandler(
            parser=_parse_response, parser_fingerprint="metadata-regression", metadata_output_key=metadata_key
        )
        if handler_type is CallableResponseHandler
        else JsonSchemaResponseHandler(metadata_output_key=metadata_key)
    )
    return SelfAskScaleScorer(
        chat_target=target,
        system_prompt="Judge the response.",
        scale=NumericRubric(minimum_value=0, maximum_value=10, category="harm"),
        response_handler=handler,
    )


@pytest.mark.parametrize("handler_type", [JsonSchemaResponseHandler, CallableResponseHandler])
@pytest.mark.parametrize("metadata_key", ["metadata", "judge_metadata"])
@pytest.mark.parametrize("override", ["1.0", 7.5])
async def test_public_score_metadata_and_replay_preserve_normalization_async(
    sqlite_instance: MemoryInterface,
    handler_type: type[JsonSchemaResponseHandler] | type[CallableResponseHandler],
    metadata_key: str,
    override: str | float,
) -> None:
    target = _target(metadata_key=metadata_key, override=override)
    scorer = _scale_scorer(target=target, handler_type=handler_type, metadata_key=metadata_key)
    expectation = ScoringExpectation(objective="Judge the response")
    live = (await scorer.score_async(scorable=ContentScorable(value="candidate"), expectation=expectation))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=live.observation_ids))[0]
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    persisted = await sqlite_instance.get_scores_async(score_ids=[live.id, replay.id])

    assert len(persisted) == 2
    for score in [live, replay, *persisted]:
        assert score.score_value == "0.2"
        assert normalize_score_to_float(score) == 0.2
        assert score.score_category == ["harm"]
        assert score.score_rationale == "The response only partially matches."
        assert score.score_metadata == {"judge_confidence": 0.9}
    target.send_prompt_async.assert_awaited_once()


@pytest.mark.parametrize("handler_type", [JsonSchemaResponseHandler, CallableResponseHandler])
@pytest.mark.parametrize("metadata_key", ["metadata", "judge_metadata"])
async def test_public_replay_rejects_pre_strip_handler_contract_async(
    sqlite_instance: MemoryInterface,
    handler_type: type[JsonSchemaResponseHandler] | type[CallableResponseHandler],
    metadata_key: str,
) -> None:
    target = _target(metadata_key=metadata_key, override="1.0")
    scorer = _scale_scorer(target=target, handler_type=handler_type, metadata_key=metadata_key)
    expectation = ScoringExpectation(objective="Judge the response")
    current_identifier = handler_type._replay_identifier

    def historical_identifier(self: JsonSchemaResponseHandler | CallableResponseHandler) -> dict[str, Any]:
        identifier = current_identifier(self)
        assert identifier is not None
        return {**identifier, "version": 1}

    # Acquire real durable evidence under the historical contract, without mocking parsing.
    with patch.object(handler_type, "_replay_identifier", historical_identifier):
        live = (await scorer.score_async(scorable=ContentScorable(value="candidate"), expectation=expectation))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=live.observation_ids))[0]

    with pytest.raises(NonReplayableObservationError, match="differs from the acquisition contract"):
        await scorer.score_observation_async(observation=observation, expectation=expectation)
    target.send_prompt_async.assert_awaited_once()


async def test_public_threshold_preserves_trusted_original_float_async(sqlite_instance: MemoryInterface) -> None:
    target = _target(metadata_key="metadata", override=7.5)
    leaf = _scale_scorer(target=target, handler_type=JsonSchemaResponseHandler, metadata_key="metadata")
    scorer = FloatScaleThresholdScorer(scorer=leaf, threshold=0.5)
    score = (
        await scorer.score_async(
            scorable=ContentScorable(value="candidate"), expectation=ScoringExpectation(objective="Judge the response")
        )
    )[0]
    persisted = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]

    for result in [score, persisted]:
        assert result.score_value == "false"
        assert result.score_category == ["harm"]
        assert "The response only partially matches." in result.score_rationale
        assert result.score_metadata is not None
        assert result.score_metadata["original_float_value"] == 0.2
        assert normalize_score_to_float(result) == 0.2
    target.send_prompt_async.assert_awaited_once()
