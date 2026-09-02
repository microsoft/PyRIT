# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import uuid
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier, store_message

from pyrit.exceptions import InvalidJsonException, remove_markdown_json
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    ContentScorable,
    Message,
    MessagePiece,
    Scorable,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    JsonSchemaResponseHandler,
    MessageFloatScaleScorer,
    MessageScorable,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorer,
    ScorerPromptValidator,
    TrueFalseInverterScorer,
    TrueFalseScorer,
)
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.message_scorer import extract_objective_from_previous_turn


@pytest.fixture
def good_json():
    return '{"score_value": "1", "rationale": "Valid response"}'


# Add missing BAD_JSON, KEY_ERROR_JSON, KEY_ERROR2_JSON definitions for test parametrize
BAD_JSON = "this is not json"
KEY_ERROR_JSON = (
    dedent(
        """
        {"poor_value": "1",
        "rationale": "The message does not contain any code or instructions that could be used to cause harm"}
        """
    )
    .strip()
    .replace("\n", " ")
)
KEY_ERROR2_JSON = (
    dedent(
        """
        {"score_value": "1",
        "bad_rationale": "The message does not contain any code or instructions that could be used to cause harm"}
        """
    )
    .strip()
    .replace("\n", " ")
)


class DummyValidator(ScorerPromptValidator):
    def validate(self, message, objective=None):
        pass

    def is_message_piece_supported(self, message_piece):
        return True


class MockScorer(MessageTrueFalseScorer):
    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer."""
        return self._create_identifier()

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        message_piece = message.get_piece()
        return [
            Score(
                score_value="true",
                score_value_description="desc",
                score_type="true_false",
                score_category=None,
                score_metadata=None,
                score_rationale="rationale",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_value_description="desc",
                score_type="true_false",
                score_category=None,
                score_metadata=None,
                score_rationale="rationale",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]

    def validate_return_scores(self, scores: list[Score]):
        assert all(s.score_value in ["true", "false"] for s in scores if s.status != ScoreStatus.UNDETERMINED)


class SelectiveValidator(ScorerPromptValidator):
    """Validator that only supports text pieces, not images."""

    def __init__(self, *, enforce_all_pieces_valid: bool = False, raise_on_no_valid_pieces: bool = False):
        super().__init__(
            supported_data_types=["text"],
            enforce_all_pieces_valid=enforce_all_pieces_valid,
            raise_on_no_valid_pieces=raise_on_no_valid_pieces,
        )


class MockFloatScorer(MessageScorer):
    """Mock scorer that tracks which pieces were scored."""

    def __init__(self, *, validator: ScorerPromptValidator):
        self.scored_piece_ids: list[str] = []
        super().__init__(validator=validator)

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer."""
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        # Track which pieces get scored
        self.scored_piece_ids.append(str(message_piece.id))

        return [
            Score(
                score_value="0.5",
                score_value_description="Test score",
                score_type="float_scale",
                score_category=None,
                score_metadata=None,
                score_rationale="Test rationale",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id or "test-id",
                objective=objective,
            )
        ]

    def validate_return_scores(self, scores: list[Score]):
        for score in scores:
            assert 0 <= float(score.score_value) <= 1

    def _build_fallback_score(
        self, *, message: Message, objective: str | None, scorer_response_blocked: bool = False
    ) -> list[Score]:
        return [
            Score(
                score_value="0.0",
                score_value_description="Mock fallback",
                score_type="float_scale",
                score_category=None,
                score_metadata=None,
                score_rationale="Mock fallback",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message.message_pieces[0].id or "test-id",
                objective=objective,
            )
        ]

    def get_scorer_metrics(self):
        return None


@pytest.mark.parametrize("bad_json", [BAD_JSON, KEY_ERROR_JSON, KEY_ERROR2_JSON])
async def test_scorer_send_chat_target_async_bad_json_exception_retries(bad_json: str, patch_central_database):
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    def _fresh_bad_json_response(*args, **kwargs):
        # A real target returns a fresh response (new piece ids) on every call; build one per
        # attempt so the retry path doesn't collide on a reused message-piece id in memory.
        return [
            Message(
                message_pieces=[MessagePiece(role="assistant", original_value=bad_json, conversation_id="test-convo")]
            )
        ]

    chat_target.send_prompt_async = AsyncMock(side_effect=_fresh_bad_json_response)
    scorer = MockScorer()
    with pytest.raises(InvalidJsonException):
        await _run_llm_scoring_async(
            chat_target=chat_target,
            response_handler=JsonSchemaResponseHandler(),
            scorer_identifier=scorer.get_identifier(),
            system_prompt="system_prompt",
            value="message_value",
            data_type="text",
            scored_prompt_id="123",
            category="category",
            objective="task",
        )

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


async def test_scorer_score_value_with_llm_exception_display_prompt_id(patch_central_database):
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(side_effect=Exception("Test exception"))

    scorer = MockScorer()

    with pytest.raises(Exception, match="Error scoring prompt with original prompt ID: 123"):
        await _run_llm_scoring_async(
            chat_target=chat_target,
            response_handler=JsonSchemaResponseHandler(),
            scorer_identifier=scorer.get_identifier(),
            system_prompt="system_prompt",
            value="message_value",
            data_type="text",
            scored_prompt_id="123",
            category="category",
            objective="task",
        )


async def test_scorer_send_chat_target_async_good_response(good_json, patch_central_database):
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    good_json_resp = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=good_json, conversation_id="test-convo")]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[good_json_resp])

    scorer = MockScorer()

    await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt="system_prompt",
        value="message_value",
        data_type="text",
        scored_prompt_id="123",
        category="category",
        objective="task",
    )

    assert chat_target.send_prompt_async.call_count == 1


async def test_scorer_remove_markdown_json_called(good_json, patch_central_database):
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    good_json_resp = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=good_json, conversation_id="test-convo")]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[good_json_resp])

    scorer = MockScorer()

    with patch(
        "pyrit.score.response_handler.remove_markdown_json", wraps=remove_markdown_json
    ) as mock_remove_markdown_json:
        await _run_llm_scoring_async(
            chat_target=chat_target,
            response_handler=JsonSchemaResponseHandler(),
            scorer_identifier=scorer.get_identifier(),
            system_prompt="system_prompt",
            value="message_value",
            data_type="text",
            scored_prompt_id="123",
            category="category",
            objective="task",
        )

        mock_remove_markdown_json.assert_called_once()


async def test_score_value_with_llm_prepended_text_message_piece_creates_multipiece_message(
    good_json, patch_central_database, tmp_path
):
    """Test that prepended_text_message_piece creates a multi-piece message (text context + main content)."""
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    good_json_resp = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=good_json, conversation_id="test-convo")]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[good_json_resp])

    scorer = MockScorer()

    image_path = tmp_path / "test_image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt="system_prompt",
        value=str(image_path),
        data_type="image_path",
        scored_prompt_id="123",
        prepended_text="objective: test\nresponse:",
        category="category",
        objective="task",
    )

    # Verify send_prompt_async was called
    chat_target.send_prompt_async.assert_called_once()

    # Get the message that was sent
    call_args = chat_target.send_prompt_async.call_args
    sent_message = call_args.kwargs["message"]

    # Should have 2 pieces: text context first, then the main content being scored
    assert len(sent_message.message_pieces) == 2

    # First piece should be the extra text context
    text_piece = sent_message.message_pieces[0]
    assert text_piece.converted_value_data_type == "text"
    assert "objective: test" in text_piece.original_value

    # Second piece should be the main content (image in this case)
    main_piece = sent_message.message_pieces[1]
    assert main_piece.converted_value_data_type == "image_path"
    assert main_piece.original_value == str(image_path)


async def test_score_value_with_llm_no_prepended_text_creates_single_piece_message(good_json, patch_central_database):
    """Test that without prepended_text_message_piece, only a single piece message is created."""
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    good_json_resp = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=good_json, conversation_id="test-convo")]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[good_json_resp])

    scorer = MockScorer()

    await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt="system_prompt",
        value="objective: test\nresponse: some text",
        data_type="text",
        scored_prompt_id="123",
        category="category",
        objective="task",
    )

    # Get the message that was sent
    call_args = chat_target.send_prompt_async.call_args
    sent_message = call_args.kwargs["message"]

    # Should have only 1 piece
    assert len(sent_message.message_pieces) == 1

    # The piece should be text with the full message
    text_piece = sent_message.message_pieces[0]
    assert text_piece.converted_value_data_type == "text"
    assert "objective: test" in text_piece.original_value
    assert "response: some text" in text_piece.original_value


async def test_score_value_with_llm_prepended_text_works_with_audio(good_json, patch_central_database, tmp_path):
    """Test that prepended_text_message_piece works with audio content (type-independent)."""
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    good_json_resp = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=good_json, conversation_id="test-convo")]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[good_json_resp])

    scorer = MockScorer()

    audio_path = tmp_path / "test_audio.wav"
    audio_path.write_bytes(b"RIFF0000WAVE")

    await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt="system_prompt",
        value=str(audio_path),
        data_type="audio_path",
        scored_prompt_id="123",
        prepended_text="objective: transcribe and evaluate\nresponse:",
        category="category",
        objective="task",
    )

    # Get the message that was sent
    call_args = chat_target.send_prompt_async.call_args
    sent_message = call_args.kwargs["message"]

    # Should have 2 pieces: text context + audio
    assert len(sent_message.message_pieces) == 2

    # First piece should be text context
    text_piece = sent_message.message_pieces[0]
    assert text_piece.converted_value_data_type == "text"

    # Second piece should be audio
    audio_piece = sent_message.message_pieces[1]
    assert audio_piece.converted_value_data_type == "audio_path"
    assert audio_piece.original_value == str(audio_path)


