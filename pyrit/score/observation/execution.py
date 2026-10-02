# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, TypeAlias

from pyrit.models import (
    ContentEntryScorable,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    Observation,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
    ToolEventsObservationPayload,
)
from pyrit.models.score.observation import _resolved_scored_evidence_digest

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


class NonReplayableObservationError(ValueError):
    """Raised when stored evidence cannot be judged again by a scorer."""


if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator, Sequence


_ObservationEvidence: TypeAlias = Message | ToolEventsObservationPayload


async def _scored_evidence_digest_async(
    *,
    scorable: ScorableUnion,
    scored_piece_id: uuid.UUID,
    memory: MemoryInterface,
    scored_message_piece: MessagePiece | None = None,
) -> str | None:
    """
    Resolve and hash the canonical input evidence used for one judgment.

    Returns:
        str | None: The digest, or None when media replay is deferred.

    Raises:
        NonReplayableObservationError: If the scored evidence cannot be resolved.
    """
    if isinstance(scorable, MessageScorable) and scored_message_piece is None:
        pieces = await memory.get_message_pieces_async(prompt_ids=[scored_piece_id])
        scored_message_piece = next((piece for piece in pieces if piece.id == scored_piece_id), None)
    content_id = scorable.content_id if isinstance(scorable, ContentEntryScorable) else None
    stored_content = await _load_content_evidence_async(memory=memory, content_id=content_id)
    try:
        return _resolved_scored_evidence_digest(
            scorable=scorable,
            scored_piece_id=scored_piece_id,
            scored_piece=scored_message_piece,
            stored_content=stored_content,
        )
    except ValueError as error:
        raise NonReplayableObservationError(str(error)) from error


async def _load_content_evidence_async(
    *, memory: MemoryInterface, content_id: uuid.UUID | None
) -> tuple[ContentScorable, str] | None:
    """
    Load stored content and its hash.

    Returns:
        tuple[ContentScorable, str] | None: The evidence, or None if unreferenced or missing.
    """
    if content_id is None:
        return None
    content = (await memory.get_scorable_content_async(content_ids=[content_id])).get(content_id)
    digest = (await memory.get_scorable_content_hashes_async(content_ids=[content_id])).get(content_id)
    return (content, digest) if content is not None and digest is not None else None


class _ScoringCollector:
    """Observations and intermediate judgments created during one root scoring operation."""

    def __init__(self) -> None:
        """Initialize empty observation and intermediate-score collections."""
        self._observations: dict[uuid.UUID, Observation] = {}
        self._scores: dict[str, Score] = {}

    def add_scores(self, scores: Sequence[Score]) -> None:
        """
        Snapshot finalized nested results without changing caller-owned scores.

        Raises:
            ValueError: If an ID is reused for a different judgment.
        """
        for score in scores:
            snapshot = score.model_copy(deep=True)
            score_id = str(score.id)
            if score_id in self._scores and self._scores[score_id] != snapshot:
                raise ValueError(f"Intermediate score ID {score_id} was reused with different values.")
            self._scores[score_id] = snapshot

    @property
    def intermediate_scores(self) -> list[Score]:
        """The results of nested scorers at any depth, excluding the public call's returned results."""
        return list(self._scores.values())

    def add(self, observation: Observation) -> None:
        """
        Add one observation, rejecting a conflicting duplicate ID.

        Raises:
            ValueError: If the ID was collected with different observation data.
        """
        existing = self._observations.get(observation.id)
        if existing is not None and existing != observation:
            raise ValueError(f"Observation ID {observation.id} was collected with conflicting values.")
        self._observations[observation.id] = observation

    def referenced_by(self, *, scores: Sequence[Score]) -> list[Observation]:
        """Return collected observations referenced by final scores in stable order."""
        return [
            self._observations[observation_id]
            for observation_id in _merge_observation_ids(scores=scores)
            if observation_id in self._observations
        ]


_CURRENT_OBSERVATION_COLLECTOR: ContextVar[_ScoringCollector | None] = ContextVar(
    "current_observation_collector",
    default=None,
)
_CURRENT_SCORE_COLLECTOR: ContextVar[_ScoringCollector | None] = ContextVar("current_score_collector", default=None)
_CURRENT_SCORING_EXPECTATION: ContextVar[ScoringExpectation | None] = ContextVar(
    "current_scoring_expectation",
    default=None,
)
_CURRENT_SCORABLE: ContextVar[Scorable | None] = ContextVar(
    "current_scorable",
    default=None,
)
_CURRENT_SCORING_MESSAGE: ContextVar[Message | None] = ContextVar(
    "current_scoring_message",
    default=None,
)


@contextmanager
def _scoring_collection() -> Iterator[_ScoringCollector]:
    """
    Collect observations and intermediate results for one public scoring call.

    Yields:
        _ScoringCollector: The root operation's collector.
    """
    collector = _ScoringCollector()
    token = _CURRENT_OBSERVATION_COLLECTOR.set(collector)
    score_token = _CURRENT_SCORE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _CURRENT_OBSERVATION_COLLECTOR.reset(token)
        _CURRENT_SCORE_COLLECTOR.reset(score_token)


