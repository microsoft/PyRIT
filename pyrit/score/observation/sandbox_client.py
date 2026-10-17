# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The injected service that runs a program in a disposable, isolated environment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, ProgramExecutionEvent, ProgramInvocation


class SandboxExecutionError(Exception):
    """
    The sandbox could not run the program.

    This is an infrastructure failure, such as an unreachable service, a failed
    authentication, or an environment that could not be provisioned. It is never raised for a
    program that fails, times out, or exceeds a limit: those are observed behavior.
    """


@dataclass(frozen=True)
class SandboxRun:
    """
    The invocations a sandbox ran for one execution attempt.

    ``events`` holds one event for each invocation that ran, in case order. When the sandbox
    stopped before running every invocation, ``reasons`` says why, so the missing cases stay
    undetermined instead of failing. ``warnings`` reports problems that do not invalidate the
    evidence collected, such as an environment the sandbox could not destroy afterwards.
    """

    events: tuple[ProgramExecutionEvent, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class SandboxClient(Protocol):
    """
    Run a program under an allowlisted runtime profile in a disposable environment.

    The client owns the environment: it creates it for the attempt and destroys it afterwards.
    It never retains an environment between attempts, builds runtime images, or starts a daemon.
    A runtime profile is a trusted name the client maps to its own configuration; the program
    cannot choose an image or a command.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret client configuration."""
        ...

    def get_runtime_profiles(self) -> frozenset[str]:
        """Return the runtime profile names this client is configured to run."""
        ...

    async def execute_async(
        self, *, program: str, runtime_profile: str, invocations: tuple[ProgramInvocation, ...]
    ) -> SandboxRun:
        """
        Run each invocation of the program once, in order, in a fresh isolated environment.

        Raises:
            SandboxExecutionError: If the sandbox could not run the program at all.
        """
        ...
