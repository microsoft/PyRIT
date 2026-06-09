# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scoring service for invoking registered scorers on demand.

This service is the thin glue between the REST surface and ``Scorer.score_async``:

* ``list_scorers_async`` enumerates ``ScorerRegistry`` so the GUI can populate a dropdown.
* ``score_conversation_async`` resolves a scorer by registry name and applies it to either
  the last assistant message in a conversation or the whole concatenated transcript
  (via ``create_conversation_scorer``).
* ``score_message_async`` scores a single message piece in a conversation.

All scoring runs through ``Scorer.score_async`` which persists scores to memory, so a
subsequent ``GET /attacks/{id}/messages`` call will surface the new scores on the
``BackendMessagePiece.scores`` field with no additional work here.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from pyrit.backend.mappers import pyrit_scores_to_dto
from pyrit.backend.models.attacks import Score
from pyrit.backend.models.scoring import (
    ScoreConversationMode,
    ScoreConversationRequest,
    ScoreMessageRequest,
    ScoreResponse,
    ScorerListResponse,
    ScorerSummary,
)
from pyrit.memory import CentralMemory
from pyrit.registry import ScorerRegistry

if TYPE_CHECKING:
    from pyrit.models import Message
    from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


def _extract_class_description(cls: type) -> str | None:
    """
    Extract the first paragraph of a class docstring as a short human-readable description.

    Matches the convention used by ``ConverterService.list_converter_catalog_async`` so the
    UI can render scorer and converter info consistently.
    """
    raw_doc = (cls.__doc__ or "").strip()
    if not raw_doc:
        return None
    first_paragraph = raw_doc.split("\n\n")[0]
    cleaned = " ".join(line.strip() for line in first_paragraph.splitlines() if line.strip())
    return cleaned or None


