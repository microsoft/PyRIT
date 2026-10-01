# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic invocation scoring over durable, correlated trace evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    MessageScorable,
    Observation,
    Score,
    ScoreStatus,
    ToolEventsObservationPayload,
    ToolsCalled,
    TraceScorable,
)
from pyrit.score.observation.execution import NonReplayableObservationError, _collect_observation
from pyrit.score.observation.message_trace_resolver import resolve_message_trace_scope
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, Scorable, ScoringExpectation
    from pyrit.score.observation.execution import _ObservationEvidence
    from pyrit.score.observation.observation_source import ObservationSource

_UNLINKED_REQUESTS_REASON = (
    "One or more requests in this conversation carry no trace link, so tool calls outside the "
    "correlated traces cannot be ruled out."
)


def match_tools_called(
    *, condition: ToolsCalled, payload: ToolEventsObservationPayload, acquisition: Acquisition
) -> bool | None:
    """
    Match execution names without I/O, preserving unknown absence.

    Returns:
        bool | None: True for observed calls, false for complete absence, otherwise None.
    """
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        return None
    observed = {event.name for event in payload.events}
    if all(tool.name in observed for tool in condition.tools):
        return True
    return False if acquisition is Acquisition.COMPLETE and payload.coverage.complete else None


class OtelToolCallScorer(TrueFalseScorer):
    """Score actual tool invocations, not requested calls or response claims."""

    CONDITION_TYPE = ToolsCalled

    def __init__(self, *, source: ObservationSource[TraceScorable]) -> None:
        """Initialize with a condition-independent, caller-configured trace source."""
        super().__init__()
        self._source = source

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"matching_version": 1, "message_scope_version": 2},
            children={"source": self._source.get_identifier()},
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        if not isinstance(scorable, (MessageScorable, TraceScorable)):
            raise TypeError("OtelToolCallScorer requires a MessageScorable or an explicit TraceScorable.")
        scope, correlation_complete = (
            resolve_message_trace_scope(scorable=scorable, memory=self._memory)
            if isinstance(scorable, MessageScorable)
            else (scorable, True)
        )
        if scope is None:
            return [
                self._build_undetermined_score(
                    rationale="Trace evidence is unavailable: no request trace links were found.",
                    scorable=scorable,
                    message_piece_id=self._piece_id_from_scorable(scorable),
                )
            ]
        observation = await self._source.acquire_async(scorable=scope)
        if observation.scorable != scope:
            raise ValueError("Trace source changed the caller's evidence anchor.")
        if not isinstance(observation.payload, ToolEventsObservationPayload) or observation.payload.scope != scope:
            raise ValueError("Trace source returned incompatible evidence or scope.")
        _collect_observation(observation)
        scores = self._score_observation(observation=observation, evidence=observation.payload, expectation=expectation)
        for score in scores:
            score.scorable = scorable
            score.message_piece_id = self._piece_id_from_scorable(scorable)
            if not correlation_complete and score.score_value == "false":
                score.score_value = None
                score.status = ScoreStatus.UNDETERMINED
                score.score_rationale = f"{score.score_rationale} {_UNLINKED_REQUESTS_REASON}"
        return scores

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        if not isinstance(evidence, ToolEventsObservationPayload):
            raise NonReplayableObservationError("Tool-call scoring requires a stored tool-event observation.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ToolsCalled)
        value = match_tools_called(condition=condition, payload=evidence, acquisition=observation.acquisition)
        names = ", ".join(tool.name for tool in condition.tools)
        rationale = (
            f"Observed execution of all required tools: {names}."
            if value is True
            else f"Complete trace evidence does not contain all required tools: {names}."
            if value is False
            else f"Trace evidence cannot establish invocation of all required tools: {names}."
        )
        return [
            Score(
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_rationale=rationale,
                score_value_description="Tool invocation; successful completion is not required.",
                scorer_class_identifier=self.get_identifier(),
                scorable=observation.scorable,
                message_piece_id=self._piece_id_from_scorable(observation.scorable),
                observation_ids=[observation.id],
            )
        ]
