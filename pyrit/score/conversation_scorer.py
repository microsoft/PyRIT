# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, cast

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from uuid import UUID


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

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        enforce_all_pieces_valid=False,
    )

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Scores the entire conversation history by concatenating all messages and passing to the wrapped scorer.

        The synthetic conversation Message is always built as ``text`` regardless of the
        triggering piece's data type or error state. Errors from individual turns are
        preserved within the rendered text (either as the rendered error JSON or, with
        ``score_blocked_content`` enabled, as the partial content). This ensures the wrapped
        scorer's text-only validator accepts the synthetic message and scores the full
        conversation, even when the triggering turn was blocked or errored; the wrapped
        scorer's fallback only fires when the rendered conversation is genuinely unscoreable.

        The wrapped scorer is invoked via its protected ``_score_async`` so it does not
        persist its own copy of the scores. The outer ``Scorer.score_async`` that invoked
        this method persists the returned scores exactly once, keyed to the original
        ``message_piece_id``.

        Args:
            message (Message): A message from the conversation to be scored.
                The conversation ID from the first message piece is used to retrieve the full conversation from memory.
            objective (str | None): Optional objective to evaluate against.

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
        conversation = (
            self._memory.get_conversation_messages(conversation_id=conversation_id) if conversation_id else []
        )

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
                    # For blocked pieces with partial content, use the partial content
                    # instead of the error JSON when score_blocked_content is enabled
                    if (
                        self.score_blocked_content
                        and piece.is_blocked()
                        and piece.prompt_metadata.get("partial_content")
                    ):
                        text = str(piece.prompt_metadata["partial_content"])
                    else:
                        text = piece.converted_value
                    conversation_text += f"{role_display}: {text}\n"

        # Create a new message with the concatenated conversation text
        # Preserve the original message piece metadata
        original_piece = message.message_pieces[0]
        conversation_message = Message(
            message_pieces=[
                MessagePiece(
                    role=original_piece.role,
                    original_value=conversation_text,
                    converted_value=conversation_text,
                    id=original_piece.id,
                    conversation_id=original_piece.conversation_id,
                    original_value_data_type="text",
                    converted_value_data_type="text",
                    response_error="none",
                    original_prompt_id=(
                        cast("UUID", original_piece.original_prompt_id)
                        if isinstance(original_piece.original_prompt_id, str)
                        else original_piece.original_prompt_id
                    ),
                    timestamp=original_piece.timestamp,
                )
            ]
        )

        wrapped_scorer = self._get_wrapped_scorer()
        # Call the wrapped scorer's protected ``_score_async`` rather than the public
        # ``score_async`` so the wrapped scorer does not persist its own copy of the
        # scores.
        return await wrapped_scorer._score_async(message=conversation_message, objective=objective)

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
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
    validator: ScorerPromptValidator | None = None,
) -> Scorer:
    """
    Create a ConversationScorer that inherits from the same type as the wrapped scorer.

    This factory dynamically creates a ConversationScorer class that inherits from the wrapped scorer's
    base class (FloatScaleScorer or TrueFalseScorer), ensuring the returned scorer is an instance
    of both ConversationScorer and the wrapped scorer's type.

    Args:
        scorer (Scorer): The scorer to wrap for conversation-level evaluation.
            Must be an instance of FloatScaleScorer or TrueFalseScorer.
        validator (ScorerPromptValidator | None): Optional validator override.
            If not provided, uses the wrapped scorer's validator.

    Returns:
        Scorer: A ConversationScorer instance that is also an instance of the wrapped scorer's type.

    Raises:
        ValueError: If the scorer is not an instance of FloatScaleScorer or TrueFalseScorer.

    Example:
        >>> float_scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)
        >>> conversation_scorer = create_conversation_scorer(scorer=float_scorer)
        >>> isinstance(conversation_scorer, FloatScaleScorer)  # True
        >>> isinstance(conversation_scorer, ConversationScorer)  # True
    """
    # Determine the base class of the wrapped scorer
    scorer_base_class: type[Scorer] | None = None

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
    class DynamicConversationScorer(ConversationScorer, scorer_base_class):  # type: ignore[valid-type]  # type: ignore[ty:unsupported-base]
        """Dynamic ConversationScorer that inherits from both ConversationScorer and the wrapped scorer's base class."""

        def __init__(self) -> None:
            # Initialize with the validator and wrapped scorer
            Scorer.__init__(self, validator=validator or ConversationScorer._DEFAULT_VALIDATOR)
            self._wrapped_scorer = scorer

        def _get_wrapped_scorer(self) -> Scorer:
            """Return the wrapped scorer."""
            return self._wrapped_scorer

        def _build_identifier(self) -> ComponentIdentifier:
            """
            Build the scorer evaluation identifier for this conversation scorer.

            Returns:
                ComponentIdentifier: The identifier for this scorer.
            """
            return self._create_identifier(
                sub_scorers=[self._wrapped_scorer.get_identifier()],
            )

    return DynamicConversationScorer()
