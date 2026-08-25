# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, cast

from pyrit.models import ComponentIdentifier, Condition, Message, MessagePiece, Score, ScoringExpectation
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from uuid import UUID


class ConversationScoringMode(str, Enum):
    """Supported methods for evaluating a stored conversation."""

    CONCATENATED = "concatenated"
    PER_TURN = "per_turn"


def _get_max_scores_by_category(scores: list[Score]) -> list[Score]:
    scores_by_category: dict[str, list[Score]] = {}
    for score in scores:
        primary_category = (score.score_category or [""])[0]
        scores_by_category.setdefault(primary_category, []).append(score)
    return [
        max(category_scores, key=lambda score: float(score.get_value()))
        for _, category_scores in sorted(scores_by_category.items())
    ]


class ConversationScorer(MessageScorer, ABC):
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
        preserved within the rendered text (either as the rendered error JSON or, with
        ``score_blocked_content`` enabled, as the partial content). This ensures the wrapped
        scorer's text-only validator accepts the synthetic message and scores the full
        conversation, even when the triggering turn was blocked or errored; the wrapped
        scorer's fallback only fires when the rendered conversation is genuinely unscoreable.

        The wrapped scorer is invoked via its protected prepared-message hook so it does not
        persist its own copy of the scores. The outer ``Scorer.score_async`` that invoked
        this method persists the returned scores exactly once, keyed to the original
        ``message_piece_id``.

        Args:
            message (Message): A message from the conversation to be scored.
                The conversation ID from the first message piece is used to retrieve the full conversation from memory.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: List of Score objects from the underlying scorer

        Raises:
            ValueError: If conversation with the given ID is not found in memory.
        """
        if not message.message_pieces:
            return []

        objective = expectation.objective if expectation else None

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
        # Call the wrapped scorer's protected prepared-message hook rather than the public
        # ``score_async`` so the wrapped scorer does not persist its own copy of the
        # scores.
        wrapped_scorer._validate_expectation(
            expectation=expectation,
            allow_unmatched_conditions=True,
        )
        return await wrapped_scorer._score_prepared_message_async(
            message=conversation_message,
            expectation=expectation,
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Not used - ConversationScorer operates at conversation level via
        ``_score_prepared_message_async``.

        This implementation satisfies the Scorer ABC requirement but is never called
        since ConversationScorer overrides ``_score_prepared_message_async``.
        """
        raise NotImplementedError("ConversationScorer does not support piecewise scoring")

    @abstractmethod
    def _get_wrapped_scorer(self) -> MessageScorer:
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
    mode: ConversationScoringMode = ConversationScoringMode.CONCATENATED,
    validator: ScorerPromptValidator | None = None,
) -> Scorer:
    """
    Create a conversation scorer using the selected scoring mode.

    The default concatenated mode renders the full stored conversation as one text message
    and scores it once. Per-turn mode scores every stored turn with the same API role as the
    triggering message, then takes the maximum float score in each category.

    This factory dynamically creates a ConversationScorer class that inherits from the wrapped scorer's
    base class (FloatScaleScorer or TrueFalseScorer), ensuring the returned scorer is an instance
    of both ConversationScorer and the wrapped scorer's type.

    Args:
        scorer (Scorer): The scorer to wrap for conversation-level evaluation.
            Must be an instance of FloatScaleScorer or TrueFalseScorer.
        mode (ConversationScoringMode): Conversation scoring behavior. Defaults to concatenated.
        validator (ScorerPromptValidator | None): Optional validator override.
            If not provided, uses the wrapped scorer's validator.

    Returns:
        Scorer: A ConversationScorer instance that is also an instance of the wrapped scorer's type.

    Raises:
        TypeError: If the dynamic scorer does not inherit from ``Scorer``.
        ValueError: If the scorer is incompatible with the selected mode.

    Example:
        >>> float_scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)
        >>> conversation_scorer = create_conversation_scorer(scorer=float_scorer)
        >>> isinstance(conversation_scorer, FloatScaleScorer)  # True
        >>> isinstance(conversation_scorer, ConversationScorer)  # True
    """
    if mode is ConversationScoringMode.CONCATENATED:
        return _create_concatenated_conversation_scorer(scorer=scorer, validator=validator)
    if mode is ConversationScoringMode.PER_TURN:
        return _create_per_turn_conversation_scorer(scorer=scorer, validator=validator)
    raise ValueError(f"Unsupported conversation scoring mode: {mode!r}.")


