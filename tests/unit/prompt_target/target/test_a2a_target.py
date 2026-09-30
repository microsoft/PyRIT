# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pyrit.exceptions import EmptyResponseException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import A2ATarget
from pyrit.prompt_target.a2a_target import _A2AConversationState
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
def a2a_dependencies(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.name != "test_a2a_target_missing_sdk_raises_import_error":
        pytest.importorskip("a2a")
    with patch.dict("os.environ", {"RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"}):
        yield


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_initialization():
    target = A2ATarget(endpoint=ENDPOINT + "/", auth_token="test-token", api_key="test-api-key")
    assert target._endpoint == ENDPOINT
    assert target._protocol_version == "0.3"
    assert "Authorization" not in target._build_headers()
    assert target._build_headers()["X-API-Key"] == "test-api-key"
    assert target._build_headers()["Accept"] == "application/json"


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_identifier():
    target = A2ATarget(endpoint=ENDPOINT, auth_token="secret-token", api_key="secret-key")
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == ENDPOINT
    assert identifier.params["protocol_version"] == "0.3"
    assert "task_timeout_seconds" not in identifier.params
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
    with pytest.raises(ValueError, match="does not support supports_system_prompt"):
        A2ATarget(endpoint=ENDPOINT, custom_configuration=system_prompt_config)


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_missing_sdk_raises_import_error() -> None:
    real_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "a2a" or name.startswith("a2a."):
            raise ImportError("No module named 'a2a'")
        return real_import(name, *args, **kwargs)

    with (
        patch("builtins.__import__", side_effect=mock_import),
        pytest.raises(ImportError, match="pip install pyrit\\[a2a\\]"),
    ):
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
@patch("httpx.AsyncClient.send")
async def test_a2a_pending_task_times_out(mock_send):
    mock_send.return_value = _rpc_response(_task_payload(None, state="working", task_id="task-stuck"))

    target = A2ATarget(endpoint=ENDPOINT, task_timeout_seconds=0.01)
    with pytest.raises(TimeoutError, match="still in state TASK_STATE_WORKING"):
        await target.send_prompt_async(message=_user_message())
    assert mock_send.call_count == 1
    assert target._conversations["conv-1234"].context_id == "ctx-1"


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
    first_message = json.loads(mock_send.call_args_list[0][0][0].content)["params"]["message"]
    second_message = json.loads(mock_send.call_args_list[1][0][0].content)["params"]["message"]
    assert first_message == second_message


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
    assert mock_send.call_count == 1


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_auth_headers_sent() -> None:
    requests: list[httpx.Request] = []

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_message_payload("Authed"))

    target = A2ATarget(
        endpoint=ENDPOINT,
        auth_token="secret-bearer-token",
        api_key="custom-key",
        transport=httpx.MockTransport(handle_async),
    )
    await target.send_prompt_async(message=_user_message())
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer secret-bearer-token"
    assert requests[0].headers["X-API-Key"] == "custom-key"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "flag",
    [
        "supports_multi_message_pieces",
        "supports_editable_history",
        "supports_system_prompt",
        "supports_json_output",
        "supports_json_schema",
        "supports_streaming_audio",
    ],
)
def test_a2a_capability_updates_reject_unsupported_flags(flag: str) -> None:
    capabilities = TargetCapabilities.model_validate({"supports_multi_turn": True, flag: True})
    with pytest.raises(ValueError, match=flag):
        A2ATarget(endpoint=ENDPOINT, custom_configuration=TargetConfiguration(capabilities=capabilities))

    target = A2ATarget(endpoint=ENDPOINT)
    original = target.capabilities
    with pytest.raises(ValueError, match=flag):
        target.apply_capabilities(capabilities=capabilities)
    assert target.capabilities == original


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_identity_excludes_operational_settings() -> None:
    target = A2ATarget(endpoint=ENDPOINT, routing_identifier="agent-one")
    same = A2ATarget(
        endpoint=ENDPOINT,
        routing_identifier="agent-one",
        task_timeout_seconds=40,
        request_timeout_seconds=10,
        poll_interval_seconds=0.1,
        auth_token="different-secret",
        headers={"X-Agent": "one"},
    )
    assert target.get_identifier().hash == same.get_identifier().hash
    for kwargs in (
        {"routing_identifier": "agent-two"},
        {"routing_identifier": "agent-one", "protocol_version": "1.0"},
        {"routing_identifier": "agent-one", "agent_card_path": "agentCard/v1.0"},
    ):
        assert target.get_identifier().hash != A2ATarget(endpoint=ENDPOINT, **kwargs).get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_context_entry_without_context_id_does_not_restore_history() -> None:
    target = A2ATarget(endpoint=ENDPOINT)
    target._conversations["conv-1234"] = _A2AConversationState()
    with pytest.raises(ValueError, match="has no upstream context"):
        await target._send_prompt_to_target_async(normalized_conversation=[_user_message(), _user_message()])


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_multiple_pieces_never_silently_discarded(mock_send: AsyncMock) -> None:
    target = A2ATarget(endpoint=ENDPOINT)
    message = Message(message_pieces=[_user_message("one").get_piece(), _user_message("two").get_piece()])
    with pytest.raises(ValueError, match="single message piece"):
        await target.send_prompt_async(message=message)
    mock_send.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "payload",
    [
        _message_payload(""),
        _task_payload(None, state="completed"),
        _task_payload(None, state="input-required"),
        _task_payload(None, state="auth-required"),
    ],
)
@patch("httpx.AsyncClient.send")
async def test_a2a_accepted_empty_result_never_resubmitted(mock_send: AsyncMock, payload: dict[str, Any]) -> None:
    mock_send.return_value = _rpc_response(payload)
    target = A2ATarget(endpoint=ENDPOINT)
    with pytest.raises(EmptyResponseException):
        await target.send_prompt_async(message=_user_message())
    mock_send.assert_called_once()
    assert target._conversations["conv-1234"].context_id == "ctx-1"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_relayed_rate_limit_is_not_safe_to_resubmit(mock_send: AsyncMock) -> None:
    mock_send.return_value = _rpc_response(
        {"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "Model 429: rate limit"}}
    )
    response = await A2ATarget(endpoint=ENDPOINT).send_prompt_async(message=_user_message())
    assert response[0].get_piece().response_error == "unknown"
    mock_send.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_request_timeout_is_explicit(mock_send: AsyncMock) -> None:
    mock_send.return_value = _rpc_response(_message_payload("ok"))
    await A2ATarget(endpoint=ENDPOINT, request_timeout_seconds=35).send_prompt_async(message=_user_message())
    assert mock_send.call_args[0][0].extensions["timeout"]["read"] == 35


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_discovery_error_does_not_fall_back_to_submission(mock_send: AsyncMock) -> None:
    from a2a.client.errors import AgentCardResolutionError

    mock_send.return_value = httpx.Response(404, request=httpx.Request("GET", ENDPOINT))
    with pytest.raises(AgentCardResolutionError):
        await A2ATarget(endpoint=ENDPOINT, protocol_version="auto").send_prompt_async(message=_user_message())
    assert all(call.args[0].method == "GET" for call in mock_send.call_args_list)


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_non_text_output_never_resubmitted(mock_send: AsyncMock) -> None:
    payload = _message_payload("ignored")
    payload["result"]["parts"] = [{"kind": "data", "data": {"answer": 42}}]
    mock_send.return_value = _rpc_response(payload)
    with pytest.raises(EmptyResponseException, match="no text"):
        await A2ATarget(endpoint=ENDPOINT).send_prompt_async(message=_user_message())
    mock_send.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_programming_errors_propagate_without_retry(mock_send: AsyncMock) -> None:
    mock_send.side_effect = TypeError("unexpected client bug")
    with pytest.raises(TypeError, match="unexpected client bug"):
        await A2ATarget(endpoint=ENDPOINT).send_prompt_async(message=_user_message())
    mock_send.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_poll_error_never_resubmits(mock_send: AsyncMock) -> None:
    from a2a.client.errors import A2AClientError

    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working")),
        httpx.Response(503, request=httpx.Request("POST", ENDPOINT)),
    ]
    with pytest.raises(A2AClientError, match="503"):
        await A2ATarget(endpoint=ENDPOINT, poll_interval_seconds=0).send_prompt_async(message=_user_message())
    assert mock_send.call_count == 2
    assert json.loads(mock_send.call_args_list[1].args[0].content)["method"] == "tasks/get"


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_empty_sdk_iterator_never_resubmitted() -> None:
    from a2a.client import Client

    client = MagicMock(spec=Client)
    iterator = MagicMock(spec=AsyncIterator)
    iterator.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
    client.send_message.return_value = iterator
    target = A2ATarget(endpoint=ENDPOINT)
    with (
        patch.object(target, "_create_a2a_client_async", return_value=client),
        pytest.raises(EmptyResponseException, match="empty response stream"),
    ):
        await target.send_prompt_async(message=_user_message())
    client.send_message.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_rejects_single_turn_override() -> None:
    capabilities = TargetCapabilities(supports_multi_turn=False)
    with pytest.raises(ValueError, match="requires supports_multi_turn=True"):
        A2ATarget(endpoint=ENDPOINT, custom_configuration=TargetConfiguration(capabilities=capabilities))
    target = A2ATarget(endpoint=ENDPOINT)
    with pytest.raises(ValueError, match="requires supports_multi_turn=True"):
        target.apply_capabilities(capabilities=capabilities)
    assert target.capabilities.supports_multi_turn


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("name", ["task_timeout_seconds", "request_timeout_seconds", "poll_interval_seconds"])
@pytest.mark.parametrize("value", [-1, float("inf"), float("-inf"), float("nan")])
def test_a2a_rejects_invalid_waits(*, name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        A2ATarget(endpoint=ENDPOINT, **{name: value})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("name", ["task_timeout_seconds", "request_timeout_seconds"])
def test_a2a_rejects_zero_timeout(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        A2ATarget(endpoint=ENDPOINT, **{name: 0})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("timeout", [None, 12.0, httpx.Timeout(10, read=30)])
@patch("httpx.AsyncClient.send")
async def test_a2a_httpx_timeout_is_preserved(mock_send: AsyncMock, timeout: httpx.Timeout | float | None) -> None:
    mock_send.return_value = _rpc_response(_message_payload("ok"))
    target = A2ATarget(endpoint=ENDPOINT, timeout=timeout)
    await target.send_prompt_async(message=_user_message())
    assert mock_send.call_args.args[0].extensions["timeout"] == httpx.Timeout(timeout).as_dict()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("timeout", [-1, 0, float("inf"), float("nan"), httpx.Timeout(10, read=-1)])
def test_a2a_rejects_invalid_httpx_timeout(timeout: httpx.Timeout | float) -> None:
    with pytest.raises(ValueError, match="HTTPX timeouts"):
        A2ATarget(endpoint=ENDPOINT, timeout=timeout)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "kwargs",
    [
        {"request_timeout_seconds": 10, "timeout": None},
        {"auth_token": "token", "auth": httpx.BasicAuth("user", "password")},
        {"auth_token": "token", "headers": {"authorization": "Bearer other"}},
        {"auth_token": "token", "api_key": "key", "api_key_header": "Authorization"},
    ],
)
def test_a2a_rejects_conflicting_options(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Specify either"):
        A2ATarget(endpoint=ENDPOINT, **kwargs)


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_provider_refreshes_discovery_submission_retry_and_polls() -> None:
    requests: list[httpx.Request] = []
    provider = AsyncMock(side_effect=["card-token", "send-token", "retry-token", "poll-token"])

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "name": "Test",
                    "description": "Test",
                    "version": "1",
                    "protocolVersion": "0.3.0",
                    "url": ENDPOINT,
                    "capabilities": {},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                },
            )
        if len(requests) == 2:
            return httpx.Response(429)
        return httpx.Response(
            200, json=(_task_payload(None, state="working") if len(requests) == 3 else _task_payload("done"))
        )

    target = A2ATarget(
        endpoint=ENDPOINT,
        protocol_version="auto",
        auth_token=provider,
        transport=httpx.MockTransport(handle_async),
        poll_interval_seconds=0,
    )
    result = await target.send_prompt_async(message=_user_message())
    assert result[0].get_value() == "done"
    assert provider.await_count == 4
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer card-token",
        "Bearer send-token",
        "Bearer retry-token",
        "Bearer poll-token",
    ]
    first = json.loads(requests[1].content)["params"]["message"]
    retry = json.loads(requests[2].content)["params"]["message"]
    assert first["messageId"] == retry["messageId"]
    assert "token" not in str(target.get_identifier().params)


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_provider_failure_is_not_retried() -> None:
    provider = AsyncMock(side_effect=ValueError("credential failed"))
    handler = AsyncMock()
    target = A2ATarget(endpoint=ENDPOINT, auth_token=provider, transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="credential failed"):
        await target.send_prompt_async(message=_user_message())
    provider.assert_awaited_once()
    handler.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_empty_provider_token_fails_before_http() -> None:
    provider = AsyncMock(return_value=" ")
    handler = AsyncMock()
    target = A2ATarget(endpoint=ENDPOINT, auth_token=provider, transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="non-empty token"):
        await target.send_prompt_async(message=_user_message())
    provider.assert_awaited_once()
    handler.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_custom_httpx_auth_is_supported() -> None:
    auth = httpx.BasicAuth("user", "password")
    requests: list[httpx.Request] = []

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_message_payload("ok"))

    target = A2ATarget(endpoint=ENDPOINT, auth=auth, transport=httpx.MockTransport(handle_async))
    await target.send_prompt_async(message=_user_message())
    assert requests[0].headers["Authorization"].startswith("Basic ")


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("action", ["send", "reset", "cancel"])
async def test_a2a_serializes_conversation_state(action: str) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    requests: list[dict[str, Any]] = []

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content)["params"]["message"])
        if len(requests) == 1:
            started.set()
            await release.wait()
        return httpx.Response(200, json=_message_payload("ok", context_id="ctx-locked"))

    target = A2ATarget(endpoint=ENDPOINT, transport=httpx.MockTransport(handle_async))
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        first = tasks.create_task(target.send_prompt_async(message=_user_message("first")))
        await started.wait()
        with pytest.raises(RuntimeError, match="conversation is in use"):
            target.set_conversation_context(conversation_id="conv-1234", context_id="replacement")
        if action == "reset":
            second = tasks.create_task(target.reset_conversation_async(conversation_id="conv-1234"))
        else:
            second = tasks.create_task(target.send_prompt_async(message=_user_message("second")))
        await asyncio.sleep(0)
        assert not second.done()
        assert len(requests) == 1
        if action == "cancel":
            first.cancel()
        release.set()
    if action == "reset":
        assert "conv-1234" not in target._conversations
        assert len(requests) == 1
    else:
        assert len(requests) == 2
        if action == "send":
            assert requests[1]["contextId"] == "ctx-locked"
    assert all(not lock.locked() for lock in target._conversation_locks.values())


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_different_conversations_remain_concurrent() -> None:
    both_started = asyncio.Event()
    requests: list[httpx.Request] = []

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 2:
            both_started.set()
        await both_started.wait()
        return httpx.Response(200, json=_message_payload("ok"))

    target = A2ATarget(endpoint=ENDPOINT, transport=httpx.MockTransport(handle_async))
    async with asyncio.timeout(5):
        await asyncio.gather(
            target.send_prompt_async(message=_user_message(conversation_id="one")),
            target.send_prompt_async(message=_user_message(conversation_id="two")),
        )
    assert len(requests) == 2


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.send")
async def test_a2a_poll_respects_retry_after(mock_send: AsyncMock, mock_sleep: AsyncMock) -> None:
    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working")),
        httpx.Response(429, headers={"Retry-After": "7"}, request=httpx.Request("POST", ENDPOINT)),
        _rpc_response(_task_payload(None, state="working")),
        _rpc_response(_task_payload("done")),
    ]
    await A2ATarget(endpoint=ENDPOINT).send_prompt_async(message=_user_message())
    assert [call.args[0] for call in mock_sleep.await_args_list] == [1, 7, 1]
    assert [json.loads(call.args[0].content)["method"] for call in mock_send.call_args_list] == [
        "message/send",
        "tasks/get",
        "tasks/get",
        "tasks/get",
    ]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("5", 5), ("Wed, 21 Oct 2015 07:28:00 GMT", 5), ("-1", 0), ("invalid", None), ("inf", None)],
)
def test_a2a_retry_after_parsing(*, header: str, expected: float | None) -> None:
    with patch("pyrit.prompt_target.a2a_target.time.time", return_value=1445412475):
        assert A2ATarget._retry_after_seconds(httpx.Response(429, headers={"Retry-After": header})) == expected


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.send")
async def test_a2a_submission_and_poll_are_rate_limited(mock_send: AsyncMock, mock_sleep: AsyncMock) -> None:
    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working")),
        _rpc_response(_task_payload("done")),
    ]
    target = A2ATarget(endpoint=ENDPOINT, max_requests_per_minute=60, poll_interval_seconds=0)
    await target.send_prompt_async(message=_user_message())
    assert [call.args[0] for call in mock_sleep.await_args_list] == [1, 0, 1]


