# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pyrit.common.deprecation import print_deprecation_message
from pyrit.exceptions import PyritException, ScorerLLMResponseBlockedException
from pyrit.models import (
    ChatMessageRole,
    Condition,
    MatchesObjective,
    Message,
    MessagePiece,
    PromptResponseError,
    Scorable,
    Score,
    ScoringExpectation,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.scorer import LEGACY_SCORE_ASYNC_REMOVED_IN, Scorer

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class MessageScoringOptions:
    """Message-only scoring policy that is not part of evidence identity."""

    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


def extract_objective_from_previous_turn(*, message: Message, memory: MemoryInterface) -> str:
    """
    Read the text of the turn before an assistant message and use it as the objective.

    .. deprecated::
        This conflates scoring with building an expectation. What to look for belongs to
        the caller that builds the ``ScoringExpectation``, not to the scorer. It exists only
        to support the deprecated ``infer_objective_from_request`` parameter, and both are
        removed in the next major release. Resolve the objective at the call site and pass
        it on the expectation instead.

    Args:
        message (Message): The assistant message whose previous turn supplies the objective.
        memory (MemoryInterface): Memory holding the conversation.

    Returns:
        str: The previous turn's text, or an empty string when there is none.
    """
    if not message.message_pieces:
        return ""

    scored_piece = message.get_piece()

    if scored_piece.api_role != "assistant":
        return ""

    # The request is the turn before the response being scored, not before whatever the
    # conversation has grown to since. Scoring an earlier response must not read the latest turn.
    previous_sequence = scored_piece.sequence - 1
    if previous_sequence < 0:
        return ""

    conversation = memory.get_message_pieces(conversation_id=scored_piece.conversation_id)

    return "\n".join(
        [
            piece.original_value
            for piece in conversation
            if piece.sequence == previous_sequence and piece.original_value_data_type == "text"
        ]
    )


class MessageScorer(Scorer):
    """
    Base class for scorers whose evidence is a single message.

    Every message-shaped concern lives here: substituting refusal and blocked content,
    validating pieces, applying the role and error filters, and falling back to a neutral
    score. ``Scorer`` stays agnostic about what a scorable is, so scorers over other kinds of
    evidence can sit beside this one. A ``MessageScorableResolver`` acquires the message;
    the scorable remains inert.

    Subclasses implement ``_score_async``, which still receives a ``Message``.
    """

    #: When True, blocked responses that contain partial content are scored using that
    #: content instead of being filtered out or short-circuited.
    score_blocked_content: bool = False

    #: When False, a blocked response from the scorer's own LLM produces the scorer
    #: family's neutral fallback score instead of raising.
    raise_if_scorer_blocks: bool = True

    def __init__(
        self,
        *,
        validator: ScorerPromptValidator,
        chat_target: PromptTarget | None = None,
        message_resolver: MessageScorableResolver | None = None,
    ) -> None:
        """
        Initialize message-specific scoring dependencies.

        Args:
            validator (ScorerPromptValidator): Validator for message pieces.
            chat_target (PromptTarget | None): Optional target used by the scorer.
            message_resolver (MessageScorableResolver | None): Evidence resolver.
        """
        self._validator = validator
        self._message_resolver = message_resolver or MessageScorableResolver()
        super().__init__(chat_target=chat_target)

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """
        Return the conditions this message scorer uses as criteria.

        An objective-required validator is the existing declaration that the scorer judges
        whether the evidence satisfies the objective. Other message scorers may read the
        objective as context without matching ``MatchesObjective``.

        Returns:
            frozenset[type[Condition]]: The matched condition types.
        """
        matched = super().matched_conditions()
        if self._validator.is_objective_required:
            return matched | {MatchesObjective}
        return matched

    def required_conditions(self) -> frozenset[type[Condition]]:
        """Return the matched conditions required by this message scorer."""
        required = super().required_conditions()
        if self._validator.is_objective_required:
            return required | {MatchesObjective}
        return required

    def _validate_expectation(
        self,
        *,
        expectation: ScoringExpectation | None,
        allow_unmatched_conditions: bool = False,
    ) -> None:
        """
        Reject conditions this scorer cannot consume, and unusable ``MatchesObjective``.

        Args:
            expectation (ScoringExpectation | None): The expectation to validate.
            allow_unmatched_conditions (bool): Permit conditions addressed to sibling leaves.

        Raises:
            ValueError: If ``MatchesObjective`` is present without an objective to match.
        """
        super()._validate_expectation(
            expectation=expectation,
            allow_unmatched_conditions=allow_unmatched_conditions,
        )
        if expectation is None or not expectation.conditions:
            return
        matches_objective = MatchesObjective in self.matched_conditions() and any(
            isinstance(condition, MatchesObjective) for condition in expectation.conditions
        )
        if matches_objective and not expectation.objective:
            raise ValueError(
                "MatchesObjective requires the expectation to carry an objective. "
                "Set ScoringExpectation.objective or drop the condition."
            )

    async def score_async(
        self,
        message: Message | None = None,
        *,
        scorable: Scorable | None = None,
        expectation: ScoringExpectation | None = None,
        message_options: MessageScoringOptions | None = None,
        objective: str | None = None,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool | None = None,
        infer_objective_from_request: bool | None = None,
    ) -> list[Score]:
        """
        Score message-shaped evidence, including the deprecated message API.

        Args:
            message (Message | None): Deprecated in-hand message.
            scorable (Scorable | None): Message-shaped evidence to acquire.
            expectation (ScoringExpectation | None): What to look for.
            message_options (MessageScoringOptions | None): Message-family policy.
            objective (str | None): Deprecated objective string.
            role_filter (ChatMessageRole | None): Deprecated role policy.
            skip_on_error_result (bool | None): Deprecated error policy. ``None`` means omitted.
            infer_objective_from_request (bool | None): Deprecated inference policy.

        Returns:
            list[Score]: The persisted scores, or an empty list when policy skips the message.
        """
        resolved_expectation, options, infer_objective = self._consolidate_message_inputs(
            message=message,
            scorable=scorable,
            expectation=expectation,
            message_options=message_options,
            objective=objective,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
            infer_objective_from_request=infer_objective_from_request,
        )
        self._validate_expectation(expectation=resolved_expectation)

        # The deprecated parameter hands over the message itself, so scoring it must not round
        # trip through a reference. Re-describing it would reload the persisted originals and
        # drop role and error state for a message that was never persisted at all.
        if message is not None:
            scores = await self._score_resolved_message_async(
                message=message,
                expectation=resolved_expectation,
                options=options,
                infer_objective_from_request=infer_objective,
            )
        else:
            scores = await self._score_message_scorable_async(
                scorable=cast("Scorable", scorable),
                expectation=resolved_expectation,
                options=options,
                infer_objective_from_request=infer_objective,
            )
        return self._validate_and_persist_scores(scores=scores)

    async def _score_nested_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score a prepared message as a child in a scorer tree.

        Conditions addressed to sibling leaves are ignored here because the root scorer
        already validates that every supplied condition reaches at least one leaf. The
        root scorer owns persistence, so this path only validates child output.

        Returns:
            list[Score]: The validated child scores.
        """
        self._validate_expectation(
            expectation=expectation,
            allow_unmatched_conditions=True,
        )
        scores = await self._score_resolved_message_async(
            message=message,
            expectation=expectation,
            options=MessageScoringOptions(),
            infer_objective_from_request=False,
        )
        if scores:
            self.validate_return_scores(scores=scores)
        return scores

    async def score_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None = None,
        message_options: MessageScoringOptions | None = None,
    ) -> list[Score]:
        """
        Score a message that is already in hand.

        Use this when the caller holds the message itself rather than a reference to it:
        an ephemeral response that was never persisted, or a scoring view a wrapping scorer
        has already prepared. Naming persisted evidence with a ``MessageScorable`` stays the
        default, because a reference is what a stored score can be audited against.

        Args:
            message (Message): The message to score.
            expectation (ScoringExpectation | None): What to look for. Defaults to None.
            message_options (MessageScoringOptions | None): Message-family policy. Defaults to None.

        Returns:
            list[Score]: The persisted scores, or an empty list when policy skips the message.
        """
        self._validate_expectation(expectation=expectation)
        scores = await self._score_resolved_message_async(
            message=message,
            expectation=expectation,
            options=message_options or MessageScoringOptions(),
            infer_objective_from_request=False,
        )
        return self._validate_and_persist_scores(scores=scores)

    def _consolidate_message_inputs(
        self,
        *,
        message: Message | None,
        scorable: Scorable | None,
        expectation: ScoringExpectation | None,
        message_options: MessageScoringOptions | None,
        objective: str | None,
        role_filter: ChatMessageRole | None,
        skip_on_error_result: bool | None,
        infer_objective_from_request: bool | None,
    ) -> tuple[ScoringExpectation | None, MessageScoringOptions, bool]:
        if message is not None and scorable is not None:
            raise ValueError("Pass either 'message' or 'scorable', not both.")
        if message is None and scorable is None:
            raise ValueError("Either 'message' or 'scorable' must be provided.")
        if objective is not None and expectation is not None:
            raise ValueError("Pass either 'objective' or 'expectation', not both.")
        if message_options is not None and (role_filter is not None or skip_on_error_result is not None):
            raise ValueError("Pass either 'message_options' or legacy message policy arguments, not both.")

        uses_legacy_parameters = (
            message is not None
            or objective is not None
            or role_filter is not None
            or skip_on_error_result is not None
            or infer_objective_from_request is not None
        )
        if uses_legacy_parameters:
            print_deprecation_message(
                old_item="Scorer.score_async(message=..., objective=..., role_filter=..., "
                "skip_on_error_result=..., infer_objective_from_request=...)",
                new_item="Scorer.score_async(scorable=..., expectation=..., message_options=...)",
                removed_in=LEGACY_SCORE_ASYNC_REMOVED_IN,
            )

        resolved_expectation = ScoringExpectation(objective=objective) if objective is not None else expectation
        options = message_options or MessageScoringOptions(
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result or False,
        )
        return resolved_expectation, options, bool(infer_objective_from_request)

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score message-shaped evidence with default message policy.

        Returns:
            list[Score]: The scores produced from the resolved message.
        """
        return await self._score_message_scorable_async(
            scorable=scorable,
            expectation=expectation,
            options=MessageScoringOptions(),
            infer_objective_from_request=False,
        )

    async def _score_message_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        options: MessageScoringOptions,
        infer_objective_from_request: bool,
    ) -> list[Score]:
        """
        Resolve a message scorable and score the message it names.

        Args:
            scorable (Scorable): A ``MessageScorable`` or a ``ContentScorable``.
            expectation (ScoringExpectation | None): What to look for.
            options (MessageScoringOptions): Message-only scoring policy.
            infer_objective_from_request (bool): Deprecated; read the objective from the
                previous turn when the expectation carries none.

        Returns:
            list[Score]: The scores, or an empty list when a filter skipped the message.

        Raises:
            TypeError: If the scorable is not message-shaped.
        """
        message = self._message_resolver.resolve(scorable=scorable, memory=self._memory)
        return await self._score_resolved_message_async(
            message=message,
            expectation=expectation,
            options=options,
            infer_objective_from_request=infer_objective_from_request,
        )

    async def _score_resolved_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
        options: MessageScoringOptions,
        infer_objective_from_request: bool,
    ) -> list[Score]:
        """
        Run the message-scoring pipeline over an acquired message.

        Args:
            message (Message): The acquired message.
            expectation (ScoringExpectation | None): What to look for.
            options (MessageScoringOptions): Message-only scoring policy.
            infer_objective_from_request (bool): Deprecated; read the objective from the
                previous turn when the expectation carries none.

        Returns:
            list[Score]: The scores, or an empty list when a filter skipped the message.

        Raises:
            ScorerLLMResponseBlockedException: If the scorer's own LLM response is blocked by
                content filtering and ``raise_if_scorer_blocks`` is True (the default).
            PyritException: If scoring raises a PyRIT exception (re-raised with enhanced context).
            RuntimeError: If scoring raises a non-PyRIT exception (wrapped with scorer context).
        """
        objective = expectation.objective if expectation else None

        # Structured refusals are persisted as blocked error pieces, but scorers should
        # receive the refusal explanation as text. Keep response_error="blocked" so
        # refusal scorers can still use their deterministic blocked-response path.
        scoring_message = self._apply_structured_refusal_substitution(message)

        # When score_blocked_content is enabled, blocked pieces with partial content
        # take precedence and are replaced with text substitutes (response_error="none").
        if self.score_blocked_content:
            scoring_message = self._apply_blocked_content_substitution(scoring_message)

        self._validator.validate(scoring_message, objective=objective)

        if options.role_filter is not None and message.get_piece().role != options.role_filter:
            logger.debug("Skipping scoring due to role filter mismatch.")
            return []

        if options.skip_on_error_result and self._should_skip_on_error(message):
            return []

        if infer_objective_from_request and (not objective):
            objective = extract_objective_from_previous_turn(message=message, memory=self._memory)

        effective_expectation = expectation
        if expectation is None and objective is not None:
            effective_expectation = ScoringExpectation(objective=objective)
        elif expectation is not None and objective != expectation.objective:
            effective_expectation = ScoringExpectation(
                objective=objective,
                conditions=expectation.conditions,
            )

        try:
            scores = await self._score_prepared_message_async(
                message=scoring_message,
                expectation=effective_expectation,
            )
        except ScorerLLMResponseBlockedException as e:
            # The scorer's own LLM response was content-filtered. By default this is a real
            # error and propagates; when raise_if_scorer_blocks is False, fall back to the
            # scorer's type default (False / 0.0) instead. The decision lives here in the
            # scorer, not the transport (see doc/code/framework.md).
            if self.raise_if_scorer_blocks:
                e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
                e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
                raise
            logger.info(
                "Scorer %s LLM response was blocked by content filtering; "
                "returning default score (raise_if_scorer_blocks=False).",
                self.__class__.__name__,
            )
            scores = self._build_fallback_score(
                message=scoring_message,
                objective=objective,
                scorer_response_blocked=True,
            )
        except PyritException as e:
            # Re-raise PyRIT exceptions with enhanced context while preserving type for retry decorators
            e.message = f"Error in scorer {self.__class__.__name__}: {e.message}"
            e.args = (f"Status Code: {e.status_code}, Message: {e.message}",)
            raise
        except Exception as e:
            # Wrap non-PyRIT exceptions for better error tracing
            raise RuntimeError(f"Error in scorer {self.__class__.__name__}: {str(e)}") from e

        if not scores and scoring_message.message_pieces:
            scores = self._build_fallback_score(message=scoring_message, objective=objective)

        self._drop_ephemeral_score_links(message=scoring_message, scores=scores)

        return scores

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Score a message after message-family policy and substitutions are applied.

        Wrapping scorers override this hook to forward the complete expectation. Existing
        leaf scorer bodies continue to receive only the objective string.

        Returns:
            list[Score]: The scores produced from the prepared message.
        """
        return await self._score_async(
            message,
            objective=expectation.objective if expectation else None,
        )

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score the given request response asynchronously.

        This default implementation scores all supported pieces in the message
        and returns a flattened list of scores. Subclasses can override this method
        to implement custom scoring logic (e.g., aggregating scores).

        Args:
            message (Message): The message to score.
            objective (str | None): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list of Score objects.
        """
        if not message.message_pieces:
            return []

        # Score only the supported pieces
        supported_pieces = self._get_supported_pieces(message)

        tasks = [self._score_piece_async(message_piece=piece, objective=objective) for piece in supported_pieces]

        if not tasks:
            return []

        # Run all piece-level scorings concurrently
        piece_score_lists = await asyncio.gather(*tasks)

        # Flatten list[list[Score]] -> list[Score]
        return [score for sublist in piece_score_lists for score in sublist]

    @abstractmethod
    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        raise NotImplementedError

    def _get_supported_pieces(self, message: Message) -> list[MessagePiece]:
        """
        Get a list of supported message pieces for this scorer.

        Returns:
            list[MessagePiece]: List of message pieces that are supported by this scorer's validator.
        """
        return [
            piece for piece in message.message_pieces if self._validator.is_message_piece_supported(message_piece=piece)
        ]

    def _should_skip_on_error(self, message: Message) -> bool:
        """
        Return whether an errored message should be skipped rather than scored.

        Returns:
            bool: True when the message should not be scored.
        """
        if not message.is_error():
            return False

        error_pieces = [
            piece for piece in message.message_pieces if piece.has_error() or piece.converted_value_data_type == "error"
        ]
        # SDK-provided structured refusals stay scoreable: the refusal text is the evidence.
        only_structured_refusals = all(piece.structured_refusal is not None for piece in error_pieces)
        # When score_blocked_content is enabled and the message has partial content,
        # don't skip — let _score_async handle the substitution.
        all_errors_have_partial_content = all(
            piece.is_blocked() and piece.prompt_metadata.get("partial_content") for piece in error_pieces
        )
        if only_structured_refusals or (self.score_blocked_content and all_errors_have_partial_content):
            return False

        logger.debug("Skipping scoring due to error in message and skip_on_error=True.")
        return True

    @staticmethod
    def _drop_ephemeral_score_links(*, message: Message, scores: list[Score]) -> None:
        """
        Clear the piece link on scores that point at pieces which were never persisted.

        Memory cannot link a score to a piece it never stored, but the score itself is
        still worth keeping.
        """
        ephemeral_piece_ids = {
            piece.id for piece in message.message_pieces if piece.not_in_memory and piece.id is not None
        }
        if not ephemeral_piece_ids:
            return

        for score in scores:
            if score.message_piece_id in ephemeral_piece_ids:
                score.message_piece_id = None  # type: ignore[ty:invalid-assignment]

    @staticmethod
    def _create_scoring_text_piece(
        *,
        piece: MessagePiece,
        content: str,
        response_error: PromptResponseError,
    ) -> MessagePiece:
        """
        Create a text scoring view that retains the persisted piece identity.

        Returns:
            MessagePiece: The text scoring view.
        """
        return MessagePiece(
            id=piece.id,
            role=piece.api_role,
            original_value=piece.original_value,
            converted_value=content,
            original_value_data_type=piece.original_value_data_type,
            converted_value_data_type="text",
            conversation_id=piece.conversation_id,
            sequence=piece.sequence,
            prompt_metadata=dict(piece.prompt_metadata),
            converter_identifiers=list(piece.converter_identifiers),  # type: ignore[arg-type]
            response_error=response_error,
            timestamp=piece.timestamp,
            original_prompt_id=piece.original_prompt_id,
            not_in_memory=piece.not_in_memory,
        )

    @classmethod
    def _create_text_piece_from_blocked(cls, piece: MessagePiece) -> MessagePiece | None:
        """
        Create a text scoring view from a blocked piece's partial content.

        Returns:
            MessagePiece | None: The scoring view, or None when content is unavailable.
        """
        partial_content = str(piece.prompt_metadata.get("partial_content", ""))
        if not partial_content:
            return None
        return cls._create_scoring_text_piece(
            piece=piece,
            content=partial_content,
            response_error="none",
        )

    @classmethod
    def _create_text_piece_from_structured_refusal(cls, piece: MessagePiece) -> MessagePiece | None:
        """
        Create a blocked text scoring view for an SDK-provided refusal.

        Returns:
            MessagePiece | None: The scoring view, or None when there is no refusal.
        """
        refusal = piece.structured_refusal
        if not refusal:
            return None
        return cls._create_scoring_text_piece(
            piece=piece,
            content=refusal,
            response_error="blocked",
        )

    def _apply_structured_refusal_substitution(self, message: Message) -> Message:
        """
        Expose structured refusal explanations while preserving blocked semantics.

        Returns:
            Message: The substituted message, or the original message.
        """
        substituted = False
        new_pieces: list[MessagePiece] = []
        for piece in message.message_pieces:
            substitute = self._create_text_piece_from_structured_refusal(piece)
            if substitute:
                new_pieces.append(substitute)
                substituted = True
                continue
            new_pieces.append(piece)
        return Message(message_pieces=new_pieces) if substituted else message

    def _apply_blocked_content_substitution(self, message: Message) -> Message:
        """
        Replace blocked pieces that have partial content with text scoring views.

        Returns:
            Message: The substituted message, or the original message.
        """
        substituted = False
        new_pieces: list[MessagePiece] = []
        for piece in message.message_pieces:
            if piece.is_blocked() and "partial_content" in piece.prompt_metadata:
                substitute = self._create_text_piece_from_blocked(piece)
                if substitute:
                    new_pieces.append(substitute)
                    substituted = True
                    continue
            new_pieces.append(piece)
        return Message(message_pieces=new_pieces) if substituted else message

    @abstractmethod
    def _build_fallback_score(
        self,
        *,
        message: Message,
        objective: str | None,
        scorer_response_blocked: bool = False,
    ) -> list[Score]:
        """
        Return the scorer family's neutral result when message evidence is unscoreable.

        Args:
            message (Message): The message-shaped evidence.
            objective (str | None): The objective associated with this call.
            scorer_response_blocked (bool): Whether the scorer's own LLM was blocked.

        Returns:
            list[Score]: One or more fallback scores.
        """
        ...
