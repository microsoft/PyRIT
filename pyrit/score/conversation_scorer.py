# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pyrit.exceptions import ScorerLLMResponseBlockedException
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    ContentScorable,
    ConversationObservationPayload,
    ConversationScorable,
    Message,
    MessagePiece,
    Scorable,
    Score,
    ScoringExpectation,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.observation.conversation_source import ConversationSource
from pyrit.score.observation.execution import _collect_observation, _ObservationEvidenceResolver
from pyrit.score.observation.observation_source import ObservationSource
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


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
    _source: ObservationSource[ConversationScorable]

    def get_chat_target(self) -> "PromptTarget | None":
        """
        Return the wrapped scorer's target.

        Returns:
            PromptTarget | None: The configured scoring target, if any.
        """
        return self._get_wrapped_scorer().get_chat_target()

    async def _score_message_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        infer_objective_from_request: bool,
        role_filter: ChatMessageRole | None,
        skip_on_error_result: bool,
    ) -> list[Score]:
        if isinstance(scorable, ConversationScorable):
            if infer_objective_from_request or role_filter is not None or skip_on_error_result:
                raise ValueError("Message-only scoring options cannot be used with a ConversationScorable.")
            expectation = self.prepare_expectation(expectation=expectation)
            scores = await self._score_conversation_async(scorable=scorable, expectation=expectation)
            self._stamp_scored_expectation(scores=scores, expectation=expectation)
            return scores
        return await super()._score_message_scorable_async(
            scorable=scorable,
            expectation=expectation,
            infer_objective_from_request=infer_objective_from_request,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
        )

    def _finalize_message_scores(
        self,
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
        expectation: ScoringExpectation | None,
    ) -> None:
        conversation_anchors = [score.scorable for score in scores]
        super()._finalize_message_scores(message=message, scores=scores, anchor=anchor, expectation=expectation)
        for score, conversation_anchor in zip(scores, conversation_anchors, strict=True):
            if isinstance(conversation_anchor, ConversationScorable):
                score.scorable = conversation_anchor

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """Return the scorer that evaluates the conversation text."""
        return (self._get_wrapped_scorer(),)

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
        scoring operation persists the results once, anchored to the conversation, with
        the real trigger-message link retained for compatibility.

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

        conversation_id = message.message_pieces[0].conversation_id
        if not conversation_id:
            raise ValueError(f"Conversation with ID {conversation_id} not found in memory.")
        scores = await self._score_conversation_async(
            scorable=ConversationScorable(conversation_id=conversation_id),
            expectation=expectation,
        )
        trigger_piece = message.message_pieces[0]
        for score in scores:
            score.message_piece_id = trigger_piece.id or trigger_piece.original_prompt_id
        return scores

    async def _score_conversation_async(
        self, *, scorable: ConversationScorable, expectation: ScoringExpectation | None
    ) -> list[Score]:
        observation = await self._source.acquire_async(scorable=scorable)
        if observation.scorable != scorable or not isinstance(observation.payload, ConversationObservationPayload):
            raise ValueError("Conversation source returned incompatible evidence or scope.")
        pieces = await asyncio.to_thread(
            _ObservationEvidenceResolver(memory=self._memory).resolve, observation=observation
        )
        if not isinstance(pieces, tuple):
            raise TypeError("Conversation evidence must resolve to an ordered tuple of message pieces.")
        text = self._render_conversation(pieces)
        if not text:
            return []
        _collect_observation(observation)
        child = self._get_wrapped_scorer()
        try:
            scores = await child._score_nested_async(
                scorable=ContentScorable(value=text),
                expectation=child._select_expectation(expectation=expectation),
            )
        except ScorerLLMResponseBlockedException as error:
            scores = [
                self._handle_blocked_judge_response(
                    error=error, objective=expectation.objective if expectation else None
                )
            ]
        for score in scores:
            score.scorable = scorable
            score.message_piece_id = None
            if observation.id not in score.observation_ids:
                score.observation_ids.append(observation.id)
        return scores

    def _render_conversation(self, pieces: tuple[MessagePiece, ...]) -> str:
        lines = []
        for piece in pieces:
            if piece.api_role not in ("user", "assistant", "tool") or not self._validator.is_role_supported(piece):
                continue
            role = "Assistant (simulated)" if piece.is_simulated else piece.api_role.capitalize()
            partial = piece.prompt_metadata.get("partial_content")
            text = (
                str(partial)
                if self.should_score_blocked_content and piece.is_blocked() and partial
                else piece.converted_value
            )
            lines.append(f"{role}: {text}\n")
        return "".join(lines)

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
    source: ObservationSource[ConversationScorable] | None = None,
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
        source (ObservationSource[ConversationScorable] | None): Whole-conversation acquisition source.

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
            self._source = source if source is not None else ConversationSource()

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
                params={
                    "rendering_version": 1,
                    "supported_roles": self._validator._supported_roles,
                    "should_score_blocked_content": self.should_score_blocked_content,
                },
                sub_scorers=[self._wrapped_scorer.get_identifier()],
                children={"source": self._source.get_identifier()},
            )
            if not isinstance(identifier, ComponentIdentifier):
                raise TypeError("Conversation scorer identifier must be a ComponentIdentifier")
            return identifier

    conversation_scorer = DynamicConversationScorer()
    if not isinstance(conversation_scorer, Scorer):
        raise TypeError("Dynamic conversation scorer must inherit from Scorer")
    return conversation_scorer