class ScoringService:
    """
    Service that surfaces registered scorers and runs them against stored conversations.

    Scoring writes to memory via ``Scorer.score_async``, so callers do not need to
    persist the returned ``Score`` DTOs themselves.
    """

    def __init__(self) -> None:
        """Initialize the scoring service."""
        self._memory = CentralMemory.get_memory_instance()
        self._registry = ScorerRegistry.get_registry_singleton()

    async def list_scorers_async(self) -> ScorerListResponse:  # pyrit-async-suffix-exempt
        """
        Enumerate every registered scorer (registry name, class, score type, description, tags).

        Returns:
            ScorerListResponse: Registered scorers in registry-name order.
        """
        items = [
            ScorerSummary(
                scorer_registry_name=entry.name,
                scorer_type=entry.instance.__class__.__name__,
                score_type=entry.instance.scorer_type,
                description=_extract_class_description(entry.instance.__class__),
                tags=sorted(entry.tags.keys()) if entry.tags else [],
            )
            for entry in self._registry.get_all_instances()
        ]
        return ScorerListResponse(items=items)

    async def score_conversation_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
        request: ScoreConversationRequest,
    ) -> ScoreResponse:
        """
        Score a conversation belonging to an attack with a registered scorer.

        Args:
            attack_result_id (str): The AttackResult primary key (used to verify existence).
            conversation_id (str): The conversation to score (must belong to the attack).
            request (ScoreConversationRequest): Scorer name, mode, and optional objective.

        Returns:
            ScoreResponse: The scores produced by the scorer (also persisted to memory).

        Raises:
            LookupError: If the attack does not exist.
            ValueError: If the conversation does not belong to the attack, the conversation
                has no scoreable assistant message, or the scorer registry name is unknown.
        """
        self._verify_conversation_belongs_to_attack(
            attack_result_id=attack_result_id, conversation_id=conversation_id
        )

        scorer = self._resolve_scorer(request.scorer_registry_name)
        conversation = list(self._memory.get_conversation(conversation_id=conversation_id))

        if not conversation:
            raise ValueError(f"Conversation '{conversation_id}' has no messages to score")

        target_message = self._select_message_for_scoring(conversation=conversation, mode=request.mode)
        effective_scorer = self._maybe_wrap_for_conversation_scoring(scorer=scorer, mode=request.mode)

        scores = await effective_scorer.score_async(message=target_message, objective=request.objective)
        return ScoreResponse(scores=pyrit_scores_to_dto(list(scores)))

    async def score_message_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
        piece_id: str,
        request: ScoreMessageRequest,
    ) -> ScoreResponse:
        """
        Score a single message piece in a conversation with a registered scorer.

        Args:
            attack_result_id (str): The AttackResult primary key (used to verify existence).
            conversation_id (str): The conversation containing the piece.
            piece_id (str): The message-piece id to score.
            request (ScoreMessageRequest): Scorer name and optional objective.

        Returns:
            ScoreResponse: The scores produced by the scorer (also persisted to memory).

        Raises:
            LookupError: If the attack does not exist, or the piece is not in the conversation.
            ValueError: If the conversation does not belong to the attack or the scorer is unknown.
        """
        self._verify_conversation_belongs_to_attack(
            attack_result_id=attack_result_id, conversation_id=conversation_id
        )

        scorer = self._resolve_scorer(request.scorer_registry_name)
        conversation = list(self._memory.get_conversation(conversation_id=conversation_id))

        target_message = self._find_message_containing_piece(conversation=conversation, piece_id=piece_id)
        if target_message is None:
            raise LookupError(
                f"Message piece '{piece_id}' is not part of conversation '{conversation_id}'"
            )

        scores = await scorer.score_async(message=target_message, objective=request.objective)
        return ScoreResponse(scores=pyrit_scores_to_dto(list(scores)))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _verify_conversation_belongs_to_attack(
        self, *, attack_result_id: str, conversation_id: str
    ) -> None:
        """
        Raise ``LookupError`` if the attack does not exist, ``ValueError`` if the
        conversation does not belong to it.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            raise LookupError(f"Attack '{attack_result_id}' not found")
        if conversation_id not in results[0].get_active_conversation_ids():
            raise ValueError(
                f"Conversation '{conversation_id}' is not part of attack '{attack_result_id}'"
            )

    def _resolve_scorer(self, scorer_registry_name: str) -> Scorer:
        """Resolve a scorer by registry name; raise ``ValueError`` when missing."""
        scorer = self._registry.get(scorer_registry_name)
        if scorer is None:
            raise ValueError(f"Scorer '{scorer_registry_name}' is not registered")
        return scorer

    @staticmethod
    def _select_message_for_scoring(
        *, conversation: list[Message], mode: ScoreConversationMode
    ) -> Message:
        """
        Pick the message to hand to ``Scorer.score_async``.

        For ``last_message`` we score only the most recent assistant turn so the result
        is comparable to a per-message score. For ``whole_conversation`` we just pick the
        last message in the conversation — the ``ConversationScorer`` wrapper uses its
        ``conversation_id`` to fetch the full transcript from memory.
        """
        if mode == "whole_conversation":
            return conversation[-1]

        # last_message: find the most recent assistant (or simulated assistant) turn.
        for message in reversed(conversation):
            if message.message_pieces and message.message_pieces[0].role in (
                "assistant",
                "simulated_assistant",
            ):
                return message
        raise ValueError("Conversation has no assistant message to score")

    @staticmethod
    def _maybe_wrap_for_conversation_scoring(
        *, scorer: Scorer, mode: ScoreConversationMode
    ) -> Scorer:
        """
        Wrap the scorer in a ``ConversationScorer`` when the caller asked for
        whole-conversation scoring. Raises ``ValueError`` if the scorer cannot be wrapped
        (i.e. it isn't a ``FloatScaleScorer`` or ``TrueFalseScorer``).
        """
        if mode != "whole_conversation":
            return scorer

        from pyrit.score.conversation_scorer import create_conversation_scorer
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        if not isinstance(scorer, (FloatScaleScorer, TrueFalseScorer)):
            raise ValueError(
                "Whole-conversation scoring requires a FloatScaleScorer or TrueFalseScorer; "
                f"got {type(scorer).__name__}"
            )
        return create_conversation_scorer(scorer=scorer)

    @staticmethod
    def _find_message_containing_piece(
        *, conversation: list[Message], piece_id: str
    ) -> Message | None:
        """Return the message in ``conversation`` whose pieces include ``piece_id``."""
        for message in conversation:
            for piece in message.message_pieces:
                if str(piece.id) == piece_id:
                    return message
        return None


# ============================================================================
# Singleton
# ============================================================================


@lru_cache(maxsize=1)
def get_scoring_service() -> ScoringService:
    """
    Get the global scoring service instance.

    Returns:
        ScoringService: The singleton ``ScoringService`` instance.
    """
    return ScoringService()


__all__ = ["ScoringService", "get_scoring_service", "Score"]