def _collect_scores(scores: Sequence[Score]) -> None:
    """Retain nested results even when observation capture is suppressed."""
    collector = _CURRENT_SCORE_COLLECTOR.get()
    if collector is not None:
        collector.add_scores(scores)


def _collect_observation(observation: Observation) -> None:
    """
    Record an observation in the active root scoring operation.

    Raises:
        RuntimeError: If there is no active scoring operation.
    """
    collector = _CURRENT_OBSERVATION_COLLECTOR.get()
    if collector is None:
        raise RuntimeError("Observations can only be collected during a scorer operation.")
    collector.add(observation)


def _has_observation_collection() -> bool:
    """
    Check whether a root scoring operation is collecting observations.

    Returns:
        bool: True when a collector is active.
    """
    return _CURRENT_OBSERVATION_COLLECTOR.get() is not None


@contextmanager
def _suppress_observation_collection() -> Iterator[None]:
    """Temporarily disable observation capture for derived evidence that cannot replay."""
    token = _CURRENT_OBSERVATION_COLLECTOR.set(None)
    try:
        yield
    finally:
        _CURRENT_OBSERVATION_COLLECTOR.reset(token)


@contextmanager
def _scoring_expectation_context(
    expectation: ScoringExpectation | None,
) -> Iterator[None]:
    """Make the effective expectation available to request-bound scoring helpers."""
    token = _CURRENT_SCORING_EXPECTATION.set(expectation)
    try:
        yield
    finally:
        _CURRENT_SCORING_EXPECTATION.reset(token)


def _get_current_scoring_expectation() -> ScoringExpectation | None:
    """
    Return the effective expectation for the active scorer call.

    Returns:
        ScoringExpectation | None: The active expectation.
    """
    return _CURRENT_SCORING_EXPECTATION.get()


@contextmanager
def _scoring_scorable_context(scorable: Scorable | None) -> Iterator[None]:
    """Make the active scorable available to request-bound scoring helpers."""
    token = _CURRENT_SCORABLE.set(scorable)
    try:
        yield
    finally:
        _CURRENT_SCORABLE.reset(token)


def _get_current_scorable() -> Scorable | None:
    """
    Return the scorable for the active scorer call.

    Returns:
        Scorable | None: The active scorable.
    """
    return _CURRENT_SCORABLE.get()


@contextmanager
def _scoring_message_context(message: Message) -> Iterator[None]:
    """Make the exact prepared message available to request-bound scoring helpers."""
    token = _CURRENT_SCORING_MESSAGE.set(message)
    try:
        yield
    finally:
        _CURRENT_SCORING_MESSAGE.reset(token)


def _get_current_scored_message_piece(*, scored_piece_id: uuid.UUID) -> MessagePiece | None:
    """
    Return the exact prepared message piece passed to the active scorer.

    Returns:
        MessagePiece | None: The matching prepared piece, if a message scorer supplied one.
    """
    message = _CURRENT_SCORING_MESSAGE.get()
    if message is None:
        return None
    return next((piece for piece in message.message_pieces if piece.id == scored_piece_id), None)


def _merge_observation_ids(*, scores: Sequence[Score]) -> list[uuid.UUID]:
    """
    Combine child observation IDs without changing first-seen order.

    Returns:
        list[uuid.UUID]: The stable, duplicate-free observation IDs.
    """
    merged: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for score in scores:
        for observation_id in score.observation_ids:
            if observation_id not in seen:
                merged.append(observation_id)
                seen.add(observation_id)
    return merged


class _ObservationEvidenceResolver:
    """Resolve managed observation references without calling their original source."""

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Initialize the resolver with the observation store."""
        self._memory = memory

    async def resolve_async(self, *, observation: Observation) -> _ObservationEvidence:
        """
        Resolve an observation's managed response references.

        Returns:
            _ObservationEvidence: The reconstructed LLM response.

        Raises:
            NonReplayableObservationError: If referenced evidence is missing, modified, or unsupported.
        """
        payload = observation.payload
        if isinstance(payload, ToolEventsObservationPayload):
            return payload
        pieces = await self._memory.get_message_pieces_async(prompt_ids=list(observation.evidence_message_piece_ids))
        pieces_by_id = {piece.id: piece for piece in pieces}
        stored_content = await _load_content_evidence_async(
            memory=self._memory, content_id=observation.scorable_content_id
        )
        try:
            observation.validate_evidence(
                message_pieces=pieces_by_id,
                stored_content=stored_content,
            )
        except ValueError as error:
            raise NonReplayableObservationError(str(error)) from error
        return Message(message_pieces=[pieces_by_id[piece_id] for piece_id in observation.response_message_piece_ids])
