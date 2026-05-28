# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""C7 integration tests: RedTeamingAttack with real tool dispatch.

These tests spawn the real ``tests/unit/tools/echo_mcp_server.py`` subprocess
and exercise the full client-side tool-calling stack:

  attack -> normalizer -> target -> @tool_loop wrapper -> MCPToolBackend ->
  MCPClient (stdio) -> echo subprocess -> tool result -> back through the
  wrapper -> Memory.

Only the OpenAI Responses HTTP layer is mocked. The MCP subprocess, the
MCPToolBackend lock, the AsyncExitStack lifecycle, the canonical envelope
round-trip, and the @tool_loop decorator's RedTeam-attack invocation path
all execute under their real implementations.
"""

from __future__ import annotations

import json
import pathlib
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig, AttackScoringConfig
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack
from pyrit.identifiers import ComponentIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import Message, MessagePiece, Score
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.tools import (
    LocalMCPServerSpec,
    MCPToolBackend,
    ToolEventBehavior,
    ToolEventPolicy,
)


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


ECHO_SERVER_PATH = pathlib.Path(__file__).resolve().parents[2] / "unit" / "tools" / "echo_mcp_server.py"


def _local_echo_spec() -> LocalMCPServerSpec:
    """Build a LocalMCPServerSpec that launches the in-tree echo server."""
    return LocalMCPServerSpec(
        command=sys.executable,
        args=(str(ECHO_SERVER_PATH),),
    )


def _mock_function_call_response(call_id: str, function_name: str, arguments: dict) -> MagicMock:
    """Build a fake Responses-API response containing a function_call section."""
    response = MagicMock()
    response.status = "completed"
    response.error = None
    section = MagicMock()
    section.type = "function_call"
    section.call_id = call_id
    section.name = function_name
    section.arguments = json.dumps(arguments)
    section.model_dump.return_value = {
        "type": "function_call",
        "call_id": call_id,
        "name": function_name,
        "arguments": json.dumps(arguments),
    }
    response.output = [section]
    return response


def _mock_text_response(text: str) -> MagicMock:
    """Build a fake Responses-API response containing a message section."""
    response = MagicMock()
    response.status = "completed"
    response.error = None
    section = MagicMock()
    section.type = "message"
    section.content = [MagicMock(text=text)]
    response.output = [section]
    return response


def _make_response_target_with_mcp_backend(
    backend: MCPToolBackend,
) -> OpenAIResponseTarget:
    """Build an OpenAIResponseTarget wired to the live MCP backend."""
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_editable_history=True,
        supports_json_output=True,
        supports_multi_message_pieces=True,
        supports_system_prompt=True,
        supports_tool_use=True,
        input_modalities=frozenset(
            {
                frozenset(["text"]),
                frozenset(["text", "image_path"]),
                frozenset(["function_call"]),
                frozenset(["tool_call"]),
                frozenset(["function_call_output"]),
                frozenset(["reasoning"]),
            }
        ),
    )
    config = TargetConfiguration(
        capabilities=caps,
        tool_event_policy=ToolEventPolicy(behavior=ToolEventBehavior.EXECUTE, max_tool_iterations=5),
        tool_backend=backend,
    )
    return OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.example.com",
        api_key="mock-key",
        custom_configuration=config,
    )


def _scripted_adversarial(prompts: list[str]) -> MagicMock:
    """Build a mock adversarial target that returns scripted prompts."""
    adversarial = MagicMock(spec=PromptTarget)
    adversarial.send_prompt_async = AsyncMock(
        side_effect=[
            [
                Message(
                    message_pieces=[
                        MessagePiece(
                            role="assistant",
                            original_value=p,
                            original_value_data_type="text",
                            conversation_id=str(uuid.uuid4()),
                        )
                    ]
                )
            ]
            for p in prompts
        ]
    )
    adversarial.get_identifier.return_value = _mock_id("MockAdversarial")
    adversarial.set_system_prompt = MagicMock()
    return adversarial


def _success_scorer() -> MagicMock:
    """Mock objective scorer that always returns True (objective met)."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock(
        return_value=[
            Score(
                score_value="true",
                score_value_description="objective met",
                score_type="true_false",
                score_category=["test"],
                score_rationale="mock rationale",
                score_metadata={},
                message_piece_id=str(uuid.uuid4()),
                scorer_class_identifier=_mock_id("MockScorer"),
            )
        ]
    )
    scorer.get_identifier.return_value = _mock_id("MockScorer")
    return scorer


@pytest.mark.asyncio
async def test_red_teaming_response_target_with_mcp_echo(patch_central_database):
    """End-to-end: RedTeamingAttack drives OpenAIResponseTarget with MCPToolBackend.

    The Response target's HTTP layer is mocked to return a function_call for
    the echo tool, followed by a stop response after the tool result arrives.
    The MCP subprocess actually executes the echo call.
    """
    backend = MCPToolBackend(servers=[_local_echo_spec()])
    async with backend:
        objective_target = _make_response_target_with_mcp_backend(backend)

        # Mock the OpenAI Responses HTTP layer on the objective target.
        responses = [
            _mock_function_call_response("call_1", "echo", {"text": "hello"}),
            _mock_text_response("Echoed: hello"),
        ]
        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return responses[len(seen) - 1]

        # Adversarial returns one prompt (RedTeamingAttack stops after objective is met)
        adversarial = _scripted_adversarial(["please echo hello"])

        attack = RedTeamingAttack(
            objective_target=objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_success_scorer()),
        )

        with patch.object(
            objective_target._async_client.responses, "create", new_callable=AsyncMock
        ) as mock_create_call:
            mock_create_call.side_effect = mock_create
            result = await attack.execute_async(objective="get the model to echo 'hello'")

        # Two HTTP calls to the Response API: initial + post-tool
        assert len(seen) == 2
        # Second call must include the function_call_output (tool result)
        second_input = seen[1]["input"]
        function_outputs = [item for item in second_input if item.get("type") == "function_call_output"]
        assert len(function_outputs) == 1
        # The output JSON contains the text "hello" because the real MCP echo
        # subprocess returned it
        assert "hello" in function_outputs[0]["output"]
        assert result is not None


