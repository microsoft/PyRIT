# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import A2ATarget


@pytest.fixture
def user_message():
    piece = MessagePiece(
        role="user",
        original_value="Hello A2A Agent",
        converted_value="Hello A2A Agent",
        conversation_id="conv-1234",
    )
    return Message(message_pieces=[piece])


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_initialization():
    target = A2ATarget(
        endpoint="https://agent.example.com/api/a2a",
        auth_token="test-token",
        api_key="test-api-key",
    )
    assert target._endpoint == "https://agent.example.com/api/a2a"
    assert target._auth_token == "test-token"
    assert target._api_key == "test-api-key"
    assert target._dialect == "auto"
    assert target._configuration.capabilities.supports_multi_turn is True


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_send_prompt_v03_success(mock_post, user_message):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "taskId": "task-xyz",
            "message": {
                "role": "assistant",
                "parts": [{"kind": "text", "text": "I am an A2A agent reply."}],
            },
        },
    }
    mock_post.return_value = mock_resp

    target = A2ATarget(endpoint="https://agent.example.com/a2a")
    responses = await target.send_prompt_async(message=user_message)

    assert len(responses) == 1
    assert responses[0].message_pieces[0].converted_value == "I am an A2A agent reply."

    # Verify v0.3 payload
    call_args = mock_post.call_args
    sent_json = call_args.kwargs["json"]
    assert sent_json["method"] == "message/send"
    assert sent_json["params"]["message"]["parts"][0]["kind"] == "text"
    assert sent_json["params"]["message"]["parts"][0]["text"] == "Hello A2A Agent"
    assert target._dialect == "v03"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_send_prompt_v03_fallback_to_v02(mock_post, user_message):
    # First call: method not found (-32601) on v0.3
    resp_err = MagicMock()
    resp_err.status_code = 200
    resp_err.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": -32601, "message": "Method not found"},
    }

    # Second call (retry with v0.2): success
    resp_v02 = MagicMock()
    resp_v02.status_code = 200
    resp_v02.json.return_value = {
        "jsonrpc": "2.0",
        "id": "2",
        "result": {
            "id": "task-v02",
            "message": {
                "role": "assistant",
                "parts": [{"type": "text", "text": "V0.2 task response"}],
            },
        },
    }
    mock_post.side_effect = [resp_err, resp_v02]

    target = A2ATarget(endpoint="https://agent.example.com/a2a")
    responses = await target.send_prompt_async(message=user_message)

    assert len(responses) == 1
    assert responses[0].message_pieces[0].converted_value == "V0.2 task response"
    assert target._dialect == "v02"
    assert mock_post.call_count == 2

    # Check that retry used tasks/send and type: text
    retry_json = mock_post.call_args_list[1].kwargs["json"]
    assert retry_json["method"] == "tasks/send"
    assert retry_json["params"]["message"]["parts"][0]["type"] == "text"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_multi_turn_task_continuity(mock_post, user_message):
    # Turn 1 response
    turn1_resp = MagicMock()
    turn1_resp.status_code = 200
    turn1_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "taskId": "server-task-789",
            "message": {"parts": [{"kind": "text", "text": "First turn answer"}]},
        },
    }
    # Turn 2 response
    turn2_resp = MagicMock()
    turn2_resp.status_code = 200
    turn2_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "2",
        "result": {
            "taskId": "server-task-789",
            "message": {"parts": [{"kind": "text", "text": "Second turn answer"}]},
        },
    }
    mock_post.side_effect = [turn1_resp, turn2_resp]

    target = A2ATarget(endpoint="https://agent.example.com/a2a")

    # Turn 1
    await target.send_prompt_async(message=user_message)
    turn1_sent = mock_post.call_args_list[0].kwargs["json"]
    assert "task_id" not in turn1_sent["params"]

    # Turn 2 with same conversation ID
    turn2_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Follow up question",
                converted_value="Follow up question",
                conversation_id="conv-1234",
            )
        ]
    )
    await target.send_prompt_async(message=turn2_message)
    turn2_sent = mock_post.call_args_list[1].kwargs["json"]
    assert turn2_sent["params"]["task_id"] == "server-task-789"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_jsonrpc_refusal_handled_as_response(mock_post, user_message):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {
            "code": -32000,
            "message": "Security policy refused the requested action.",
        },
    }
    mock_post.return_value = mock_resp

    target = A2ATarget(endpoint="https://agent.example.com/a2a")
    responses = await target.send_prompt_async(message=user_message)

    assert len(responses) == 1
    assert (
        responses[0].message_pieces[0].converted_value == "[A2A Refusal] Security policy refused the requested action."
    )


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_auth_headers_sent(mock_post, user_message):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {"message": {"parts": [{"kind": "text", "text": "Authenticated"}]}},
    }
    mock_post.return_value = mock_resp

    target = A2ATarget(
        endpoint="https://agent.example.com/a2a",
        auth_token="jwt.token.here",
        api_key="custom-key-123",
        api_key_header="X-Custom-Key",
    )
    await target.send_prompt_async(message=user_message)

    headers = mock_post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer jwt.token.here"
    assert headers["X-Custom-Key"] == "custom-key-123"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.get")
async def test_a2a_agent_card_discovery(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "name": "CustomerSupportAgent",
        "description": "Handles billing and support queries",
        "version": "1.0.0",
    }
    mock_get.return_value = mock_resp

    target = A2ATarget(endpoint="https://agent.example.com/a2a/tasks")
    card = await target.get_agent_card_async()

    assert card["name"] == "CustomerSupportAgent"
    assert mock_get.call_args[0][0] == "https://agent.example.com/.well-known/agent-card.json"
