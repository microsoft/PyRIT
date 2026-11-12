# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The experimental contract for connecting to an isolated code-execution backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pyrit._code_execution.capabilities import SandboxCapabilities
    from pyrit.models import ComponentIdentifier
    from pyrit.models.code_execution import _ProgramInvocation, _RuntimeProfileName, _SandboxRun


class SandboxExecutionError(Exception):
    """
    The sandbox could not run the program.

    This is an infrastructure failure, such as an unreachable service, a failed
    authentication, or an environment that could not be provisioned. It is never raised for a
    program that fails, times out, or exceeds a limit: those are observed behavior.
    """


class SandboxInputError(ValueError):
    """The request does not fit the sandbox, so nothing ran. The caller, or a model, can correct it."""


@runtime_checkable
class SandboxClient(Protocol):
    """
    Connect to a caller-configured backend that runs code in isolation.

    Each invocation runs in a fresh environment. The client requests execution and cleanup,
    and waits for cleanup before a cancelled call returns. It does not set up the backend
    service, build images, or retain environments between calls. It declares its runtimes and
    limits in ``get_capabilities`` and checks every request against them before anything runs.
    A runtime profile is a trusted name the client maps to its own configuration; the program
    cannot choose an image or a command. The caller supplies the running backend and trusted
    runtimes, and decides how to use the returned results.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret client configuration."""
        ...

    def get_runtime_profile_names(self) -> frozenset[_RuntimeProfileName]:
        """Return the names that select this client's configured runtimes."""
        ...

    def get_capabilities(self) -> SandboxCapabilities:
        """Return the client's runtimes and the limits each call must fit."""
        ...

    async def execute_async(
        self,
        *,
        program: str,
        runtime_profile_name: _RuntimeProfileName,
        invocations: tuple[_ProgramInvocation, ...],
        timeout_seconds: float | None = None,
    ) -> _SandboxRun:
        """
        Run each invocation of the program once, in order, in a fresh isolated environment.

        Args:
            program (str): The code to run, without extracting it from surrounding prose.
            runtime_profile_name (_RuntimeProfileName): One of the names returned by
                ``get_runtime_profile_names``. The client owns the configuration selected by this name.
            invocations (tuple[_ProgramInvocation, ...]): A nonempty sequence of arguments and input.
            timeout_seconds (float | None): The time limit of each invocation, at most the declared
                ``max_timeout_seconds``. Defaults to the declared ``default_timeout_seconds``.

        Returns:
            _SandboxRun: The invocation results and any execution gaps or cleanup warnings.

        Raises:
            SandboxInputError: If the request does not fit the declared capabilities; nothing ran.
            SandboxExecutionError: If the sandbox could not run the program at all.
        """
        ...
