# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend-neutral evidence about how a program behaved when an isolated sandbox ran it."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: A trusted runtime profile name, such as ``python-3.12-restricted``. It names a sandbox
#: configuration the caller allowlisted; it is never an image name or a command.
RuntimeProfileName = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]


class ProgramInvocation(BaseModel):
    """
    One bounded invocation of a program: its arguments and standard input.

    An invocation is a test stimulus. It carries no expected result, so a source that runs it
    cannot see which output passes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    arguments: tuple[Annotated[str, Field(pattern=r"^[^\x00]*$")], ...] = ()
    stdin: str | None = None


class ProgramExecutionEvent(BaseModel):
    """
    What one invocation did when the sandbox ran it.

    The event records enough to audit the result and to judge it again. It does not make
    execution reproducible: a program can read the clock, use randomness, or depend on its
    runtime. Replay judges this stored evidence again without running the program.

    ``stdout`` and ``stderr`` hold at most the source's configured limit. Each stream has its
    own truncation marker, so a matcher never treats a missing suffix as proof of absence.
    ``exit_code`` is ``None`` when the program did not exit on its own, for example because the
    sandbox stopped it at its time or resource limit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_index: int = Field(ge=0)
    runtime_profile: RuntimeProfileName
    runtime_image_digest: str = Field(min_length=1)
    resolved_command: tuple[str, ...] = Field(min_length=1)
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    resource_limit_exceeded: bool = False
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_exit_state(self) -> ProgramExecutionEvent:
        """
        Keep the exit state consistent with how the run ended.

        Returns:
            ProgramExecutionEvent: The validated event.

        Raises:
            ValueError: If a run the sandbox stopped also reports an exit code of its own.
        """
        if self.timed_out and self.exit_code is not None:
            raise ValueError("A timed-out invocation did not exit on its own and has no exit code.")
        return self

    @property
    def output_truncated(self) -> bool:
        """Whether either output stream was cut at the retention limit."""
        return self.stdout_truncated or self.stderr_truncated