@pytest.mark.asyncio
async def test_red_teaming_persists_canonical_transcript_in_memory(patch_central_database):
    """End-to-end: after a successful tool dispatch the DB shows the full chain.

    Verifies the canonical envelope contract (§13): the conversation written
    to Memory must contain the user message, the assistant function_call, the
    tool function_call_output (with matching call_id), and the assistant's
    final text -- in that order.
    """
    backend = MCPToolBackend(servers=[_local_echo_spec()])
    async with backend:
        objective_target = _make_response_target_with_mcp_backend(backend)

        responses = [
            _mock_function_call_response("call_xyz", "echo", {"text": "world"}),
            _mock_text_response("Echoed: world"),
        ]
        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return responses[len(seen) - 1]

        adversarial = _scripted_adversarial(["echo world"])

        attack = RedTeamingAttack(
            objective_target=objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_success_scorer()),
        )

        with patch.object(
            objective_target._async_client.responses, "create", new_callable=AsyncMock
        ) as mock_create_call:
            mock_create_call.side_effect = mock_create
            result = await attack.execute_async(objective="echo world")

        # Read the conversation back from Memory
        memory = CentralMemory.get_memory_instance()
        assert result is not None
        objective_conv_id = result.conversation_id
        assert objective_conv_id, "Attack result must carry the objective-target conversation id"

        pieces = list(memory.get_message_pieces(conversation_id=objective_conv_id))
        # Filter out system prompts; we care about the user/assistant/tool chain
        data_types_in_order = [p.original_value_data_type for p in pieces]
        # The chain MUST contain function_call followed by function_call_output (canonical envelope)
        assert "function_call" in data_types_in_order
        assert "function_call_output" in data_types_in_order

        fc_index = data_types_in_order.index("function_call")
        fco_index = data_types_in_order.index("function_call_output")
        assert fc_index < fco_index, "function_call must precede function_call_output in DB"

        fc_envelope = json.loads(pieces[fc_index].original_value)
        fco_envelope = json.loads(pieces[fco_index].original_value)
        assert fc_envelope["call_id"] == fco_envelope["call_id"] == "call_xyz"
        assert fc_envelope["name"] == "echo"
        # The tool result envelope's `output` is JSON-encoded; the underlying echo result is "world"
        assert "world" in fco_envelope["output"]


@pytest.mark.asyncio
async def test_red_teaming_dispatches_all_tool_calls_per_turn(patch_central_database):
    """Multi-call-per-turn dispatch (intentional behavior change vs pre-C6 loop).

    When the model emits two function_call sections in one response, BOTH
    must dispatch through the MCPToolBackend. The pre-C6 in-class loop in
    OpenAIResponseTarget only dispatched the LAST call per turn; the C6
    migration onto @tool_loop changes this to "dispatch every call in
    declaration order." Verify by issuing both an `echo` and an `add` call
    and asserting both results land in the second API call's input.
    """
    backend = MCPToolBackend(servers=[_local_echo_spec()])
    async with backend:
        objective_target = _make_response_target_with_mcp_backend(backend)

        # First response contains TWO function_calls; second is the stop text.
        multi_call_response = MagicMock()
        multi_call_response.status = "completed"
        multi_call_response.error = None

        echo_section = MagicMock()
        echo_section.type = "function_call"
        echo_section.call_id = "call_echo"
        echo_section.name = "echo"
        echo_section.arguments = json.dumps({"text": "hi"})
        echo_section.model_dump.return_value = {
            "type": "function_call",
            "call_id": "call_echo",
            "name": "echo",
            "arguments": json.dumps({"text": "hi"}),
        }
        add_section = MagicMock()
        add_section.type = "function_call"
        add_section.call_id = "call_add"
        add_section.name = "add"
        add_section.arguments = json.dumps({"a": 3, "b": 4})
        add_section.model_dump.return_value = {
            "type": "function_call",
            "call_id": "call_add",
            "name": "add",
            "arguments": json.dumps({"a": 3, "b": 4}),
        }
        multi_call_response.output = [echo_section, add_section]

        responses = [
            multi_call_response,
            _mock_text_response("done"),
        ]
        seen = []

        async def mock_create(**kwargs):
            seen.append(kwargs)
            return responses[len(seen) - 1]

        adversarial = _scripted_adversarial(["call echo and add"])

        attack = RedTeamingAttack(
            objective_target=objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_success_scorer()),
        )

        with patch.object(objective_target._async_client.responses, "create", new_callable=AsyncMock) as mc:
            mc.side_effect = mock_create
            await attack.execute_async(objective="dispatch both tools")

        assert len(seen) == 2
        second_input = seen[1]["input"]
        outputs = [item for item in second_input if item.get("type") == "function_call_output"]
        assert len(outputs) == 2, "Both tool calls must be dispatched per the new behavior"
        call_ids = [o["call_id"] for o in outputs]
        assert call_ids == ["call_echo", "call_add"], "Outputs must preserve declaration order"
        # Real MCP subprocess: echo("hi") returned "hi", add(3, 4) returned 7
        assert "hi" in outputs[0]["output"]
        assert "7" in outputs[1]["output"]