def test_extract_objective_from_previous_turn(patch_central_database):
    """
    Test that extract_objective_from_previous_turn properly gathers text from the
    last turn. We'll mock out the memory's get_message_pieces method.
    """
    mock_memory = MagicMock(spec=MemoryInterface)

    response_piece = MessagePiece(original_value="og prompt", role="assistant", conversation_id="xyz", sequence=2)

    mock_memory.get_message_pieces.return_value = [
        MessagePiece(role="user", original_value="Not applicable", original_value_data_type="text", sequence=0),
        MessagePiece(
            role="user",
            original_value="User's question about the universe",
            converted_value="Not the task",
            original_value_data_type="text",
            sequence=1,
        ),
        response_piece,
    ]

    extracted_task = extract_objective_from_previous_turn(message=response_piece.to_message(), memory=mock_memory)
    assert "User's question about the universe" in extracted_task


async def test_scorer_score_responses_batch_async(patch_central_database):
    """
    Test that score_prompts_batch_async names each message as evidence and
    delegates batching to score_batch_async.
    """
    scorer = MockScorer()

    with patch.object(scorer, "score_async", new_callable=AsyncMock) as mock_score_async:
        fake_scores = [MagicMock(), MagicMock()]
        mock_score_async.return_value = fake_scores

        user_req = MessagePiece(role="user", original_value="Hello user", sequence=1).to_message()
        assistant_resp = MessagePiece(role="assistant", original_value="Hello from assistant", sequence=2).to_message()

        results = await scorer.score_prompts_batch_async(messages=[user_req, assistant_resp], batch_size=10)

        assert mock_score_async.call_count == 2

        # Get the call_args for the first call
        _, first_call_kwargs = mock_score_async.call_args_list[0]

        assert first_call_kwargs["scorable"] == MessageScorable.from_message(user_req)
        assert first_call_kwargs["expectation"] == ScoringExpectation(objective="")

        assert fake_scores[0] in results
        assert len(fake_scores) == 2


async def test_score_prompts_batch_async_emits_deprecation_warning(patch_central_database):
    """Test the message-shaped batch API warns and points at the scorable batch API."""
    scorer = MockScorer()

    with patch.object(scorer, "score_async", new_callable=AsyncMock) as mock_score_async:
        mock_score_async.return_value = [MagicMock()]
        message = MessagePiece(role="user", original_value="Hello user", sequence=1).to_message()

        with pytest.warns(DeprecationWarning, match="score_prompts_batch_async"):
            await scorer.score_prompts_batch_async(messages=[message])


async def test_score_prompts_batch_async_rejects_explicit_empty_objectives():
    """Test explicit empty objectives are rejected for non-empty message batches."""
    scorer = MockScorer()
    message = MessagePiece(role="user", original_value="Hello user", sequence=1).to_message()

    with pytest.raises(ValueError, match="objectives"):
        await scorer.score_prompts_batch_async(messages=[message], objectives=[])


async def test_score_image_batch_async_rejects_explicit_empty_objectives():
    """Test explicit empty objectives are rejected for non-empty image batches."""
    scorer = MockScorer()

    with pytest.raises(ValueError, match="objectives"):
        await scorer.score_image_batch_async(image_paths=["test_image.png"], objectives=[])


async def test_score_prompts_batch_async_defaults_objectives_when_none(patch_central_database):
    """Test that objectives=None defaults to empty-string objectives matching message count."""
    scorer = MockScorer()

    with patch.object(scorer, "score_async", new_callable=AsyncMock) as mock_score_async:
        mock_score_async.return_value = [MagicMock()]
        message = MessagePiece(role="user", original_value="Hello user", sequence=1).to_message()

        await scorer.score_prompts_batch_async(messages=[message])

        _, call_kwargs = mock_score_async.call_args
        assert call_kwargs["expectation"] == ScoringExpectation(objective="")


async def test_score_image_batch_async_works_when_objectives_none(patch_central_database):
    """Test that objectives=None omits objectives from the batch call."""
    scorer = MockScorer()

    with patch.object(scorer, "score_image_async", new_callable=AsyncMock) as mock_score_image:
        mock_score_image.return_value = [MagicMock()]

        await scorer.score_image_batch_async(image_paths=["test.png"])

        mock_score_image.assert_called_once()
        _, call_kwargs = mock_score_image.call_args
        assert "objective" not in call_kwargs


class MockThresholdInnerScorer(MessageFloatScaleScorer):
    """Minimal float-scale scorer to wrap in a FloatScaleThresholdScorer."""

    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer."""
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="0.8",
                score_value_description="desc",
                score_type="float_scale",
                score_category=None,
                score_metadata=None,
                score_rationale="rationale",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]


async def test_score_batch_async_rejects_mismatched_expectations():
    """Test that expectations must match the number of scorables."""
    scorer = MockScorer()
    scorable = MessageScorable(message_piece_ids=(uuid.uuid4(),))

    with pytest.raises(ValueError, match="expectations"):
        await scorer.score_batch_async(scorables=[scorable], expectations=[])


async def test_score_batch_async_returns_empty_for_no_scorables():
    """Test that an empty batch does no work."""
    scorer = MockScorer()

    assert await scorer.score_batch_async(scorables=[]) == []


async def test_score_batch_async_passes_no_expectation_by_default(patch_central_database):
    """Test that expectations=None passes no expectation to score_async."""
    scorer = MockScorer()
    message = store_message(MessagePiece(role="user", original_value="Hello user", sequence=1).to_message())

    with patch.object(scorer, "score_async", new_callable=AsyncMock) as mock_score_async:
        mock_score_async.return_value = [MagicMock()]

        await scorer.score_batch_async(scorables=[MessageScorable.from_message(message)])

        _, call_kwargs = mock_score_async.call_args
        assert call_kwargs["expectation"] is None


async def test_score_batch_async_supports_non_message_scorers(patch_central_database):
    """Test that wrapper scorers, which are not MessageScorers, can still batch."""
    scorer = FloatScaleThresholdScorer(scorer=MockThresholdInnerScorer(), threshold=0.5)
    assert not isinstance(scorer, MessageScorer)

    message = store_message(MessagePiece(role="assistant", original_value="Hello from assistant").to_message())

    scores = await scorer.score_batch_async(
        scorables=[MessageScorable.from_message(message)],
        expectations=[ScoringExpectation(objective="test objective")],
    )

    assert len(scores) == 1
    assert scores[0].get_value() is True


async def test_score_response_async_empty_scorers(patch_central_database):
    """Test that score_response_async returns empty list when no scorers provided."""
    response = Message(
        message_pieces=[MessagePiece(role="assistant", original_value="test", conversation_id="test-convo")]
    )

    result = await MessageScorer.score_response_async(response=store_message(response), objective="test task")
    assert result == {"auxiliary_scores": [], "objective_scores": []}


async def test_score_response_async_no_matching_role(patch_central_database):
    """A scorer that declares only assistant roles stays silent on a user-only response."""
    response = Message(
        message_pieces=[
            MessagePiece(role="user", original_value="test1", conversation_id="test-convo"),
            MessagePiece(role="user", original_value="test2", conversation_id="test-convo"),
        ]
    )

    scorer = MockScorer()
    scorer._validator = ScorerPromptValidator(supported_roles=["assistant"])
    scorer._score_async = AsyncMock(return_value=[])

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        objective_scorer=scorer,
        auxiliary_scorers=[scorer],
        objective="test task",
    )
    assert result == {"auxiliary_scores": [], "objective_scores": []}
    # Role policy is a declared capability, so the scorer never reads the evidence.
    scorer._score_async.assert_not_called()


async def test_score_response_async_parallel_execution(patch_central_database):
    """Test that score_response_async runs all scorers in parallel on all filtered pieces."""
    piece1 = MessagePiece(role="assistant", original_value="response1", conversation_id="test-convo")
    piece2 = MessagePiece(role="assistant", original_value="response2", conversation_id="test-convo")
    piece3 = MessagePiece(role="assistant", original_value="user input", conversation_id="test-convo")

    response = Message(message_pieces=[piece1, piece2, piece3])

    # Create mock scores
    score1_1 = MagicMock(spec=Score)
    score1_2 = MagicMock(spec=Score)
    score2_1 = MagicMock(spec=Score)
    score2_2 = MagicMock(spec=Score)

    # Create mock scorers
    scorer1 = MockScorer()
    scorer1.score_async = AsyncMock(side_effect=[[score1_1], [score1_2]])

    scorer2 = MockScorer()
    scorer2.score_async = AsyncMock(side_effect=[[score2_1], [score2_2]])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=[scorer1, scorer2], objective="test task"
    )

    assert score1_1 in result["auxiliary_scores"]
    assert score2_1 in result["auxiliary_scores"]
    expected_scorable = MessageScorable.from_message(store_message(response))
    # Every scorer receives the response as it arrived; policy belongs to the scorer.
    scorer1.score_async.assert_any_call(
        scorable=expected_scorable,
        expectation=ScoringExpectation(objective="test task"),
    )
    scorer2.score_async.assert_any_call(
        scorable=expected_scorable,
        expectation=ScoringExpectation(objective="test task"),
    )


async def test_score_response_select_first_success_async_empty_scorers(patch_central_database):
    """Test that score_response_select_first_success_async returns None when no scorers provided."""
    response = Message(
        message_pieces=[MessagePiece(role="assistant", original_value="test", conversation_id="test-convo")]
    )

    result = await MessageScorer.score_response_multiple_scorers_async(
        response=store_message(response), scorers=[], objective="test task"
    )

    assert result == []


async def test_score_async_no_matching_role(patch_central_database):
    """A scorer returns no scores when it declares none of the roles in the message."""
    response = Message(message_pieces=[MessagePiece(role="user", original_value="test", conversation_id="test-convo")])
    scorer = MockScorer()
    scorer._validator = ScorerPromptValidator(supported_roles=["assistant"])
    result = await scorer.score_async(
        scorable=MessageScorable.from_message(store_message(response)),
        expectation=ScoringExpectation(objective="test task"),
    )

    assert result == []


async def test_score_response_async_finds_success():
    """Test that score_response_async returns first successful score."""
    piece1 = MessagePiece(role="assistant", original_value="response1", conversation_id="test-convo")
    piece2 = MessagePiece(role="assistant", original_value="response2", conversation_id="test-convo")

    response = Message(message_pieces=[piece1, piece2])

    # Create mock scores
    score1 = MagicMock(spec=Score)
    score1.get_value.return_value = False  # Failure

    score2 = MagicMock(spec=Score)
    score2.get_value.return_value = True  # Success

    score3 = MagicMock(spec=Score)
    score3.get_value.return_value = True  # Another success (should not be reached)

    # Create mock scorers
    scorer1 = MockScorer()
    scorer1.score_async = AsyncMock(side_effect=[[score1], [score3]])

    scorer2 = MockScorer()
    scorer2.score_async = AsyncMock(return_value=[score2])

    result = await MessageScorer.score_response_multiple_scorers_async(
        response=response, scorers=[scorer1, scorer2], objective="test task"
    )

    # Should return the first successful score (score2)
    assert len(result) == 2
    assert score2 in result

    # scorer1 should be called only once (for piece1)
    assert scorer1.score_async.call_count == 1
    # scorer2 should be called only once (for piece1, returning success)
    assert scorer2.score_async.call_count == 1


async def test_score_response_success_async_no_success_returns_first():
    """Test that score_response_success_async returns first score when no success found."""
    piece1 = MessagePiece(role="assistant", original_value="response1", conversation_id="test-convo")
    piece2 = MessagePiece(role="assistant", original_value="response2", conversation_id="test-convo")

    response = Message(message_pieces=[piece1, piece2])

    # Create mock scores (all failures)
    score1 = MagicMock(spec=Score)
    score1.get_value.return_value = False

    score2 = MagicMock(spec=Score)
    score2.get_value.return_value = False

    score3 = MagicMock(spec=Score)
    score3.get_value.return_value = False

    score4 = MagicMock(spec=Score)
    score4.get_value.return_value = False

    # Create mock scorers
    scorer1 = MockScorer()
    scorer1.score_async = AsyncMock(side_effect=[[score1], [score3]])

    scorer2 = MockScorer()
    scorer2.score_async = AsyncMock(side_effect=[[score2], [score4]])

    result = await MessageScorer.score_response_multiple_scorers_async(
        response=response, scorers=[scorer1, scorer2], objective="test task"
    )

    assert score1 in result
    assert score2 in result

    assert scorer1.score_async.call_count == 1
    assert scorer2.score_async.call_count == 1


async def test_score_response_success_async_parallel_scoring_per_piece(patch_central_database):
    """Test that score_response_success_async runs scorers in parallel for each piece."""
    piece1 = MessagePiece(role="assistant", original_value="response1", conversation_id="test-convo")
    piece2 = MessagePiece(role="assistant", original_value="response2", conversation_id="test-convo")

    response = store_message(Message(message_pieces=[piece1, piece2]))

    # Track call order
    call_order = []

    def _first_value(scorable: MessageScorable) -> str:
        # A scorable names pieces rather than carrying them, so read it back from memory.
        return (
            MessageScorableResolver()
            .resolve(
                scorable=scorable,
                memory=CentralMemory.get_memory_instance(),
            )
            .message_pieces[0]
            .original_value
        )

    async def mock_score_async_1(*, scorable: MessageScorable, **kwargs) -> list[Score]:
        call_order.append(("scorer1", _first_value(scorable)))
        score = MagicMock(spec=Score)
        score.get_value.return_value = False
        return [score]

    async def mock_score_async_2(*, scorable: MessageScorable, **kwargs) -> list[Score]:
        call_order.append(("scorer2", _first_value(scorable)))
        score = MagicMock(spec=Score)
        score.get_value.return_value = False
        return [score]

    scorer1 = MockScorer()
    scorer1.score_async = mock_score_async_1

    scorer2 = MockScorer()
    scorer2.score_async = mock_score_async_2

    await MessageScorer.score_response_multiple_scorers_async(
        response=response, scorers=[scorer1, scorer2], objective="test task"
    )

    assert len(call_order) == 2

    assert ("scorer1", "response1") in call_order[:2]
    assert ("scorer2", "response1") in call_order[:2]


async def test_score_response_async_no_scorers():
    """Test score_response_async with no scorers provided."""
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value="test")])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=None, objective_scorer=None, objective="test task"
    )

    assert result == {"auxiliary_scores": [], "objective_scores": []}


async def test_score_response_async_auxiliary_only():
    """Test score_response_async with only auxiliary scorers."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    # Create mock auxiliary scores
    aux_score1 = MagicMock(spec=Score)
    aux_score2 = MagicMock(spec=Score)

    # Create mock auxiliary scorers
    aux_scorer1 = MockScorer()
    aux_scorer1.score_async = AsyncMock(return_value=[aux_score1])

    aux_scorer2 = MockScorer()
    aux_scorer2.score_async = AsyncMock(return_value=[aux_score2])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=[aux_scorer1, aux_scorer2], objective_scorer=None, objective="test task"
    )

    # Should have auxiliary scores but no objective scores
    assert len(result["auxiliary_scores"]) == 2
    assert aux_score1 in result["auxiliary_scores"]
    assert aux_score2 in result["auxiliary_scores"]
    assert result["objective_scores"] == []