@pytest.mark.usefixtures("patch_central_database")
async def test_a2a_poll_deadline_cancels_inflight_http_request() -> None:
    requests: list[httpx.Request] = []

    async def handle_async(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=_task_payload(None, state="working"))
        await asyncio.Event().wait()
        raise AssertionError("The poll must be cancelled.")

    target = A2ATarget(
        endpoint=ENDPOINT,
        task_timeout_seconds=0.02,
        poll_interval_seconds=0,
        timeout=None,
        transport=httpx.MockTransport(handle_async),
    )
    with pytest.raises(TimeoutError, match="TASK_STATE_WORKING"):
        await target.send_prompt_async(message=_user_message())
    assert len(requests) == 2
    assert all(not lock.locked() for lock in target._conversation_locks.values())


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.send")
async def test_a2a_poll_deadline_bounds_retry_after(mock_send: AsyncMock) -> None:
    mock_send.side_effect = [
        _rpc_response(_task_payload(None, state="working")),
        httpx.Response(429, headers={"Retry-After": "3600"}, request=httpx.Request("POST", ENDPOINT)),
    ]
    target = A2ATarget(endpoint=ENDPOINT, task_timeout_seconds=0.02, poll_interval_seconds=0)
    async with asyncio.timeout(5):
        with pytest.raises(TimeoutError, match="TASK_STATE_WORKING"):
            await target.send_prompt_async(message=_user_message())
    assert mock_send.call_count == 2
