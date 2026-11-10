# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic invocation scoring over durable, correlated trace evidence."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    MessageScorable,
    Observation,
    Score,
    ScoreStatus,
    ToolEventsObservationPayload,
    ToolsCalled,
    ToolsCalledInOrder,
    TraceScorable,
)
from pyrit.score.observation.execution import NonReplayableObservationError, _collect_observation
from pyrit.score.observation.message_trace_resolver import resolve_message_trace_scope_async
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import ComponentIdentifier, Scorable, ScoringExpectation, ToolExecution
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


def _definitely_precedes(*, first: ToolExecution, second: ToolExecution, tolerance: timedelta) -> bool:
    """
    Check whether ``first`` finished before ``second`` started, by more than the clock tolerance.

    Returns:
        bool: True only for a finished attempt that ends strictly before the other starts.
    """
    return first.end_time is not None and first.end_time + tolerance < second.start_time


def _ordered_chain_exists(*, runs: Sequence[Sequence[ToolExecution]], tolerance: timedelta, definite: bool) -> bool:
    """
    Search for one attempt per tool that respects the required order.

    With ``definite``, every adjacent pair must be ordered beyond doubt. Otherwise a pair only
    must not be ordered the wrong way beyond doubt, so overlapping attempts remain possible.

    Returns:
        bool: Whether such a chain of attempts exists.
    """
    previous: ToolExecution | None = None
    for index, attempts in enumerate(runs):
        if previous is None:
            candidates = list(attempts)
        elif definite:
            candidates = [
                run for run in attempts if _definitely_precedes(first=previous, second=run, tolerance=tolerance)
            ]
        else:
            candidates = [
                run for run in attempts if not _definitely_precedes(first=run, second=previous, tolerance=tolerance)
            ]
        if definite and index < len(runs) - 1:
            # The next tool must start after this attempt ends, so the earliest finished end is best.
            candidates = [run for run in candidates if run.end_time is not None]
            if not candidates:
                return False
            previous = min(candidates, key=lambda run: run.end_time or run.start_time)
        else:
            # The next tool must not end before this attempt starts, so the earliest start is best.
            if not candidates:
                return False
            previous = min(candidates, key=lambda run: run.start_time)
    return True


def match_tools_called_in_order(
    *,
    condition: ToolsCalledInOrder,
    payload: ToolEventsObservationPayload,
    acquisition: Acquisition,
    clock_skew_tolerance: timedelta = timedelta(0),
) -> bool | None:
    """
    Match the relative order of execution attempts without I/O, preserving unknown order.

    An order is observed only when one attempt ends before the next tool's attempt starts, by
    more than ``clock_skew_tolerance``. Overlapping, unfinished, or near-simultaneous attempts
    establish no order. An observed order stands with partial evidence. A negative needs
    complete evidence in which every candidate order is contradicted or a tool never ran.

    Returns:
        bool | None: True for an observed order, false for a complete contradiction, otherwise None.
    """
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        return None
    runs = [[event for event in payload.events if event.name == tool.name] for tool in condition.tools]
    if _ordered_chain_exists(runs=runs, tolerance=clock_skew_tolerance, definite=True):
        return True
    if acquisition is not Acquisition.COMPLETE or not payload.coverage.complete:
        return None
    # A tool that never ran leaves no possible order, so complete evidence of its absence is false.
    return None if _ordered_chain_exists(runs=runs, tolerance=clock_skew_tolerance, definite=False) else False


class OtelToolCallScorer(TrueFalseScorer):
    """Score actual tool invocations, not requested calls or response claims."""

    SUPPORTED_SCORABLE_TYPES = (MessageScorable, TraceScorable)
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
            raise TypeError(f"{type(self).__name__} requires a MessageScorable or an explicit TraceScorable.")
        scope, correlation_complete = (
            await resolve_message_trace_scope_async(scorable=scorable, memory=self._memory)
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
            if not correlation_complete and not score.is_undetermined and score.get_value() is False:
                score.set_value(None)
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


class OtelToolCallOrderScorer(OtelToolCallScorer):
    """
    Score the relative order of actual tool invocations from the same evidence as ``OtelToolCallScorer``.

    The order comes from span timestamps. ``clock_skew_tolerance`` widens the band in which two
    attempts are treated as unordered, for traces whose spans come from different clocks.
    """

    CONDITION_TYPE = ToolsCalledInOrder

    def __init__(
        self, *, source: ObservationSource[TraceScorable], clock_skew_tolerance: timedelta = timedelta(0)
    ) -> None:
        """
        Initialize with a caller-configured trace source and a clock tolerance.

        Raises:
            ValueError: If the tolerance is negative or not finite.
        """
        seconds = clock_skew_tolerance.total_seconds()
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("clock_skew_tolerance must not be negative.")
        self._clock_skew_tolerance = clock_skew_tolerance
        super().__init__(source=source)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "matching_version": 1,
                "message_scope_version": 2,
                "clock_skew_tolerance_seconds": self._clock_skew_tolerance.total_seconds(),
            },
            children={"source": self._source.get_identifier()},
        )

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        if not isinstance(evidence, ToolEventsObservationPayload):
            raise NonReplayableObservationError("Tool-order scoring requires a stored tool-event observation.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ToolsCalledInOrder)
        value = match_tools_called_in_order(
            condition=condition,
            payload=evidence,
            acquisition=observation.acquisition,
            clock_skew_tolerance=self._clock_skew_tolerance,
        )
        names = " -> ".join(tool.name for tool in condition.tools)
        rationale = (
            f"Observed execution of the required tools in order: {names}."
            if value is True
            else f"Complete trace evidence contradicts the required tool order: {names}."
            if value is False
            else f"Trace evidence cannot establish the required tool order: {names}."
        )
        return [
            Score(
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_rationale=rationale,
                score_value_description="Tool invocation order; successful completion is not required.",
                scorer_class_identifier=self.get_identifier(),
                scorable=observation.scorable,
                message_piece_id=self._piece_id_from_scorable(observation.scorable),
                observation_ids=[observation.id],
            )
        ]