async def test_score_response_async_objective_only():
    """Test score_response_async with only objective scorers."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    # Create mock objective score
    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = True

    # Create mock objective scorer
    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=None, objective_scorer=obj_scorer, objective="test task"
    )

    # Should have objective score but no auxiliary scores
    assert result["auxiliary_scores"] == []
    assert len(result["objective_scores"]) == 1
    assert result["objective_scores"][0] == obj_score


async def test_score_response_async_both_types():
    """Test score_response_async with both auxiliary and objective scorers."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    # Create mock scores
    aux_score = MagicMock(spec=Score)
    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = False  # Not successful

    # Create mock scorers
    aux_scorer = MockScorer()
    aux_scorer.score_async = AsyncMock(return_value=[aux_score])

    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=[aux_scorer], objective_scorer=obj_scorer, objective="test task"
    )

    # Should have both types of scores
    assert len(result["auxiliary_scores"]) == 1
    assert result["auxiliary_scores"][0] == aux_score
    assert len(result["objective_scores"]) == 1
    assert result["objective_scores"][0] == obj_score


async def test_score_response_async_multiple_pieces(patch_central_database):
    """Test score_response_async with multiple response pieces."""
    piece1 = MessagePiece(role="assistant", original_value="response1", conversation_id="test-convo")
    piece2 = MessagePiece(role="assistant", original_value="response2", conversation_id="test-convo")
    response = Message(message_pieces=[piece1, piece2])

    # Create mock scores
    aux_scores = [MagicMock(spec=Score) for _ in range(4)]  # 2 pieces x 2 scorers
    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = True  # Success on first piece

    # Create mock auxiliary scorers
    aux_scorer1 = MockScorer()
    aux_scorer1.score_async = AsyncMock(side_effect=[[aux_scores[0]], [aux_scores[1]]])

    aux_scorer2 = MockScorer()
    aux_scorer2.score_async = AsyncMock(side_effect=[[aux_scores[2]], [aux_scores[3]]])

    # Create mock objective scorer
    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        auxiliary_scorers=[aux_scorer1, aux_scorer2],
        objective_scorer=obj_scorer,
        objective="test task",
    )

    # TEMPORARY fix means there should only be 2 auxiliary scores, one per Message
    assert len(result["auxiliary_scores"]) == 2

    # The following commented-out lines should be uncommented when the permanent solution is implemented
    # # Should have all auxiliary scores
    # assert len(result["auxiliary_scores"]) == 4  # noqa: ERA001
    # for score in aux_scores:
    #     assert score in result["auxiliary_scores"]  # noqa: ERA001

    # Should have only one objective score (first success)
    assert len(result["objective_scores"]) == 1
    assert result["objective_scores"][0] == obj_score


async def test_score_response_async_dispatches_on_errored_response(patch_central_database):
    """Every scorer still receives an errored response; only the scorer decides what it means."""
    piece1 = MessagePiece(
        role="assistant", original_value="error", response_error="blocked", conversation_id="test-convo"
    )
    piece2 = MessagePiece(
        role="assistant", original_value="error", response_error="processing", conversation_id="test-convo"
    )
    response = Message(message_pieces=[piece1, piece2])

    # Create mock scores
    aux_score = MagicMock(spec=Score)
    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = True

    # Create mock scorers
    aux_scorer = MockScorer()
    aux_scorer.score_async = AsyncMock(return_value=[aux_score])

    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        auxiliary_scorers=[aux_scorer],
        objective_scorer=obj_scorer,
        objective="test task",
    )

    assert result == {"auxiliary_scores": [aux_score], "objective_scores": [obj_score]}

    aux_scorer.score_async.assert_called_once()
    obj_scorer.score_async.assert_called_once()


async def test_score_response_async_errored_response_is_undetermined(patch_central_database):
    """A response with nothing readable reports an undetermined verdict instead of no verdict."""
    piece = MessagePiece(
        role="assistant",
        original_value="transport failed",
        original_value_data_type="error",
        response_error="processing",
        conversation_id="test-convo",
    )
    response = Message(message_pieces=[piece])

    obj_scorer = MockScorer()

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        objective_scorer=obj_scorer,
        objective="test task",
    )

    assert len(result["objective_scores"]) == 1
    assert result["objective_scores"][0].status == ScoreStatus.UNDETERMINED


