# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from typing import Any

import pytest
from aiohttp import web

from pyrit.memory import SQLiteMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import A2ATarget


@pytest.fixture
async def a2a_test_server():
    server_state: dict[str, Any] = {
        "contexts": {},
        "tasks": {},
        "poll_counts": {},
        "send_counts": {},
    }

    async def handle_rpc(request: web.Request) -> web.Response:
        data = await request.json()
        req_id = data.get("id", "1")
        method = data.get("method")
        params = data.get("params", {})

        if method == "message/send":
            msg = params.get("message", {})
            context_id = msg.get("contextId") or str(uuid.uuid4())
            task_id = msg.get("taskId")
            parts = msg.get("parts", [])
            text = parts[0].get("text", "") if parts else ""

            server_state["send_counts"][context_id] = server_state["send_counts"].get(context_id, 0) + 1

            if "rate-limit-poll" in text:
                t_id = "task-rl-1"
                server_state["tasks"][t_id] = {"state": "working", "context_id": context_id}
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "task",
                            "id": t_id,
                            "contextId": context_id,
                            "status": {"state": "working"},
                        },
                    }
                )

            if "fail me" in text:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "task",
                            "id": "task-fail-1",
                            "contextId": context_id,
                            "status": {
                                "state": "failed",
                                "message": {
                                    "messageId": str(uuid.uuid4()),
                                    "role": "agent",
                                    "parts": [{"kind": "text", "text": "Task execution failed: quota exceeded"}],
                                },
                            },
                        },
                    }
                )

            if "ask confirmation" in text:
                t_id = "task-ask-1"
                server_state["tasks"][t_id] = {"state": "input-required", "context_id": context_id}
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "task",
                            "id": t_id,
                            "contextId": context_id,
                            "status": {
                                "state": "input-required",
                                "message": {
                                    "messageId": str(uuid.uuid4()),
                                    "role": "agent",
                                    "parts": [{"kind": "text", "text": "Are you sure you want to proceed?"}],
                                },
                            },
                            "artifacts": [
                                {
                                    "artifactId": "art-partial",
                                    "parts": [{"kind": "text", "text": "partial preview"}],
                                }
                            ],
                        },
                    }
                )

            if task_id == "task-ask-1":
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "task",
                            "id": task_id,
                            "contextId": context_id,
                            "status": {"state": "completed"},
                            "artifacts": [
                                {
                                    "artifactId": "art-done",
                                    "parts": [{"kind": "text", "text": "Action confirmed and executed."}],
                                }
                            ],
                        },
                    }
                )

            # Multi-turn memory
            if "my secret is" in text:
                secret = text.split("my secret is", 1)[1].strip()
                server_state["contexts"][context_id] = secret
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "message",
                            "role": "agent",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "parts": [{"kind": "text", "text": "I stored your secret."}],
                        },
                    }
                )

            if "what is my secret" in text:
                secret = server_state["contexts"].get(context_id, "unknown")
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "message",
                            "role": "agent",
                            "messageId": str(uuid.uuid4()),
                            "contextId": context_id,
                            "parts": [{"kind": "text", "text": f"Your secret is {secret}."}],
                        },
                    }
                )

            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "kind": "message",
                        "role": "agent",
                        "messageId": str(uuid.uuid4()),
                        "contextId": context_id,
                        "parts": [{"kind": "text", "text": f"Echo: {text}"}],
                    },
                }
            )

        if method == "tasks/get":
            t_id = params.get("id")
            count = server_state["poll_counts"].get(t_id, 0)
            server_state["poll_counts"][t_id] = count + 1

            if t_id == "task-rl-1":
                if count == 0:
                    return web.Response(status=429, text="Rate limited, try again")
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "kind": "task",
                            "id": t_id,
                            "contextId": server_state["tasks"][t_id]["context_id"],
                            "status": {"state": "completed"},
                            "artifacts": [
                                {
                                    "artifactId": "art-rl-done",
                                    "parts": [{"kind": "text", "text": "Task finished after 429 poll retry."}],
                                }
                            ],
                        },
                    }
                )

        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )

    app = web.Application()
    app.router.add_post("/", handle_rpc)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    sockets = getattr(server, "sockets", None)
    assert sockets
    port = sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    try:
        yield url, server_state
    finally:
        await runner.cleanup()


def _make_msg(text: str, conversation_id: str) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value=text,
                converted_value=text,
                conversation_id=conversation_id,
            )
        ]
    )


async def test_a2a_http_multi_turn_continuity(sqlite_instance: SQLiteMemory, a2a_test_server):
    url, server_state = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="0.3")
    cid = str(uuid.uuid4())

    r1 = await target.send_prompt_async(message=_make_msg("my secret is avocado-42", conversation_id=cid))
    assert r1[0].message_pieces[0].converted_value == "I stored your secret."

    r2 = await target.send_prompt_async(message=_make_msg("what is my secret?", conversation_id=cid))
    assert r2[0].message_pieces[0].converted_value == "Your secret is avocado-42."


async def test_a2a_http_input_required_continuation(sqlite_instance: SQLiteMemory, a2a_test_server):
    url, server_state = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="0.3")
    cid = str(uuid.uuid4())

    r1 = await target.send_prompt_async(message=_make_msg("ask confirmation for payment", conversation_id=cid))
    # Must prioritize question from status message over partial artifacts
    assert r1[0].message_pieces[0].converted_value == "Are you sure you want to proceed?"

    r2 = await target.send_prompt_async(message=_make_msg("yes, proceed", conversation_id=cid))
    assert r2[0].message_pieces[0].converted_value == "Action confirmed and executed."


async def test_a2a_http_failed_task(sqlite_instance: SQLiteMemory, a2a_test_server):
    url, server_state = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="0.3")
    cid = str(uuid.uuid4())

    responses = await target.send_prompt_async(message=_make_msg("fail me please", conversation_id=cid))
    piece = responses[0].message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert "quota exceeded" in piece.converted_value


async def test_a2a_http_polling_rate_limit_no_resubmission(sqlite_instance: SQLiteMemory, a2a_test_server):
    url, server_state = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="0.3", poll_interval_seconds=0.05)
    cid = str(uuid.uuid4())

    responses = await target.send_prompt_async(message=_make_msg("rate-limit-poll test", conversation_id=cid))
    assert responses[0].message_pieces[0].converted_value == "Task finished after 429 poll retry."

    # Verify that poll was retried
    assert server_state["poll_counts"].get("task-rl-1") == 2
    # Verify that prompt was submitted exactly ONCE (not resubmitted due to 429 on poll!)
    ctx = target._conversations[cid].context_id
    assert server_state["send_counts"][ctx] == 1


@pytest.mark.skipif(
    not os.getenv("A2A_FOUNDRY_ENDPOINT"),
    reason="A2A_FOUNDRY_ENDPOINT environment variable not set",
)
async def test_a2a_foundry_live_integration(sqlite_instance: SQLiteMemory):
    endpoint = os.environ["A2A_FOUNDRY_ENDPOINT"]
    token = os.environ.get("A2A_FOUNDRY_AUTH_TOKEN")
    api_key = os.environ.get("A2A_FOUNDRY_API_KEY")

    target = A2ATarget(endpoint=endpoint, auth_token=token, api_key=api_key)
    msg = _make_msg("Ping test for A2A Foundry integration", conversation_id=str(uuid.uuid4()))
    responses = await target.send_prompt_async(message=msg)
    assert responses[0].message_pieces[0].converted_value
