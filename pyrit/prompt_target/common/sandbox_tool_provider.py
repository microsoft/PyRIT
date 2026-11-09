# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Give a model tools that run its code in a ``Sandbox``."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from pyrit._code_execution import (
    SandboxBusyError,
    SandboxExecutionError,
    SandboxInputError,
    SandboxSessionLostError,
    SandboxStateKind,
    SandboxUnavailableError,
    resolve_sandbox,
)
from pyrit.models.code_execution import _ProgramInvocation
from pyrit.prompt_target.common.tool_provider import Tool, ToolInfrastructureError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit._code_execution import RuntimeProfile, Sandbox
    from pyrit.models.code_execution import _SandboxRun

_PATH_PATTERN = r"^[^\x00-\x1f\x7f]+$"

# Every file program starts here. It drops the working directory and the program's own folder from the
# module search path, so a file the model wrote (say, json.py) can't stand in for a standard module, and it
# reports errors briefly, without repeating the path.
_FILE_PROGRAM_PRELUDE = """import os, sys
_ours = {os.getcwd()} | ({os.path.dirname(os.path.abspath(__file__))} if "__file__" in globals() else set())
sys.path[:] = [entry for entry in sys.path if entry and os.path.abspath(entry) not in _ours]
import base64, json


def _report(error):
    message = getattr(error, "strerror", None) or str(error)
    print(json.dumps({"error": type(error).__name__, "message": message[:300]}))


"""

_WRITE_FILE_PROGRAM = """from pathlib import Path
try:
    path = Path({path!r})
    path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode({data!r})
    with path.open({mode!r}) as handle:
        handle.write(data)
    print(json.dumps({{"bytes_written": len(data)}}))
except (OSError, ValueError) as error:
    _report(error)
"""

_READ_FILE_PROGRAM = """from pathlib import Path
try:
    with Path({path!r}).open("rb") as handle:
        data = handle.read({limit} + 1)
    print(json.dumps({{"data": base64.b64encode(data[:{limit}]).decode("ascii"), "truncated": len(data) > {limit}}}))
except (OSError, ValueError) as error:
    _report(error)
"""

_LIST_DIR_PROGRAM = """try:
    entries, size, truncated = [], 0, False
    with os.scandir({path!r}) as iterator:
        for entry in iterator:
            name = entry.name + ("/" if entry.is_dir() else "")
            size += len(json.dumps(name)) + 2
            if size > {budget}:
                truncated = True
                break
            entries.append(name)
    print(json.dumps({{"entries": sorted(entries), "truncated": truncated}}))
except (OSError, ValueError) as error:
    _report(error)
"""


class _RunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    program: str = Field(min_length=1)
    arguments: list[str] = Field(default_factory=list, max_length=128)
    stdin: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


class _WriteFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(max_length=1024, pattern=_PATH_PATTERN)
    content: str
    append: bool = False


class _ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(max_length=1024, pattern=_PATH_PATTERN)


class _ListDirArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(default=".", max_length=1024, pattern=_PATH_PATTERN)


def _path_schema(description: str) -> dict[str, object]:
    return {"type": "string", "description": description, "maxLength": 1024}


class _SandboxTool(Tool):
    """A tool that runs one program in a sandbox."""

    def __init__(self, *, sandbox: Sandbox, name: str, description: str, parameters: dict[str, object]) -> None:
        super().__init__(name=name, description=description, parameters=parameters)
        self._sandbox = sandbox

    async def _run_async(
        self, *, program: str, runtime_profile_name: str, invocation: _ProgramInvocation, timeout_seconds: float | None
    ) -> _SandboxRun:
        """
        Run the program once, turning a sandbox failure into a failure of the tool's infrastructure.

        Returns:
            _SandboxRun: The run, with exactly one event.

        Raises:
            SandboxInputError: If the sandbox rejected the input; nothing ran.
            ToolInfrastructureError: If the sandbox failed, so the model can't fix it. It records whether
                anything may have run.
        """
        try:
            run = await self._sandbox.execute_async(
                program=program,
                runtime_profile_name=runtime_profile_name,
                invocations=(invocation,),
                timeout_seconds=timeout_seconds,
            )
        except (SandboxBusyError, SandboxSessionLostError, SandboxUnavailableError) as error:
            message = f"The sandbox could not run {self.name}, and nothing ran: {error}"
            raise ToolInfrastructureError(message, invoked=False) from error
        except SandboxExecutionError as error:
            raise ToolInfrastructureError(f"The sandbox could not run {self.name}: {error}") from error
        if len(run.events) != 1:
            raise ToolInfrastructureError(f"The sandbox returned {len(run.events)} results for one run of {self.name}.")
        return run


