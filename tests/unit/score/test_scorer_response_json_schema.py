# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared coverage for the ``response_json_schema`` forwarding wired into the
self-ask scorers. Each scorer loads the schema embedded in (or referenced by)
its system prompt YAML and forwards it to the scoring target via
``MessagePiece.prompt_metadata`` so schema-aware targets can enforce the
response shape.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import COMMON_JSON_SCHEMAS, JSON_SCHEMA_METADATA_KEY, Message, MessagePiece
from pyrit.score import (
    ContentClassifier,
    ContentClassifierPaths,
    InsecureCodeScorer,
    LikertScalePaths,
    NumericRubric,
    SelfAskCategoryScorer,
    SelfAskLikertScorer,
    SelfAskScaleScorer,
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    TrueFalseQuestionPaths,
)

SCALE_SCHEMA = COMMON_JSON_SCHEMAS["scale_with_rationale"]


def _mock_target(json_response: str) -> MagicMock:
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(
        return_value=[Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])]
    )
    return chat_target


def _make_scorer(scorer_id: str):
    if scorer_id == "true_false":
        target = _mock_target('{"score_value": "True", "description": "d", "rationale": "r", "metadata": "m"}')
        scorer = SelfAskTrueFalseScorer.from_question(
            chat_target=target, question=TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.GROUNDED.value)
        )
    elif scorer_id == "category":
        target = _mock_target('{"score_value": "True", "description": "d", "rationale": "r", "category": "bullying"}')
        scorer = SelfAskCategoryScorer.from_content_classifier(
            chat_target=target,
            content_classifier=ContentClassifier.from_yaml(ContentClassifierPaths.HARMFUL_CONTENT_CLASSIFIER.value),
        )
    elif scorer_id == "insecure_code":
        target = _mock_target('{"score_value": 0.5, "rationale": "r", "metadata": "m"}')
        scorer = InsecureCodeScorer.from_harm_categories(chat_target=target)
    elif scorer_id == "scale":
        target = _mock_target('{"score_value": "1", "description": "d", "rationale": "r"}')
        scorer = SelfAskScaleScorer.from_scale(
            chat_target=target,
            scale=NumericRubric.from_yaml(SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value),
        )
    elif scorer_id == "likert":
        target = _mock_target('{"score_value": "1", "description": "d", "rationale": "r"}')
        scorer = SelfAskLikertScorer.from_likert_scale(
            chat_target=target,
            likert_scale=LikertScalePaths.CYBER_SCALE.load(),
        )
    else:  # pragma: no cover - guard against typos in parametrization
        raise ValueError(f"Unknown scorer id: {scorer_id}")
    return scorer, target


def _loaded_schema(scorer):
    """Return the response schema regardless of where the scorer stores it.

    The composition-migrated true/false scorer keeps it on its response handler
    (``_response_handler.json_response_config.json_schema``); the other, not-yet-migrated scorers
    still expose ``_response_json_schema`` directly.
    """
    handler = getattr(scorer, "_response_handler", None)
    if handler is not None:
        return handler.json_response_config.json_schema
    return scorer._response_json_schema


# Expected required-property sets for the schema each scorer loads. Asserting the
# shape (rather than the full dict) keeps these tests resilient to wording tweaks
# in the schema descriptions while still pinning the contract that matters.
_EXPECTED_REQUIRED = {
    "true_false": {"score_value", "description", "rationale", "metadata"},
    "category": {"score_value", "description", "rationale", "category"},
    "insecure_code": {"score_value", "rationale", "metadata"},
    "scale": {"score_value", "description", "rationale"},
    "likert": {"score_value", "description", "rationale"},
}

_ALL_SCORERS = list(_EXPECTED_REQUIRED)


@pytest.mark.parametrize("scorer_id", _ALL_SCORERS)
async def test_scorer_loads_response_json_schema(scorer_id: str, patch_central_database):
    """Each scorer must load a response schema from its system prompt YAML."""
    scorer, _ = _make_scorer(scorer_id)

    schema = _loaded_schema(scorer)
    assert schema is not None, f"{scorer_id} scorer did not load a response_json_schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == _EXPECTED_REQUIRED[scorer_id]


@pytest.mark.parametrize("scorer_id", ["scale", "likert"])
async def test_scale_scorers_use_shared_schema(scorer_id: str, patch_central_database):
    """The scale and Likert scorers reference the shared bundled schema by name."""
    scorer, _ = _make_scorer(scorer_id)
    assert _loaded_schema(scorer) == SCALE_SCHEMA


@pytest.mark.parametrize("scorer_id", _ALL_SCORERS)
async def test_scorer_forwards_schema_to_target(scorer_id: str, patch_central_database):
    """The loaded schema must reach the target via prompt_metadata so it can be enforced."""
    scorer, target = _make_scorer(scorer_id)

    await scorer.score_text_async("some response", objective="test objective")

    _, kwargs = target.send_prompt_async.call_args
    message_piece = kwargs["message"].message_pieces[-1]
    assert message_piece.prompt_metadata[JSON_SCHEMA_METADATA_KEY] == _loaded_schema(scorer)
    assert message_piece.prompt_metadata.get("response_format") == "json"


@pytest.mark.parametrize("scorer_id", _ALL_SCORERS)
async def test_scorer_identifier_includes_schema(scorer_id: str, patch_central_database):
    """The scorer identifier must carry the schema so identical-config scorers hash the same."""
    scorer, _ = _make_scorer(scorer_id)

    identifier = scorer.get_identifier()
    assert identifier.params["response_json_schema"] == _loaded_schema(scorer)
