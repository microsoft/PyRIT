# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score whether a program behaves as expected, over evidence from an isolated execution."""

from __future__ import annotations

import uuid
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
        ProgramExecutionEvent,
        ProgramResult,
        Scorable,
        ScoringExpectation,
        TextMatcher,
    )
    from pyrit.score.observation.execution import _ObservationEvidence
    from pyrit.score.observation.observation_source import ObservationSource


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


def match_case(*, expected: ProgramResult, event: ProgramExecutionEvent) -> bool | None:
    """
    Decide whether one invocation produced its expected result.

    A run the sandbox stopped at its time or resource limit fails, because the program did not
    finish. Otherwise the exit code and each stream that has a matcher must all agree.

    Args:
        expected (ProgramResult): The expected result.
        event (ProgramExecutionEvent): What the invocation did.

    Returns:
        bool | None: True when every check passes, False when one fails, None when a cut
        stream leaves a check open.
    """
    if event.timed_out or event.resource_limit_exceeded:
        return False
    if event.exit_code is None:
        return None
    if expected.exit_code is not None and event.exit_code != expected.exit_code:
        return False
    checks: list[bool | None] = []
    if expected.stdout is not None:
        checks.append(_stream_matches(matcher=expected.stdout, text=event.stdout, truncated=event.stdout_truncated))
    if expected.stderr is not None:
        checks.append(_stream_matches(matcher=expected.stderr, text=event.stderr, truncated=event.stderr_truncated))
    if False in checks:
        return False
    return None if None in checks else True


def match_program_behaves(
    *, condition: ProgramBehaves, payload: ProgramExecutionObservationPayload, acquisition: Acquisition
) -> bool | None:
    """
    Match every case against the execution evidence, without I/O.

    One failing case is enough for a false verdict, even when other cases did not run. A true
    verdict needs every case to have run and passed.

    Args:
        condition (ProgramBehaves): The cases and their expected results.
        payload (ProgramExecutionObservationPayload): The acquired execution evidence.
        acquisition (Acquisition): Whether every invocation ran.

    Returns:
        bool | None: True when every case passes, False when one fails, otherwise None.
    """
    if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
        return None
    unknown = False
    for index, case in enumerate(condition.cases):
        event = payload.event_for(index)
        if event is None:
            unknown = True
            continue
        result = match_case(expected=case.expected, event=event)
        if result is False:
            return False
        unknown = unknown or result is None
    return None if unknown else True


class ProgramExecutionScorer(TrueFalseScorer):
    """
    Score whether a program behaves as expected, judged from what an isolated sandbox observed.

    The ``ProgramBehaves`` condition carries each case's stimulus and expected result. The scorer
    splits them: it builds a ``ProgramExecutionScorable`` from the program it is given, the
    runtime profile and the invocations, and sends only that to its source. Each expected result
    stays with the scorer, so the code that runs the program never learns which output passes.

    The program is the scored content, run verbatim: loose text, stored loose text, or a stored
    message with one text piece. Code is not extracted from surrounding prose, so a response that wraps code in
    explanation fails its cases rather than being guessed at.

    The verdict is true when every case passes and false when any case fails. A compile error,
    nonzero exit, wrong output, timeout, or resource limit is a failure the sandbox observed. A
    sandbox that could not run the program leaves the verdict undetermined, as does a case that
    did not run or a check that cut output cannot settle.

    Every scoring call is a new execution attempt. Scoring an explicit ``ProgramExecutionScorable``
    reuses that attempt's evidence when the source has already observed it, and the stored
    observation can be judged again without running the program.
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
            if (scorable.runtime_profile, scorable.invocations) != (condition.runtime_profile, condition.invocations):
                raise ValueError(
                    "A ProgramExecutionScorable must run the runtime profile and invocations of the "
                    "ProgramBehaves condition."
                )
            execution = scorable
        elif isinstance(scorable, (MessageScorable, ContentScorable, ContentEntryScorable)):
            execution = ProgramExecutionScorable(
                execution_id=uuid.uuid4(),
                program=scorable,
                runtime_profile=condition.runtime_profile,
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
        # A reused or shared attempt's evidence may already be stored. Memory links an identical
        # stored observation instead of inserting it again, atomically under its row lock.
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
        if (execution.runtime_profile, execution.invocations) != (condition.runtime_profile, condition.invocations):
            raise NonReplayableObservationError(
                "The stored execution ran different invocations or a different runtime profile than the "
                "ProgramBehaves condition."
            )
        value = match_program_behaves(condition=condition, payload=evidence, acquisition=observation.acquisition)
        return [
            Score(
                score_value=None if value is None else str(value).lower(),
                status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
                score_type="true_false",
                score_rationale=self._rationale(
                    condition=condition, payload=evidence, acquisition=observation.acquisition, value=value
                ),
                score_value_description="The program produced the expected result for every case.",
                scorer_class_identifier=self.get_identifier(),
                scorable=observation.scorable,
                message_piece_id=self._piece_id_from_scorable(observation.scorable),
                observation_ids=[observation.id],
            )
        ]

    @staticmethod
    def _rationale(
        *,
        condition: ProgramBehaves,
        payload: ProgramExecutionObservationPayload,
        acquisition: Acquisition,
        value: bool | None,
    ) -> str:
        """
        Explain the verdict in terms of the cases.

        Returns:
            str: The rationale.
        """
        total = len(condition.cases)
        profile = condition.runtime_profile
        if value is True:
            return f"All {total} case(s) produced the expected result under {profile}."
        if acquisition in (Acquisition.ERROR, Acquisition.UNAVAILABLE):
            gaps = ", ".join(payload.reasons) or "no execution evidence"
            return f"The program's behavior under {profile} could not be observed ({gaps})."
        outcomes = []
        for index, case in enumerate(condition.cases):
            event = payload.event_for(index)
            if event is None:
                outcomes.append(f"case {index} did not run")
                continue
            result = match_case(expected=case.expected, event=event)
            if result is True:
                continue
            if event.timed_out:
                detail = "timed out"
            elif event.resource_limit_exceeded:
                detail = "exceeded a resource limit"
            elif event.exit_code is None:
                detail = "did not report an exit code"
            elif case.expected.exit_code is not None and event.exit_code != case.expected.exit_code:
                detail = f"exited with {event.exit_code}, expected {case.expected.exit_code}"
            elif result is False:
                detail = "output did not match"
            else:
                detail = "output was cut before the check could be settled"
            outcomes.append(f"case {index} {detail}")
        summary = "; ".join(outcomes)
        if value is False:
            return f"The program did not produce the expected result under {profile}: {summary}."
        gaps = ", ".join(payload.reasons)
        suffix = f" ({gaps})" if gaps else ""
        return f"The program's behavior under {profile} could not be fully established: {summary}{suffix}."