class _RunProgramTool(_SandboxTool):
    """``run_<runtime>``: run a program the model wrote."""

    def __init__(self, *, sandbox: Sandbox, profile: RuntimeProfile, max_output_chars: int) -> None:
        capabilities = sandbox.get_capabilities()
        if capabilities.state_kind is SandboxStateKind.ENVIRONMENT:
            persistence = "Files it writes stay for later calls until the sandbox is reset."
        else:
            persistence = "Nothing persists between calls."
        runtime = profile.description or profile.name
        default_timeout = f"{capabilities.default_timeout_seconds:g}"
        super().__init__(
            sandbox=sandbox,
            name=f"run_{profile.tool_name or re.sub(r'[^A-Za-z0-9_]', '_', profile.name)[:60]}",
            description=(
                f"Run a {runtime} program in {capabilities.description}. Each call runs the program once and returns "
                f"its exit code and output as JSON. {persistence} Output past {max_output_chars} characters per "
                "stream is cut."
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
        self._sandbox.validate_request(
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
            object: The exit code and bounded output, or the reason the sandbox rejected the call.
        """
        values = _RunArguments.model_validate(arguments)
        invocation = _ProgramInvocation(arguments=tuple(values.arguments), stdin=values.stdin)
        try:
            run = await self._run_async(
                program=values.program,
                runtime_profile_name=self._profile_name,
                invocation=invocation,
                timeout_seconds=values.timeout_seconds,
            )
        except SandboxInputError as error:
            return {"error": "SandboxInputError", "message": str(error)}
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
        if run.execution_ids:
            result["execution_id"] = run.execution_ids[0]
        return result


class _FileTool(_SandboxTool):
    """A file operation, run as a fixed Python program in the sandbox's file runtime."""

    def __init__(
        self, *, sandbox: Sandbox, name: str, description: str, properties: dict[str, object], required: list[str]
    ) -> None:
        super().__init__(
            sandbox=sandbox,
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        )
        runtime = sandbox.get_capabilities().file_runtime_profile_name
        if runtime is None:
            raise ValueError("File tools need a sandbox with a file runtime.")
        self._runtime = runtime

    def _validate_program(self, *, program: str) -> None:
        """
        Check that the file program fits the sandbox.

        Raises:
            SandboxInputError: If it does not.
        """
        self._sandbox.validate_request(
            program=program, runtime_profile_name=self._runtime, invocations=(_ProgramInvocation(),)
        )

    async def _run_file_program_async(self, *, program: str) -> dict[str, Any]:
        """
        Run a file program and read the JSON result it prints.

        Returns:
            dict[str, Any]: The result, or the error the model caused.

        Raises:
            ToolInfrastructureError: If the program reported no result.
        """
        try:
            run = await self._run_async(
                program=program,
                runtime_profile_name=self._runtime,
                invocation=_ProgramInvocation(),
                timeout_seconds=None,
            )
        except SandboxInputError as error:
            return {"error": "SandboxInputError", "message": str(error)}
        event = run.events[0]
        if event.timed_out:
            return {"error": "TimeoutError", "message": f"{self.name} did not finish in time."}
        try:
            result = json.loads(event.stdout)
        except ValueError:
            result = None
        if not isinstance(result, dict):
            detail = event.stderr.strip()[:500] or "no output"
            raise ToolInfrastructureError(f"{self.name} reported no result (exit code {event.exit_code}): {detail}")
        return result


class _WriteFileTool(_FileTool):
    def __init__(self, *, sandbox: Sandbox) -> None:
        super().__init__(
            sandbox=sandbox,
            name="write_file",
            description=(
                "Write UTF-8 text to a file in the sandbox, creating missing folders. Paths are relative to the "
                "working directory unless absolute. Use append to add to a file, for content too long for one call."
            ),
            properties={
                "path": _path_schema("The file to write."),
                "content": {"type": "string", "description": "The text to write."},
                "append": {"type": "boolean", "description": "Add to the end of the file instead of replacing it."},
            },
            required=["path", "content"],
        )

    def validate_arguments(self, *, arguments: dict[str, object]) -> None:
        """
        Check the call, including that the content fits in one program.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Raises:
            ValueError: If the arguments are invalid or the content is too long for one call.
            SandboxInputError: If the sandbox rejects the call for another reason.
        """
        program = self._program(_WriteFileArguments.model_validate(arguments))
        try:
            self._validate_program(program=program)
        except SandboxInputError as error:
            if len(program.encode("utf-8")) <= self._sandbox.get_capabilities().max_input_bytes:
                raise
            raise ValueError(
                "The content is too long for one write_file call; write it in parts, with append for the rest."
            ) from error

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        Write the file.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Returns:
            object: The path and bytes written, or the error.
        """
        values = _WriteFileArguments.model_validate(arguments)
        result = await self._run_file_program_async(program=self._program(values))
        return result if "error" in result else {"path": values.path, **result}

    @staticmethod
    def _program(values: _WriteFileArguments) -> str:
        return _FILE_PROGRAM_PRELUDE + _WRITE_FILE_PROGRAM.format(
            path=values.path,
            data=base64.b64encode(values.content.encode("utf-8")).decode("ascii"),
            mode="ab" if values.append else "wb",
        )


class _ReadFileTool(_FileTool):
    def __init__(self, *, sandbox: Sandbox, max_output_chars: int) -> None:
        output_bytes = sandbox.get_capabilities().max_output_bytes
        self._limit = max(min(max_output_chars, (output_bytes - 256) * 3 // 4), 1)
        super().__init__(
            sandbox=sandbox,
            name="read_file",
            description=(
                f"Read a text file in the sandbox, up to {self._limit} bytes, decoded as UTF-8. Paths are relative to "
                "the working directory unless absolute."
            ),
            properties={"path": _path_schema("The file to read.")},
            required=["path"],
        )

    def validate_arguments(self, *, arguments: dict[str, object]) -> None:
        """
        Check the call.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.
        """
        self._validate_program(program=self._program(_ReadFileArguments.model_validate(arguments)))

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        Read the file.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Returns:
            object: The content and whether it was cut, or the error.

        Raises:
            ToolInfrastructureError: If the program's result is malformed.
        """
        values = _ReadFileArguments.model_validate(arguments)
        result = await self._run_file_program_async(program=self._program(values))
        if "error" in result:
            return result
        try:
            data = base64.b64decode(result["data"], validate=True)
        except (KeyError, TypeError, binascii.Error) as error:
            raise ToolInfrastructureError("read_file reported a malformed result.") from error
        return {
            "path": values.path,
            "content": data.decode("utf-8", errors="replace"),
            "truncated": bool(result.get("truncated")),
        }

    def _program(self, values: _ReadFileArguments) -> str:
        return _FILE_PROGRAM_PRELUDE + _READ_FILE_PROGRAM.format(path=values.path, limit=self._limit)


class _ListDirTool(_FileTool):
    def __init__(self, *, sandbox: Sandbox, max_output_chars: int) -> None:
        self._budget = max(min(max_output_chars, sandbox.get_capabilities().max_output_bytes - 256), 64)
        super().__init__(
            sandbox=sandbox,
            name="list_dir",
            description="List a folder in the sandbox. Folder names end with /. Defaults to the working directory.",
            properties={"path": _path_schema("The folder to list.")},
            required=[],
        )

    def validate_arguments(self, *, arguments: dict[str, object]) -> None:
        """
        Check the call.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.
        """
        self._validate_program(program=self._program(_ListDirArguments.model_validate(arguments)))

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        """
        List the folder.

        Args:
            arguments (dict[str, object]): Arguments supplied by the model.

        Returns:
            object: The entries and whether the list was cut, or the error.
        """
        return await self._run_file_program_async(program=self._program(_ListDirArguments.model_validate(arguments)))

    def _program(self, values: _ListDirArguments) -> str:
        return _FILE_PROGRAM_PRELUDE + _LIST_DIR_PROGRAM.format(path=values.path, budget=self._budget)


class SandboxToolProvider:
    """
    Give a model tools that run its code in a ``Sandbox``.

    Each runtime becomes a ``run_<tool_name>`` tool, such as ``run_python``; the name comes from the
    runtime's ``tool_name``, or else from its name. An ``environment`` sandbox with a file runtime also
    offers ``write_file``, ``read_file`` and ``list_dir``, which run as small Python programs. Each call
    runs once, and its result is JSON: the exit code and output, cut at ``max_output_chars`` per stream.
    Arguments are checked against the sandbox's limits before anything runs, so a rejected call is never
    recorded as invoked. A sandbox failure raises ``ToolInfrastructureError``, so a target with a bounded
    tool loop ends the turn instead of asking the model to fix it.

    Experimental: it is built on ``pyrit._code_execution``, which may change without a deprecation cycle.
    """

    DEFAULT_MAX_OUTPUT_CHARS: ClassVar[int] = 32_768

    def __init__(
        self,
        *,
        sandbox: Sandbox | str,
        tool_names: Sequence[str] | None = None,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        """
        Build the tools a sandbox can offer.

        Args:
            sandbox (Sandbox | str): The sandbox, or its name in the ``SandboxRegistry``.
            tool_names (Sequence[str] | None): The tools to offer, in order. Defaults to every tool the
                sandbox supports.
            max_output_chars (int): The most characters of each output stream a result keeps.

        Raises:
            ValueError: If a tool is not offered or repeats, two runtimes would share a tool name, or
                the output limit is not positive.
        """
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive.")
        self._sandbox = resolve_sandbox(sandbox)
        self._max_output_chars = max_output_chars
        built = self._build_tools()
        available = {tool.name: tool for tool in built}
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

    def _build_tools(self) -> list[Tool]:
        """
        Build every tool the sandbox supports.

        Returns:
            list[Tool]: The tools.
        """
        capabilities = self._sandbox.get_capabilities()
        tools: list[Tool] = [
            _RunProgramTool(sandbox=self._sandbox, profile=profile, max_output_chars=self._max_output_chars)
            for profile in capabilities.runtime_profiles
        ]
        if capabilities.state_kind is SandboxStateKind.ENVIRONMENT and capabilities.file_runtime_profile_name:
            tools += [
                _WriteFileTool(sandbox=self._sandbox),
                _ReadFileTool(sandbox=self._sandbox, max_output_chars=self._max_output_chars),
                _ListDirTool(sandbox=self._sandbox, max_output_chars=self._max_output_chars),
            ]
        return tools