async def test_score_response_async_dispatches_to_a_non_message_scorer_on_error(patch_central_database):
    """A scorer whose evidence is not the response must still run when the response failed.

    This is the contract that unblocks trace and tool-call scoring: a scorer that reads
    evidence the response never held (for example, whether a tool was called) is asked
    even when the target itself errored.
    """

    class ToolCallScorer(Scorer):
        """A scorer whose evidence never comes from the response."""

        def __init__(self) -> None:
            super().__init__()
            self.seen_scorables: list[Scorable] = []

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_scorable_async(self, *, scorable, expectation=None) -> list[Score]:
            self.seen_scorables.append(scorable)
            return [
                Score(
                    score_value="true",
                    score_value_description="tool call observed",
                    score_type="true_false",
                    score_category=None,
                    score_metadata=None,
                    score_rationale="the agent called the tool before the target errored",
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=uuid.uuid4(),
                    objective=expectation.objective if expectation else None,
                )
            ]

        def validate_return_scores(self, scores: list[Score]) -> None:
            pass

        def get_scorer_metrics(self):
            return None

    piece = MessagePiece(
        role="assistant",
        original_value="transport failed",
        original_value_data_type="error",
        response_error="processing",
        conversation_id="test-convo",
    )
    scorer = ToolCallScorer()

    scores = await MessageScorer.score_response_multiple_scorers_async(
        response=store_message(Message(message_pieces=[piece])),
        scorers=[scorer],
        objective="test task",
    )

    assert len(scores) == 1
    assert len(scorer.seen_scorables) == 1


async def test_score_response_async_scores_partly_errored_response(patch_central_database):
    """A response is scored on the pieces that came through; one bad piece is not enough to stop it."""
    piece1 = MessagePiece(role="assistant", original_value="good response", conversation_id="test-convo")
    piece2 = MessagePiece(
        role="assistant", original_value="error", response_error="blocked", conversation_id="test-convo"
    )
    response = Message(message_pieces=[piece1, piece2])

    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = True

    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        objective_scorer=obj_scorer,
        objective="test task",
    )

    assert result["objective_scores"] == [obj_score]
    obj_scorer.score_async.assert_called_once()


async def test_score_response_async_includes_error_pieces(patch_central_database):
    """Test score_response_async includes error pieces."""
    piece1 = MessagePiece(role="assistant", original_value="good response", conversation_id="test-convo")
    piece2 = MessagePiece(
        role="assistant", original_value="error", response_error="blocked", conversation_id="test-convo"
    )
    response = Message(message_pieces=[piece1, piece2])

    # Create mock scores
    aux_scores = [MagicMock(spec=Score), MagicMock(spec=Score)]
    obj_score = MagicMock(spec=Score)
    obj_score.get_value.return_value = True

    # Create mock scorers
    aux_scorer = MockScorer()
    aux_scorer.score_async = AsyncMock(side_effect=[[aux_scores[0]], [aux_scores[1]]])

    obj_scorer = MockScorer()
    obj_scorer.score_async = AsyncMock(return_value=[obj_score])

    result = await MessageScorer.score_response_async(
        response=store_message(response),
        auxiliary_scorers=[aux_scorer],
        objective_scorer=obj_scorer,
        objective="test task",
    )

    # Temporary fix means there should only be 1 auxiliary score (first piece)
    assert len(result["auxiliary_scores"]) == 1
    # The following commented-out lines should be uncommented when the permanent solution is implemented
    # # Should score both pieces for auxiliary
    # assert len(result["auxiliary_scores"]) == 2  # noqa: ERA001

    # But only one objective score (first success)
    assert len(result["objective_scores"]) == 1

    # # Verify both pieces were scored for auxiliary
    # assert aux_scorer.score_async.call_count == 2  # noqa: ERA001


async def test_score_response_async_objective_failure():
    """Test score_response_async when no objective succeeds."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    # Create mock scores (all failures)
    obj_score1 = MagicMock(spec=Score)
    obj_score1.get_value.return_value = False

    obj_score2 = MagicMock(spec=Score)
    obj_score2.get_value.return_value = False

    # Create mock objective scorers
    obj_scorer1 = MockScorer()
    obj_scorer1.score_async = AsyncMock(return_value=[obj_score1])

    obj_scorer2 = MockScorer()
    obj_scorer2.score_async = AsyncMock(return_value=[obj_score2])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=None, objective_scorer=obj_scorer1, objective="test task"
    )

    # Should return the first score as failure indicator
    assert result["auxiliary_scores"] == []
    assert len(result["objective_scores"]) == 1
    assert result["objective_scores"][0] == obj_score1


async def test_score_response_async_concurrent_execution():
    """Test that auxiliary and objective scoring happen concurrently."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    # Track call order to verify concurrent execution
    call_order = []

    async def mock_aux_score_async(**kwargs) -> list[Score]:
        call_order.append("aux_start")
        # Yield so the other scorer can interleave (proves concurrent execution).
        await asyncio.sleep(0)
        call_order.append("aux_end")
        return [MagicMock(spec=Score)]

    async def mock_obj_score_async(**kwargs) -> list[Score]:
        call_order.append("obj_start")
        # Yield so the other scorer can interleave (proves concurrent execution).
        await asyncio.sleep(0)
        call_order.append("obj_end")
        score = MagicMock(spec=Score)
        score.get_value.return_value = True
        return [score]

    aux_scorer = MockScorer()
    aux_scorer.score_async = mock_aux_score_async

    obj_scorer = MockScorer()
    obj_scorer.score_async = mock_obj_score_async

    await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=[aux_scorer], objective_scorer=obj_scorer, objective="test task"
    )

    # Both should start before either finishes (concurrent execution)
    assert call_order.index("aux_start") < call_order.index("obj_end")
    assert call_order.index("obj_start") < call_order.index("aux_end")


async def test_score_response_async_empty_lists():
    """Test score_response_async with empty scorer lists."""
    piece = MessagePiece(role="assistant", original_value="response")
    response = Message(message_pieces=[piece])

    result = await MessageScorer.score_response_async(
        response=response, auxiliary_scorers=[], objective_scorer=None, objective="test task"
    )

    assert result == {"auxiliary_scores": [], "objective_scores": []}


async def test_get_supported_pieces_filters_unsupported_data_types(patch_central_database):
    """Test that _get_supported_pieces only returns pieces with supported data types."""
    validator = SelectiveValidator(enforce_all_pieces_valid=False)
    scorer = MockFloatScorer(validator=validator)

    # Verify validator is configured correctly
    assert "text" in validator._supported_data_types
    assert (
        "image_path" not in validator._supported_data_types
        or len([dt for dt in validator._supported_data_types if dt != "text"]) == 0
    )

    # Create a response with mixed data types
    text_id = uuid.uuid4()
    text_piece = MessagePiece(
        role="assistant",
        original_value="text response",
        converted_value_data_type="text",
        id=text_id,
        conversation_id="test-convo",
    )
    image_piece = MessagePiece(
        role="assistant",
        original_value="image.png",
        converted_value_data_type="image_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )
    audio_piece = MessagePiece(
        role="assistant",
        original_value="audio.wav",
        converted_value_data_type="audio_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )

    # Verify validator filtering works
    assert validator.is_message_piece_supported(text_piece) is True
    assert validator.is_message_piece_supported(image_piece) is False
    assert validator.is_message_piece_supported(audio_piece) is False

    response = Message(message_pieces=[text_piece, image_piece, audio_piece])

    # Score the response
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))

    # Should only score the text piece
    assert len(scorer.scored_piece_ids) == 1
    assert scorer.scored_piece_ids[0] == str(text_id)
    assert len(scores) == 1
    assert scores[0].message_piece_id == text_id


async def test_unsupported_pieces_ignored_when_enforce_all_pieces_valid_false(patch_central_database):
    """Test that unsupported pieces don't cause errors when enforce_all_pieces_valid=False."""
    validator = SelectiveValidator(enforce_all_pieces_valid=False)
    scorer = MockFloatScorer(validator=validator)

    # Create a response with only unsupported types and one supported
    text_id = uuid.uuid4()
    text_piece = MessagePiece(
        role="assistant",
        original_value="text response",
        converted_value_data_type="text",
        id=text_id,
        conversation_id="test-convo",
    )
    image_piece = MessagePiece(
        role="assistant",
        original_value="image.png",
        converted_value_data_type="image_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )

    response = Message(message_pieces=[image_piece, text_piece])

    # Should not raise an error, just skip the image piece
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))

    assert len(scores) == 1
    assert len(scorer.scored_piece_ids) == 1
    assert scorer.scored_piece_ids[0] == str(text_id)


async def test_all_unsupported_pieces_raises_error(patch_central_database):
    """Test that having no supported pieces raises a clear error when raise_on_no_valid_pieces=True."""
    validator = SelectiveValidator(enforce_all_pieces_valid=False, raise_on_no_valid_pieces=True)
    scorer = MockFloatScorer(validator=validator)

    # Create a response with only unsupported types
    image_piece = MessagePiece(
        role="assistant",
        original_value="image.png",
        converted_value_data_type="image_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )
    audio_piece = MessagePiece(
        role="assistant",
        original_value="audio.wav",
        converted_value_data_type="audio_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )

    response = Message(message_pieces=[image_piece, audio_piece])

    # Should raise error from validator because no valid pieces to score
    with pytest.raises(ValueError, match="There are no valid pieces to score"):
        await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))

    # No pieces should have been scored
    assert len(scorer.scored_piece_ids) == 0


async def test_true_false_scorer_uses_supported_pieces_only(patch_central_database):
    """Test that TrueFalseScorer also uses _get_supported_pieces via base implementation."""
    validator = SelectiveValidator(enforce_all_pieces_valid=False)

    class TestTrueFalseScorer(MessageTrueFalseScorer):
        def __init__(self):
            self.scored_piece_ids = []
            super().__init__(validator=validator)

        def _build_identifier(self) -> ComponentIdentifier:
            """Build the scorer evaluation identifier for this test scorer."""
            return self._create_identifier()

        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            self.scored_piece_ids.append(message_piece.id)
            return [
                Score(
                    score_value="true",
                    score_value_description="Test",
                    score_type="true_false",
                    score_category=None,
                    score_metadata=None,
                    score_rationale="Test",
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=message_piece.id or "test-id",
                    objective=objective,
                )
            ]

    scorer = TestTrueFalseScorer()

    # Create mixed response
    text_id = uuid.uuid4()
    text_piece = MessagePiece(
        role="assistant",
        original_value="text",
        converted_value_data_type="text",
        id=text_id,
        conversation_id="test-convo",
    )
    image_piece = MessagePiece(
        role="assistant",
        original_value="image.png",
        converted_value_data_type="image_path",
        id=uuid.uuid4(),
        conversation_id="test-convo",
    )

    response = Message(message_pieces=[text_piece, image_piece])

    # Score the response
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))

    # Should only score the text piece
    assert len(scorer.scored_piece_ids) == 1
    assert scorer.scored_piece_ids[0] == text_id
    # TrueFalseScorer aggregates to single score
    assert len(scores) == 1
    assert scores[0].score_value == "true"


