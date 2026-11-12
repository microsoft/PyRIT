# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Give a model tools that run its code in a sandbox."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from pyrit._code_execution import SandboxExecutionError, SandboxInputError
from pyrit.models.code_execution import _ProgramInvocation
from pyrit.prompt_target.common.tool_provider import Tool, ToolInfrastructureError, ToolInputError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit._code_execution import RuntimeProfile, SandboxClient
    from pyrit.models.code_execution import _SandboxRun


class _RunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    program: str = Field(min_length=1)
    arguments: list[str] = Field(default_factory=list, max_length=128)
    stdin: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class _RunProgramTool(Tool):
    """``run_<runtime>``: run a program the model wrote, once."""

    def __init__(self, *, sandbox: SandboxClient, profile: RuntimeProfile, max_output_chars: int) -> None:
        capabilities = sandbox.get_capabilities()
        runtime = profile.description or profile.name
        default_timeout = f"{capabilities.default_timeout_seconds:g}"
        super().__init__(
            name=f"run_{profile.tool_name or re.sub(r'[^A-Za-z0-9_]', '_', profile.name)[:60]}",
            description=(
                f"Run a {runtime} program in {capabilities.description}. Each call runs the program once and returns "
                f"its exit code and output as JSON. Nothing persists between calls. Output past {max_output_chars} "
                "characters per stream is cut."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "program": {"type": "string", "description": f"The {runtime} program to run, verbatim."},
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command-line arguments passed to the program.",
                    },
                    "stdin": {"type": "string", "description": "Text passed to the program's standard input."},
                    "timeout_seconds": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": capabilities.max_timeout_seconds,
                        "description": f"Seconds before the program is stopped. Defaults to {default_timeout}.",
                    },
                },
                "required": ["program"],
                "additionalProperties": False,
            },
        )
        self._sandbox = sandbox
        self._profile_name = profile.name
        self._max_output_chars = max_output_chars

    def validate_arguments(self, *, arguments: dict[str, object]) -> None:
        """
        Check the call against the schema and the sandbox's limits, without running anything.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Raises:
            ValueError: If the arguments are invalid or exceed the sandbox's limits.
        """
        values = _RunArguments.model_validate(arguments)
        if not values.program.strip():
            raise ValueError("program must not be blank.")
        self._sandbox.get_capabilities().validate_request(
            program=values.program,
            runtime_profile_name=self._profile_name,
            invocations=(_ProgramInvocation(arguments=tuple(values.arguments), stdin=values.stdin),),
            timeout_seconds=values.timeout_seconds,
        )

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        Run the program once.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Returns:
            object: The exit code and bounded output.

        Raises:
            ToolInputError: If the sandbox rejected the input; nothing ran.
            ToolInfrastructureError: If the sandbox failed, so the model can't fix it.
        """
        values = _RunArguments.model_validate(arguments)
        try:
            run = await self._sandbox.execute_async(
                program=values.program,
                runtime_profile_name=self._profile_name,
                invocations=(_ProgramInvocation(arguments=tuple(values.arguments), stdin=values.stdin),),
                timeout_seconds=values.timeout_seconds,
            )
        except SandboxInputError as error:
            raise ToolInputError(str(error)) from error
        except SandboxExecutionError as error:
            raise ToolInfrastructureError(f"The sandbox could not run {self.name}: {error}") from error
        if len(run.events) != 1:
            raise ToolInfrastructureError(f"The sandbox returned {len(run.events)} results for one run of {self.name}.")
        return self._result(run=run)

    def _result(self, *, run: _SandboxRun) -> dict[str, object]:
        event, limit = run.events[0], self._max_output_chars
        result: dict[str, object] = {
            "exit_code": event.exit_code,
            "stdout": event.stdout[:limit],
            "stderr": event.stderr[:limit],
            "stdout_truncated": event.stdout_truncated or len(event.stdout) > limit,
            "stderr_truncated": event.stderr_truncated or len(event.stderr) > limit,
            "timed_out": event.timed_out,
            "resource_limit_exceeded": event.resource_limit_exceeded,
            "duration_ms": event.duration_ms,
        }
        if run.warnings:
            result["warnings"] = list(run.warnings)
        return result


class SandboxToolProvider:
    """
    Give a model tools that run its code in a sandbox.

    Each runtime becomes a ``run_<tool_name>`` tool, such as ``run_python``; the name comes from the
    runtime's ``tool_name``, or else from its name. Each call runs once, and its result is JSON: the exit
    code and output, cut at ``max_output_chars`` per stream. Arguments are checked against the sandbox's
    declared limits before anything runs, so a rejected call is never recorded as invoked. A sandbox's input
    rejection raises ``ToolInputError`` with the same no-run guarantee. A sandbox failure raises
    ``ToolInfrastructureError``, so a target with a bounded tool loop ends the turn instead of asking the
    model to fix it.

    Experimental: it is built on ``pyrit._code_execution``, which may change without a deprecation cycle.
    """

    DEFAULT_MAX_OUTPUT_CHARS: ClassVar[int] = 32_768

    def __init__(
        self,
        *,
        sandbox: SandboxClient,
        tool_names: Sequence[str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        """
        Build the tools a sandbox can offer.

        Args:
            sandbox (SandboxClient): The sandbox the tools run in.
            tool_names (Sequence[str] | None): The tools to offer, in order. Defaults to one tool per runtime.
            max_output_chars (int): The most characters of each output stream a result keeps.

        Raises:
            ValueError: If a tool is not offered or repeats, two runtimes would share a tool name, or
                the output limit is not positive.
        """
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive.")
        self._sandbox = sandbox
        self._max_output_chars = max_output_chars
        built = [
            _RunProgramTool(sandbox=sandbox, profile=profile, max_output_chars=max_output_chars)
            for profile in sandbox.get_capabilities().runtime_profiles
        ]
        available: dict[str, Tool] = {tool.name: tool for tool in built}
        if len(available) != len(built):
            raise ValueError("Two runtimes would share a tool name; give one of them a distinct tool_name.")
        names = list(available) if tool_names is None else list(tool_names)
        unknown = [name for name in names if name not in available]
        if unknown:
            raise ValueError(f"The sandbox does not offer tools {unknown}; available: {sorted(available)}.")
        if len(set(names)) != len(names):
            raise ValueError("tool_names must not repeat.")
        self._tools: list[Tool] = [available[name] for name in names]

    @property
    def identifier(self) -> dict[str, object]:
        """Non-secret configuration: the sandbox's identity, the tools, and the output limit."""
        sandbox = self._sandbox.get_identifier()
        return {
            "type": type(self).__name__,
            "sandbox": {"class_name": sandbox.class_name, "params": sandbox.params, "hash": sandbox.hash},
            "tools": [tool.name for tool in self._tools],
            "max_output_chars": self._max_output_chars,
        }

    async def get_tools_async(self) -> list[Tool]:
        """
        Return the configured tools.

        Returns:
            list[Tool]: The tools, in configuration order.
        """
        return list(self._tools)
