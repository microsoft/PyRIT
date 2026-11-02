# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire how a program behaves by asking an injected sandbox to run it."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from pyrit._code_execution import SandboxExecutionError
from pyrit.memory import CentralMemory
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    MessageScorable,
    Observation,
    ProgramExecutionObservationPayload,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver

if TYPE_CHECKING:
    from pyrit._code_execution import SandboxClient
    from pyrit.models import ProgramExecutionScorable
    from pyrit.models.code_execution import _ProgramExecutionEvent

logger = logging.getLogger(__name__)


class _NoProgramError(Exception):
    """The named evidence holds nothing that can be submitted as a program."""


class ProgramExecutionSource:
    """
    Submit verbatim program text and invocation inputs to an injected ``SandboxClient``.

    Expected results never reach the source. Memory is needed only to resolve stored program
    references. A message must contain exactly one text piece; blocked or failed responses
    leave evidence unavailable. Code is not extracted from prose.

    Each acquisition runs afresh and returns a new observation. Program failures are execution
    evidence; infrastructure failures are acquisition errors. A run that stops after some
    invocations leaves partial evidence. Streams retain at most ``max_output_chars`` characters
    with truncation flags, and cleanup warnings remain in ``sandbox_warnings`` metadata.
    """

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

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the acquisition version, output limit, and sandbox configuration.

        Returns:
            ComponentIdentifier: The nonsecret source configuration.
        """
        return ComponentIdentifier.of(
            self,
            params={"acquisition_version": 3, "max_output_chars": self._max_output_chars},
            children={"sandbox": self._sandbox.get_identifier()},
        )

    async def acquire_async(self, *, scorable: ProgramExecutionScorable) -> Observation:
        """
        Run every invocation afresh and return what the program did.

        Args:
            scorable (ProgramExecutionScorable): The program, runtime profile, and invocations.

        Returns:
            Observation: The execution evidence, with its acquisition state.

        Raises:
            ValueError: If the runtime profile is not one the sandbox offers, the sandbox
                returns events for invocations it was not given, or the program reference
                names missing or several pieces.
        """
        if scorable.runtime_profile_name not in self._sandbox.get_runtime_profile_names():
            raise ValueError(
                f"Runtime profile name {scorable.runtime_profile_name!r} is not offered by the sandbox; "
                f"available names: {sorted(self._sandbox.get_runtime_profile_names())}."
            )
        try:
            program = await self._resolve_program_async(scorable=scorable)
        except _NoProgramError as error:
            return self._observation(scorable=scorable, acquisition=Acquisition.UNAVAILABLE, reasons=(str(error),))
        try:
            run = await self._sandbox.execute_async(
                program=program,
                runtime_profile_name=scorable.runtime_profile_name,
                invocations=scorable.invocations,
            )
        except SandboxExecutionError as error:
            logger.warning("Sandbox could not run program under %s: %s", scorable.runtime_profile_name, error)
            return self._observation(
                scorable=scorable, acquisition=Acquisition.ERROR, reasons=(f"sandbox error: {error}",)
            )
        events = tuple(self._bounded(event) for event in run.events)
        if any(event.invocation_index >= len(scorable.invocations) for event in events):
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

    def _bounded(self, event: _ProgramExecutionEvent) -> _ProgramExecutionEvent:
        """
        Cut each stream to the retention limit, keeping any truncation the sandbox reported.

        Returns:
            _ProgramExecutionEvent: The event as it is retained.
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
        events: tuple[_ProgramExecutionEvent, ...] = (),
        program_sha256: str | None = None,
        reasons: tuple[str, ...] = (),
    ) -> Observation:
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            payload=ProgramExecutionObservationPayload(
                program_sha256=program_sha256,
                events=events,
                reasons=reasons,
            ),
        )