async def test_base_scorer_score_async_implementation(patch_central_database):
    """Test that the base Scorer._score_async implementation works correctly."""
    validator = SelectiveValidator(enforce_all_pieces_valid=False)
    scorer = MockFloatScorer(validator=validator)

    # Create response with multiple supported pieces
    text_id1 = uuid.uuid4()
    text_id2 = uuid.uuid4()
    text_piece1 = MessagePiece(
        role="assistant",
        original_value="text 1",
        converted_value_data_type="text",
        id=text_id1,
        conversation_id="test-convo",
    )
    text_piece2 = MessagePiece(
        role="assistant",
        original_value="text 2",
        converted_value_data_type="text",
        id=text_id2,
        conversation_id="test-convo",
    )

    response = Message(message_pieces=[text_piece1, text_piece2])

    # Score the response
    scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(response)))

    # Should score both pieces
    assert len(scorer.scored_piece_ids) == 2
    assert str(text_id1) in scorer.scored_piece_ids
    assert str(text_id2) in scorer.scored_piece_ids
    assert len(scores) == 2


class TestLegacyDirectScorerSubclass:
    """Scorers written against the pre-2.0 base keep working behind a deprecation warning."""

    @staticmethod
    def _build_legacy_scorer_class():
        class LegacyScorer(Scorer):
            def __init__(self, *, validator: ScorerPromptValidator):
                super().__init__(validator=validator)
                self.scored_messages: list[Message] = []

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
                self.scored_messages.append(message)
                return [
                    Score(
                        score_value="true",
                        score_value_description="legacy",
                        score_type="true_false",
                        score_category=None,
                        score_metadata=None,
                        score_rationale="legacy",
                        scorer_class_identifier=self.get_identifier(),
                        message_piece_id=message.get_piece().id,
                        objective=objective,
                    )
                ]

            def validate_return_scores(self, scores: list[Score]) -> None:
                pass

            def get_scorer_metrics(self):
                return None

        return LegacyScorer

    def test_legacy_scorer_is_instantiable(self):
        legacy_class = self._build_legacy_scorer_class()

        assert "_score_scorable_async" not in legacy_class.__abstractmethods__

    def test_legacy_validator_argument_warns(self):
        legacy_class = self._build_legacy_scorer_class()

        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            scorer = legacy_class(validator=DummyValidator())

        assert scorer._validator is not None

    async def test_legacy_scorer_scores_a_scorable(self, patch_central_database):
        legacy_class = self._build_legacy_scorer_class()
        with pytest.warns(DeprecationWarning):
            scorer = legacy_class(validator=DummyValidator())
        message = store_message(
            MessagePiece(role="assistant", original_value="legacy response", conversation_id="legacy").to_message()
        )

        with pytest.warns(DeprecationWarning, match="_score_async"):
            scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert scorer.scored_messages[0].get_value() == "legacy response"

    async def test_legacy_piece_only_scorer_is_adapted(self, patch_central_database):
        class LegacyPieceScorer(TrueFalseScorer):
            def __init__(self, *, validator: ScorerPromptValidator):
                super().__init__(validator=validator)

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                return [Score(score_value="true", score_type="true_false", objective=objective)]

        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            scorer = LegacyPieceScorer(validator=DummyValidator())

        with pytest.warns(DeprecationWarning, match="_score_async"):
            scores = await scorer.score_async(
                scorable=ContentScorable(value="legacy content"),
                expectation=ScoringExpectation(objective="legacy objective"),
            )

        assert scores[0].get_value() is True
        assert scores[0].objective == "legacy objective"

    async def test_legacy_true_false_piece_scorer_keeps_message_aggregation(self, patch_central_database):
        class LegacyTrueFalsePieceScorer(TrueFalseScorer):
            def __init__(self, *, validator: ScorerPromptValidator):
                super().__init__(validator=validator)

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                return [
                    Score(
                        score_value=str(message_piece.converted_value == "match"),
                        score_type="true_false",
                        objective=objective,
                    )
                ]

        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            scorer = LegacyTrueFalsePieceScorer(validator=DummyValidator())
        message = store_message(
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value="no match", sequence=0),
                    MessagePiece(role="assistant", original_value="match", sequence=0),
                ]
            )
        )

        with pytest.warns(DeprecationWarning, match="_score_async"):
            scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert scores[0].get_value() is True

    async def test_legacy_float_piece_scorer_keeps_family_fan_out(self, patch_central_database):
        class LegacyFloatPieceScorer(FloatScaleScorer):
            def __init__(self, *, validator: ScorerPromptValidator):
                super().__init__(validator=validator)

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                return [Score(score_value="0.5", score_type="float_scale", objective=objective)]

        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            scorer = LegacyFloatPieceScorer(validator=DummyValidator())
        message = store_message(
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value="first", sequence=0),
                    MessagePiece(role="assistant", original_value="second", sequence=0),
                ]
            )
        )

        with pytest.warns(DeprecationWarning, match="_score_async"):
            scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 2

    @staticmethod
    def _build_legacy_family_scorer_class():
        class LegacyFamilyScorer(TrueFalseScorer):
            def __init__(self) -> None:
                super().__init__(validator=DummyValidator())

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                return [Score(score_value="true", score_type="true_false", objective=objective)]

        return LegacyFamilyScorer

    async def test_legacy_family_scorer_can_nest_in_a_wrapper(self, patch_central_database):
        from pyrit.score import TrueFalseCompositeScorer, TrueFalseScoreAggregator

        with pytest.warns(DeprecationWarning, match="Scorer.__init__"):
            legacy_scorer = self._build_legacy_family_scorer_class()()
        composite = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[legacy_scorer])
        message = store_message(
            MessagePiece(role="assistant", original_value="legacy", conversation_id="legacy-nested").to_message()
        )

        scores = await composite.score_async(scorable=MessageScorable.from_message(message))

        assert scores[0].get_value() is True

    async def test_scorer_score_response_async_still_dispatches(self, patch_central_database):
        scorer = MockScorer()
        message = store_message(
            MessagePiece(role="assistant", original_value="response", conversation_id="legacy-response").to_message()
        )

        with pytest.warns(DeprecationWarning, match="Scorer.score_response_async"):
            results = await Scorer.score_response_async(response=message, objective_scorer=scorer)

        assert len(results["objective_scores"]) == 1

    async def test_scorer_score_response_async_preserves_role_filter(self, patch_central_database):
        scorer = MockScorer()
        message = store_message(
            MessagePiece(role="assistant", original_value="response", conversation_id="legacy-role").to_message()
        )

        with pytest.warns(DeprecationWarning, match="role_filter"):
            results = await Scorer.score_response_async(
                response=message,
                objective_scorer=scorer,
                role_filter="user",
            )

        assert results["objective_scores"] == []

    async def test_scorer_score_response_multiple_scorers_async_still_dispatches(self, patch_central_database):
        scorer = MockScorer()
        message = store_message(
            MessagePiece(role="assistant", original_value="response", conversation_id="legacy-multi").to_message()
        )

        with pytest.warns(DeprecationWarning, match="score_response_multiple_scorers_async"):
            scores = await Scorer.score_response_multiple_scorers_async(response=message, scorers=[scorer])

        assert len(scores) == 1


# Tests for get_identifier and identifier


def test_mock_scorer_get_identifier_returns_type():
    """Test that get_identifier returns a ComponentIdentifier with the correct class_name."""
    scorer = MockScorer()
    identifier = scorer.get_identifier()

    assert identifier.class_name == "MockScorer"


def test_mock_scorer_get_identifier_includes_hash():
    """Test that get_identifier returns a ComponentIdentifier with a hash field."""
    scorer = MockScorer()
    identifier = scorer.get_identifier()

    assert hasattr(identifier, "hash")
    assert isinstance(identifier.hash, str)
    assert len(identifier.hash) == 64  # SHA256 hex digest length


def test_mock_scorer_get_identifier_deterministic():
    """Test that get_identifier returns the same values for the same scorer."""
    scorer = MockScorer()

    id1 = scorer.get_identifier()
    id2 = scorer.get_identifier()

    assert id1 == id2


def test_mock_scorer_get_identifier_hash_deterministic():
    """Test that the hash is consistent across multiple calls."""
    scorer = MockScorer()

    hash1 = scorer.get_identifier().hash
    hash2 = scorer.get_identifier().hash

    assert hash1 == hash2


def test_mock_scorer_get_identifier_is_component_identifier():
    """Test that get_identifier returns a ComponentIdentifier."""
    scorer = MockScorer()
    sid = scorer.get_identifier()

    assert isinstance(sid, ComponentIdentifier)
    assert sid.class_name == "MockScorer"


def test_mock_scorer_identifier_lazy_build():
    """Test that identifier is built lazily on first access."""
    scorer = MockScorer()

    # Before accessing, _identifier should be None
    assert scorer._identifier is None

    # After accessing via get_identifier(), it should be built
    _ = scorer.get_identifier()
    assert scorer._identifier is not None


def test_mock_float_scorer_get_identifier():
    """Test get_identifier for MockFloatScorer."""
    validator = DummyValidator()
    scorer = MockFloatScorer(validator=validator)

    identifier = scorer.get_identifier()

    assert identifier.class_name == "MockFloatScorer"
    assert hasattr(identifier, "hash")


