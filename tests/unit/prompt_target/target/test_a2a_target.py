# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pyrit.exceptions import EmptyResponseException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import A2ATarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

ENDPOINT = "https://agent.example.com/a2a"


def _user_message(text: str = "Hello A2A Agent", conversation_id: str = "conv-1234") -> Message:
    piece = MessagePiece(
        role="user",
        original_value=text,
        converted_value=text,
        conversation_id=conversation_id,
    )
    return Message(message_pieces=[piece])


def _rpc_response(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    req = httpx.Request("POST", ENDPOINT)
    return httpx.Response(status_code=status_code, json=payload, request=req)


def _task_payload(
    text: str | None,
    *,
    state: str = "completed",
    task_id: str = "task-1",
    context_id: str = "ctx-1",
    status_message: str | None = None,
) -> dict[str, Any]:
    status_obj: dict[str, Any] = {"state": state}
    if status_message is not None:
        status_obj["message"] = {
            "role": "agent",
            "messageId": "msg-status-1",
            "parts": [{"kind": "text", "text": status_message}],
        }
    result: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": status_obj,
    }
    if text is not None:
        result["artifacts"] = [
            {
                "artifactId": "art-1",
                "parts": [{"kind": "text", "text": text}],
            }
        ]
    else:
        result["artifacts"] = []
    return {"jsonrpc": "2.0", "id": "1", "result": result}


