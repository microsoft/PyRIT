# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece, Score
from pyrit.score import (
    Scorer,
    SelfAskGeneralFloatScaleScorer,
    create_conversation_scorer,
)
from pyrit.score.conversation_scorer import ConversationScorer
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


def _make_scorer_id(name: str = "TestScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test_module",
        class_description="",
        identifier_type="instance",
    )


class MockFloatScaleScorer(FloatScaleScorer):
    """Mock FloatScaleScorer for testing"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> None:
        self._set_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []


class MockTrueFalseScorer(TrueFalseScorer):
    """Mock TrueFalseScorer for testing"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> None:
        self._set_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []


class MockUnsupportedScorer(Scorer):
    """Mock unsupported Scorer for testing error cases"""

    def __init__(self):
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))

    def _build_identifier(self) -> None:
        self._set_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass

    def get_scorer_metrics(self):
        return None


@pytest.mark.asyncio
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
    mock_scorer.score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    scores = await scorer.score_async(message)

    assert len(scores) == 1
    result_score = scores[0]
    assert result_score.score_value == "0.25"
    assert result_score.score_value_description == "Moderate concern"
    assert result_score.score_rationale == "Valid rationale"

    # Verify the underlying scorer was called with conversation history
    mock_scorer.score_async.assert_awaited_once()
    call_args = mock_scorer.score_async.call_args
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
    mock_scorer.score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)
    await scorer.score_async(message)

    call_args = mock_scorer.score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    expected_conversation = "User: User message\nAssistant: Assistant message\n"
    assert called_piece.original_value == expected_conversation
    assert "System message" not in called_piece.original_value


@pytest.mark.asyncio
async def test_conversation_history_scorer_preserves_metadata(patch_central_database):
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_piece = MessagePiece(
        role="assistant",
        original_value="Response",
        conversation_id=conversation_id,
        labels={"test": "label"},
        prompt_target_identifier={"target": "test"},
        attack_identifier={"attack": "test"},
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
    mock_scorer.score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    scorer = create_conversation_scorer(scorer=mock_scorer)

    await scorer.score_async(message)

    call_args = mock_scorer.score_async.call_args
    called_message = call_args.kwargs["message"]
    called_piece = called_message.message_pieces[0]

    assert called_piece.id == message_piece.id
    assert called_piece.conversation_id == message_piece.conversation_id
    assert called_piece.labels == message_piece.labels
    assert called_piece.prompt_target_identifier == message_piece.prompt_target_identifier
    assert called_piece.attack_identifier == message_piece.attack_identifier


@pytest.mark.asyncio
async def test_conversation_scorer_regenerates_score_ids_to_prevent_collisions(patch_central_database):
    """Test that ConversationScorer regenerates score IDs to prevent database UNIQUE constraint violations."""
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())

    message_piece = MessagePiece(
        role="assistant",
        original_value="Test response",
        conversation_id=conversation_id,
        sequence=1,
    )
    memory.add_message_pieces_to_memory(message_pieces=[message_piece])

    # Create a score and capture its original ID
    score = Score(
        score_value="0.5",
        score_value_description="Test",
        score_rationale="Test rationale",
        score_metadata={},
        score_category=["test"],
        scorer_class_identifier=_make_scorer_id(),
        message_piece_id=message_piece.id,
        objective="test",
        score_type="float_scale",
    )
    original_id = score.id

    # Mock scorer returns the score (which will be mutated by ConversationScorer)
    mock_scorer = MagicMock(spec=SelfAskGeneralFloatScaleScorer)
    mock_scorer._validator = ScorerPromptValidator(supported_data_types=["text"])
    mock_scorer.score_async = AsyncMock(return_value=[score])
    mock_scorer.validate_return_scores = MagicMock()

    # Create conversation scorer and score the message
    conv_scorer = create_conversation_scorer(scorer=mock_scorer)
    message = MagicMock()
    message.message_pieces = [message_piece]
    result_scores = await conv_scorer.score_async(message)

    # Verify that ConversationScorer regenerated the ID
    assert len(result_scores) == 1
    assert result_scores[0].id != original_id, "ConversationScorer should regenerate score IDs to prevent collisions"
    assert isinstance(result_scores[0].id, uuid.UUID), "Regenerated ID should be a valid UUID"


def test_conversation_scorer_cannot_be_instantiated_directly():
    """Test that ConversationScorer raises TypeError when instantiated directly due to abstract method."""
    validator = ScorerPromptValidator(supported_data_types=["text"])

    with pytest.raises(
        TypeError,
        match=r"Can't instantiate abstract class ConversationScorer.*_get_wrapped_scorer",
    ):
        ConversationScorer(validator=validator)


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
    original_scorer.custom_attr = "test_value"  # type: ignore

    conv_scorer = create_conversation_scorer(scorer=original_scorer)

    # Verify wrapped scorer is preserved
    assert isinstance(conv_scorer, ConversationScorer)
    # Access via attribute since _get_wrapped_scorer is available at runtime
    assert hasattr(conv_scorer, "_wrapped_scorer")
    wrapped = getattr(conv_scorer, "_wrapped_scorer")
    assert wrapped is original_scorer
    assert wrapped.custom_attr == "test_value"  # type: ignore


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
