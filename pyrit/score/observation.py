# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, TypeAlias

from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
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
)
from pyrit.models.score.observation import (
    _content_scorable_digest,
    _message_piece_digest,
    _response_piece_digest,
)

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


class NonReplayableObservationError(ValueError):
    """Raised when stored evidence cannot be judged again by a scorer."""


if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator, Sequence


_ObservationEvidence: TypeAlias = Message


def _scored_evidence_digest(
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
    if isinstance(scorable, MessageScorable):
        if scored_message_piece is not None and scored_message_piece.id != scored_piece_id:
            raise NonReplayableObservationError(
                f"Prepared message piece {scored_message_piece.id} does not match scored piece {scored_piece_id}."
            )
        piece = scored_message_piece
        if piece is None:
            pieces = memory.get_message_pieces(prompt_ids=[scored_piece_id])
            piece = next((item for item in pieces if item.id == scored_piece_id), None)
        if piece is None:
            raise NonReplayableObservationError(f"Scored message piece {scored_piece_id} is missing.")
        if piece.converted_value_data_type in MEDIA_PATH_DATA_TYPES:
            return None
        return _message_piece_digest(piece, include_id=False)
    if isinstance(scorable, ContentScorable):
        if scorable.data_type in MEDIA_PATH_DATA_TYPES:
            return None
        return _content_scorable_digest(scorable)
    if isinstance(scorable, ContentEntryScorable):
        content = memory.get_scorable_content(content_ids=[scorable.content_id]).get(scorable.content_id)
        content_digest = memory.get_scorable_content_hashes(content_ids=[scorable.content_id]).get(scorable.content_id)
        if content is None or content.data_type != scorable.data_type or content_digest is None:
            raise NonReplayableObservationError(f"Scored content {scorable.content_id} is missing.")
        if content.data_type in MEDIA_PATH_DATA_TYPES:
            return None
        if _content_scorable_digest(content) != content_digest:
            raise NonReplayableObservationError(f"Scored content {scorable.content_id} was modified.")
        return content_digest
    raise NonReplayableObservationError(f"Scorable type {type(scorable).__name__} cannot replay judgment evidence.")


class _ObservationCollector:
    """Observations created during one root scoring operation."""

    def __init__(self) -> None:
        """Initialize an empty observation collection."""
        self._observations: dict[uuid.UUID, Observation] = {}

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


_CURRENT_OBSERVATION_COLLECTOR: ContextVar[_ObservationCollector | None] = ContextVar(
    "current_observation_collector",
    default=None,
)
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
def _observation_collection() -> Iterator[_ObservationCollector]:
    """
    Create the observation collector for one public scoring call.

    Yields:
        _ObservationCollector: The root operation's collector.
    """
    collector = _ObservationCollector()
    token = _CURRENT_OBSERVATION_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _CURRENT_OBSERVATION_COLLECTOR.reset(token)


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


def _replay_message_piece_id(observation: Observation) -> uuid.UUID | None:
    """
    Resolve the scored piece when the observation remains message-anchored.

    Returns:
        uuid.UUID | None: The canonical scored piece ID, if the anchor contains it.
    """
    scored_piece_id = observation.payload.scored_piece_id
    if isinstance(observation.scorable, MessageScorable) and scored_piece_id in observation.scorable.message_piece_ids:
        return scored_piece_id
    return None


class _ObservationEvidenceResolver:
    """Resolve managed observation references without calling their original source."""

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Initialize the resolver with the observation store."""
        self._memory = memory

    def resolve(self, *, observation: Observation) -> _ObservationEvidence:
        """
        Resolve an observation's managed response references.

        Returns:
            _ObservationEvidence: The reconstructed LLM response.

        Raises:
            NonReplayableObservationError: If a referenced message piece is missing.
        """
        payload = observation.payload
        scored_evidence_digest = _scored_evidence_digest(
            scorable=observation.scorable,
            scored_piece_id=payload.scored_piece_id,
            memory=self._memory,
        )
        if scored_evidence_digest != payload.scored_evidence_digest:
            raise NonReplayableObservationError(f"Observation {observation.id} references modified scored evidence.")
        pieces = self._memory.get_message_pieces(prompt_ids=list(payload.message_piece_ids))
        pieces_by_id = {str(piece.id): piece for piece in pieces}
        missing = [str(piece_id) for piece_id in payload.message_piece_ids if str(piece_id) not in pieces_by_id]
        if missing:
            raise NonReplayableObservationError(
                f"Observation {observation.id} references missing message pieces: {missing}."
            )
        ordered_pieces = [pieces_by_id[str(piece_id)] for piece_id in payload.message_piece_ids]
        modified = [
            str(piece.id)
            for piece, expected_digest in zip(ordered_pieces, payload.message_piece_digests, strict=True)
            if _response_piece_digest(piece, include_id=True) != expected_digest
        ]
        if modified:
            raise NonReplayableObservationError(
                f"Observation {observation.id} references modified message pieces: {modified}."
            )
        return Message(message_pieces=ordered_pieces)
