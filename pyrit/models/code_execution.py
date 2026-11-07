# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private shared data for experimental code execution; these contracts may change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: A trusted runtime profile name, such as ``python-3.12-restricted``. It names a sandbox
#: configuration the caller allowlisted; it is never an image name or a command.
_RuntimeProfileName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class _ProgramInvocation(BaseModel):
    """The arguments and standard input for one invocation of a program."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arguments: tuple[Annotated[str, Field(pattern=r"^[^\x00]*$")], ...] = ()
    stdin: str | None = None


class _ProgramExecutionEvent(BaseModel):
    """
    What one invocation did when the sandbox ran it.

    An event records an outcome, not a guarantee that the next execution will produce it.
    A program can read the clock, use randomness, or depend on its runtime.

    Each output stream records whether its retained text is incomplete, including interrupted capture.
    ``runtime_image_digest`` and ``resolved_command`` are optional provenance: a backend that cannot
    expose them leaves them unset.
    ``exit_code`` is ``None`` when the program did not exit on its own, for example because the
    sandbox stopped it at its time or resource limit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_index: int = Field(ge=0)
    runtime_profile_name: _RuntimeProfileName
    runtime_image_digest: str | None = Field(default=None, min_length=1)
    resolved_command: tuple[str, ...] | None = Field(default=None, min_length=1)
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    resource_limit_exceeded: bool = False
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_exit_state(self) -> _ProgramExecutionEvent:
        """
        Keep the exit state consistent with how the run ended.

        Returns:
            _ProgramExecutionEvent: The validated event.

        Raises:
            ValueError: If a run the sandbox stopped also reports an exit code of its own.
        """
        if self.timed_out and self.exit_code is not None:
            raise ValueError("A timed-out invocation did not exit on its own and has no exit code.")
        return self

    @property
    def output_truncated(self) -> bool:
        """Whether either output stream could not be retained in full."""
        return self.stdout_truncated or self.stderr_truncated


@dataclass(frozen=True)
class _SandboxRun:
    """
    Results from one request to execute a program's invocations.

    ``events`` holds one event per invocation that ran, in invocation order. ``reasons`` explains
    why any invocations did not run. ``warnings`` reports cleanup problems that do not invalidate
    the results already collected.
    """

    events: tuple[_ProgramExecutionEvent, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
