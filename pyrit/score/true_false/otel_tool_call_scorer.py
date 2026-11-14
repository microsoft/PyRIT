# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Deterministic invocation scoring over durable, correlated trace evidence."""

from __future__ import annotations

import logging
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
from pyrit.score.observation.message_trace_resolver import resolve_message_trace_scope_async
from pyrit.score.tool_argument_matching import describe_tool_requirement, match_tool_requirement
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from pyrit.models import ComponentIdentifier, Scorable, ScoringExpectation
    from pyrit.score.observation.execution import _ObservationEvidence
    from pyrit.score.observation.observation_source import ObservationSource

logger = logging.getLogger(__name__)

_NO_TRACE_ARGUMENTS_REASON = (
    "Trace evidence does not retain tool arguments, so it cannot confirm an argument requirement; "
    "use MessageToolCallScorer to match arguments."
)

_UNLINKED_REQUESTS_REASON = (
    "One or more requests in this conversation carry no trace link, so tool calls outside the "
    "correlated traces cannot be ruled out."
)


def match_tools_called(
    *, condition: ToolsCalled, payload: ToolEventsObservationPayload, acquisition: Acquisition
) -> bool | None:
    """
    Match executions without I/O, preserving unknown absence.

    A requirement with argument constraints needs an execution whose retained arguments match.
    Its absence can still be refuted from complete evidence, because no call means no matching call.

    Returns:
        bool | None: True for observed calls, false for complete absence, otherwise None. An
            exhaustive condition is also false when a tool it does not name was observed, and
            true only when complete evidence rules out every other tool.
    """
    return _match_tools_called(condition=condition, payload=payload, acquisition=acquisition)[0]


def _match_tools_called(
    *, condition: ToolsCalled, payload: ToolEventsObservationPayload, acquisition: Acquisition
) -> tuple[bool | None, bool]:
    """
    Match execution names and report whether the verdict rests on the absence of other calls.

    A verdict that rests on absence is only as good as the evidence is complete, so a caller
    that knows of uncorrelated requests must not keep it. A verdict from an observed call holds.

    Returns:
        tuple[bool | None, bool]: The verdict, and whether it depends on complete evidence.
    """
    observed = {event.name for event in payload.events}
    required = {tool.name for tool in condition.tools}
    if condition.exhaustive and not observed <= required:
        return False, False
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        return None, False
    calls: dict[str, list[Mapping[str, JsonValue] | None]] = {}
    for event in payload.events:
        # Trace events do not retain arguments, so they cannot confirm an argument requirement.
        calls.setdefault(event.name, []).append(None)
    verdicts = [match_tool_requirement(requirement=tool, calls=calls.get(tool.name, [])) for tool in condition.tools]
    complete = acquisition is Acquisition.COMPLETE and payload.coverage.complete
    if all(verdict is True for verdict in verdicts):
        if not condition.exhaustive:
            return True, False
        return (True, True) if complete else (None, False)
    return (False, True) if complete and any(verdict is False for verdict in verdicts) else (None, False)


def _rationale(*, condition: ToolsCalled, payload: ToolEventsObservationPayload, value: bool | None) -> str:
    """
    Explain a tool-call verdict in terms of the evidence that decided it.

    Returns:
        str: The rationale.
    """
    names = ", ".join(describe_tool_requirement(tool) for tool in condition.tools)
    required = {tool.name for tool in condition.tools}
    extra = sorted({event.name for event in payload.events} - required)
    expected = f"exactly the required tools: {names}" if names else "no tool execution"
    if value is True:
        return f"Complete trace evidence shows {expected}."
    if value is False and extra:
        return f"Observed execution of tools outside the required set: {', '.join(extra)}."
    if value is False:
        return f"Complete trace evidence does not contain all required tools: {names}."
    return f"Trace evidence cannot establish {expected}."


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
            raise TypeError("OtelToolCallScorer requires a MessageScorable or an explicit TraceScorable.")
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
        condition = self._get_required_condition(expectation=expectation, condition_type=ToolsCalled)
        _, rests_on_absence = _match_tools_called(
            condition=condition, payload=observation.payload, acquisition=observation.acquisition
        )
        for score in scores:
            score.scorable = scorable
            score.message_piece_id = self._piece_id_from_scorable(scorable)
            if not correlation_complete and rests_on_absence and not score.is_undetermined:
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
        names = ", ".join(describe_tool_requirement(tool) for tool in condition.tools)
        constrains_arguments = any(tool.arguments for tool in condition.tools)
        if constrains_arguments:
            logger.warning(
                "OtelToolCallScorer received a requirement with argument constraints, which trace evidence "
                "cannot confirm. Use MessageToolCallScorer to match arguments."
            )
        if condition.exhaustive:
            rationale = _rationale(condition=condition, payload=evidence, value=value)
        else:
            rationale = (
                f"Observed execution of all required tools: {names}."
                if value is True
                else f"Complete trace evidence does not contain all required tools: {names}."
                if value is False
                else f"Trace evidence cannot establish invocation of all required tools: {names}."
            )
        if value is None and constrains_arguments:
            rationale = f"{rationale} {_NO_TRACE_ARGUMENTS_REASON}"
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