def _create_concatenated_conversation_scorer(
    *,
    scorer: Scorer,
    validator: ScorerPromptValidator | None,
) -> Scorer:
    """
    Create the original full-transcript conversation scorer.

    Returns:
        Scorer: Dynamic conversation scorer matching the wrapped scorer family.

    Raises:
        TypeError: If the dynamic scorer has an invalid type or identifier.
        ValueError: If the wrapped scorer is not a float-scale or true/false scorer.
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

    # Both branches above narrow to a MessageScorer, which supplies the prepared-message hook.
    wrapped_scorer: MessageScorer = scorer

    # Dynamically create a class that inherits from both ConversationScorer and the scorer's base class
    class DynamicConversationScorer(ConversationScorer, scorer_base_class):  # type: ignore[valid-type]  # type: ignore[ty:unsupported-base]
        """Dynamic ConversationScorer that inherits from both ConversationScorer and the wrapped scorer's base class."""

        def __init__(self) -> None:
            # Initialize with the validator and wrapped scorer
            MessageScorer.__init__(self, validator=validator or ConversationScorer._DEFAULT_VALIDATOR)
            self._wrapped_scorer: MessageScorer = wrapped_scorer

        def _get_wrapped_scorer(self) -> MessageScorer:
            """
            Return the wrapped scorer.

            Returns:
                MessageScorer: The scorer used for conversation-level evaluation.

            Raises:
                TypeError: If the stored wrapped scorer is not a ``MessageScorer``.
            """
            wrapped_scorer = self._wrapped_scorer
            if not isinstance(wrapped_scorer, MessageScorer):
                raise TypeError("Wrapped conversation scorer must inherit from MessageScorer")
            return wrapped_scorer

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


def _create_per_turn_conversation_scorer(
    *,
    scorer: Scorer,
    validator: ScorerPromptValidator | None,
) -> Scorer:
    """
    Create a float scorer that takes the category-wise maximum across same-role turns.

    Returns:
        Scorer: Dynamic per-turn float-scale conversation scorer.

    Raises:
        TypeError: If the dynamic scorer has an invalid type.
        ValueError: If the wrapped scorer is not a float-scale scorer.
    """
    if not isinstance(scorer, FloatScaleScorer):
        raise ValueError("Per-turn conversation scoring currently requires a FloatScaleScorer.")

    wrapped_scorer: FloatScaleScorer = scorer

    class DynamicPerTurnConversationScorer(ConversationScorer, FloatScaleScorer):
        """Score each same-role turn and aggregate the maximum value by category."""

        def __init__(self) -> None:
            MessageScorer.__init__(self, validator=validator or ConversationScorer._DEFAULT_VALIDATOR)
            self._wrapped_scorer = wrapped_scorer

        @property
        def score_blocked_content(self) -> bool:
            return self._wrapped_scorer.score_blocked_content

        @score_blocked_content.setter
        def score_blocked_content(self, value: bool) -> None:
            self._wrapped_scorer.score_blocked_content = value

        @property
        def raise_if_scorer_blocks(self) -> bool:
            return self._wrapped_scorer.raise_if_scorer_blocks

        @raise_if_scorer_blocks.setter
        def raise_if_scorer_blocks(self, value: bool) -> None:
            self._wrapped_scorer.raise_if_scorer_blocks = value

        def _get_wrapped_scorer(self) -> MessageScorer:
            return self._wrapped_scorer

        def _build_identifier(self) -> ComponentIdentifier:
            return self._create_identifier(
                params={"conversation_scoring_mode": ConversationScoringMode.PER_TURN.value},
                sub_scorers=[self._wrapped_scorer.get_identifier()],
            )

        async def _score_prepared_message_async(
            self,
            *,
            message: Message,
            expectation: ScoringExpectation | None,
        ) -> list[Score]:
            trigger_piece = message.get_piece()
            conversation_id = trigger_piece.conversation_id
            conversation = (
                await asyncio.to_thread(
                    self._memory.get_conversation_messages,
                    conversation_id=conversation_id,
                )
                if conversation_id
                else []
            )
            if not conversation:
                raise ValueError(f"Conversation with ID {conversation_id} not found in memory.")

            selected_messages = [
                candidate for candidate in conversation if candidate.get_piece().api_role == trigger_piece.api_role
            ]
            score_batches = await self._wrapped_scorer._score_nested_messages_async(
                messages=selected_messages,
                expectation=expectation,
                context_messages=conversation,
            )
            child_scores = [score for batch in score_batches for score in batch]
            winning_scores = _get_max_scores_by_category(child_scores)
            objective = expectation.objective if expectation else None
            aggregated_scores: list[Score] = []
            for winner in winning_scores:
                metadata = {
                    **(winner.score_metadata or {}),
                    "conversation_scoring_mode": ConversationScoringMode.PER_TURN.value,
                    "scored_turn_count": len(selected_messages),
                }
                if winner.message_piece_id is not None:
                    metadata["winning_message_piece_id"] = str(winner.message_piece_id)
                aggregated_scores.append(
                    Score(
                        score_value=str(winner.get_value()),
                        score_value_description=winner.score_value_description,
                        score_type="float_scale",
                        score_category=winner.score_category,
                        score_metadata=metadata,
                        score_rationale=winner.score_rationale,
                        scorer_class_identifier=self.get_identifier(),
                        message_piece_id=trigger_piece.id,
                        objective=objective,
                    )
                )
            return aggregated_scores

    conversation_scorer = DynamicPerTurnConversationScorer()
    if not isinstance(conversation_scorer, Scorer):
        raise TypeError("Dynamic per-turn conversation scorer must inherit from Scorer")
    return conversation_scorer
