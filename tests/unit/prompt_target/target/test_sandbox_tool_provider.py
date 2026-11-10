# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.responses import ResponseOutputText

from pyrit._code_execution import (
    RuntimeProfile,
    SandboxCapabilities,
    SandboxClient,
    SandboxExecutionError,
    SandboxInputError,
)
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import (
    AttackOutcome,
    ComponentIdentifier,
    Message,
    MessagePiece,
    ScoreStatus,
    ScoringExpectation,
    ToolCallRequirement,
    ToolExecutionMetadata,
    ToolLoopStop,
    ToolLoopStopReason,
    ToolsCalled,
)
from pyrit.models.code_execution import _ProgramExecutionEvent, _SandboxRun
from pyrit.prompt_target import OpenAIResponseTarget, SandboxToolProvider, Tool, ToolInfrastructureError, ToolInputError
from pyrit.score import MessageToolCallScorer

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.memory import SQLiteMemory
    from pyrit.models.code_execution import _ProgramInvocation

_CAPABILITIES = SandboxCapabilities(
    runtime_profiles=(RuntimeProfile(name="python"), RuntimeProfile(name="bash")),
    description="a test sandbox",
    max_timeout_seconds=60.0,
    default_timeout_seconds=30.0,
)


@dataclass(frozen=True)
class _Run:
    program: str
    runtime_profile_name: str
    arguments: tuple[str, ...]
    stdin: str | None
    timeout_seconds: float


class _FakeSandbox:
    """A sandbox client that never runs code: it records each run and answers with ``handler``."""

    def __init__(
        self,
        *,
        capabilities: SandboxCapabilities = _CAPABILITIES,
        handler: Callable[[_Run], _ProgramExecutionEvent | _SandboxRun] | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._handler = handler
        self.error: Exception | None = None
        self.runs: list[_Run] = []

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"name": "fake"})

    def get_runtime_profile_names(self) -> frozenset[str]:
        return self._capabilities.get_runtime_profile_names()

    def get_capabilities(self) -> SandboxCapabilities:
        return self._capabilities

    async def execute_async(
        self,
        *,
        program: str,
        runtime_profile_name: str,
        invocations: tuple[_ProgramInvocation, ...],
        timeout_seconds: float | None = None,
    ) -> _SandboxRun:
        timeout = self._capabilities.validate_request(
            program=program,
            runtime_profile_name=runtime_profile_name,
            invocations=invocations,
            timeout_seconds=timeout_seconds,
        )
        if self.error is not None:
            raise self.error
        events = []
        for index, invocation in enumerate(invocations):
            run = _Run(program, runtime_profile_name, invocation.arguments, invocation.stdin, timeout)
            self.runs.append(run)
            answer = self._handler(run) if self._handler else _event(exit_code=0)
            if isinstance(answer, _SandboxRun):
                return answer
            events.append(answer.model_copy(update={"invocation_index": index}))
        return _SandboxRun(events=tuple(events))


def _event(**fields: Any) -> _ProgramExecutionEvent:
    return _ProgramExecutionEvent(invocation_index=0, runtime_profile_name="python", duration_ms=5, **fields)


async def _tools_async(provider: SandboxToolProvider) -> dict[str, Tool]:
    return {tool.name: tool for tool in await provider.get_tools_async()}


async def _call_async(tool: Tool, **arguments: Any) -> Any:
    tool.validate_arguments(arguments=arguments)
    return await tool.execute_async(arguments=arguments)


def test_the_fake_is_a_sandbox_client() -> None:
    assert isinstance(_FakeSandbox(), SandboxClient)


