# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.responses import ResponseOutputText
from pydantic import ValidationError
from unit._code_execution.local_python_sandbox import LocalPythonSandbox

from pyrit._code_execution import (
    FakeRun,
    FakeSandbox,
    FakeSandboxHook,
    ProcessResult,
    RuntimeProfile,
    SandboxBusyError,
    SandboxCapabilities,
    SandboxExecutionError,
    SandboxInputError,
    SandboxRegistry,
    SandboxStateKind,
)
from pyrit.models import (
    Equals,
    Message,
    MessagePiece,
    MessageScorable,
    ProgramBehaves,
    ProgramCase,
    ProgramResult,
    ToolExecutionMetadata,
    ToolLoopStop,
    ToolLoopStopReason,
)
from pyrit.prompt_target import OpenAIResponseTarget, SandboxToolProvider, Tool, ToolInfrastructureError
from pyrit.score import SandboxCheckScorer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from pyrit.memory import SQLiteMemory


@pytest.fixture
def registry() -> Iterator[SandboxRegistry]:
    SandboxRegistry.reset_registry_singleton()
    yield SandboxRegistry.get_registry_singleton()
    SandboxRegistry.reset_registry_singleton()


async def _tools_async(provider: SandboxToolProvider) -> dict[str, Tool]:
    return {tool.name: tool for tool in await provider.get_tools_async()}


async def _call_async(tool: Tool, **arguments: Any) -> Any:
    tool.validate_arguments(arguments=arguments)
    return await tool.execute_async(arguments=arguments)


async def test_tools_come_from_the_sandbox_runtimes_and_state_kind_async() -> None:
    environment = await _tools_async(SandboxToolProvider(sandbox=FakeSandbox()))
    assert list(environment) == ["run_python", "run_bash", "write_file", "read_file", "list_dir"]
    capabilities = SandboxCapabilities(
        runtime_profiles=(RuntimeProfile(name="python-3.12", description="Python 3.12"),),
        state_kind=SandboxStateKind.CALL,
        max_timeout_seconds=10.0,
        default_timeout_seconds=5.0,
    )
    call = await _tools_async(SandboxToolProvider(sandbox=FakeSandbox(capabilities=capabilities)))
    assert list(call) == ["run_python_3_12"]
    run_tool = call["run_python_3_12"]
    assert "Python 3.12" in run_tool.description and "Nothing persists" in run_tool.description
    timeout = run_tool.parameters["properties"]["timeout_seconds"]  # type: ignore[index]
    assert timeout["maximum"] == 10.0 and "Defaults to 5" in timeout["description"]


async def test_tool_names_select_the_tools_in_order_async(registry: SandboxRegistry) -> None:
    registry.register(FakeSandbox(), name="linux")
    provider = SandboxToolProvider(sandbox="linux", tool_names=["read_file", "run_python"])
    assert list(await _tools_async(provider)) == ["read_file", "run_python"]
    assert provider.identifier["tools"] == ["read_file", "run_python"]
    assert provider.identifier["sandbox"]["params"] == {"name": "fake", "state_kind": "environment"}  # type: ignore[index]


@pytest.mark.parametrize(("tool_names", "message"), [(["run_ruby"], "does not offer"), (["list_dir"] * 2, "repeat")])
def test_tool_names_must_be_offered_once(tool_names: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SandboxToolProvider(sandbox=FakeSandbox(), tool_names=tool_names)


async def test_a_runtime_can_name_its_tool_and_names_must_not_collide_async() -> None:
    profiles = (RuntimeProfile(name="windows-powershell", tool_name="powershell"), RuntimeProfile(name="py.3"))
    capabilities = SandboxCapabilities(runtime_profiles=profiles, state_kind=SandboxStateKind.CALL)
    assert list(await _tools_async(SandboxToolProvider(sandbox=FakeSandbox(capabilities=capabilities)))) == [
        "run_powershell",
        "run_py_3",
    ]
    colliding = capabilities.model_copy(
        update={"runtime_profiles": (RuntimeProfile(name="py.3"), RuntimeProfile(name="py-3"))}
    )
    with pytest.raises(ValueError, match="share a tool name"):
        SandboxToolProvider(sandbox=FakeSandbox(capabilities=colliding))


async def test_run_tool_returns_the_exit_code_and_bounded_output_async() -> None:
    def handler(run: FakeRun) -> ProcessResult:
        return ProcessResult(
            exit_code=3, stdout="x" * 50, stderr="err", execution_id="exec-1", warnings=("slow cleanup",)
        )

    fake = FakeSandbox(handler=handler)
    tools = await _tools_async(SandboxToolProvider(sandbox=fake, max_output_chars=10))
    result = await _call_async(
        tools["run_python"], program="print('x' * 50)", arguments=["a"], stdin="in", timeout_seconds=5
    )
    assert result == {
        "exit_code": 3,
        "stdout": "x" * 10,
        "stderr": "err",
        "stdout_truncated": True,
        "stderr_truncated": False,
        "timed_out": False,
        "resource_limit_exceeded": False,
        "duration_ms": result["duration_ms"],
        "warnings": ["slow cleanup"],
        "execution_id": "exec-1",
    }
    (run,) = fake.runs
    assert (run.runtime_profile_name, run.arguments, run.stdin, run.timeout_seconds) == ("python", ("a",), "in", 5)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"program": "   "},
        {"program": 7},
        {"program": "x", "arguments": "not a list"},
        {"program": "x", "arguments": ["nul\x00"]},
        {"program": "x", "unexpected": True},
        {"program": "x", "timeout_seconds": True},
        {"program": "x", "timeout_seconds": 500},
        {"program": "x" * 70_000},
    ],
)
async def test_invalid_calls_are_rejected_before_anything_runs_async(arguments: dict[str, Any]) -> None:
    fake = FakeSandbox()
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ValueError):
        tools["run_python"].validate_arguments(arguments=arguments)
    assert fake.runs == []