def _message_payload(text: str, *, message_id: str = "m-1", context_id: str = "ctx-1") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "kind": "message",
            "role": "agent",
            "messageId": message_id,
            "contextId": context_id,
            "parts": [{"kind": "text", "text": text}],
        },
    }


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setenv("RETRY_WAIT_MIN_SECONDS", "0")
    monkeypatch.setenv("RETRY_WAIT_MAX_SECONDS", "0")


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_initialization():
    target = A2ATarget(endpoint=ENDPOINT + "/", auth_token="test-token", api_key="test-api-key")
    assert target._endpoint == ENDPOINT
    assert target._protocol_version == "0.3"
    assert target._build_headers()["Authorization"] == "Bearer test-token"
    assert target._build_headers()["X-API-Key"] == "test-api-key"
    assert target._build_headers()["Accept"] == "application/json"


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_identifier():
    target = A2ATarget(endpoint=ENDPOINT, auth_token="secret-token", api_key="secret-key")
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == ENDPOINT
    assert identifier.params["protocol_version"] == "0.3"
    assert identifier.params["task_timeout_seconds"] == 120.0
    assert "secret-token" not in str(identifier.params)
    assert "secret-key" not in str(identifier.params)


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_rejects_unknown_protocol_version():
    with pytest.raises(ValueError, match="Unsupported A2A protocol_version"):
        A2ATarget(endpoint=ENDPOINT, protocol_version="v9")  # type: ignore[arg-type]


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_rejects_unsupported_capabilities():
    invalid_config = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            input_modalities=frozenset({frozenset(["image_path"])}),
        )
    )
    with pytest.raises(ValueError, match="only supports text input modality"):
        A2ATarget(endpoint=ENDPOINT, custom_configuration=invalid_config)

    system_prompt_config = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
        )
    )
    with pytest.raises(ValueError, match="does not support system prompts"):
        A2ATarget(endpoint=ENDPOINT, custom_configuration=system_prompt_config)


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_missing_sdk_raises_import_error(monkeypatch):
    import pyrit.prompt_target.a2a_target as a2a_mod

    monkeypatch.setattr(a2a_mod, "_a2a_cache", None)

    real_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "a2a" or name.startswith("a2a."):
            raise ImportError("No module named 'a2a'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", mock_import)

    with pytest.raises(ImportError, match="pip install pyrit\\[a2a\\]"):
        A2ATarget(endpoint=ENDPOINT)


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_target_missing_upstream_context_raises():
    target = A2ATarget(endpoint=ENDPOINT)
    msg1 = Message(message_pieces=[MessagePiece(role="user", original_value="turn 1", conversation_id="conv-1")])
    msg2 = Message(message_pieces=[MessagePiece(role="user", original_value="turn 2", conversation_id="conv-1")])
    with pytest.raises(ValueError, match="has no upstream context for conversation"):
        await target._send_prompt_to_target_async(normalized_conversation=[msg1, msg2])


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_target_set_and_reset_conversation_context():
    target = A2ATarget(endpoint=ENDPOINT)
    target.set_conversation_context(conversation_id="conv-1", context_id="ctx-restored")
    assert target._conversations["conv-1"].context_id == "ctx-restored"

    await target.reset_conversation_async(conversation_id="conv-1")
    assert "conv-1" not in target._conversations


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_send_prompt_message_result(mock_send):
    mock_send.return_value = _rpc_response(_message_payload("Direct reply", context_id="ctx-abc"))

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Direct reply"
    assert target._conversations["conv-1234"].context_id == "ctx-abc"

    sent_req = mock_send.call_args[0][0]
    payload = json.loads(sent_req.content)
    assert payload["method"] == "message/send"
    assert payload["params"]["message"]["parts"][0]["text"] == "Hello A2A Agent"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_send_prompt_task_completed(mock_send):
    mock_send.return_value = _rpc_response(_task_payload("Task answer", state="completed", context_id="ctx-task"))

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Task answer"
    assert target._conversations["conv-1234"].context_id == "ctx-task"


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.send")
async def test_a2a_polls_pending_task(mock_send, mock_sleep):
    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working", task_id="task-42")),
        _rpc_response(_task_payload("Polled reply", state="completed", task_id="task-42")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Polled reply"
    assert mock_send.call_count == 2
    second_req = mock_send.call_args_list[1][0][0]
    second_payload = json.loads(second_req.content)
    assert second_payload["method"] == "tasks/get"
    assert second_payload["params"]["id"] == "task-42"


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.send")
async def test_a2a_pending_task_times_out(mock_send, mock_sleep):
    mock_send.return_value = _rpc_response(_task_payload(None, state="working", task_id="task-stuck"))

    target = A2ATarget(endpoint=ENDPOINT, task_timeout_seconds=0.0)
    with pytest.raises(TimeoutError, match="still in state TASK_STATE_WORKING"):
        await target.send_prompt_async(message=_user_message())


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_input_required_task_extracts_question_from_status_message(mock_send):
    mock_send.return_value = _rpc_response(
        _task_payload(
            text="partial artifact",
            state="input-required",
            task_id="task-ask",
            context_id="ctx-ask",
            status_message="What is your confirmation code?",
        )
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    # Prioritizes status message question over partial artifacts
    assert responses[0].message_pieces[0].converted_value == "What is your confirmation code?"
    assert target._conversations["conv-1234"].open_task_id == "task-ask"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_input_required_continuation(mock_send):
    mock_send.side_effect = [
        _rpc_response(
            _task_payload(
                None,
                state="input-required",
                task_id="task-open",
                context_id="ctx-open",
                status_message="Confirm?",
            )
        ),
        _rpc_response(_task_payload("Confirmed!", state="completed", task_id="task-open", context_id="ctx-open")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    r1 = await target.send_prompt_async(message=_user_message("start", conversation_id="c1"))
    assert r1[0].message_pieces[0].converted_value == "Confirm?"

    r2 = await target.send_prompt_async(message=_user_message("yes", conversation_id="c1"))
    assert r2[0].message_pieces[0].converted_value == "Confirmed!"

    second_req = mock_send.call_args_list[1][0][0]
    payload = json.loads(second_req.content)
    assert payload["params"]["message"]["taskId"] == "task-open"
    assert payload["params"]["message"]["contextId"] == "ctx-open"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_task_failed_state_returns_error_response(mock_send):
    mock_send.return_value = _rpc_response(
        _task_payload(None, state="failed", task_id="task-fail", status_message="Quota exceeded")
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    piece = responses[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert piece.response_error == "unknown"
    assert "Quota exceeded" in piece.converted_value
    assert target._conversations["conv-1234"].open_task_id is None


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_task_canceled_state_returns_error_response(mock_send):
    mock_send.return_value = _rpc_response(_task_payload(None, state="canceled", task_id="task-cancel"))

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    piece = responses[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert "CANCELED" in piece.converted_value


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_content_filter_error_is_blocked(mock_send):
    mock_send.return_value = _rpc_response(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "error": {"code": -32000, "message": "content_filter: prompt was blocked"},
        }
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    piece = responses[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert piece.response_error == "blocked"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_initial_send_rate_limit_retried(mock_send):
    mock_send.side_effect = [
        httpx.Response(429, request=httpx.Request("POST", ENDPOINT)),
        _rpc_response(_task_payload("Success after retry")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Success after retry"
    assert mock_send.call_count == 2


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.send")
async def test_a2a_polling_rate_limit_does_not_resubmit_prompt(mock_send, mock_sleep):
    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working", task_id="t-safe")),
        httpx.Response(429, request=httpx.Request("POST", ENDPOINT)),  # poll fails with 429
        _rpc_response(_task_payload("Poll success", state="completed", task_id="t-safe")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Poll success"
    assert mock_send.call_count == 3
    # First call: message/send
    assert json.loads(mock_send.call_args_list[0][0][0].content)["method"] == "message/send"
    # Second call: tasks/get (got 429)
    assert json.loads(mock_send.call_args_list[1][0][0].content)["method"] == "tasks/get"
    # Third call: tasks/get retry (did NOT call message/send again!)
    assert json.loads(mock_send.call_args_list[2][0][0].content)["method"] == "tasks/get"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_empty_response_raises(mock_send):
    mock_send.return_value = _rpc_response(_task_payload(None, state="completed", task_id="t-empty"))

    target = A2ATarget(endpoint=ENDPOINT)
    with pytest.raises(EmptyResponseException, match="completed but returned no text response"):
        await target.send_prompt_async(message=_user_message())


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_auth_headers_sent(mock_send):
    mock_send.return_value = _rpc_response(_message_payload("Authed"))

    target = A2ATarget(endpoint=ENDPOINT, auth_token="secret-bearer-token", api_key="custom-key")
    await target.send_prompt_async(message=_user_message())

    sent_req = mock_send.call_args[0][0]
    assert sent_req.headers["Authorization"] == "Bearer secret-bearer-token"
    assert sent_req.headers["X-API-Key"] == "custom-key"