class TestTrueFalseScorerEmptyResults:
    """Tests for true/false results when no pieces are scored."""

    @pytest.fixture
    def no_valid_pieces_validator(self):
        """Validator that doesn't raise on no valid pieces and only supports text."""
        return ScorerPromptValidator(
            supported_data_types=["text"],
            enforce_all_pieces_valid=False,
            raise_on_no_valid_pieces=False,
        )

    @pytest.fixture
    def true_false_scorer_returns_empty(self, no_valid_pieces_validator):
        """Create a TrueFalseScorer where _score_piece_async returns empty list."""

        class TestTrueFalseScorer(MessageTrueFalseScorer):
            def __init__(self, *, validator):
                super().__init__(validator=validator)

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                # Return empty list to simulate no scorable pieces
                return []

        return TestTrueFalseScorer(validator=no_valid_pieces_validator)

    async def test_blocked_response_returns_specific_rationale(
        self, true_false_scorer_returns_empty, patch_central_database
    ):
        """Test that a blocked response returns a rationale mentioning 'blocked'."""
        blocked_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="",
            converted_value_data_type="text",
            conversation_id="test-convo",
            response_error="blocked",
        )
        response = Message(message_pieces=[blocked_piece])

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert "blocked" in scores[0].score_rationale.lower()
        assert "blocked" in scores[0].score_value_description.lower()

    async def test_error_response_returns_undetermined_score(
        self, true_false_scorer_returns_empty, patch_central_database
    ):
        """Test that a non-blocked error response is undetermined rather than false."""
        # response_error must be a valid PromptResponseError: "blocked", "none", "processing", "empty", "unknown"
        error_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="",
            converted_value_data_type="text",
            conversation_id="test-convo",
            response_error="unknown",
        )
        response = Message(message_pieces=[error_piece])

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert len(scores) == 1
        assert scores[0].is_undetermined
        assert "error" in scores[0].score_rationale.lower()
        assert "unknown" in scores[0].score_rationale

    async def test_supported_piece_with_no_result_returns_empty(
        self, true_false_scorer_returns_empty, patch_central_database
    ):
        normal_piece = MessagePiece(
            role="assistant",
            original_value="some text",
            converted_value="some text",
            converted_value_data_type="text",
            conversation_id="test-convo",
            response_error="none",
        )
        response = Message(message_pieces=[normal_piece])

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert scores == []

    async def test_blocked_takes_precedence_over_generic_error(
        self, true_false_scorer_returns_empty, patch_central_database
    ):
        """Test that blocked status is checked before generic has_error check."""
        # response_error="blocked" should mention "blocked" not just "error"
        blocked_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="",
            converted_value_data_type="text",
            conversation_id="test-convo",
            response_error="blocked",
        )
        response = Message(message_pieces=[blocked_piece])

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        # Should specifically mention blocked, not generic error
        assert "blocked" in scores[0].score_rationale.lower()
        # The description should also mention blocked, not just "error"
        assert "blocked" in scores[0].score_value_description.lower()

    @pytest.mark.parametrize("error_first", [False, True])
    async def test_non_blocking_error_takes_precedence_across_all_pieces(
        self, true_false_scorer_returns_empty, patch_central_database, error_first
    ):
        """A transport error makes the result undetermined in either piece order."""
        blocked_piece = MessagePiece(
            role="assistant",
            original_value="blocked",
            converted_value_data_type="error",
            conversation_id="test-convo",
            response_error="blocked",
        )
        error_piece = MessagePiece(
            role="assistant",
            original_value="transport failed",
            converted_value_data_type="error",
            conversation_id="test-convo",
            response_error="processing",
        )
        pieces = [error_piece, blocked_piece] if error_first else [blocked_piece, error_piece]

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(Message(message_pieces=pieces)))
        )

        assert len(scores) == 1
        assert scores[0].status == ScoreStatus.UNDETERMINED
        assert "processing" in scores[0].score_rationale

    async def test_error_takes_precedence_over_unsupported_data_type(
        self, true_false_scorer_returns_empty, patch_central_database
    ):
        """A transport failure is undetermined when no readable piece applies."""
        response = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="transport failed",
                    converted_value_data_type="error",
                    conversation_id="test-convo",
                    response_error="processing",
                ),
                MessagePiece(
                    role="assistant",
                    original_value="unsupported",
                    converted_value_data_type="image_path",
                    conversation_id="test-convo",
                ),
            ]
        )

        scores = await true_false_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert len(scores) == 1
        assert scores[0].status == ScoreStatus.UNDETERMINED
        assert "processing" in scores[0].score_rationale


class TestFloatScaleScorerEmptyResults:
    """Tests for float-scale results when no pieces are scored."""

    @pytest.fixture
    def no_valid_pieces_validator(self):
        """Validator that doesn't raise on no valid pieces and only supports text."""
        return ScorerPromptValidator(
            supported_data_types=["text"],
            enforce_all_pieces_valid=False,
            raise_on_no_valid_pieces=False,
        )

    @pytest.fixture
    def float_scale_scorer_returns_empty(self, no_valid_pieces_validator):
        """Create a FloatScaleScorer whose _score_piece_async returns an empty list."""
        from pyrit.score.float_scale.float_scale_scorer import MessageFloatScaleScorer

        class _TestFloatScaleScorer(MessageFloatScaleScorer):
            def __init__(self, *, validator):
                super().__init__(validator=validator)

            def _build_identifier(self) -> ComponentIdentifier:
                return self._create_identifier()

            async def _score_piece_async(
                self, message_piece: MessagePiece, *, objective: str | None = None
            ) -> list[Score]:
                return []

        return _TestFloatScaleScorer(validator=no_valid_pieces_validator)

    async def test_blocked_response_returns_zero_with_blocked_rationale(
        self, float_scale_scorer_returns_empty, patch_central_database
    ):
        """A blocked response yields Score(0.0) with a rationale mentioning 'blocked'."""
        blocked_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="",
            converted_value_data_type="error",
            conversation_id="test-convo",
            response_error="blocked",
        )
        response = Message(message_pieces=[blocked_piece])

        scores = await float_scale_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert len(scores) == 1
        assert scores[0].score_type == "float_scale"
        assert scores[0].get_value() == 0.0
        assert "blocked" in scores[0].score_rationale.lower()
        assert "blocked" in scores[0].score_value_description.lower()

    async def test_other_error_response_returns_undetermined_score(
        self, float_scale_scorer_returns_empty, patch_central_database
    ):
        """A non-blocked error response is undetermined rather than 0.0."""
        error_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="",
            converted_value_data_type="error",
            conversation_id="test-convo",
            response_error="unknown",
        )
        response = Message(message_pieces=[error_piece])

        scores = await float_scale_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert len(scores) == 1
        assert scores[0].is_undetermined
        assert "error" in scores[0].score_rationale.lower()
        assert "unknown" in scores[0].score_rationale

    async def test_supported_piece_with_no_result_returns_empty(
        self, float_scale_scorer_returns_empty, patch_central_database
    ):
        normal_piece = MessagePiece(
            role="assistant",
            original_value="some text",
            converted_value="some text",
            converted_value_data_type="text",
            conversation_id="test-convo",
            response_error="none",
        )
        response = Message(message_pieces=[normal_piece])

        scores = await float_scale_scorer_returns_empty.score_async(
            scorable=MessageScorable.from_message(store_message(response))
        )

        assert scores == []

    async def test_text_only_scorer_filters_blocked_via_validator(
        self, float_scale_scorer_returns_empty, patch_central_database
    ):
        """A text-only FloatScaleScorer never invokes _score_piece_async for blocked pieces;
        the unified fallback returns 0.0 directly."""
        blocked_piece = MessagePiece(
            role="assistant",
            original_value="",
            converted_value="error-json-blob",
            converted_value_data_type="error",
            conversation_id="test-convo",
            response_error="blocked",
        )
        response = Message(message_pieces=[blocked_piece])

        # _score_piece_async should not be called because validator filters the error piece
        with patch.object(
            float_scale_scorer_returns_empty, "_score_piece_async", new_callable=AsyncMock
        ) as mock_score_piece:
            scores = await float_scale_scorer_returns_empty.score_async(
                scorable=MessageScorable.from_message(store_message(response))
            )

        mock_score_piece.assert_not_called()
        assert len(scores) == 1
        assert scores[0].get_value() == 0.0


async def test_score_value_with_llm_skips_reasoning_piece(good_json, patch_central_database):
    """Test that _score_value_with_llm extracts JSON from the text piece, not a reasoning piece."""
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    # Simulate a reasoning model response: first piece is reasoning, second is the actual text with JSON
    reasoning_piece = MessagePiece(
        role="assistant",
        original_value="Let me think about this...",
        original_value_data_type="reasoning",
        converted_value="Let me think about this...",
        converted_value_data_type="reasoning",
        conversation_id="test-convo",
    )
    text_piece = MessagePiece(
        role="assistant",
        original_value=good_json,
        conversation_id="test-convo",
    )
    response_message = Message(message_pieces=[reasoning_piece, text_piece])
    chat_target.send_prompt_async = AsyncMock(return_value=[response_message])

    scorer = MockScorer()

    result = await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt="system_prompt",
        value="message_value",
        data_type="text",
        scored_prompt_id="123",
        category="category",
        objective="task",
    )

    assert result.raw_score_value == "1"
    assert result.score_rationale == "Valid response"


async def test_score_value_with_llm_without_system_prompt(good_json, patch_central_database):
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    response_message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=good_json,
                conversation_id="test-convo",
            )
        ]
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[response_message])
    scorer = MockScorer()

    await _run_llm_scoring_async(
        chat_target=chat_target,
        response_handler=JsonSchemaResponseHandler(),
        scorer_identifier=scorer.get_identifier(),
        system_prompt=None,
        value="message_value",
        data_type="text",
        scored_prompt_id="123",
        category="category",
        objective="task",
    )

    chat_target.set_system_prompt.assert_not_called()


