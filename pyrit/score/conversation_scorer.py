# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from abc import ABC, abstractmethod
from typing import Optional, Type, cast
from uuid import UUID

from pyrit.models import Message, MessagePiece, Score
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class ConversationScorer(Scorer, ABC):
    """
    Scorer that evaluates entire conversation history rather than individual messages.

    This scorer wraps another scorer (FloatScaleScorer or TrueFalseScorer) and evaluates
    the full conversation context. Useful for multi-turn conversations where context matters
    (e.g., psychosocial harms that emerge over time or persuasion/deception over many messages).

    The ConversationScorer dynamically inherits from the same base class as the wrapped scorer,
    ensuring proper type compatibility.

    Note: This class cannot be instantiated directly. Use create_conversation_scorer() factory instead.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        enforce_all_pieces_valid=True,
    )

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        """
        Scores the entire conversation history by concatenating all messages and passing to the wrapped scorer.

        Args:
            message (Message): A message from the conversation to be scored.
                The conversation ID from the first message piece is used to retrieve the full conversation from memory.
            objective (Optional[str]): Optional objective to evaluate against.

        Returns:
            list[Score]: List of Score objects from the underlying scorer

        Raises:
            ValueError: If conversation with the given ID is not found in memory.
        """
        if not message.message_pieces:
            return []

        # Get conversation ID from the first message piece
        conversation_id = message.message_pieces[0].conversation_id

        # Retrieve the full conversation from memory using the conversation_id
        conversation = self._memory.get_conversation(conversation_id=conversation_id)

        if not conversation:
            raise ValueError(f"Conversation with ID {conversation_id} not found in memory.")

        # Build the full conversation text
        conversation_text = ""

        # Goes through each message in the conversation and appends user/assistant messages only
        # Explicitly excludes system, tool, developer messages from being scored/included in conversation history
        # they are allowed in validation but not included in the scored conversation text
        for conv_message in conversation:
            for piece in conv_message.message_pieces:
                # Only include user and assistant messages in the conversation text
                if piece.api_role in ["user", "assistant", "tool"]:
                    role_display = "Assistant (simulated)" if piece.is_simulated else piece.api_role.capitalize()
                    conversation_text += f"{role_display}: {piece.converted_value}\n"

        # Create a new message with the concatenated conversation text
        # Preserve the original message piece metadata
        original_piece = message.message_pieces[0]
        conversation_message = Message(
            message_pieces=[
                MessagePiece(
                    role=original_piece.get_role_for_storage(),
                    original_value=conversation_text,
                    converted_value=conversation_text,
                    id=original_piece.id,
                    conversation_id=original_piece.conversation_id,
                    labels=original_piece.labels,
                    prompt_target_identifier=original_piece.prompt_target_identifier,
                    attack_identifier=original_piece.attack_identifier,
                    original_value_data_type=original_piece.original_value_data_type,
                    converted_value_data_type=original_piece.converted_value_data_type,
                    response_error=original_piece.response_error,
                    originator=original_piece.originator,
                    original_prompt_id=(
                        cast(UUID, original_piece.original_prompt_id)
                        if isinstance(original_piece.original_prompt_id, str)
                        else original_piece.original_prompt_id
                    ),
                    timestamp=original_piece.timestamp,
                )
            ]
        )

        wrapped_scorer = self._get_wrapped_scorer()
        scores = await wrapped_scorer.score_async(message=conversation_message, objective=objective)

        # Generate new IDs for the scores to avoid ID collisions when the wrapped scorer's
        # scores are already in the database
        for score in scores:
            score.id = uuid.uuid4()

        return scores

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Not used - ConversationScorer operates at conversation level via _score_async.

        This implementation satisfies the Scorer ABC requirement but is never called
        since ConversationScorer overrides _score_async.
        """
        raise NotImplementedError("ConversationScorer uses _score_async, not _score_piece_async")

    @abstractmethod
    def _get_wrapped_scorer(self) -> Scorer:
        """
        Abstract method to enforce that ConversationScorer cannot be instantiated directly.

        This must be implemented by the factory-created subclass.
        """
        pass

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate scores by delegating to the wrapped scorer's validation.

        Args:
            scores (list[Score]): The scores to validate.
        """
        wrapped_scorer = self._get_wrapped_scorer()
        wrapped_scorer.validate_return_scores(scores)


def create_conversation_scorer(
    *,
    scorer: Scorer,
    validator: Optional[ScorerPromptValidator] = None,
) -> Scorer:
    """
    Create a ConversationScorer that inherits from the same type as the wrapped scorer.

    This factory dynamically creates a ConversationScorer class that inherits from the wrapped scorer's
    base class (FloatScaleScorer or TrueFalseScorer), ensuring the returned scorer is an instance
    of both ConversationScorer and the wrapped scorer's type.

    Args:
        scorer (Scorer): The scorer to wrap for conversation-level evaluation.
            Must be an instance of FloatScaleScorer or TrueFalseScorer.
        validator (Optional[ScorerPromptValidator]): Optional validator override.
            If not provided, uses the wrapped scorer's validator.

    Returns:
        Scorer: A ConversationScorer instance that is also an instance of the wrapped scorer's type.

    Raises:
        ValueError: If the scorer is not an instance of FloatScaleScorer or TrueFalseScorer.

    Example:
        >>> float_scorer = SelfAskLikertScorer(chat_target=target, likert_scale=scale)
        >>> conversation_scorer = create_conversation_scorer(scorer=float_scorer)
        >>> isinstance(conversation_scorer, FloatScaleScorer)  # True
        >>> isinstance(conversation_scorer, ConversationScorer)  # True
    """
    # Determine the base class of the wrapped scorer
    scorer_base_class: Optional[Type[Scorer]] = None

    if isinstance(scorer, FloatScaleScorer):
        scorer_base_class = FloatScaleScorer
    elif isinstance(scorer, TrueFalseScorer):
        scorer_base_class = TrueFalseScorer
    else:
        raise ValueError(
            f"Unsupported scorer type: {type(scorer).__name__}. "
            f"Scorer must be an instance of FloatScaleScorer or TrueFalseScorer."
        )

    # Dynamically create a class that inherits from both ConversationScorer and the scorer's base class
    class DynamicConversationScorer(ConversationScorer, scorer_base_class):  # type: ignore
        """Dynamic ConversationScorer that inherits from both ConversationScorer and the wrapped scorer's base class."""

        def __init__(self) -> None:
            # Initialize with the validator and wrapped scorer
            Scorer.__init__(self, validator=validator or ConversationScorer._default_validator)
            self._wrapped_scorer = scorer

        def _get_wrapped_scorer(self) -> Scorer:
            """Return the wrapped scorer."""
            return self._wrapped_scorer

        def _build_identifier(self) -> None:
            """Build the scorer evaluation identifier for this conversation scorer."""
            self._set_identifier(
                sub_scorers=[self._wrapped_scorer],
            )

    return DynamicConversationScorer()
