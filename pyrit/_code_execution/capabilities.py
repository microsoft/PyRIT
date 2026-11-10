# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""What a sandbox offers and its limits, and the check every request passes before anything runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit._code_execution.client import SandboxInputError
from pyrit.models.code_execution import _RuntimeProfileName  # noqa: TC001 - pydantic field type

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models.code_execution import _ProgramInvocation


class RuntimeProfile(BaseModel):
    """
    One trusted way a sandbox runs program text, such as Python or PowerShell.

    ``name`` is what callers pass; the client maps it to its own configuration. ``tool_name``
    optionally names the runtime for model tools (``run_<tool_name>``), such as ``powershell`` for a
    profile named ``windows-powershell``. ``description`` tells a model what the runtime is, such as
    "Python 3.12".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: _RuntimeProfileName
    tool_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]{1,60}$")
    description: str = Field(default="", max_length=200)


class SandboxCapabilities(BaseModel):
    """
    What a sandbox offers, and its limits. They stay the same for the client's lifetime.

    Limits apply to each call. ``max_input_bytes`` bounds the UTF-8 size of the program text, its
    standard input and its arguments together; ``max_arguments_bytes``, when set, also bounds the
    arguments alone, for backends that pass them on a command line. ``max_output_bytes`` is the most
    output of each stream the sandbox returns before marking it truncated. Timeouts are in seconds.
    ``description`` tells a model where its code runs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_profiles: tuple[RuntimeProfile, ...] = Field(min_length=1)
    description: str = Field(default="an isolated sandbox", min_length=1, max_length=500)
    max_input_bytes: int = Field(default=65_536, ge=1)
    max_arguments_bytes: int | None = Field(default=None, ge=1)
    max_output_bytes: int = Field(default=32_768, ge=1)
    max_timeout_seconds: float = Field(default=120.0, gt=0)
    default_timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _validate_consistency(self) -> SandboxCapabilities:
        """
        Reject capabilities that contradict each other.

        Returns:
            SandboxCapabilities: The validated capabilities.

        Raises:
            ValueError: If profile names repeat or the default timeout exceeds the limit.
        """
        names = [profile.name for profile in self.runtime_profiles]
        if len(set(names)) != len(names):
            raise ValueError("Runtime profile names must be unique.")
        if self.default_timeout_seconds > self.max_timeout_seconds:
            raise ValueError("default_timeout_seconds cannot exceed max_timeout_seconds.")
        return self

    def get_runtime_profile_names(self) -> frozenset[_RuntimeProfileName]:
        """
        Return the names that select the sandbox's runtimes.

        Returns:
            frozenset[_RuntimeProfileName]: The runtime profile names.
        """
        return frozenset(profile.name for profile in self.runtime_profiles)

    def validate_request(
        self,
        *,
        program: str,
        runtime_profile_name: str,
        invocations: Sequence[_ProgramInvocation],
        timeout_seconds: float | None = None,
    ) -> float:
        """
        Check a request against these runtimes and limits, without running anything.

        Args:
            program (str): The program text.
            runtime_profile_name (str): The runtime to run it in.
            invocations (Sequence[_ProgramInvocation]): The arguments and input of each run.
            timeout_seconds (float | None): The time limit of each run. Defaults to ``default_timeout_seconds``.

        Returns:
            float: The time limit each run gets.

        Raises:
            SandboxInputError: If the request does not fit; the message says what to change.
        """
        if runtime_profile_name not in self.get_runtime_profile_names():
            available = sorted(self.get_runtime_profile_names())
            raise SandboxInputError(
                f"Runtime profile {runtime_profile_name!r} is not offered by this sandbox; available: {available}."
            )
        if not invocations:
            raise SandboxInputError("At least one program invocation is required.")
        program_bytes = _utf8_size(program, field="program")
        for invocation in invocations:
            arguments_bytes = sum(_utf8_size(argument, field="argument") for argument in invocation.arguments)
            if self.max_arguments_bytes is not None and arguments_bytes > self.max_arguments_bytes:
                raise SandboxInputError(
                    f"The arguments take {arguments_bytes} bytes; this sandbox accepts at most "
                    f"{self.max_arguments_bytes}. Pass long data through standard input instead."
                )
            size = program_bytes + arguments_bytes + _utf8_size(invocation.stdin or "", field="stdin")
            if size > self.max_input_bytes:
                raise SandboxInputError(
                    f"The program, its input and its arguments take {size} bytes; "
                    f"this sandbox accepts at most {self.max_input_bytes}."
                )
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not 0 < timeout <= self.max_timeout_seconds:
            raise SandboxInputError(f"timeout_seconds must be more than 0 and at most {self.max_timeout_seconds:g}.")
        return float(timeout)


def _utf8_size(text: str, *, field: str) -> int:
    """
    Return the UTF-8 size of text.

    Returns:
        int: The size in bytes.

    Raises:
        SandboxInputError: If the text can't be encoded, for example because it holds a lone surrogate.
    """
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise SandboxInputError(f"The {field} is not valid UTF-8 text.") from error
