# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score a response by running a fixed check program in the sandbox the model's code ran in."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pyrit._code_execution import SandboxStateKind, resolve_sandbox
from pyrit.models import (
    ContentEntryScorable,
    ContentScorable,
    MessageScorable,
    ProgramExecutionObservationPayload,
    ProgramExecutionScorable,
    Score,
    ScoreStatus,
)
from pyrit.score.observation.execution import NonReplayableObservationError, _collect_observation
from pyrit.score.observation.program_execution_source import ProgramExecutionSource
from pyrit.score.true_false.program_execution_scorer import _evaluate_program, _Evaluation
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit._code_execution import Sandbox
    from pyrit.models import (
        Acquisition,
        ComponentIdentifier,
        Observation,
        ProgramBehaves,
        Scorable,
        ScorableUnion,
        ScoringExpectation,
    )
    from pyrit.score.observation.execution import _ObservationEvidence


class SandboxCheckScorer(TrueFalseScorer):
    """
    Score a response by what a fixed check program finds in a sandbox, not by what the response says.

    After a turn, the check runs in the sandbox the model's code ran in, so it sees what that code left
    behind. The response's own text never runs. ``condition`` gives the check's runtime, its invocations,
    and the result each must produce: every case passing scores true, and a failing case scores false. A
    sandbox failure, or a check that times out or exceeds a resource limit, leaves the score undetermined,
    never false. The check's program and output are stored as an observation linked to the score.

    The verdict reflects the sandbox when the check runs, so every call runs the check again; stored
    verdicts are never reused. Callers own isolation: reset the sandbox between attempts, and don't share
    one sandbox across concurrent attempts. The check runs as the same account as the model's code, so it
    must not trust files the model could have written.

    Experimental: it is built on ``pyrit._code_execution``, which may change without a deprecation cycle.
    """

    SUPPORTED_SCORABLE_TYPES = (MessageScorable, ContentScorable, ContentEntryScorable)

    def __init__(
        self,
        *,
        sandbox: Sandbox | str,
        check_program: str,
        condition: ProgramBehaves,
        max_output_chars: int = 64_000,
    ) -> None:
        """
        Initialize with the check and the sandbox it runs in.

        Args:
            sandbox (Sandbox | str): The sandbox the model's code runs in, or its name in the
                ``SandboxRegistry``. Its state must persist between calls.
            check_program (str): The program that looks for the effect.
            condition (ProgramBehaves): The check's runtime, invocations and expected results.
            max_output_chars (int): The most characters of each output stream kept as evidence.

        Raises:
            ValueError: If the check is empty, the sandbox keeps no state between calls, or it does not
                offer the check's runtime.
        """
        if not check_program.strip():
            raise ValueError("check_program must not be empty.")
        resolved = resolve_sandbox(sandbox)
        if resolved.get_capabilities().state_kind is not SandboxStateKind.ENVIRONMENT:
            raise ValueError("A check needs a sandbox whose state persists, so it can see what the model's code did.")
        if condition.runtime_profile_name not in resolved.get_runtime_profile_names():
            raise ValueError(
                f"The sandbox does not offer runtime {condition.runtime_profile_name!r}; "
                f"available: {sorted(resolved.get_runtime_profile_names())}."
            )
        super().__init__()
        self._check_program = check_program
        self._condition = condition
        self._source = ProgramExecutionSource(sandbox=resolved, max_output_chars=max_output_chars)

    def find_matching_scores(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None = None,
    ) -> list[Score]:
        """
        Match no stored result: a check reads the sandbox as it is now, so an earlier verdict may be stale.

        Args:
            scores (Sequence[Score]): Candidate results.
            scorable (Scorable): The evidence a new evaluation would read.
            expectation (ScoringExpectation | None): The input a new evaluation would receive.

        Returns:
            list[Score]: Always empty.
        """
        self.prepare_expectation(expectation=expectation)
        return []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "check_sha256": hashlib.sha256(self._check_program.encode("utf-8")).hexdigest(),
                "condition": self._condition.model_dump(mode="json"),
            },
            children={"source": self._source.get_identifier()},
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        if not isinstance(scorable, (MessageScorable, ContentScorable, ContentEntryScorable)):
            raise TypeError("SandboxCheckScorer scores a MessageScorable, ContentScorable, or ContentEntryScorable.")
        check = ProgramExecutionScorable(
            program=ContentScorable(value=self._check_program),
            runtime_profile_name=self._condition.runtime_profile_name,
            invocations=self._condition.invocations,
        )
        observation = await self._source.acquire_async(scorable=check)
        if not isinstance(observation.payload, ProgramExecutionObservationPayload):
            raise ValueError("The program execution source returned incompatible evidence.")
        _collect_observation(observation)
        return [self._build_score(payload=observation.payload, observation=observation, anchor=scorable)]

    def _score_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Judge a stored run of this check again, without running it.

        Returns:
            list[Score]: The verdict on the stored output.

        Raises:
            NonReplayableObservationError: If the observation is not a run of this check.
        """
        check = observation.scorable
        if (
            not isinstance(evidence, ProgramExecutionObservationPayload)
            or not isinstance(check, ProgramExecutionScorable)
            or check.program != ContentScorable(value=self._check_program)
            or (check.runtime_profile_name, check.invocations)
            != (self._condition.runtime_profile_name, self._condition.invocations)
        ):
            raise NonReplayableObservationError("The observation is not a stored run of this check.")
        return [self._build_score(payload=evidence, observation=observation, anchor=check)]

    def _build_score(
        self, *, payload: ProgramExecutionObservationPayload, observation: Observation, anchor: ScorableUnion
    ) -> Score:
        evaluation = self._evaluate(payload=payload, acquisition=observation.acquisition)
        return Score(
            score_value=None if evaluation.value is None else str(evaluation.value).lower(),
            status=ScoreStatus.UNDETERMINED if evaluation.value is None else ScoreStatus.COMPLETE,
            score_type="true_false",
            score_rationale=f"Check program: {evaluation.rationale}",
            score_value_description="The check program found the expected effect in the sandbox.",
            scorer_class_identifier=self.get_identifier(),
            scorable=anchor,
            message_piece_id=self._piece_id_from_scorable(anchor),
            observation_ids=[observation.id],
        )

    def _evaluate(self, *, payload: ProgramExecutionObservationPayload, acquisition: Acquisition) -> _Evaluation:
        """
        Evaluate the check's output; a check that did not finish says nothing about the effect.

        Returns:
            _Evaluation: The verdict and its explanation.
        """
        unfinished = [event for event in payload.events if event.timed_out or event.resource_limit_exceeded]
        if unfinished:
            return _Evaluation(value=None, rationale="The check did not finish: it timed out or hit a resource limit.")
        return _evaluate_program(condition=self._condition, payload=payload, acquisition=acquisition)
