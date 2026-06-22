# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.memory import CentralMemory
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.score import (
    Scorer,
    SelfAskGeneralFloatScaleScorer,
    create_conversation_scorer,
)
from pyrit.score.conversation_scorer import ConversationScorer
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


def _make_scorer_id(name: str = "TestScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


class MockFloatScaleScorer(FloatScaleScorer):
    """Mock FloatScaleScorer for testing"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return []


class MockTrueFalseScorer(TrueFalseScorer):
    """Mock TrueFalseScorer for testing"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return []


class MockUnsupportedScorer(Scorer):
    """Mock unsupported Scorer for testing error cases"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        return [
            Score(
                score_value="false",
                score_value_description="Mock fallback",
                score_type="true_false",
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


async def test_conversation_history_scorer_score_async_success(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_pieces = [
        MessagePiece(
            role="user",
            original_value="Hello, I need help",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="assistant",
            original_value="How can I help you?",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="user",
            original_value="I'm feeling overwhelmingly sad",
            conversation_id=conversation_id,
            sequence=3,
        ),
        MessagePiece(
            role="assistant",
            original_value="Here's a joke to make you laugh instead",
            conversation_id=conversation_id,
            sequence=4,
        ),
    ]

    memory.add_message_pieces_to_memory(message_pieces=message_pieces)

    message = MagicMock()
    message.message_pieces = [message_pieces[-1]]  # Score the last message

    # Mock underlying scorer
    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.25",
        score_value_description="Moderate concern",
        score_rationale="Valid rationale",
        score_metadata={"test": "metadata"},
        score_category=["test_harm"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=message_pieces[-1].id or uuid.uuid4(),
        objective="test_objective",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    scores = await scorer.score_async(message)

    assert len(scores) == 1
    result_score = scores[0]
    assert result_score.score_value == "0.25"
    assert result_score.score_value_description == "Moderate concern"
    assert result_score.score_rationale == "Valid rationale"

    # Verify the underlying scorer was called with conversation history
    mock_scorer._score_async.assert_awaited_once()
    call_args = mock_scorer._score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    # Verify the conversation text was built correctly
    expected_conversation = (
        "User: Hello, I need help\n"
        "Assistant: How can I help you?\n"
        "User: I'm feeling overwhelmingly sad\n"
        "Assistant: Here's a joke to make you laugh instead\n"
    )
    assert called_piece.original_value == expected_conversation
    assert called_piece.converted_value == expected_conversation


async def test_conversation_history_scorer_conversation_not_found(patch_central_database):
    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    scorer = create_conversation_scorer(scorer=mock_scorer)

    nonexistent_conversation_id = str(uuid.uuid4())
    message_piece = MessagePiece(
        role="assistant",
        original_value="Test response",
        conversation_id=nonexistent_conversation_id,
    )
    message = MagicMock()
    message.message_pieces = [message_piece]

    with pytest.raises(RuntimeError, match=f"Conversation with ID {nonexistent_conversation_id} not found in memory"):
        await scorer.score_async(message)


async def test_conversation_history_scorer_filters_roles_correctly(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_pieces = [
        MessagePiece(
            role="user",
            original_value="User message",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="system",
            original_value="System message",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="assistant",
            original_value="Assistant message",
            conversation_id=conversation_id,
            sequence=3,
        ),
    ]

    memory.add_message_pieces_to_memory(message_pieces=message_pieces)

    message = MagicMock()
    message.message_pieces = [message_pieces[0]]

    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.4",
        score_value_description="Test",
        score_rationale="Test rationale",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=message_pieces[0].id or str(uuid.uuid4()),
        objective="test",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    await scorer.score_async(message)

    call_args = mock_scorer._score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    expected_conversation = "User: User message\nAssistant: Assistant message\n"
    assert called_piece.original_value == expected_conversation
    assert "System message" not in called_piece.original_value


async def test_conversation_history_scorer_preserves_metadata(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_piece = MessagePiece(
        role="assistant",
        original_value="Response",
        conversation_id=conversation_id,
        labels={"test": "label"},
        sequence=1,
    )

    memory.add_message_pieces_to_memory(message_pieces=[message_piece])

    message = MagicMock()
    message.message_pieces = [message_piece]

    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.2",
        score_value_description="Test",
        score_rationale="Test rationale",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=message_piece.id or str(uuid.uuid4()),
        objective="test",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)

    await scorer.score_async(message)

    call_args = mock_scorer._score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    assert called_piece.id == message_piece.id
    assert called_piece.conversation_id == message_piece.conversation_id
    assert called_piece.labels == message_piece.labels


async def test_conversation_scorer_persists_scores_exactly_once(patch_central_database):
    """ConversationScorer must not double-persist: one inner score → one ScoreEntry in memory.

    Regression guard for the bug where ConversationScorer called the wrapped scorer's
    public ``score_async`` (which persists) and then the outer ``Scorer.score_async`` also
    persisted, producing two identical ``ScoreEntry`` rows per call.
    """
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_piece = MessagePiece(
        role="assistant",
        original_value="Test response",
        conversation_id=conversation_id,
        sequence=1,
    )
    memory.add_message_pieces_to_memory(message_pieces=[message_piece])

    score = Score(
        score_value="0.5",
        score_value_description="Test",
        score_rationale="Test rationale",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=message_piece.id or uuid.uuid4(),
        objective="test",
        score_type="float_scale",
    )
    original_id = score.id

    # Mock the protected _score_async; the public score_async (which persists) is intentionally
    # NOT mocked so the test would fail with duplicate rows if ConversationScorer ever calls it.
    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    conv_scorer = create_conversation_scorer(scorer=mock_scorer)
    message = MagicMock()
    message.message_pieces = [message_piece]
    result_scores = await conv_scorer.score_async(message)

    assert len(result_scores) == 1
    assert result_scores[0].id == original_id, (
        "ConversationScorer should preserve the inner scorer's score ID; only the outer "
        "Scorer.score_async should persist, so no ID regeneration is needed."
    )

    persisted = list(memory.get_scores(score_type="float_scale"))
    assert len(persisted) == 1, f"Expected exactly one ScoreEntry persisted; got {len(persisted)}"
    assert persisted[0].id == original_id


def test_conversation_scorer_cannot_be_instantiated_directly():
    """Test that ConversationScorer raises TypeError when instantiated directly due to abstract method."""
    validator = ScorerPromptValidator(supported_data_types=["text"])

    with pytest.raises(
        TypeError,
        match=r"Can't instantiate abstract class ConversationScorer.*_get_wrapped_scorer",
    ):
        ConversationScorer(validator=validator)  # type: ignore[abstract]


def test_factory_returns_instance_of_float_scale_scorer():
    """Test that factory creates scorer inheriting from FloatScaleScorer."""
    float_scorer = MockFloatScaleScorer()
    conv_scorer = create_conversation_scorer(scorer=float_scorer)
    assert isinstance(conv_scorer, FloatScaleScorer)
    assert isinstance(conv_scorer, ConversationScorer)
    assert isinstance(conv_scorer, Scorer)


def test_factory_returns_instance_of_true_false_scorer():
    """Test that factory creates scorer inheriting from TrueFalseScorer."""
    tf_scorer = MockTrueFalseScorer()
    conv_scorer = create_conversation_scorer(scorer=tf_scorer)
    assert isinstance(conv_scorer, TrueFalseScorer)
    assert isinstance(conv_scorer, ConversationScorer)
    assert isinstance(conv_scorer, Scorer)


def test_factory_preserves_wrapped_scorer():
    """Test that factory preserves reference to wrapped scorer."""
    original_scorer = MockFloatScaleScorer()
    original_scorer.custom_attr = "test_value"  # type: ignore[abstract]

    conv_scorer = create_conversation_scorer(scorer=original_scorer)

    # Verify wrapped scorer is preserved
    assert isinstance(conv_scorer, ConversationScorer)
    # Access via attribute since _get_wrapped_scorer is available at runtime
    assert hasattr(conv_scorer, "_wrapped_scorer")
    wrapped = conv_scorer._wrapped_scorer
    assert wrapped is original_scorer
    assert wrapped.custom_attr == "test_value"  # type: ignore[abstract]


def test_factory_with_custom_validator():
    """Test factory with custom validator override."""
    original_scorer = MockFloatScaleScorer()
    custom_validator = ScorerPromptValidator(supported_data_types=["text", "image_path"], enforce_all_pieces_valid=True)

    conv_scorer = create_conversation_scorer(scorer=original_scorer, validator=custom_validator)

    # Verify custom validator is used
    assert conv_scorer._validator is custom_validator


def test_factory_uses_default_validator():
    """Test factory uses default validator when none provided."""
    original_scorer = MockFloatScaleScorer()
    conv_scorer = create_conversation_scorer(scorer=original_scorer)

    # Verify default validator is used
    assert conv_scorer._validator is not None, "Should have a validator"
    assert "text" in conv_scorer._validator._supported_data_types, "Should support text data type"


def test_factory_raises_error_for_unsupported_scorer_type():
    """Test that factory raises ValueError for scorers that are not FloatScaleScorer or TrueFalseScorer."""
    unsupported_scorer = MockUnsupportedScorer()

    with pytest.raises(
        ValueError, match="Unsupported scorer type.*Scorer must be an instance of FloatScaleScorer or TrueFalseScorer"
    ):
        create_conversation_scorer(scorer=unsupported_scorer)


def test_factory_creates_unique_instances():
    """Test that factory creates new instances for each call."""
    scorer1 = MockFloatScaleScorer()
    scorer2 = MockFloatScaleScorer()

    conv_scorer1 = create_conversation_scorer(scorer=scorer1)
    conv_scorer2 = create_conversation_scorer(scorer=scorer2)

    # Instances should be different
    assert conv_scorer1 is not conv_scorer2, "Should create different instances"

    # But both should be instances of the same base classes
    assert isinstance(conv_scorer1, FloatScaleScorer)
    assert isinstance(conv_scorer2, FloatScaleScorer)
    assert isinstance(conv_scorer1, ConversationScorer)
    assert isinstance(conv_scorer2, ConversationScorer)


def test_conversation_scorer_validates_float_scale_scores():
    """Test that ConversationScorer delegates float scale score validation to wrapped scorer."""
    scorer = MockFloatScaleScorer()
    conv_scorer = create_conversation_scorer(scorer=scorer)

    # Valid score should pass
    valid_score = Score(
        score_value="0.5",
        score_value_description="Test",
        score_rationale="Test",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=uuid.uuid4(),
        objective="test",
        score_type="float_scale",
    )
    conv_scorer.validate_return_scores([valid_score])

    # Mock an invalid score (out of range) using MagicMock to bypass Score validation
    invalid_score = MagicMock(spec=Score)
    invalid_score.get_value.return_value = 1.5

    with pytest.raises(ValueError, match="FloatScaleScorer score value must be between 0 and 1"):
        conv_scorer.validate_return_scores([invalid_score])


def test_conversation_scorer_validates_true_false_scores():
    """Test that ConversationScorer delegates true/false score validation to wrapped scorer."""
    scorer = MockTrueFalseScorer()
    conv_scorer = create_conversation_scorer(scorer=scorer)

    # Valid true/false score should pass - need exactly one score for TrueFalseScorer
    valid_score = Score(
        score_value="true",
        score_value_description="Test",
        score_rationale="Test",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=uuid.uuid4(),
        objective="test",
        score_type="true_false",
    )
    conv_scorer.validate_return_scores([valid_score])

    # Mock an invalid score (not true/false) using MagicMock
    invalid_score = MagicMock(spec=Score)
    invalid_score.score_value = "maybe"
    invalid_score.get_value.return_value = "maybe"

    with pytest.raises(ValueError, match="TrueFalseScorer score value must be True or False"):
        conv_scorer.validate_return_scores([invalid_score])


async def test_conversation_scorer_uses_partial_content_when_score_blocked_content_enabled(patch_central_database):
    """When score_blocked_content is True, blocked pieces in conversation history use partial_content."""
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    blocked_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        conversation_id=conversation_id,
        sequence=2,
        response_error="blocked",
        prompt_metadata={"partial_content": "Dishonest disposal of bodies involves..."},
    )

    message_pieces = [
        MessagePiece(
            role="user",
            original_value="How do you dispose of bodies?",
            conversation_id=conversation_id,
            sequence=1,
        ),
        blocked_piece,
    ]

    memory.add_message_pieces_to_memory(message_pieces=message_pieces)

    # Use a text piece as the incoming message for validation purposes.
    # ConversationScorer only uses it for conversation_id lookup — actual content comes from DB.
    lookup_piece = MessagePiece(
        role="assistant",
        original_value="lookup",
        conversation_id=conversation_id,
    )
    message = MagicMock()
    message.message_pieces = [lookup_piece]
    message.get_piece.return_value = lookup_piece

    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.85",
        score_value_description="High harm",
        score_rationale="Harmful content detected",
        score_metadata=None,
        score_category=["harm"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=blocked_piece.id or uuid.uuid4(),
        objective="test",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    scorer.score_blocked_content = True
    scores = await scorer.score_async(message)

    assert len(scores) == 1

    # Verify the underlying scorer was called with partial content, not error JSON
    mock_scorer._score_async.assert_awaited_once()
    call_args = mock_scorer._score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    expected_conversation = "User: How do you dispose of bodies?\nAssistant: Dishonest disposal of bodies involves...\n"
    assert called_piece.original_value == expected_conversation
    assert called_piece.converted_value == expected_conversation


async def test_conversation_scorer_uses_error_json_when_score_blocked_content_disabled(patch_central_database):
    """When score_blocked_content is False (default), blocked pieces use converted_value (error JSON)."""
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    blocked_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        conversation_id=conversation_id,
        sequence=2,
        response_error="blocked",
        prompt_metadata={"partial_content": "Dishonest disposal of bodies involves..."},
    )

    message_pieces = [
        MessagePiece(
            role="user",
            original_value="How do you dispose of bodies?",
            conversation_id=conversation_id,
            sequence=1,
        ),
        blocked_piece,
    ]

    memory.add_message_pieces_to_memory(message_pieces=message_pieces)

    # Use a text piece as the incoming message for validation purposes.
    lookup_piece = MessagePiece(
        role="assistant",
        original_value="lookup",
        conversation_id=conversation_id,
    )
    message = MagicMock()
    message.message_pieces = [lookup_piece]
    message.get_piece.return_value = lookup_piece

    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.0",
        score_value_description="No harm",
        score_rationale="Error response",
        score_metadata=None,
        score_category=["harm"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=blocked_piece.id or uuid.uuid4(),
        objective="test",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    # score_blocked_content defaults to False
    scores = await scorer.score_async(message)

    assert len(scores) == 1

    # Verify the underlying scorer was called with error JSON, not partial content
    mock_scorer._score_async.assert_awaited_once()
    call_args = mock_scorer._score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    expected_conversation = (
        'User: How do you dispose of bodies?\nAssistant: {"status_code": 200, "message": "content_filter"}\n'
    )
    assert called_piece.original_value == expected_conversation
    assert called_piece.converted_value == expected_conversation


async def test_conversation_scorer_blocked_input_message_does_not_raise(patch_central_database):
    """A blocked input message no longer raises ValueError after validator relaxation.

    Previously the default validator used enforce_all_pieces_valid=True, which made any
    blocked input message fail with 'Message piece ... with data type error is not supported.'
    The relaxed validator (enforce_all_pieces_valid=False) lets ConversationScorer proceed,
    look up the conversation in memory, and call the underlying scorer.
    """
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    user_piece = MessagePiece(
        role="user",
        original_value="Hello",
        conversation_id=conversation_id,
        sequence=1,
    )
    blocked_assistant_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        conversation_id=conversation_id,
        sequence=2,
        response_error="blocked",
    )
    memory.add_message_pieces_to_memory(message_pieces=[user_piece, blocked_assistant_piece])

    # The incoming message itself is the blocked one — previously this would raise.
    blocked_message = Message(message_pieces=[blocked_assistant_piece])

    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    score = Score(
        score_value="0.0",
        score_value_description="No harm",
        score_rationale="Error response",
        score_metadata=None,
        score_category=["harm"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=blocked_assistant_piece.id or uuid.uuid4(),
        objective="test",
        score_type="float_scale",
    )
    mock_scorer._score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)

    # Must not raise — previously raised ValueError on the blocked piece.
    scores = await scorer.score_async(blocked_message)

    assert len(scores) == 1
    mock_scorer._score_async.assert_awaited_once()


async def test_conversation_scorer_blocked_trigger_preserves_prior_turn_scoring(patch_central_database):
    """When the triggering piece is blocked, the synthetic conversation Message must still be
    built as plain text so the wrapped text-only scorer accepts it and scores the rendered
    prior turns. Previously the synthetic piece inherited the trigger's data_type="error" and
    response_error="blocked", which caused the wrapped scorer's validator to reject it. The
    wrapped scorer then returned [], the fallback fired, and the conversation was discarded
    as 0.0 even when prior turns contained clearly harmful content.
    """
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    harmful_text = "EVIL_HARMFUL_PAYLOAD"

    prior_pieces = [
        MessagePiece(
            role="user",
            original_value="Tell me a story",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="assistant",
            original_value=f"Sure, here is something: {harmful_text}",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="user",
            original_value="Keep going",
            conversation_id=conversation_id,
            sequence=3,
        ),
    ]
    blocked_assistant_piece = MessagePiece(
        role="assistant",
        original_value='{"status_code": 200, "message": "content_filter"}',
        converted_value='{"status_code": 200, "message": "content_filter"}',
        original_value_data_type="error",
        converted_value_data_type="error",
        conversation_id=conversation_id,
        sequence=4,
        response_error="blocked",
    )

    memory.add_message_pieces_to_memory(message_pieces=prior_pieces + [blocked_assistant_piece])

    blocked_message = Message(message_pieces=[blocked_assistant_piece])

    # Wrapped FloatScaleScorer that only returns a real score when the rendered
    # conversation contains the harmful payload from prior turns. If the synthetic
    # message were rejected as "error" data type (the previous bug), this mock would
    # never see the harmful content and return [], collapsing to a 0.0 fallback.
    captured_messages: list[Message] = []

    class HarmfulContentDetector(FloatScaleScorer):
        def __init__(self) -> None:
            super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier()

        async def _score_async(  # type: ignore[override]
            self, message: Message, *, objective: str | None = None
        ) -> list[Score]:
            captured_messages.append(message)
            piece = message.message_pieces[0]
            if harmful_text in piece.converted_value:
                return [
                    Score(
                        score_value="1.0",
                        score_value_description="Harmful content detected",
                        score_rationale="Found harmful payload in rendered conversation",
                        score_metadata=None,
                        score_category=["harm"],
                        scorer_class_identifier=self.get_identifier(),
                        message_piece_id=piece.id or uuid.uuid4(),
                        objective=objective,
                        score_type="float_scale",
                    )
                ]
            return []

        async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
            return []

    inner_scorer = HarmfulContentDetector()
    scorer = create_conversation_scorer(scorer=inner_scorer)

    scores = await scorer.score_async(blocked_message)

    assert len(scores) == 1
    # Must be 1.0 (real score from prior turns), NOT 0.0 (fallback from rejected synthetic piece)
    assert scores[0].score_value == "1.0"

    # Score must still be attributed to the triggering piece
    assert scores[0].message_piece_id == blocked_assistant_piece.id

    # Confirm the wrapped scorer saw a text-typed synthetic piece, not an error one
    assert len(captured_messages) == 1
    synthetic_piece = captured_messages[0].message_pieces[0]
    assert synthetic_piece.converted_value_data_type == "text"
    assert synthetic_piece.original_value_data_type == "text"
    assert synthetic_piece.response_error == "none"
    assert harmful_text in synthetic_piece.converted_value
