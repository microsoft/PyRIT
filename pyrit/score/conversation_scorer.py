# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.models import (
    ComponentIdentifier,
    Condition,
    ContentScorable,
    Message,
    MessagePiece,
    Scorable,
    Score,
    ScoringExpectation,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer


class ConversationScorer(MessageScorer, ABC):
    """
    Scorer that evaluates entire conversation history rather than individual messages.

    This scorer wraps a float-scale or true/false scorer that supports text
    ``ContentScorable`` evidence and evaluates the full conversation context.

    The ConversationScorer dynamically inherits from the same base class as the wrapped scorer,
    ensuring proper type compatibility.

    Note: This class cannot be instantiated directly. Use create_conversation_scorer() factory instead.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        enforce_all_pieces_valid=False,
    )

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Report the conditions matched by the wrapped scorer.

        Returns:
            frozenset[type[Condition]]: The matched condition types.
        """
        return self._get_wrapped_scorer().matched_conditions()

    def required_conditions(self) -> frozenset[type[Condition]]:
        """
        Report the conditions required by the wrapped scorer.

        Returns:
            frozenset[type[Condition]]: The required condition types.
        """
        return self._get_wrapped_scorer().required_conditions()

    def _build_scoring_message(self, *, message: Message) -> Message | None:
        """
        Keep the trigger that identifies the conversation to acquire.

        The trigger content is not sent to the child scorer. ``_score_prepared_message_async``
        replaces it with a text view of the full conversation. Overriding this hook keeps an
        unreadable trigger, because the conversation behind it is still there to read.

        Returns:
            Message | None: The trigger message, or None if it has no pieces.
        """
        return message if message.message_pieces else None

    def _reads_any_role(self, *, message: Message, anchor: Scorable | None) -> bool:
        """
        Defer role policy until the conversation locator has acquired its evidence.

        Returns:
            bool: True because the trigger identifies history; it is not the evidence itself.
        """
        return True

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Return ``[]`` when the conversation trigger does not yield applicable evidence.

        Returns:
            list[Score]: Always ``[]``.
        """
        return []

    def _validate_scoring_message(self, *, message: Message, objective: str | None) -> None:
        """Skip message validation because the trigger is only a conversation locator."""

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Scores the entire conversation history by concatenating all messages and passing to the wrapped scorer.

        The synthetic conversation Message is always built as ``text`` regardless of the
        triggering piece's data type or error state. Errors from individual turns are
        preserved within the rendered text (either as the partial content, or as the rendered
        error JSON when ``should_score_blocked_content`` is turned off). This ensures the wrapped
        scorer's text-only validator accepts the synthetic message and scores the full
        conversation, even when the triggering turn was blocked or errored; the wrapped
        scorer returns ``[]`` when the rendered conversation is not applicable.

        The wrapped scorer is invoked through its non-persisting nested path. The outer
        ``Scorer.score_async`` persists the returned scores exactly once, anchored to the
        trigger message.

        Args:
            message (Message): A message from the conversation to be scored.
                The conversation ID from the first message piece is used to retrieve the full conversation from memory.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: The wrapped scorer's completed or undetermined results, or ``[]``
                when no applicable conversation evidence or child score exists.

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
                if piece.api_role in ["user", "assistant", "tool"] and self._validator.is_role_supported(piece):
                    role_display = "Assistant (simulated)" if piece.is_simulated else piece.api_role.capitalize()
                    # For blocked pieces with partial content, use the partial content
                    # instead of the error JSON when should_score_blocked_content is enabled
                    if (
                        self.should_score_blocked_content
                        and piece.is_blocked()
                        and piece.prompt_metadata.get("partial_content")
                    ):
                        text = str(piece.prompt_metadata["partial_content"])
                    else:
                        text = piece.converted_value
                    conversation_text += f"{role_display}: {text}\n"

        if not conversation_text:
            return []

        wrapped_scorer = self._get_wrapped_scorer()
        scores = await wrapped_scorer._score_nested_async(
            scorable=ContentScorable(value=conversation_text),
            expectation=expectation,
        )
        trigger_piece = message.message_pieces[0]
        for score in scores:
            score.message_piece_id = trigger_piece.id or trigger_piece.original_prompt_id
            score.scorable = None
        return scores

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Not used - ConversationScorer operates at conversation level via
        ``_score_prepared_message_async``.

        This implementation satisfies the Scorer ABC requirement but is never called
        since ConversationScorer overrides ``_score_prepared_message_async``.
        """
        raise NotImplementedError("ConversationScorer does not support piecewise scoring")

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

    This factory dynamically creates a ConversationScorer class that inherits from the
    wrapped scorer's message-family base. The returned scorer is an instance of both
    ``ConversationScorer`` and the wrapped scorer's result family.

    Args:
        scorer (Scorer): The true/false or float-scale scorer to wrap for
            conversation-level evaluation. It must support text ``ContentScorable`` evidence.
        validator (ScorerPromptValidator | None): Optional validator override.
            If not provided, uses the conversation scorer's default text validator.

    Returns:
        Scorer: A ConversationScorer instance that is also an instance of the wrapped scorer's type.

    Raises:
        TypeError: If the dynamic scorer does not inherit from ``Scorer``.
        ValueError: If the scorer is outside the true/false and float-scale families.

    Example:
        >>> float_scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)
        >>> conversation_scorer = create_conversation_scorer(scorer=float_scorer)
        >>> isinstance(conversation_scorer, FloatScaleScorer)  # True
        >>> isinstance(conversation_scorer, ConversationScorer)  # True
    """
    # Determine the base class of the wrapped scorer
    scorer_base_class: type[Scorer] | None = None

    if isinstance(scorer, FloatScaleScorer):
        scorer_base_class = MessageFloatScaleScorer
    elif isinstance(scorer, TrueFalseScorer):
        scorer_base_class = MessageTrueFalseScorer
    else:
        raise ValueError(
            f"Unsupported scorer type: {type(scorer).__name__}. "
            "Scorer must belong to the true/false or float-scale family."
        )

    # Dynamically create a class that inherits from both ConversationScorer and the scorer's base class
    class DynamicConversationScorer(ConversationScorer, scorer_base_class):  # type: ignore[valid-type]  # type: ignore[ty:unsupported-base]
        """Dynamic ConversationScorer that inherits from both ConversationScorer and the wrapped scorer's base class."""

        _wrapped_scorer: Scorer

        def __init__(self) -> None:
            # Initialize with the validator and wrapped scorer
            MessageScorer.__init__(self, validator=validator or ConversationScorer._DEFAULT_VALIDATOR)
            self._wrapped_scorer = scorer

        def _get_wrapped_scorer(self) -> Scorer:
            """
            Return the wrapped scorer.

            Returns:
                Scorer: The scorer used for conversation-level evaluation.
            """
            return self._wrapped_scorer

        def _build_identifier(self) -> ComponentIdentifier:
            """
            Build the scorer evaluation identifier for this conversation scorer.

            Returns:
                ComponentIdentifier: The identifier for this scorer.

            Raises:
                TypeError: If identifier construction returns an unexpected type.
            """
            identifier = self._create_identifier(
                sub_scorers=[self._wrapped_scorer.get_identifier()],
            )
            if not isinstance(identifier, ComponentIdentifier):
                raise TypeError("Conversation scorer identifier must be a ComponentIdentifier")
            return identifier

    conversation_scorer = DynamicConversationScorer()
    if not isinstance(conversation_scorer, Scorer):
        raise TypeError("Dynamic conversation scorer must inherit from Scorer")
    return conversation_scorer