async def test_score_value_with_llm_raises_when_scorer_response_blocked(patch_central_database):
    """When the scorer's own LLM response is blocked, the transport raises ScorerLLMResponseBlockedException."""
    from pyrit.exceptions import ScorerLLMResponseBlockedException

    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    blocked_piece = MessagePiece(
        role="assistant",
        original_value="",
        original_value_data_type="error",
        converted_value="",
        converted_value_data_type="error",
        conversation_id="test-convo",
        response_error="blocked",
    )
    blocked_response = Message(message_pieces=[blocked_piece])
    chat_target.send_prompt_async = AsyncMock(return_value=[blocked_response])

    scorer = MockScorer()

    with pytest.raises(ScorerLLMResponseBlockedException, match="blocked by content filtering"):
        await _run_llm_scoring_async(
            chat_target=chat_target,
            response_handler=JsonSchemaResponseHandler(),
            scorer_identifier=scorer.get_identifier(),
            system_prompt="system_prompt",
            value="message_value",
            data_type="text",
            scored_prompt_id="test-prompt-id",
            category="category",
            objective="task",
        )

    # A blocked response is a terminal condition, not a transient JSON error: it must not retry.
    assert chat_target.send_prompt_async.call_count == 1


async def test_score_value_with_llm_raises_empty_response_when_no_text_piece(patch_central_database):
    """A no-text response that wasn't content-filtered raises EmptyResponseException, not blocked."""
    from pyrit.exceptions import EmptyResponseException

    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    # An error piece that is NOT flagged as blocked (e.g. a flaky/empty response) and no text piece.
    non_text_piece = MessagePiece(
        role="assistant",
        original_value="",
        original_value_data_type="error",
        converted_value="",
        converted_value_data_type="error",
        conversation_id="test-convo",
        response_error="unknown",
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[Message(message_pieces=[non_text_piece])])

    scorer = MockScorer()

    with pytest.raises(EmptyResponseException, match="no text to parse"):
        await _run_llm_scoring_async(
            chat_target=chat_target,
            response_handler=JsonSchemaResponseHandler(),
            scorer_identifier=scorer.get_identifier(),
            system_prompt="system_prompt",
            value="message_value",
            data_type="text",
            scored_prompt_id="test-prompt-id",
            category="category",
            objective="task",
        )

    # No parseable text is terminal here, not a transient JSON error: it must not retry.
    assert chat_target.send_prompt_async.call_count == 1


# ── Axis B: the scorer's own LLM response is blocked (raise_if_scorer_blocks) ─────────────


class _ForwarderTrueFalseScorer(MessageTrueFalseScorer):
    """TrueFalseScorer whose piece scoring uses the shared LLM scoring composition helper."""

    def __init__(self, *, chat_target: PromptTarget) -> None:
        super().__init__(validator=DummyValidator())
        self._prompt_target = chat_target
        self._system_prompt = "system"
        self._response_handler = JsonSchemaResponseHandler()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        unvalidated = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            response_handler=self._response_handler,
            scorer_identifier=self.get_identifier(),
            system_prompt=self._system_prompt,
            value=message_piece.converted_value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            objective=objective,
        )
        return [unvalidated.to_score(score_value=unvalidated.raw_score_value, score_type="true_false")]


class _DirectTransportTrueFalseScorer(MessageTrueFalseScorer):
    """TrueFalseScorer that calls ``_run_llm_scoring_async`` directly, like SelfAskTrueFalseScorer."""

    def __init__(self, *, chat_target: PromptTarget) -> None:
        from pyrit.score import JsonSchemaResponseHandler

        super().__init__(validator=DummyValidator())
        self._prompt_target = chat_target
        self._system_prompt = "system"
        self._response_handler = JsonSchemaResponseHandler()

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        from pyrit.score.llm_scoring import _run_llm_scoring_async

        unvalidated = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=message_piece.converted_value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            objective=objective,
        )
        return [unvalidated.to_score(score_value=unvalidated.raw_score_value, score_type="true_false")]


class _ForwarderFloatScaleScorer(MessageFloatScaleScorer):
    """FloatScaleScorer whose piece scoring uses the shared LLM scoring composition helper."""

    def __init__(self, *, chat_target: PromptTarget) -> None:
        super().__init__(validator=DummyValidator())
        self._prompt_target = chat_target
        self._system_prompt = "system"
        self._response_handler = JsonSchemaResponseHandler(numeric_value=True)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        unvalidated = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            response_handler=self._response_handler,
            scorer_identifier=self.get_identifier(),
            system_prompt=self._system_prompt,
            value=message_piece.converted_value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            objective=objective,
        )
        return [unvalidated.to_score(score_value=unvalidated.raw_score_value, score_type="float_scale")]


def _make_scorer_blocking_target() -> MagicMock:
    """A chat target mock whose response is fully blocked by content filtering."""
    chat_target = MagicMock(PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.set_system_prompt = MagicMock()
    blocked_piece = MessagePiece(
        role="assistant",
        original_value="",
        original_value_data_type="error",
        converted_value="",
        converted_value_data_type="error",
        conversation_id="scorer-convo",
        response_error="blocked",
    )
    chat_target.send_prompt_async = AsyncMock(return_value=[Message(message_pieces=[blocked_piece])])
    return chat_target


def _make_normal_input_message() -> Message:
    """A normal (non-blocked) message to be scored."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="some response to score",
                converted_value="some response to score",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id="input-convo",
            )
        ]
    )


@pytest.mark.usefixtures("patch_central_database")
class TestScorerResponseBlocked:
    """Axis B: behavior when the scorer's own LLM response is content-filtered."""

    async def test_raises_by_default(self):
        from pyrit.exceptions import ScorerLLMResponseBlockedException

        scorer = _ForwarderTrueFalseScorer(chat_target=_make_scorer_blocking_target())

        with pytest.raises(ScorerLLMResponseBlockedException, match="blocked by content filtering"):
            await scorer.score_async(scorable=MessageScorable.from_message(store_message(_make_normal_input_message())))

    async def test_returns_undetermined_when_flag_disabled(self):
        target = _make_scorer_blocking_target()
        scorer = _ForwarderTrueFalseScorer(chat_target=target)
        scorer.raise_if_scorer_blocks = False

        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(store_message(_make_normal_input_message()))
        )

        assert len(scores) == 1
        # A blocked scorer response means nothing was determined, not that the answer is no.
        assert scores[0].is_undetermined
        assert scores[0].score_value is None
        assert "blocked by content filtering" in scores[0].score_rationale
        # Blocked is terminal: no retry storm.
        assert target.send_prompt_async.call_count == 1

    async def test_returns_undetermined_for_float_scale_when_flag_disabled(self):
        scorer = _ForwarderFloatScaleScorer(chat_target=_make_scorer_blocking_target())
        scorer.raise_if_scorer_blocks = False

        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(store_message(_make_normal_input_message()))
        )

        assert len(scores) == 1
        assert scores[0].is_undetermined
        assert scores[0].score_value is None
        assert "blocked by content filtering" in scores[0].score_rationale

    async def test_direct_transport_caller_raises_by_default(self):
        from pyrit.exceptions import ScorerLLMResponseBlockedException

        scorer = _DirectTransportTrueFalseScorer(chat_target=_make_scorer_blocking_target())

        with pytest.raises(ScorerLLMResponseBlockedException, match="blocked by content filtering"):
            await scorer.score_async(scorable=MessageScorable.from_message(store_message(_make_normal_input_message())))

    async def test_direct_transport_caller_returns_undetermined_when_flag_disabled(self):
        scorer = _DirectTransportTrueFalseScorer(chat_target=_make_scorer_blocking_target())
        scorer.raise_if_scorer_blocks = False

        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(store_message(_make_normal_input_message()))
        )

        assert len(scores) == 1
        assert scores[0].is_undetermined
        assert scores[0].score_value is None
        assert "blocked by content filtering" in scores[0].score_rationale


# ── Helpers for should_score_blocked_content tests ───────────────────────────


class _AcceptAllValidator(ScorerPromptValidator):
    """Validator that accepts all pieces (like SelfAskRefusalScorer's default)."""

    def validate(self, message: Message, objective: str | None = None) -> None:
        pass

    def is_message_piece_supported(self, message_piece: MessagePiece) -> bool:
        return True


class _TextOnlyValidator(ScorerPromptValidator):
    """Validator that only accepts text pieces (like SelfAskTrueFalseScorer's default)."""

    def __init__(self) -> None:
        super().__init__(supported_data_types=["text", "image_path"])

    def validate(self, message: Message, objective: str | None = None) -> None:
        pass


class _BlockedContentScorer(MessageTrueFalseScorer):
    """A mock TrueFalseScorer that records what pieces it was asked to score."""

    def __init__(self, *, validator: ScorerPromptValidator | None = None) -> None:
        super().__init__(validator=validator or _TextOnlyValidator())
        self.scored_pieces: list[MessagePiece] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        self.scored_pieces.append(message_piece)
        return [
            Score(
                score_value="true",
                score_value_description="desc",
                score_type="true_false",
                score_category=None,
                score_metadata=None,
                score_rationale="rationale",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=str(message_piece.id),
                objective=objective,
            )
        ]


class _MockRefusalScorer(MessageTrueFalseScorer):
    """Mimics SelfAskRefusalScorer: accepts all types, short-circuits on blocked."""

    def __init__(self) -> None:
        super().__init__(validator=_AcceptAllValidator())
        self.scored_pieces: list[MessagePiece] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        self.scored_pieces.append(message_piece)
        if message_piece.response_error == "blocked":
            return [
                Score(
                    score_value="true",
                    score_value_description="Refusal detected",
                    score_type="true_false",
                    score_category=None,
                    score_metadata=None,
                    score_rationale="Content was filtered, constituting a refusal.",
                    scorer_class_identifier=self.get_identifier(),
                    message_piece_id=str(message_piece.id),
                    objective=objective,
                )
            ]
        return [
            Score(
                score_value="false",
                score_value_description="Not a refusal",
                score_type="true_false",
                score_category=None,
                score_metadata=None,
                score_rationale="The response contains substantive content.",
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=str(message_piece.id),
                objective=objective,
            )
        ]


def _make_blocked_piece(
    *,
    partial_content: str | None = None,
    structured_refusal: str | None = None,
    conversation_id: str = "test-convo",
    role: ChatMessageRole = "assistant",
) -> MessagePiece:
    """Create a blocked MessagePiece, optionally with partial content metadata."""
    metadata: dict = {}
    if partial_content is not None:
        metadata["partial_content"] = partial_content
    piece = MessagePiece(
        role=role,
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        conversation_id=conversation_id,
        response_error="blocked",
        prompt_metadata=metadata,
    )
    if structured_refusal:
        piece.mark_as_structured_refusal(refusal=structured_refusal)
    return piece