async def test_input_the_sandbox_rejects_reaches_the_model_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.EXECUTE, error=SandboxInputError("request too large for the host"))
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    result = await _call_async(tools["run_python"], program="print(1)")
    assert result == {"error": "SandboxInputError", "message": "request too large for the host"}


async def test_a_sandbox_failure_is_an_infrastructure_error_async() -> None:
    fake = FakeSandbox()
    fake.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxExecutionError("host unreachable"))
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ToolInfrastructureError, match="host unreachable"):
        await _call_async(tools["run_python"], program="print(1)")


@pytest.mark.parametrize("refusal", ["busy", "closed"])
async def test_a_refusal_where_nothing_ran_is_not_an_invocation_async(refusal: str) -> None:
    fake = FakeSandbox(acquire_timeout_seconds=0.05)
    if refusal == "busy":
        fake.fail_next(hook=FakeSandboxHook.OPEN, error=SandboxBusyError("held"), times=1_000)
    else:
        await fake.close_async()
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with (
        patch.object(FakeSandbox, "_BUSY_RETRY_INITIAL_SECONDS", 0.01),
        pytest.raises(ToolInfrastructureError) as raised,
    ):
        await _call_async(tools["run_python"], program="print(1)")
    assert raised.value.invoked is False and fake.runs == []


async def test_long_paths_stay_errors_the_model_can_fix_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    tools = await _tools_async(SandboxToolProvider(sandbox=sandbox))
    assert "error" in await _call_async(tools["read_file"], path="\U0001f600" * 400)
    assert "error" in await _call_async(tools["list_dir"], path="中" * 700)
    await sandbox.release_async()


async def test_list_dir_keeps_its_result_within_the_output_limit_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path, max_output_bytes=200_000)
    for index in range(300):
        (sandbox.workspace / f"file_{index:04d}_with_a_long_name.txt").write_text("x")
    tools = await _tools_async(SandboxToolProvider(sandbox=sandbox, max_output_chars=1_000))
    listing = await _call_async(tools["list_dir"])
    assert listing["truncated"] and len(json.dumps(listing["entries"])) <= 1_000
    await sandbox.release_async()


async def test_file_tools_ignore_modules_the_model_wrote_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path, isolated=False)
    forged = 'import sys\nsys.stdout.write(\'{"data": "Zm9yZ2Vk", "truncated": false}\')\nsys.exit(0)\n'
    (sandbox.workspace / "json.py").write_text(forged)
    (sandbox.workspace / "base64.py").write_text(forged)
    (sandbox.workspace / "real.txt").write_text("real")
    tools = await _tools_async(SandboxToolProvider(sandbox=sandbox))
    assert (await _call_async(tools["read_file"], path="real.txt"))["content"] == "real"
    await sandbox.release_async()


async def test_a_file_program_without_a_result_is_an_infrastructure_error_async() -> None:
    fake = FakeSandbox(handler=lambda run: ProcessResult(exit_code=1, stderr="python: not found"))
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ToolInfrastructureError, match="python: not found"):
        await _call_async(tools["list_dir"])


async def test_file_tools_write_read_and_list_files_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    tools = await _tools_async(SandboxToolProvider(sandbox=sandbox))
    content = "print('héllo')\r\nline two\n"
    written = await _call_async(tools["write_file"], path="src/main.py", content=content)
    assert written == {"path": "src/main.py", "bytes_written": len(content.encode("utf-8"))}
    await _call_async(tools["write_file"], path="src/main.py", content="# more\n", append=True)
    assert (sandbox.workspace / "src" / "main.py").read_bytes() == (content + "# more\n").encode("utf-8")
    read = await _call_async(tools["read_file"], path="src/main.py")
    assert read == {"path": "src/main.py", "content": content + "# more\n", "truncated": False}
    assert await _call_async(tools["list_dir"]) == {"entries": ["src/"], "truncated": False}
    assert await _call_async(tools["list_dir"], path="src") == {"entries": ["main.py"], "truncated": False}
    ran = await _call_async(tools["run_python"], program="print(open('src/main.py', encoding='utf-8').read()[:5])")
    assert ran["exit_code"] == 0 and ran["stdout"].strip() == "print"
    await sandbox.close_async()


