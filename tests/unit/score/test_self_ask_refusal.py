# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import COMMON_JSON_SCHEMAS, JSON_SCHEMA_METADATA_KEY, Message, MessagePiece, SeedPrompt
from pyrit.prompt_target.common.json_response_config import _JsonResponseConfig
from pyrit.score import JsonSchemaResponseHandler, RefusalScorerPaths, SelfAskRefusalScorer


@pytest.fixture
def scorer_true_false_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True", "description": "This is true", "rationale": "rationale for true"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


async def test_refusal_scorer_score(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    score = await scorer.score_text_async("true false")

    assert len(score) == 1
    assert score[0].get_value() is True
    assert score[0].score_value_description == "This is true"
    assert score[0].score_rationale == "rationale for true"
    assert score[0].scorer_class_identifier.class_name == "SelfAskRefusalScorer"


async def test_refusal_scorer_set_system_prompt(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false")

    chat_target.set_system_prompt.assert_called_once()


async def test_refusal_scorer_no_task(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false")

    _, kwargs = chat_target.send_prompt_async.call_args
    # Without an objective, the scorer uses "Not provided" as the objective placeholder
    expected = "conversation_objective: Not provided\nresponse_to_evaluate_input: true false"
    assert kwargs["message"].message_pieces[0].original_value == expected


async def test_refusal_scorer_with_task(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("true false", objective="task")

    _, kwargs = chat_target.send_prompt_async.call_args

    expected = "conversation_objective: task\nresponse_to_evaluate_input: true false"
    assert kwargs["message"].message_pieces[0].original_value == expected


async def test_refusal_scorer_image_non_block(scorer_true_false_response: Message, patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with tempfile.NamedTemporaryFile(delete=True) as temp_image:
        result = await scorer.score_image_async(temp_image.name)
        assert result[0].get_value() is False

    # Any image doesn't need LLM evaluation, it just checks whether it was blocked
    chat_target.send_prompt_async.assert_not_called()


async def test_refusal_scorer_adds_to_memory(scorer_true_false_response: Message):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskRefusalScorer(chat_target=chat_target)
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


async def test_refusal_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskRefusalScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


async def test_self_ask_objective_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    json_response = (
        dedent(
            """
            {"bad_value_key": "True", "rationale": "rationale for true"}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskRefusalScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


async def test_score_async_filtered_response(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    request = MessagePiece(
        role="assistant",
        original_value="blocked response",
        response_error="blocked",
        conversation_id=str(uuid4()),
    ).to_message()
    memory.add_message_pieces_to_memory(message_pieces=request.message_pieces)
    scores = await scorer.score_async(request)

    assert len(scores) == 1
    assert scores[0].score_value == "true"
    assert scores[0].score_value_description == "Refusal detected"
    assert scores[0].score_rationale == "Content was filtered, constituting a refusal."
    assert scores[0].scorer_class_identifier.class_name == "SelfAskRefusalScorer"


class TestRefusalScorerPaths:
    """Tests for RefusalScorerPaths enum."""

    def test_refusal_scorer_paths_objective_strict_exists(self):
        """Test that OBJECTIVE_STRICT path exists in enum."""
        assert RefusalScorerPaths.OBJECTIVE_STRICT is not None
        assert isinstance(RefusalScorerPaths.OBJECTIVE_STRICT.value, Path)
        assert "refusal_objective_strict.yaml" in str(RefusalScorerPaths.OBJECTIVE_STRICT.value)

    def test_refusal_scorer_paths_objective_lenient_exists(self):
        """Test that OBJECTIVE_LENIENT path exists in enum."""
        assert RefusalScorerPaths.OBJECTIVE_LENIENT is not None
        assert isinstance(RefusalScorerPaths.OBJECTIVE_LENIENT.value, Path)
        assert "refusal_objective_lenient.yaml" in str(RefusalScorerPaths.OBJECTIVE_LENIENT.value)

    def test_refusal_scorer_paths_no_objective_strict_exists(self):
        """Test that NO_OBJECTIVE_STRICT path exists in enum."""
        assert RefusalScorerPaths.NO_OBJECTIVE_STRICT is not None
        assert isinstance(RefusalScorerPaths.NO_OBJECTIVE_STRICT.value, Path)
        assert "refusal_no_objective_strict.yaml" in str(RefusalScorerPaths.NO_OBJECTIVE_STRICT.value)

    def test_refusal_scorer_paths_no_objective_lenient_exists(self):
        """Test that NO_OBJECTIVE_LENIENT path exists in enum."""
        assert RefusalScorerPaths.NO_OBJECTIVE_LENIENT is not None
        assert isinstance(RefusalScorerPaths.NO_OBJECTIVE_LENIENT.value, Path)
        assert "refusal_no_objective_lenient.yaml" in str(RefusalScorerPaths.NO_OBJECTIVE_LENIENT.value)


class TestRefusalScorerSystemPromptPath:
    """Tests for constructing the scorer from a RefusalScorerPaths system prompt."""

    async def test_refusal_scorer_with_objective_lenient(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test that OBJECTIVE_LENIENT mode can be used."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        scorer = SelfAskRefusalScorer(
            chat_target=chat_target,
            system_prompt=SeedPrompt.from_yaml_file(RefusalScorerPaths.OBJECTIVE_LENIENT.value),
        )

        score = await scorer.score_text_async("some response")
        assert len(score) == 1

    async def test_refusal_scorer_with_no_objective_strict(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test that NO_OBJECTIVE_STRICT mode can be used."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        scorer = SelfAskRefusalScorer(
            chat_target=chat_target,
            system_prompt=SeedPrompt.from_yaml_file(RefusalScorerPaths.NO_OBJECTIVE_STRICT.value),
        )

        score = await scorer.score_text_async("some response")
        assert len(score) == 1

    async def test_refusal_scorer_with_objective_strict_and_objective(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test OBJECTIVE_STRICT mode with an objective."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        scorer = SelfAskRefusalScorer(
            chat_target=chat_target,
            system_prompt=SeedPrompt.from_yaml_file(RefusalScorerPaths.OBJECTIVE_STRICT.value),
        )

        score = await scorer.score_text_async("some response", objective="generate harmful content")
        assert len(score) == 1
        assert score[0].get_value() is True


EXPECTED_REFUSAL_RESPONSE_JSON_SCHEMA = COMMON_JSON_SCHEMAS["true_false_with_rationale"]


@pytest.mark.parametrize("scorer_path", list(RefusalScorerPaths))
async def test_refusal_scorer_loads_response_json_schema(scorer_path: RefusalScorerPaths, patch_central_database):
    """Test that each refusal YAML populates the response handler schema with the expected schema."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskRefusalScorer(
        chat_target=chat_target,
        system_prompt=SeedPrompt.from_yaml_file(scorer_path.value),
    )

    assert scorer._response_handler.response_schema is not None
    assert scorer._response_handler.response_schema == EXPECTED_REFUSAL_RESPONSE_JSON_SCHEMA


async def test_refusal_scorer_passes_response_json_schema_to_target(
    scorer_true_false_response: Message, patch_central_database
):
    """Test that response_json_schema is forwarded to the prompt target via prompt_metadata."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)

    await scorer.score_text_async("some response", objective="test objective")

    _, kwargs = chat_target.send_prompt_async.call_args
    message_piece = kwargs["message"].message_pieces[0]
    assert message_piece.prompt_metadata["json_schema"] == EXPECTED_REFUSAL_RESPONSE_JSON_SCHEMA


async def test_refusal_scorer_omits_json_schema_when_seed_has_none(
    scorer_true_false_response: Message, patch_central_database
):
    """When the seed prompt has no schema, prompt_metadata must NOT include the json_schema key."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)
    # Simulate a scorer constructed from a YAML without a schema.
    scorer._response_handler = JsonSchemaResponseHandler(response_schema=None)

    await scorer.score_text_async("some response", objective="test objective")

    _, kwargs = chat_target.send_prompt_async.call_args
    message_piece = kwargs["message"].message_pieces[0]
    assert JSON_SCHEMA_METADATA_KEY not in message_piece.prompt_metadata
    # response_format must still be set so the target returns JSON.
    assert message_piece.prompt_metadata.get("response_format") == "json"


@pytest.mark.parametrize("scorer_path", list(RefusalScorerPaths))
async def test_refusal_scorer_identifier_includes_schema(scorer_path: RefusalScorerPaths, patch_central_database):
    """The scorer identifier must carry the schema so identical-config scorers hash the same."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskRefusalScorer(
        chat_target=chat_target,
        system_prompt=SeedPrompt.from_yaml_file(scorer_path.value),
    )

    identifier = scorer.get_identifier()
    assert identifier.params["response_json_schema"] == EXPECTED_REFUSAL_RESPONSE_JSON_SCHEMA


async def test_refusal_scorer_metadata_round_trips_through_json_response_config(
    scorer_true_false_response: Message, patch_central_database
):
    """The prompt_metadata produced by the scorer must be consumable by _JsonResponseConfig."""
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskRefusalScorer(chat_target=chat_target)
    await scorer.score_text_async("some response", objective="test objective")

    _, kwargs = chat_target.send_prompt_async.call_args
    metadata = kwargs["message"].message_pieces[0].prompt_metadata

    config = _JsonResponseConfig.from_metadata(metadata=metadata)
    assert config.enabled is True
    assert config.json_schema == EXPECTED_REFUSAL_RESPONSE_JSON_SCHEMA


class TestRefusalScorerPromptFormatString:
    """Tests for prompt_format_string parameter."""

    async def test_refusal_scorer_custom_prompt_format(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test that custom prompt_format_string is used."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        custom_format = "Goal: {objective}\nAI Response: {response}"
        scorer = SelfAskRefusalScorer(
            chat_target=chat_target,
            prompt_format_string=custom_format,
        )

        await scorer.score_text_async("test response", objective="test objective")

        _, kwargs = chat_target.send_prompt_async.call_args
        expected = "Goal: test objective\nAI Response: test response"
        assert kwargs["message"].message_pieces[0].original_value == expected

    async def test_refusal_scorer_custom_prompt_format_no_objective(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test custom prompt_format_string with no objective uses 'Not provided'."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        custom_format = "Goal: {objective}\nAI Response: {response}"
        scorer = SelfAskRefusalScorer(
            chat_target=chat_target,
            prompt_format_string=custom_format,
        )

        await scorer.score_text_async("test response")

        _, kwargs = chat_target.send_prompt_async.call_args
        expected = "Goal: Not provided\nAI Response: test response"
        assert kwargs["message"].message_pieces[0].original_value == expected

    async def test_refusal_scorer_default_prompt_format(
        self, scorer_true_false_response: Message, patch_central_database
    ):
        """Test that default prompt format is used when not specified."""
        chat_target = MagicMock()
        chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
        chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

        scorer = SelfAskRefusalScorer(chat_target=chat_target)

        await scorer.score_text_async("test response", objective="test objective")

        _, kwargs = chat_target.send_prompt_async.call_args
        expected = "conversation_objective: test objective\nresponse_to_evaluate_input: test response"
        assert kwargs["message"].message_pieces[0].original_value == expected


def test_refusal_init_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskRefusalScorer(chat_target=None)


def test_refusal_score_category_normalized_from_str(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskRefusalScorer(chat_target=chat_target, score_category="custom_refusal")
    assert scorer._score_category == ["custom_refusal"]


def test_refusal_score_category_normalized_from_sequence(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    scorer = SelfAskRefusalScorer(chat_target=chat_target, score_category=["a", "b"])
    assert scorer._score_category == ["a", "b"]


def test_refusal_init_system_prompt_str_and_invalid_type(patch_central_database):
    chat_target = MagicMock()
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskRefusalScorer(chat_target=chat_target, system_prompt="verbatim")
    assert scorer._system_prompt == "verbatim"

    with pytest.raises(TypeError, match="system_prompt must be a SeedPrompt, str, or None"):
        SelfAskRefusalScorer(chat_target=chat_target, system_prompt=123)
