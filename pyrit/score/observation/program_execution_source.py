# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire how a program behaves by asking an injected sandbox to run it."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.memory import CentralMemory
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    MessageScorable,
    Observation,
    ProgramExecutionEvent,
    ProgramExecutionObservationPayload,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.observation.sandbox_client import SandboxExecutionError

if TYPE_CHECKING:
    from pyrit.models import ProgramExecutionScorable
    from pyrit.score.observation.sandbox_client import SandboxClient

logger = logging.getLogger(__name__)

# Namespace for the IDs of reusable execution evidence, derived from the source and the attempt.
_OBSERVATION_NAMESPACE = uuid.UUID("20acb8a7-a3ed-5df1-b292-83d20448a0fd")
_REUSABLE = (Acquisition.COMPLETE, Acquisition.PARTIAL)


class _NoProgramError(Exception):
    """The named evidence holds nothing that can be submitted as a program."""


@dataclass
class _InFlight:
    """One running acquisition and how many callers are waiting for it."""

    task: asyncio.Future[Observation]
    waiters: int = 0


class ProgramExecutionSource:
    """
    Run the program a ``ProgramExecutionScorable`` names, once per invocation, in a sandbox.

    The source receives only the derived scorable: the program, the runtime profile, and the
    invocations. It never sees expected results, so it can run the program but cannot tell
    which output passes. An injected ``SandboxClient`` owns the disposable environment; the
    source only submits the attempt and waits for it.

    The program is the named text, used verbatim. A message must name exactly one text piece;
    code is never extracted from surrounding prose. A message that holds a blocked or failed
    response has no program to run, so the evidence is unavailable.

    Program failure is not acquisition failure. A compile error, a nonzero exit, wrong output,
    a timeout, or an enforced resource limit is complete evidence that the program misbehaved.
    Only a sandbox that cannot run the program makes the acquisition an error, and only a
    sandbox that stops early leaves it partial, so broken infrastructure never looks like a
    program that failed its tests.

    Each stream keeps at most ``max_output_chars`` characters and records when it was cut.
    A completed or partial observation is reused when the same scorable is acquired again, and
    concurrent callers for one attempt share a single run, so one execution attempt is observed
    once; a new ``execution_id`` runs the program again. The source holds recent observations,
    and its evidence for an attempt always has the same ID, derived from the source
    configuration and the attempt. Once memory stores that observation, as scoring does, a
    later acquisition loads it instead of running the program, however many attempts the
    source has seen since; every stored score therefore judges the attempt's one stored run.
    Evidence that was never stored and has left the cache runs again under the same ID, and
    memory refuses a second, different run of an attempt that already has stored evidence,
    so contradicting results are never stored for one attempt. Acquisition reads memory, so
    the central memory instance must be set.
    Problems the sandbox reports after the evidence was collected, such as a container it
    could not remove, are kept in the observation's ``sandbox_warnings`` metadata.
    """

    _MAX_REUSED_OBSERVATIONS = 256

    def __init__(self, *, sandbox: SandboxClient, max_output_chars: int = 64_000) -> None:
        """
        Initialize with the sandbox that runs submitted programs.

        Args:
            sandbox (SandboxClient): Runs programs under its allowlisted runtime profiles.
            max_output_chars (int): The most characters of each stream retained per invocation.

        Raises:
            ValueError: If the output limit is not positive.
        """
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive.")
        self._sandbox = sandbox
        self._max_output_chars = max_output_chars
        self._observed: OrderedDict[ProgramExecutionScorable, Observation] = OrderedDict()
        self._in_flight: dict[ProgramExecutionScorable, _InFlight] = {}

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the acquisition version, output limit, and sandbox configuration.

        Returns:
            ComponentIdentifier: The nonsecret source configuration.
        """
        return ComponentIdentifier.of(
            self,
            params={"acquisition_version": 1, "max_output_chars": self._max_output_chars},
            children={"sandbox": self._sandbox.get_identifier()},
        )

    async def acquire_async(self, *, scorable: ProgramExecutionScorable) -> Observation:
        """
        Run every invocation once and return what the program did.

        Args:
            scorable (ProgramExecutionScorable): The program, runtime profile, and invocations.

        Returns:
            Observation: The execution evidence, with its acquisition state.

        Raises:
            ValueError: If the runtime profile is not one the sandbox offers, the sandbox
                returns events for invocations it was not given, or the program reference
                names missing or several pieces.
        """
        reused = self._observed.get(scorable)
        if reused is not None:
            self._observed.move_to_end(scorable)
            return reused
        flight = self._in_flight.get(scorable)
        if flight is None:
            flight = _InFlight(task=asyncio.ensure_future(self._acquire_uncached_async(scorable=scorable)))
            self._in_flight[scorable] = flight
            flight.task.add_done_callback(lambda _task, key=scorable: self._in_flight.pop(key, None))
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        finally:
            flight.waiters -= 1
            if flight.waiters == 0 and not flight.task.done():
                # Every caller of this attempt was cancelled, so nobody needs its result. Wait for
                # the sandbox's cleanup before the caller's cancellation completes, so that
                # cancelling still means the environment is gone.
                flight.task.cancel()
                await asyncio.wait({flight.task})
                if not flight.task.cancelled() and flight.task.exception() is not None:
                    logger.debug("Cancelled execution %s ended with %r", scorable.execution_id, flight.task.exception())

    async def _acquire_uncached_async(self, *, scorable: ProgramExecutionScorable) -> Observation:
        """
        Run the attempt once; ``acquire_async`` shares this work between concurrent callers.

        Returns:
            Observation: The execution evidence, with its acquisition state.

        Raises:
            ValueError: If the runtime profile is not offered, the program reference is
                unusable, or the sandbox returns events for invocations it was not given.
        """
        if scorable.runtime_profile not in self._sandbox.get_runtime_profiles():
            raise ValueError(
                f"Runtime profile {scorable.runtime_profile!r} is not offered by the sandbox; "
                f"available profiles: {sorted(self._sandbox.get_runtime_profiles())}."
            )
        stored = await CentralMemory.get_memory_instance().get_observations_async(
            observation_ids=[self._attempt_observation_id(scorable)]
        )
        if stored:
            self._remember(scorable=scorable, observation=stored[0])
            return stored[0]
        try:
            program = await self._resolve_program_async(scorable=scorable)
        except _NoProgramError as error:
            return self._observation(scorable=scorable, acquisition=Acquisition.UNAVAILABLE, reasons=(str(error),))
        try:
            run = await self._sandbox.execute_async(
                program=program,
                runtime_profile=scorable.runtime_profile,
                invocations=scorable.invocations,
            )
        except SandboxExecutionError as error:
            logger.warning("Sandbox could not run program for execution %s: %s", scorable.execution_id, error)
            return self._observation(
                scorable=scorable, acquisition=Acquisition.ERROR, reasons=(f"sandbox error: {error}",)
            )
        events = tuple(self._bounded(event) for event in run.events)
        if any(event.case_index >= len(scorable.invocations) for event in events):
            raise ValueError("The sandbox returned an event for an invocation it was not given.")
        program_sha256 = hashlib.sha256(program.encode("utf-8")).hexdigest()
        if len(events) == len(scorable.invocations):
            observation = self._observation(
                scorable=scorable, acquisition=Acquisition.COMPLETE, events=events, program_sha256=program_sha256
            )
        else:
            reasons = run.reasons or ("the sandbox stopped before running every invocation",)
            acquisition = Acquisition.PARTIAL if events else Acquisition.ERROR
            observation = self._observation(
                scorable=scorable,
                acquisition=acquisition,
                events=events,
                program_sha256=program_sha256 if events else None,
                reasons=reasons,
            )
        if run.warnings:
            observation = observation.model_copy(update={"metadata": {"sandbox_warnings": "; ".join(run.warnings)}})
        if observation.acquisition in (Acquisition.COMPLETE, Acquisition.PARTIAL):
            self._remember(scorable=scorable, observation=observation)
        return observation

    async def _resolve_program_async(self, *, scorable: ProgramExecutionScorable) -> str:
        """
        Read the program text the scorable names.

        Returns:
            str: The code to submit, verbatim.

        Raises:
            _NoProgramError: If the named message holds a blocked or failed response.
            ValueError: If the reference cannot be resolved to one text piece, or stored
                content is missing.
        """
        reference = scorable.program
        if isinstance(reference, ContentScorable):
            return reference.value
        if isinstance(reference, MessageScorable) and len(reference.message_piece_ids) != 1:
            raise ValueError("A program message reference must name exactly one message piece.")
        message = await MessageScorableResolver().resolve_async(
            scorable=reference, memory=CentralMemory.get_memory_instance()
        )
        piece = message.get_piece()
        if piece.has_error():
            raise _NoProgramError(f"the program message holds a {piece.response_error} response, not a program")
        if piece.converted_value_data_type != "text":
            raise ValueError(f"A program must be text, not {piece.converted_value_data_type}.")
        return piece.converted_value

    def _bounded(self, event: ProgramExecutionEvent) -> ProgramExecutionEvent:
        """
        Cut each stream to the retention limit, keeping any truncation the sandbox reported.

        Returns:
            ProgramExecutionEvent: The event as it is retained.
        """
        limit = self._max_output_chars
        if len(event.stdout) <= limit and len(event.stderr) <= limit:
            return event
        return event.model_copy(
            update={
                "stdout": event.stdout[:limit],
                "stderr": event.stderr[:limit],
                "stdout_truncated": event.stdout_truncated or len(event.stdout) > limit,
                "stderr_truncated": event.stderr_truncated or len(event.stderr) > limit,
            }
        )

    def _observation(
        self,
        *,
        scorable: ProgramExecutionScorable,
        acquisition: Acquisition,
        events: tuple[ProgramExecutionEvent, ...] = (),
        program_sha256: str | None = None,
        reasons: tuple[str, ...] = (),
    ) -> Observation:
        # Only reusable evidence takes the attempt's ID; an error is retried as a new observation.
        observation_id = self._attempt_observation_id(scorable) if acquisition in _REUSABLE else uuid.uuid4()
        return Observation(
            id=observation_id,
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            payload=ProgramExecutionObservationPayload(
                program_sha256=program_sha256,
                events=events,
                reasons=reasons,
            ),
        )

    def _attempt_observation_id(self, scorable: ProgramExecutionScorable) -> uuid.UUID:
        """
        Derive the ID of an attempt's reusable evidence from the source configuration and the attempt.

        Returns:
            uuid.UUID: The same ID for the same source configuration and attempt.
        """
        return uuid.uuid5(_OBSERVATION_NAMESPACE, f"{self.get_identifier().hash}:{scorable.model_dump_json()}")

    def _remember(self, *, scorable: ProgramExecutionScorable, observation: Observation) -> None:
        self._observed[scorable] = observation
        while len(self._observed) > self._MAX_REUSED_OBSERVATIONS:
            self._observed.popitem(last=False)
