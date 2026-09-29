# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Message, Score


@runtime_checkable
class ConversationSource(Protocol):
    """
    The data a conversation printer needs, decoupled from where it comes from.

    ``MemoryConversationSource`` (CentralMemory) backs the framework/notebook path;
    a REST-backed source backs the thin CLI client. Printers depend only on this
    Protocol, so ``pyrit.output`` needs no knowledge of either backend and never
    imports ``pyrit.cli``.
    """

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """Return the ordered messages for a conversation."""
        ...

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """Return the scores attached to the given message-piece ids (empty if none)."""
        ...


class ObjectiveScoreConversationSource:
    """
    ``ConversationSource`` that keeps only the objective score on each message piece.

    A response can carry several scores, such as an auxiliary refusal score next to the
    objective score, and auxiliary verdicts read backwards relative to attack success.
    This source wraps another source and, for each piece, keeps only the objective scorer's
    score stored on that piece, matched by identity hash and then by class name. That is the
    score the CLI's ``RestApiConversationSource`` surfaces. Without an objective scorer it
    keeps no scores.

    ``get_messages_async`` reads a conversation's scores in one batch, grouped by the piece
    that stores them (a duplicated piece's scores are stored on its original), the way the
    backend groups them for the CLI. Call it before ``get_scores_async`` for that conversation.
    """

    def __init__(
        self,
        *,
        source: ConversationSource,
        objective_scorer_identifier: ComponentIdentifier | None,
    ) -> None:
        """
        Wrap a source so only the objective score on each piece is kept.

        Args:
            source (ConversationSource): The source to read messages and scores from.
            objective_scorer_identifier (ComponentIdentifier | None): The scorer whose score to keep,
                or None to keep no scores.
        """
        self._source = source
        self._objective_scorer_identifier = objective_scorer_identifier
        self._score_owner_ids: dict[str, str] = {}
        self._scores_by_owner: dict[str, list[Score]] = {}

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """
        Return the ordered messages for a conversation and read the scores stored on their pieces.

        Args:
            conversation_id (str): The conversation to read.

        Returns:
            list[Message]: The conversation's messages in order.
        """
        messages = await self._source.get_messages_async(conversation_id=conversation_id)
        self._score_owner_ids = {
            str(piece.id): str(piece.original_prompt_id or piece.id)
            for message in messages
            for piece in message.message_pieces
        }
        self._scores_by_owner = {}
        if self._objective_scorer_identifier is None or not self._score_owner_ids:
            return messages
        owner_ids = sorted(set(self._score_owner_ids.values()))
        for score in await self._source.get_scores_async(prompt_ids=owner_ids):
            self._scores_by_owner.setdefault(str(score.message_piece_id), []).append(score)
        return messages

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Return the objective score stored on each of the given message pieces.

        Scores stored on another piece of the same message (for example a message-level score)
        are skipped, so every piece keeps at most one score.

        Args:
            prompt_ids (list[str]): The message-piece ids to fetch scores for.

        Returns:
            list[Score]: The objective scores for those pieces (empty if none match).
        """
        objective_scorer_identifier = self._objective_scorer_identifier
        if objective_scorer_identifier is None:
            return []
        objective_scores: list[Score] = []
        for prompt_id in prompt_ids:
            owner_id = self._score_owner_ids.get(prompt_id, prompt_id)
            objective_score = self._select_objective_score(
                scores=self._scores_by_owner.get(owner_id, []),
                objective_scorer_identifier=objective_scorer_identifier,
            )
            if objective_score is not None:
                objective_scores.append(objective_score)
        return objective_scores

    @staticmethod
    def _select_objective_score(
        *,
        scores: list[Score],
        objective_scorer_identifier: ComponentIdentifier,
    ) -> Score | None:
        """
        Pick the objective scorer's score, matching the identity hash before the class name.

        Args:
            scores (list[Score]): The scores attached to one message piece.
            objective_scorer_identifier (ComponentIdentifier): The objective scorer to match.

        Returns:
            Score | None: The objective score, or None when no score matches.
        """
        class_name_match: Score | None = None
        for score in scores:
            identifier = score.scorer_class_identifier
            if identifier is None:
                continue
            if identifier.hash == objective_scorer_identifier.hash:
                return score
            if class_name_match is None and identifier.class_name == objective_scorer_identifier.class_name:
                class_name_match = score
        return class_name_match


class MemoryConversationSource:
    """``ConversationSource`` backed by ``CentralMemory`` (framework / notebook path)."""

    def __init__(self) -> None:
        """Resolve the process-wide memory instance (deferred import)."""
        from pyrit.memory import CentralMemory

        self._memory = CentralMemory.get_memory_instance()

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        """
        Return the ordered messages for a conversation from memory.

        Args:
            conversation_id (str): The conversation to read.

        Returns:
            list[Message]: The conversation's messages in order.
        """
        return list(self._memory.get_conversation_messages(conversation_id=conversation_id))

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        """
        Return the scores attached to the given message-piece ids from memory.

        Args:
            prompt_ids (list[str]): The message-piece ids to fetch scores for.

        Returns:
            list[Score]: The scores for those pieces (empty if none).
        """
        return list(self._memory.get_prompt_scores(prompt_ids=prompt_ids))
