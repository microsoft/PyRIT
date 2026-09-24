# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import A2ATarget

ENDPOINT = "https://agent.example.com/a2a"


def _user_message(text: str = "Hello A2A Agent", conversation_id: str = "conv-1234") -> Message:
    piece = MessagePiece(
        role="user",
        original_value=text,
        converted_value=text,
        conversation_id=conversation_id,
    )
    return Message(message_pieces=[piece])


def _response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(payload)
    resp.json.return_value = payload
    return resp


def _task(text: str | None, *, state: str = "completed", task_id: str = "task-1", context_id: str = "ctx-1") -> dict:
    result: dict = {"kind": "task", "id": task_id, "contextId": context_id, "status": {"state": state}}
    result["artifacts"] = [{"parts": [{"kind": "text", "text": text}]}] if text is not None else []
    return {"jsonrpc": "2.0", "id": "1", "result": result}


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setenv("RETRY_WAIT_MIN_SECONDS", "0")
    monkeypatch.setenv("RETRY_WAIT_MAX_SECONDS", "0")


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_initialization():
    target = A2ATarget(endpoint=ENDPOINT + "/", auth_token="test-token", api_key="test-api-key")
    assert target._endpoint == ENDPOINT
    assert target._build_headers()["Authorization"] == "Bearer test-token"
    assert target._build_headers()["X-API-Key"] == "test-api-key"