def _make_normal_piece(*, conversation_id: str = "test-convo") -> MessagePiece:
    """Create a normal text MessagePiece."""
    return MessagePiece(
        role="assistant",
        original_value="Hello, how can I help?",
        conversation_id=conversation_id,
    )


# ── _create_text_piece_from_blocked tests ────────────────────────────────────


class TestCreateTextPieceFromBlocked:
    def test_returns_text_piece_with_partial_content(self):
        piece = _make_blocked_piece(partial_content="Harmful partial text here")
        substitute = MessageScorer._create_text_piece_from_blocked(piece)

        assert substitute is not None
        assert substitute.converted_value == "Harmful partial text here"
        assert substitute.converted_value_data_type == "text"
        assert substitute.response_error == "none"
        assert substitute.id == piece.id

    def test_preserves_original_value(self):
        piece = _make_blocked_piece(partial_content="partial")
        substitute = MessageScorer._create_text_piece_from_blocked(piece)

        assert substitute is not None
        assert substitute.original_value == piece.original_value
        assert substitute.original_value_data_type == piece.original_value_data_type

    def test_returns_none_when_no_partial_content(self):
        piece = _make_blocked_piece()
        assert MessageScorer._create_text_piece_from_blocked(piece) is None

    def test_returns_none_when_empty_partial_content(self):
        piece = _make_blocked_piece(partial_content="")
        assert MessageScorer._create_text_piece_from_blocked(piece) is None

    def test_preserves_conversation_id(self):
        piece = _make_blocked_piece(partial_content="partial")
        substitute = MessageScorer._create_text_piece_from_blocked(piece)
        assert substitute is not None
        assert substitute.conversation_id == piece.conversation_id

    def test_preserves_simulated_assistant_role(self):
        piece = _make_blocked_piece(partial_content="partial", role="simulated_assistant")
        substitute = MessageScorer._create_text_piece_from_blocked(piece)
        assert substitute is not None
        assert substitute.role == "simulated_assistant"

    def test_response_error_is_none_not_blocked(self):
        """Substitute must have response_error='none' so refusal short-circuits don't fire."""
        piece = _make_blocked_piece(partial_content="partial text")
        substitute = MessageScorer._create_text_piece_from_blocked(piece)
        assert substitute is not None
        assert substitute.response_error == "none"
        assert not substitute.is_blocked()
        assert not substitute.has_error()


class TestCreateTextPieceFromStructuredRefusal:
    def test_returns_blocked_text_piece_with_refusal_explanation(self):
        piece = _make_blocked_piece(structured_refusal="I cannot assist with that request.")

        substitute = MessageScorer._create_text_piece_from_structured_refusal(piece)

        assert substitute is not None
        assert substitute.converted_value == "I cannot assist with that request."
        assert substitute.converted_value_data_type == "text"
        assert substitute.response_error == "blocked"
        assert substitute.id == piece.id

    def test_returns_none_for_generic_blocked_response(self):
        assert MessageScorer._create_text_piece_from_structured_refusal(_make_blocked_piece()) is None


# ── score_async with should_score_blocked_content tests ──────────────────────


@pytest.mark.usefixtures("patch_central_database")
class TestScoreAsyncWithBlockedContent:
    async def test_disabled_skips_blocked_piece_text_only_scorer(self):
        """With the flag off, a text-only scorer filters out blocked error-type pieces."""
        scorer = _BlockedContentScorer()
        scorer.should_score_blocked_content = False
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert len(scorer.scored_pieces) == 0

    async def test_default_substitutes_blocked_piece_for_text_only_scorer(self):
        """By default a text-only scorer gets a text substitute and scores it."""
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "true"
        assert len(scorer.scored_pieces) == 1
        assert scorer.scored_pieces[0].converted_value == "harmful text"
        assert scorer.scored_pieces[0].converted_value_data_type == "text"

    async def test_refusal_scorer_does_not_receive_unreadable_blocked_piece(self):
        """A raw error piece does not reach a leaf scorer when blocked content is disabled."""
        scorer = _MockRefusalScorer()
        scorer.should_score_blocked_content = False
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert scorer.scored_pieces == []

    async def test_refusal_scorer_evaluates_partial_content_by_default(self):
        """By default a refusal scorer gets the substitute (response_error=none) and evaluates it."""
        scorer = _MockRefusalScorer()
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert scorer.scored_pieces[0].response_error == "none"
        assert scorer.scored_pieces[0].converted_value == "harmful text"

    async def test_no_substitute_when_no_partial_content(self):
        """400 full block with no partial content: no substitute, same behavior."""
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece()])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert len(scorer.scored_pieces) == 0

    async def test_normal_piece_unaffected_by_flag(self):
        """Normal text pieces are scored the same regardless of flag."""
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_normal_piece()])

        scorer.should_score_blocked_content = False
        scores_off = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))
        scorer.scored_pieces.clear()
        scorer.should_score_blocked_content = True
        scores_on = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert scores_off[0].score_value == scores_on[0].score_value

    async def test_mixed_pieces_only_blocked_substituted(self):
        """In a multi-piece message, only blocked pieces get substituted."""
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_normal_piece(), _make_blocked_piece(partial_content="partial harmful")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1  # TrueFalseScorer aggregates
        assert len(scorer.scored_pieces) == 2
        assert scorer.scored_pieces[0].converted_value == "Hello, how can I help?"
        assert scorer.scored_pieces[1].converted_value == "partial harmful"
        assert scorer.scored_pieces[1].response_error == "none"


# ── unreadable evidence interaction tests ────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
class TestUnreadableEvidenceWithBlockedContent:
    async def test_blocked_content_disabled_reports_neutral_verdict(self):
        scorer = _BlockedContentScorer()
        scorer.should_score_blocked_content = False
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert scorer.scored_pieces == []

    async def test_partial_content_behind_a_block_is_scored(self):
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "true"

    async def test_block_without_partial_content_reports_neutral_verdict(self):
        scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece()])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert scorer.scored_pieces == []

    async def test_error_type_without_response_error_flag_is_undetermined(self):
        scorer = _BlockedContentScorer()
        msg = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="transport failed",
                    original_value_data_type="error",
                    converted_value_data_type="error",
                    response_error="none",
                )
            ]
        )

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scores[0].status == ScoreStatus.UNDETERMINED
        assert scorer.scored_pieces == []

    @pytest.mark.parametrize(
        "validator",
        [
            SelectiveValidator(enforce_all_pieces_valid=True),
            SelectiveValidator(raise_on_no_valid_pieces=True),
        ],
    )
    async def test_structured_refusal_is_scored_as_text(self, validator: ScorerPromptValidator):
        scorer = _BlockedContentScorer(validator=validator)
        refusal = "I cannot assist with that request."
        piece = _make_blocked_piece(structured_refusal=refusal)
        msg = Message(message_pieces=[piece])

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert scorer.scored_pieces[0].id == piece.id
        assert scorer.scored_pieces[0].converted_value == refusal
        assert scorer.scored_pieces[0].converted_value_data_type == "text"
        assert scorer.scored_pieces[0].response_error == "blocked"

    async def test_readable_piece_beside_a_runtime_error_is_scored(self):
        scorer = _BlockedContentScorer()
        msg = Message(
            message_pieces=[
                _make_blocked_piece(
                    partial_content="Partial content",
                    structured_refusal="I cannot assist.",
                ),
                MessagePiece(
                    role="assistant",
                    original_value="transport failed",
                    original_value_data_type="error",
                    converted_value_data_type="error",
                    conversation_id="test-convo",
                    response_error="processing",
                ),
            ]
        )

        scores = await scorer.score_async(scorable=MessageScorable.from_message(store_message(msg)))

        assert len(scores) == 1
        assert [piece.converted_value for piece in scorer.scored_pieces] == ["Partial content"]


# ── score_response_async passthrough tests ───────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
class TestScoreResponseAsyncBlockedContent:
    async def test_score_response_async_scores_partial_content(self):
        obj_scorer = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        result = await MessageScorer.score_response_async(
            response=store_message(msg),
            objective_scorer=obj_scorer,
            objective="test",
        )

        assert len(result["objective_scores"]) == 1
        assert result["objective_scores"][0].score_value == "true"
        assert obj_scorer.scored_pieces[0].converted_value == "harmful text"

    async def test_score_response_async_disabled_does_not_substitute(self):
        obj_scorer = _BlockedContentScorer()
        obj_scorer.should_score_blocked_content = False
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        result = await MessageScorer.score_response_async(
            response=store_message(msg),
            objective_scorer=obj_scorer,
            objective="test",
        )

        assert result["objective_scores"][0].score_value == "false"
        assert len(obj_scorer.scored_pieces) == 0

    async def test_score_response_multiple_scorers_scores_partial_content(self):
        scorer1 = _BlockedContentScorer()
        scorer2 = _BlockedContentScorer()
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        scores = await MessageScorer.score_response_multiple_scorers_async(
            response=store_message(msg),
            scorers=[scorer1, scorer2],
            objective="test",
        )

        assert len(scores) == 2
        assert len(scorer1.scored_pieces) == 1
        assert len(scorer2.scored_pieces) == 1

    async def test_score_response_async_does_not_filter_generic_wrapper_content(self):
        leaf_scorer = _BlockedContentScorer()
        objective_scorer = TrueFalseInverterScorer(scorer=leaf_scorer)
        msg = Message(message_pieces=[_make_blocked_piece(partial_content="harmful text")])

        result = await MessageScorer.score_response_async(
            response=store_message(msg),
            objective_scorer=objective_scorer,
            objective="test",
        )

        assert len(result["objective_scores"]) == 1
        assert leaf_scorer.scored_pieces[0].converted_value == "harmful text"
