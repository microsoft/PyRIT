# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score whether a location holds written content, over acquired surface evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    Contains,
    ContentWritten,
    MessageScorable,
    Score,
    ScoreStatus,
    SurfaceObservationPayload,
    SurfaceScorable,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.observation.execution import NonReplayableObservationError, _collect_observation
from pyrit.score.text_matching import match_text
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Observation, Scorable, ScoringExpectation
    from pyrit.score.observation.execution import _ObservationEvidence
    from pyrit.score.observation.observation_source import ObservationSource


def match_content_written(
    *, condition: ContentWritten, payload: SurfaceObservationPayload, acquisition: Acquisition
) -> bool | None:
    """
    Match the content criterion without I/O, preserving unknown absence.

    A false verdict needs complete acquisition and every candidate's text retained in full.
    A retained prefix can prove a ``Contains`` match, but cannot establish an ``Equals`` or
    ``Regex`` verdict.

    Args:
        condition (ContentWritten): What counts as written.
        payload (SurfaceObservationPayload): The acquired snapshot.
        acquisition (Acquisition): Whether the snapshot covers every named location.

    Returns:
        bool | None: True for written content, false for complete absence, otherwise None.
    """
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        return None
    unknown = False
    for entry in payload.entries:
        if condition.matcher is None:
            if entry.size_bytes > 0:
                return True
            continue
        if entry.content is None or (entry.content_truncated and not isinstance(condition.matcher, Contains)):
            unknown = True
            continue
        if match_text(matcher=condition.matcher, text=entry.content):
            return True
        if entry.content_truncated:
            unknown = True
    if acquisition is Acquisition.COMPLETE and payload.coverage.complete and not unknown:
        return False
    return None


class FileWriteScorer(TrueFalseScorer):
    """
    Score whether a location holds written content, judged from surface evidence.

    The ``ContentWritten`` condition supplies the locator, and the source reads that location
    when the scorer runs. The verdict is true when a covered location holds the content, false
    only when the source read every covered location in full and none does, and undetermined
    otherwise.

    A location shows what it holds, not which run wrote it: content that was there before a
    run also counts, and a write that a run later removed is not observed. To attribute a write
    to one attempt, give each attempt its own location, or look for content that only that
    attempt can produce. This scorer reads one fixed source root; shared-root attempts must
    run one at a time and start with a clean root for write attribution. Given a message, the
    scorer judges only the latest message of its conversation, because a later turn can
    change the location.
    """

    CONDITION_TYPE = ContentWritten
    _LATER_TURN_RATIONALE = (
        "The conversation continued after this message, and a later turn can change the location. "
        "The location is read as it is now, so its state after this message is unknown."
    )

    def __init__(self, *, source: ObservationSource[SurfaceScorable]) -> None:
        """
        Initialize with a condition-independent, caller-configured surface source.

        Args:
            source (ObservationSource[SurfaceScorable]): Reads the locations scorables name.
        """
        super().__init__()
        self._source = source

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"matching_version": 2},
            children={"source": self._source.get_identifier()},
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        condition = self._get_required_condition(expectation=expectation, condition_type=ContentWritten)
        surface_scorable = SurfaceScorable(uri=condition.uri, match=condition.match)
        if isinstance(scorable, SurfaceScorable):
            if scorable != surface_scorable:
                raise ValueError("A SurfaceScorable must name the same location as the ContentWritten condition.")
        elif isinstance(scorable, MessageScorable):
            if await self._has_later_turn_async(scorable=scorable):
                return [
                    self._build_undetermined_score(
                        rationale=self._LATER_TURN_RATIONALE,
                        scorable=scorable,
                        message_piece_id=self._piece_id_from_scorable(scorable),
                    )
                ]
        else:
            raise TypeError("FileWriteScorer requires a MessageScorable or an explicit SurfaceScorable.")

        observation = await self._source.acquire_async(scorable=surface_scorable)
        if observation.scorable != surface_scorable:
            raise ValueError("Surface source changed the caller's evidence anchor.")
        if (
            not isinstance(observation.payload, SurfaceObservationPayload)
            or observation.payload.scope != surface_scorable
        ):
            raise ValueError("Surface source returned incompatible evidence or scope.")
        _collect_observation(observation)
        scores = self._score_observation(observation=observation, evidence=observation.payload, expectation=expectation)
        for score in scores:
            score.scorable = scorable
            score.message_piece_id = self._piece_id_from_scorable(scorable)
        return scores

    async def _has_later_turn_async(self, *, scorable: MessageScorable) -> bool:
        """
        Check whether the conversation continued after the scored message.

        Returns:
            bool: True when the conversation holds a message after the scored one.

        Raises:
            ValueError: If the reference names missing pieces, pieces that do not form one
                stored message, or a message outside a conversation.
        """
        message = await MessageScorableResolver().resolve_async(scorable=scorable, memory=self._memory)
        piece = message.message_pieces[0]
        if not piece.conversation_id:
            raise ValueError("File write scoring of a message requires a stored conversation.")
        conversation = await self._memory.get_message_pieces_async(conversation_id=piece.conversation_id)
        return any(other.sequence > piece.sequence for other in conversation)

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        if not isinstance(evidence, SurfaceObservationPayload):
            raise NonReplayableObservationError("File write scoring requires a stored surface observation.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ContentWritten)
        if (evidence.scope.uri, evidence.scope.match) != (condition.uri, condition.match):
            raise NonReplayableObservationError(
                "The stored surface evidence covers a different location than the ContentWritten condition."
            )
        value = match_content_written(condition=condition, payload=evidence, acquisition=observation.acquisition)
        target = f"{condition.uri} ({condition.match.value})"
        criterion = "nonempty content" if condition.matcher is None else "content matching the expected text criterion"
        if value is True:
            rationale = f"A location covered by {target} holds {criterion}."
        elif value is False:
            rationale = f"Every location covered by {target} was read in full; none holds {criterion}."
        else:
            gaps = ", ".join(evidence.coverage.reasons) or "content not retained in full"
            rationale = f"Surface evidence cannot establish whether {target} holds {criterion} ({gaps})."
        return [
            Score(
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_rationale=rationale,
                score_value_description="The named location held the content when it was read.",
                scorer_class_identifier=self.get_identifier(),
                scorable=observation.scorable,
                message_piece_id=self._piece_id_from_scorable(observation.scorable),
                observation_ids=[observation.id],
            )
        ]
