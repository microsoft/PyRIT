# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score whether a program behaves as expected, over evidence from an isolated execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    Contains,
    ContentEntryScorable,
    ContentScorable,
    MessageScorable,
    ProgramBehaves,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    Score,
    ScoreStatus,
)
from pyrit.score.observation.execution import NonReplayableObservationError, _collect_observation
from pyrit.score.text_matching import match_text
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.models import (
        ComponentIdentifier,
        Observation,
        ProgramResult,
        Scorable,
        ScoringExpectation,
        TextMatcher,
    )
    from pyrit.models.code_execution import _ProgramExecutionEvent
    from pyrit.score.observation.execution import _ObservationEvidence
    from pyrit.score.observation.observation_source import ObservationSource


@dataclass(frozen=True)
class _Evaluation:
    value: bool | None
    rationale: str


def _stream_matches(*, matcher: TextMatcher, text: str, truncated: bool) -> bool | None:
    """
    Match one output stream, treating a cut stream as unknown unless a prefix proves a match.

    Returns:
        bool | None: Whether the stream matches, or None when the retained text cannot tell.
    """
    if truncated and not isinstance(matcher, Contains):
        return None
    if match_text(matcher=matcher, text=text):
        return True
    return None if truncated else False


def _evaluate_case(*, expected: ProgramResult, event: _ProgramExecutionEvent | None) -> _Evaluation:
    """
    Evaluate one case once, keeping its explanation with the decision.

    Returns:
        _Evaluation: The case's verdict and explanation.
    """
    if event is None:
        return _Evaluation(value=None, rationale="did not run")
    if event.timed_out:
        return _Evaluation(value=False, rationale="timed out")
    if event.resource_limit_exceeded:
        return _Evaluation(value=False, rationale="exceeded a resource limit")
    if event.exit_code is None:
        return _Evaluation(value=None, rationale="did not report an exit code")
    if expected.exit_code is not None and event.exit_code != expected.exit_code:
        return _Evaluation(value=False, rationale=f"exited with {event.exit_code}, expected {expected.exit_code}")
    checks: list[bool | None] = []
    if expected.stdout is not None:
        checks.append(_stream_matches(matcher=expected.stdout, text=event.stdout, truncated=event.stdout_truncated))
    if expected.stderr is not None:
        checks.append(_stream_matches(matcher=expected.stderr, text=event.stderr, truncated=event.stderr_truncated))
    if False in checks:
        return _Evaluation(value=False, rationale="output did not match")
    if None in checks:
        return _Evaluation(value=None, rationale="output was cut before the check could be settled")
    return _Evaluation(value=True, rationale="")


def _evaluate_program(
    *, condition: ProgramBehaves, payload: ProgramExecutionObservationPayload, acquisition: Acquisition
) -> _Evaluation:
    """
    Index evidence once and reuse case evaluations for the verdict and rationale.

    Returns:
        _Evaluation: The program's verdict and explanation.
    """
    profile_name = condition.runtime_profile_name
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        gaps = ", ".join(payload.reasons) or "no execution evidence"
        return _Evaluation(
            value=None, rationale=f"The program's behavior under {profile_name} could not be observed ({gaps})."
        )
    events = {event.invocation_index: event for event in payload.events}
    evaluations = [
        _evaluate_case(expected=case.expected, event=events.get(index)) for index, case in enumerate(condition.cases)
    ]
    if all(evaluation.value is True for evaluation in evaluations):
        return _Evaluation(
            value=True, rationale=f"All {len(evaluations)} case(s) produced the expected result under {profile_name}."
        )
    summary = "; ".join(
        f"case {index} {evaluation.rationale}"
        for index, evaluation in enumerate(evaluations)
        if evaluation.value is not True
    )
    if any(evaluation.value is False for evaluation in evaluations):
        return _Evaluation(
            value=False, rationale=f"The program did not produce the expected result under {profile_name}: {summary}."
        )
    gaps = ", ".join(payload.reasons)
    suffix = f" ({gaps})" if gaps else ""
    return _Evaluation(
        value=None,
        rationale=f"The program's behavior under {profile_name} could not be fully established: {summary}{suffix}.",
    )


class ProgramExecutionScorer(TrueFalseScorer):
    """
    Compare execution evidence with the expected results in a ``ProgramBehaves`` condition.

    The source receives verbatim program text, a runtime profile name, and invocation inputs,
    but never expected results. Each case checks its expected exit and output; a timeout or
    resource limit fails the case. All cases passing yields true, any proven failure yields
    false, and insufficient evidence leaves the verdict undetermined.

    Every scoring call runs afresh. ``score_observation_async`` replays saved evidence without
    executing the program.
    """

    CONDITION_TYPE = ProgramBehaves

    def __init__(self, *, source: ObservationSource[ProgramExecutionScorable]) -> None:
        """
        Initialize with a condition-independent, caller-configured execution source.

        Args:
            source (ObservationSource[ProgramExecutionScorable]): Runs the programs scorables name.
        """
        super().__init__()
        self._source = source

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"matching_version": 1},
            children={"source": self._source.get_identifier()},
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        condition = self._get_required_condition(expectation=expectation, condition_type=ProgramBehaves)
        if isinstance(scorable, ProgramExecutionScorable):
            if (scorable.runtime_profile_name, scorable.invocations) != (
                condition.runtime_profile_name,
                condition.invocations,
            ):
                raise ValueError(
                    "A ProgramExecutionScorable must run the runtime profile and invocations of the "
                    "ProgramBehaves condition."
                )
            execution = scorable
        elif isinstance(scorable, (MessageScorable, ContentScorable, ContentEntryScorable)):
            execution = ProgramExecutionScorable(
                program=scorable,
                runtime_profile_name=condition.runtime_profile_name,
                invocations=condition.invocations,
            )
        else:
            raise TypeError(
                "ProgramExecutionScorer requires a MessageScorable, a ContentScorable, a ContentEntryScorable, "
                "or a ProgramExecutionScorable."
            )

        observation = await self._source.acquire_async(scorable=execution)
        if observation.scorable != execution:
            raise ValueError("Program execution source changed the caller's evidence anchor.")
        if not isinstance(observation.payload, ProgramExecutionObservationPayload):
            raise ValueError("Program execution source returned incompatible evidence.")
        _collect_observation(observation)
        scores = self._score_observation(observation=observation, evidence=observation.payload, expectation=expectation)
        for score in scores:
            score.scorable = scorable
            score.message_piece_id = self._piece_id_from_scorable(scorable)
        return scores

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        if not isinstance(evidence, ProgramExecutionObservationPayload) or not isinstance(
            observation.scorable, ProgramExecutionScorable
        ):
            raise NonReplayableObservationError("Program behavior scoring requires a stored program execution.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ProgramBehaves)
        execution = observation.scorable
        if (execution.runtime_profile_name, execution.invocations) != (
            condition.runtime_profile_name,
            condition.invocations,
        ):
            raise NonReplayableObservationError(
                "The stored execution ran different invocations or a different runtime profile than the "
                "ProgramBehaves condition."
            )
        evaluation = _evaluate_program(condition=condition, payload=evidence, acquisition=observation.acquisition)
        return [
            Score(
                score_value=None if evaluation.value is None else str(evaluation.value).lower(),
                status=ScoreStatus.UNDETERMINED if evaluation.value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_rationale=evaluation.rationale,
                score_value_description="The program produced the expected result for every case.",
                scorer_class_identifier=self.get_identifier(),
                scorable=observation.scorable,
                message_piece_id=self._piece_id_from_scorable(observation.scorable),
                observation_ids=[observation.id],
            )
        ]