async def test_each_runtime_becomes_a_run_tool_described_by_the_capabilities_async() -> None:
    assert list(await _tools_async(SandboxToolProvider(sandbox=_FakeSandbox()))) == ["run_python", "run_bash"]
    capabilities = SandboxCapabilities(
        runtime_profiles=(RuntimeProfile(name="python-3.12", description="Python 3.12"),),
        description="a disposable container",
        max_timeout_seconds=10.0,
        default_timeout_seconds=5.0,
    )
    tools = await _tools_async(SandboxToolProvider(sandbox=_FakeSandbox(capabilities=capabilities)))
    assert list(tools) == ["run_python_3_12"]
    run_tool = tools["run_python_3_12"]
    assert "Python 3.12" in run_tool.description and "a disposable container" in run_tool.description
    assert "Nothing persists" in run_tool.description
    timeout = run_tool.parameters["properties"]["timeout_seconds"]  # type: ignore[index]
    assert timeout["maximum"] == 10.0 and "Defaults to 5" in timeout["description"]


async def test_tool_names_select_the_tools_in_order_async() -> None:
    provider = SandboxToolProvider(sandbox=_FakeSandbox(), tool_names=["run_bash", "run_python"])
    assert list(await _tools_async(provider)) == ["run_bash", "run_python"]
    assert provider.identifier["tools"] == ["run_bash", "run_python"]
    assert provider.identifier["sandbox"]["params"] == {"name": "fake"}  # type: ignore[index]


@pytest.mark.parametrize(("tool_names", "message"), [(["run_ruby"], "does not offer"), (["run_bash"] * 2, "repeat")])
def test_tool_names_must_be_offered_once(tool_names: list[str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SandboxToolProvider(sandbox=_FakeSandbox(), tool_names=tool_names)


def test_max_output_chars_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        SandboxToolProvider(sandbox=_FakeSandbox(), max_output_chars=0)


async def test_a_runtime_can_name_its_tool_and_names_must_not_collide_async() -> None:
    profiles = (RuntimeProfile(name="windows-powershell", tool_name="powershell"), RuntimeProfile(name="py.3"))
    capabilities = SandboxCapabilities(runtime_profiles=profiles)
    assert list(await _tools_async(SandboxToolProvider(sandbox=_FakeSandbox(capabilities=capabilities)))) == [
        "run_powershell",
        "run_py_3",
    ]
    colliding = capabilities.model_copy(
        update={"runtime_profiles": (RuntimeProfile(name="py.3"), RuntimeProfile(name="py-3"))}
    )
    with pytest.raises(ValueError, match="share a tool name"):
        SandboxToolProvider(sandbox=_FakeSandbox(capabilities=colliding))


async def test_run_tool_returns_the_exit_code_and_bounded_output_async() -> None:
    def handler(run: _Run) -> _SandboxRun:
        return _SandboxRun(events=(_event(exit_code=3, stdout="x" * 50, stderr="err"),), warnings=("slow cleanup",))

    fake = _FakeSandbox(handler=handler)
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
        "duration_ms": 5,
        "warnings": ["slow cleanup"],
    }
    (run,) = fake.runs
    assert (run.runtime_profile_name, run.arguments, run.stdin, run.timeout_seconds) == ("python", ("a",), "in", 5)


async def test_the_default_timeout_comes_from_the_capabilities_async() -> None:
    fake = _FakeSandbox()
    await _call_async((await _tools_async(SandboxToolProvider(sandbox=fake)))["run_bash"], program="echo hi")
    assert [(run.runtime_profile_name, run.timeout_seconds) for run in fake.runs] == [("bash", 30.0)]


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
    fake = _FakeSandbox()
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ValueError):
        tools["run_python"].validate_arguments(arguments=arguments)
    assert fake.runs == []


async def test_input_the_sandbox_rejects_reaches_the_model_async() -> None:
    fake = _FakeSandbox()
    fake.error = SandboxInputError("request too large for the host")
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ToolInputError, match="request too large for the host"):
        await _call_async(tools["run_python"], program="print(1)")
    assert fake.runs == []


async def test_a_sandbox_failure_is_an_infrastructure_error_async() -> None:
    fake = _FakeSandbox()
    fake.error = SandboxExecutionError("host unreachable")
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ToolInfrastructureError, match="host unreachable") as raised:
        await _call_async(tools["run_python"], program="print(1)")
    assert raised.value.invoked is True