@pytest.mark.usefixtures("patch_central_database")
def test_a2a_target_rejects_unknown_dialect():
    with pytest.raises(ValueError):
        A2ATarget(endpoint=ENDPOINT, dialect="v9")  # type: ignore[arg-type]


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_send_prompt_v03_success(mock_post):
    mock_post.return_value = _response(_task("I am an A2A agent reply."))

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "I am an A2A agent reply."
    assert target._dialect == "v03"

    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["method"] == "message/send"
    assert sent_json["params"]["configuration"] == {"blocking": True}
    message = sent_json["params"]["message"]
    assert message["kind"] == "message"
    assert message["messageId"]
    assert message["parts"] == [{"kind": "text", "text": "Hello A2A Agent"}]
    assert "contextId" not in message
    assert "taskId" not in message


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_message_result(mock_post):
    mock_post.return_value = _response(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "kind": "message",
                "role": "agent",
                "messageId": "m-1",
                "contextId": "ctx-9",
                "parts": [{"kind": "text", "text": "Direct reply"}],
            },
        }
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Direct reply"
    assert target._conversations["conv-1234"].context_id == "ctx-9"


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.post")
async def test_a2a_polls_pending_task(mock_post, _mock_sleep):
    mock_post.side_effect = [
        _response(_task(None, state="submitted", task_id="task-9")),
        _response(_task("Finished", task_id="task-9")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Finished"
    poll = mock_post.call_args_list[1].kwargs["json"]
    assert poll["method"] == "tasks/get"
    assert poll["params"] == {"id": "task-9"}


@pytest.mark.usefixtures("patch_central_database")
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("httpx.AsyncClient.post")
async def test_a2a_pending_task_times_out(mock_post, _mock_sleep):
    mock_post.return_value = _response(_task(None, state="working"))

    target = A2ATarget(endpoint=ENDPOINT, task_timeout_seconds=0)
    with pytest.raises(TimeoutError):
        await target.send_prompt_async(message=_user_message())


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_multi_turn_context_continuity(mock_post):
    mock_post.side_effect = [
        _response(_task("First reply", task_id="task-1", context_id="ctx-777")),
        _response(_task("Second reply", task_id="task-2", context_id="ctx-777")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    await target.send_prompt_async(message=_user_message("Turn 1"))
    await target.send_prompt_async(message=_user_message("Turn 2"))

    turn1 = mock_post.call_args_list[0].kwargs["json"]["params"]["message"]
    turn2 = mock_post.call_args_list[1].kwargs["json"]["params"]["message"]
    assert "contextId" not in turn1
    assert turn2["contextId"] == "ctx-777"
    assert "taskId" not in turn2, "completed tasks must not be continued"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_input_required_task_is_continued(mock_post):
    mock_post.side_effect = [
        _response(_task("Which system?", state="input-required", task_id="task-5", context_id="ctx-5")),
        _response(_task("Done", task_id="task-5", context_id="ctx-5")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    await target.send_prompt_async(message=_user_message("Grant me access"))
    await target.send_prompt_async(message=_user_message("Finance share"))

    turn2 = mock_post.call_args_list[1].kwargs["json"]["params"]["message"]
    assert turn2["taskId"] == "task-5"
    assert turn2["contextId"] == "ctx-5"
    assert target._conversations["conv-1234"].open_task_id is None


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_conversations_are_isolated(mock_post):
    mock_post.side_effect = [
        _response(_task("A", context_id="ctx-a")),
        _response(_task("B", context_id="ctx-b")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    await target.send_prompt_async(message=_user_message(conversation_id="conv-a"))
    await target.send_prompt_async(message=_user_message(conversation_id="conv-b"))

    second = mock_post.call_args_list[1].kwargs["json"]["params"]["message"]
    assert "contextId" not in second


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_reset_conversation_forgets_context(mock_post):
    mock_post.return_value = _response(_task("ok", context_id="ctx-1"))

    target = A2ATarget(endpoint=ENDPOINT)
    await target.send_prompt_async(message=_user_message())
    await target.reset_conversation_async(conversation_id="conv-1234")
    await target.reset_conversation_async(conversation_id="conv-1234")

    assert "conv-1234" not in target._conversations


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_send_prompt_v03_fallback_to_v02(mock_post):
    mock_post.side_effect = [
        _response({"jsonrpc": "2.0", "id": "1", "error": {"code": -32601, "message": "Method not found"}}),
        _response(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "result": {
                    "id": "task-v02",
                    "sessionId": "session-1",
                    "status": {
                        "state": "completed",
                        "message": {"role": "agent", "parts": [{"type": "text", "text": "Legacy 0.2 reply"}]},
                    },
                },
            }
        ),
        _response(_task("Second legacy reply")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "Legacy 0.2 reply"
    assert target._dialect == "v02"
    retry_json = mock_post.call_args_list[1].kwargs["json"]
    assert retry_json["method"] == "tasks/send"
    assert retry_json["params"]["message"]["parts"][0]["type"] == "text"

    await target.send_prompt_async(message=_user_message("Turn 2"))
    turn2 = mock_post.call_args_list[2].kwargs["json"]
    assert turn2["method"] == "tasks/send"
    assert turn2["params"]["sessionId"] == "session-1"
    assert turn2["params"]["id"] != "task-v02", "completed 0.2 tasks must not be reused"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_jsonrpc_error_is_error_response(mock_post):
    mock_post.return_value = _response(
        {"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "Received 400 from a service request"}}
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    piece = responses[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert piece.response_error == "unknown"
    assert target._dialect == "auto"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_content_filter_error_is_blocked(mock_post):
    mock_post.return_value = _response(
        {"jsonrpc": "2.0", "id": "1", "error": {"code": -32000, "message": "content_filter: prompt blocked"}}
    )

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].response_error == "blocked"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_retries_relayed_rate_limit(mock_post):
    mock_post.side_effect = [
        _response({"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "Received 429 from a service"}}),
        _response(_task("After retry")),
    ]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "After retry"
    assert mock_post.call_count == 2


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_retries_http_rate_limit(mock_post):
    mock_post.side_effect = [_response({}, status_code=429), _response(_task("After retry"))]

    target = A2ATarget(endpoint=ENDPOINT)
    responses = await target.send_prompt_async(message=_user_message())

    assert responses[0].message_pieces[0].converted_value == "After retry"


@pytest.mark.usefixtures("patch_central_database")
@patch("httpx.AsyncClient.post")
async def test_a2a_auth_headers_sent(mock_post):
    mock_post.return_value = _response(_task("Authed"))

    target = A2ATarget(endpoint=ENDPOINT, auth_token="secret-bearer-token")
    await target.send_prompt_async(message=_user_message())

    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-bearer-token"