async def test_file_tools_report_the_models_mistakes_async(tmp_path: Path) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    tools = await _tools_async(SandboxToolProvider(sandbox=sandbox))
    missing = await _call_async(tools["read_file"], path="missing.txt")
    assert missing["error"] == "FileNotFoundError"
    assert (await _call_async(tools["list_dir"], path="missing"))["error"] == "FileNotFoundError"
    (sandbox.workspace / "big.txt").write_text("x" * 5_000)
    read = await _call_async(tools["read_file"], path="big.txt")
    assert read["truncated"] and 0 < len(read["content"]) < 5_000
    await sandbox.close_async()


async def test_write_file_content_too_long_for_one_call_is_rejected_with_advice_async() -> None:
    fake = FakeSandbox()
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ValueError, match="with append"):
        tools["write_file"].validate_arguments(arguments={"path": "big.txt", "content": "x" * 60_000})
    with pytest.raises(ValidationError):
        tools["write_file"].validate_arguments(arguments={"path": "bad\npath", "content": "x"})
    assert fake.runs == []


def _function_call(call_id: str, name: str, arguments: dict[str, Any]) -> MagicMock:
    response = MagicMock(status="completed", error=None)
    section = MagicMock(type="function_call", call_id=call_id, arguments=json.dumps(arguments))
    section.name = name
    response.output = [section]
    return response


def _text(text: str) -> MagicMock:
    response = MagicMock(status="completed", error=None)
    response.output = [
        MagicMock(type="message", content=[ResponseOutputText(annotations=[], text=text, type="output_text")])
    ]
    return response


def _target(provider: SandboxToolProvider) -> OpenAIResponseTarget:
    return OpenAIResponseTarget(
        model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", tool_providers=[provider]
    )


def _tool_outputs(messages: list[Message]) -> list[tuple[dict[str, Any], bool]]:
    outputs = []
    for message in messages:
        piece = message.get_piece()
        if piece.original_value_data_type == "function_call_output":
            execution = ToolExecutionMetadata.from_metadata(metadata=piece.prompt_metadata)
            assert execution is not None
            outputs.append((json.loads(json.loads(piece.original_value)["output"]), execution.invoked))
    return outputs


@pytest.mark.usefixtures("patch_central_database")
async def test_the_check_sees_what_the_models_tool_call_did_async(
    registry: SandboxRegistry, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    sandbox = LocalPythonSandbox(root=tmp_path)
    registry.register(sandbox, name="linux")
    condition = ProgramBehaves(
        runtime_profile_name="python", cases=(ProgramCase(expected=ProgramResult(stdout=Equals(value="present"))),)
    )
    check = SandboxCheckScorer(
        sandbox="linux",
        check_program="import os\nprint('present' if os.path.exists('flag.txt') else 'absent')\n",
        condition=condition,
    )
    target = _target(SandboxToolProvider(sandbox="linux"))
    replies = [_function_call("call_1", "run_python", {"program": "open('flag.txt', 'w').write('x')"}), _text("Done.")]
    request = MessagePiece(role="user", original_value="Go", conversation_id=str(uuid.uuid4())).to_message()
    await sqlite_instance.add_message_to_memory_async(request=request)
    scorable = MessageScorable(message_piece_ids=(request.get_piece().id,))
    (before,) = await check.score_async(scorable=scorable)

    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=replies):
        responses = await target.send_prompt_async(message=request)
    await sqlite_instance.add_message_to_memory_async(request=responses[-1])
    (after,) = await check.score_async(scorable=MessageScorable(message_piece_ids=(responses[-1].get_piece().id,)))

    assert before.get_value() is False and after.get_value() is True
    await registry.close_all_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_a_call_rejected_before_running_is_recorded_as_not_invoked_async() -> None:
    fake = FakeSandbox()
    target = _target(SandboxToolProvider(sandbox=fake))
    replies = [_function_call("call_1", "run_python", {"program": "x" * 70_000}), _text("I will shorten it.")]
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=replies):
        responses = await target.send_prompt_async(message=MessagePiece(role="user", original_value="Go").to_message())
    ((output, invoked),) = _tool_outputs(responses)
    assert output["error"] == "malformed_arguments" and "at most 65536" in output["message"] and invoked is False
    assert fake.runs == [] and fake.open_sessions == set()


@pytest.mark.usefixtures("patch_central_database")
async def test_a_refused_call_ends_the_turn_without_an_invocation_async() -> None:
    fake = FakeSandbox()
    await fake.close_async()
    target = _target(SandboxToolProvider(sandbox=fake))
    replies = [_function_call("call_1", "run_python", {"program": "print(1)"})]
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=replies):
        responses = await target.send_prompt_async(message=MessagePiece(role="user", original_value="Go").to_message())
    ((output, invoked),) = _tool_outputs(responses)
    assert output["error"] == "tool_infrastructure_error" and invoked is False
    final = responses[-1].get_piece()
    stop = ToolLoopStop.from_metadata(metadata=final.prompt_metadata)
    assert final.response_error == "processing" and stop is not None
    assert stop.reason is ToolLoopStopReason.TOOL_INFRASTRUCTURE_ERROR