@pytest.mark.parametrize("events", [0, 2])
async def test_a_sandbox_that_returns_the_wrong_number_of_results_is_an_infrastructure_error_async(
    events: int,
) -> None:
    fake = _FakeSandbox(handler=lambda run: _SandboxRun(events=(_event(exit_code=0),) * events))
    tools = await _tools_async(SandboxToolProvider(sandbox=fake))
    with pytest.raises(ToolInfrastructureError, match=f"returned {events} results"):
        await _call_async(tools["run_python"], program="print(1)")


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
async def test_a_sandbox_failure_ends_the_turn_with_every_call_answered_async() -> None:
    fake = _FakeSandbox()
    fake.error = SandboxExecutionError("host unreachable")
    target = _target(SandboxToolProvider(sandbox=fake))
    calls = _function_call("call_failed", "run_python", {"program": "first"})
    calls.output.extend(_function_call("call_skipped", "run_python", {"program": "second"}).output)
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=[calls]) as create:
        responses = await target.send_prompt_async(message=MessagePiece(role="user", original_value="Go").to_message())
    create.assert_awaited_once()
    assert _tool_outputs(responses) == [
        ({"error": "tool_infrastructure_error", "message": "The tool's infrastructure failed."}, True),
        ({"error": "tool_not_executed", "message": "An earlier tool call failed."}, False),
    ]
    final = responses[-1].get_piece()
    assert final.response_error == "processing"
    assert ToolLoopStop.from_metadata(metadata=final.prompt_metadata) == ToolLoopStop(
        reason=ToolLoopStopReason.TOOL_INFRASTRUCTURE_ERROR
    )
    target.validate_tool_history(responses)


@pytest.mark.usefixtures("patch_central_database")
async def test_a_call_rejected_before_running_is_recorded_as_not_invoked_async() -> None:
    fake = _FakeSandbox()
    target = _target(SandboxToolProvider(sandbox=fake))
    replies = [_function_call("call_1", "run_python", {"program": "x" * 70_000}), _text("I will shorten it.")]
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=replies):
        responses = await target.send_prompt_async(message=MessagePiece(role="user", original_value="Go").to_message())
    ((output, invoked),) = _tool_outputs(responses)
    assert output["error"] == "malformed_arguments" and "at most 65536" in output["message"] and invoked is False
    assert fake.runs == []


@pytest.mark.parametrize("rejected", [True, False])
async def test_adapter_rejection_is_not_scored_as_execution_async(
    *, rejected: bool, sqlite_instance: SQLiteMemory
) -> None:
    sandbox = _FakeSandbox(handler=lambda run: _event(exit_code=3))
    if rejected:
        sandbox.error = SandboxInputError("request rejected by adapter")
    target = _target(SandboxToolProvider(sandbox=sandbox))
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
        max_attempts_on_failure=0,
    )
    expectation = ScoringExpectation(
        objective="use the sandbox tool",
        conditions=(ToolsCalled(tools=(ToolCallRequirement(name="run_python"),)),),
    )
    replies = [_function_call("call_1", "run_python", {"program": "raise ValueError('bad input')"}), _text("done")]
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=replies) as create:
        result = await attack.execute_async(objective="use the sandbox tool", expectation=expectation)
    assert create.await_count == 2
    assert result.last_response is not None
    pieces = await sqlite_instance.get_message_pieces_async(conversation_id=result.last_response.conversation_id)
    outputs = [piece for piece in pieces if piece.original_value_data_type == "function_call_output"]
    assert len(outputs) == 1
    assert ToolExecutionMetadata.from_metadata(metadata=outputs[0].prompt_metadata) == ToolExecutionMetadata(
        invoked=not rejected
    )
    assert len(sandbox.runs) == (0 if rejected else 1)
    output = json.loads(json.loads(outputs[0].original_value)["output"])
    assert result.automated_score is not None
    if rejected:
        assert output == {
            "error": "tool_input_error",
            "function": "run_python",
            "message": "request rejected by adapter",
        }
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.automated_score.status is ScoreStatus.UNDETERMINED
        assert result.automated_score.score_value is None
    else:
        assert output["exit_code"] == 3
        assert result.outcome is AttackOutcome.SUCCESS
        assert result.automated_score.get_value() is True
