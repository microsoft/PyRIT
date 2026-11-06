# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import uuid

import pytest

from pyrit.memory import SQLiteMemory
from pyrit.models import JsonResponseConfig, MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import ClaudeChatTarget, tool


@pytest.mark.run_only_if_all_tests
@pytest.mark.parametrize("json_output", [False, True])
async def test_claude_tool_continuation_and_replay_async(*, sqlite_instance: SQLiteMemory, json_output: bool) -> None:
    if os.environ.get("RUN_ANTHROPIC_TESTS", "").lower() != "true":
        pytest.skip("Set RUN_ANTHROPIC_TESTS=true to authorize billable Anthropic calls.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for Anthropic integration tests.")
    pytest.importorskip("litellm")
    executions: list[int] = []

    @tool
    async def add_one_async(*, value: int) -> int:
        """Add one to a number."""
        executions.append(value)
        return value + 1

    target = ClaudeChatTarget(
        model_name=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        tools=[add_one_async],
        max_tokens=512,
        max_tool_rounds=2,
    )
    normalizer = PromptNormalizer()
    conversation_id = str(uuid.uuid4())
    request = MessagePiece(
        role="user",
        original_value="Call add_one_async with value=7 exactly once. Return the result"
        + (" as a JSON object with key 'answer'." if json_output else " in one short sentence."),
        prompt_metadata=JsonResponseConfig(enabled=json_output).to_metadata(),
    )
    reply = await normalizer.send_prompt_async(
        target=target, message=request.to_message(), conversation_id=conversation_id
    )
    assert reply.get_piece().response_error == "none"
    assert executions == [7]
    if json_output:
        assert json.loads(reply.get_value())["answer"] == 8
    else:
        assert "8" in reply.get_value()
    history = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    assert any(message.api_role == "tool" for message in history)
    target.validate_tool_history(history)
    second = await normalizer.send_prompt_async(
        target=target,
        message=MessagePiece(role="user", original_value="What was that result? Do not call tools again.").to_message(),
        conversation_id=conversation_id,
    )
    assert second.get_piece().response_error == "none"
    assert "8" in second.get_value()
    assert executions == [7]


@pytest.mark.run_only_if_all_tests
async def test_claude_json_without_executable_tools_async(sqlite_instance: SQLiteMemory) -> None:
    if os.environ.get("RUN_ANTHROPIC_TESTS", "").lower() != "true":
        pytest.skip("Set RUN_ANTHROPIC_TESTS=true to authorize billable Anthropic calls.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for Anthropic integration tests.")
    pytest.importorskip("litellm")
    target = ClaudeChatTarget(model_name=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), max_tokens=256)
    responses = await target.send_prompt_async(
        message=MessagePiece(
            role="user",
            original_value="Return a JSON object with answer equal to 8.",
            prompt_metadata=JsonResponseConfig(enabled=True).to_metadata(),
        ).to_message()
    )
    assert responses[-1].get_piece().response_error == "none"
    assert json.loads(responses[-1].get_value())["answer"] == 8
