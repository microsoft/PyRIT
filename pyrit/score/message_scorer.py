# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyrit.exceptions import PyritException, ScorerLLMResponseBlockedException
from pyrit.models import (
    ContentScorable,
    Message,
    MessagePiece,
    MessageReferenceScorable,
    MessageScorable,
    Scorable,
    Score,
    ScoringExpectation,
    group_message_pieces_into_conversations,
)
from pyrit.score.scorer import Scorer

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface

logger = logging.getLogger(__name__)

#: Scorable kinds a MessageScorer can reduce to a single Message.
_SUPPORTED_SCORABLES = (MessageScorable, MessageReferenceScorable, ContentScorable)


def extract_objective_from_previous_turn(*, message: Message, memory: MemoryInterface) -> str:
    """
    Read the text of the turn before an assistant message and use it as the objective.

    This is what to look for, so it belongs to the caller that builds the expectation, not
    to the scorer. It lives here because it is message-shaped.

    Args:
        message (Message): The assistant message whose previous turn supplies the objective.
        memory (MemoryInterface): Memory holding the conversation.

    Returns:
        str: The previous turn's text, or an empty string when there is none.
    """
    if not message.message_pieces:
        return ""

    piece = message.get_piece()

    if piece.api_role != "assistant":
        return ""

    conversation = memory.get_message_pieces(conversation_id=piece.conversation_id)
    if not conversation:
        return ""

    last_prompt = max(conversation, key=lambda x: x.sequence)

    return "\n".join(
        [
            piece.original_value
            for piece in conversation
            if piece.sequence == last_prompt.sequence - 1 and piece.original_value_data_type == "text"
        ]
    )


class MessageScorer(Scorer):
    """
    Base class for scorers whose evidence is a single message.

    Every message-shaped concern lives here: resolving a message scorable to a ``Message``,
    substituting refusal and blocked content, validating pieces, applying the role and error
    filters, and falling back to a neutral score. ``Scorer`` stays agnostic about what a
    scorable is, so scorers over other kinds of evidence can sit beside this one.

    Subclasses implement ``_score_async``, which still receives a ``Message``.
    """

    async def _score_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        infer_objective_from_request: bool = False,
    ) -> list[Score]:
        """
        Resolve a message scorable and score the message it names.

        Args:
            scorable (Scorable): A ``MessageScorable``, ``MessageReferenceScorable``, or
                ``ContentScorable``.
            expectation (ScoringExpectation | None): What to look for.
            infer_objective_from_request (bool): Deprecated; read the objective from the
                previous turn when the expectation carries none.

        Returns:
            list[Score]: The scores, or an empty list when a filter skipped the message.

        Raises:
            TypeError: If the scorable is not message-shaped.
            ScorerLLMResponseBlockedException: If the scorer's own LLM response is blocked by
                content filtering and ``raise_if_scorer_blocks`` is True (the default).
            PyritException: If scoring raises a PyRIT exception (re-raised with enhanced context).
            RuntimeError: If scoring raises a non-PyRIT exception (wrapped with scorer context).
        """
        if not isinstance(scorable, _SUPPORTED_SCORABLES):
            raise TypeError(
                f"{self.__class__.__name__} scores messages, so it cannot score {type(scorable).__name__}. "
                "Pass a MessageScorable, a MessageReferenceScorable, or a ContentScorable."
            )

        message = self._resolve_message(scorable)
        role_filter = getattr(scorable, "role_filter", None)
        skip_on_error_result = getattr(scorable, "skip_on_error_result", False)
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

        if role_filter is not None and message.get_piece().role != role_filter:
            logger.debug("Skipping scoring due to role filter mismatch.")
            return []

        if skip_on_error_result and self._should_skip_on_error(message):
            return []

        if infer_objective_from_request and (not objective):
            objective = extract_objective_from_previous_turn(message=message, memory=self._memory)

        try:
            scores = await self._score_async(
                scoring_message,
                objective=objective,
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

    def _resolve_message(self, scorable: MessageScorable | MessageReferenceScorable | ContentScorable) -> Message:
        """
        Return the message a message-shaped scorable names.

        Loose content has no conversation behind it, so it becomes a message that is marked
        as never persisted. Phase 2 gives scores a scorable of their own and removes this.

        Returns:
            Message: The message to score.

        Raises:
            ValueError: If the referenced pieces are not in memory or do not form one message.
        """
        if isinstance(scorable, MessageScorable):
            return scorable.message

        if isinstance(scorable, ContentScorable):
            piece = MessagePiece(
                role="user",
                original_value=scorable.value,
                original_value_data_type=scorable.data_type,
            )
            piece.not_in_memory = True
            return Message(message_pieces=[piece])

        pieces = self._memory.get_message_pieces(prompt_ids=list(scorable.message_piece_ids))
        if not pieces:
            raise ValueError(f"No message pieces found in memory for ids {list(scorable.message_piece_ids)}.")

        conversations = group_message_pieces_into_conversations(pieces)
        messages = [message for conversation in conversations for message in conversation]
        if len(messages) != 1:
            raise ValueError(
                f"Expected the referenced pieces to form exactly one message, got {len(messages)}. "
                "Reference pieces from a single message, or use a ConversationScorable."
            )
        return messages[0]

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
