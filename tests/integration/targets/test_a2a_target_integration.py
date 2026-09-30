# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Literal

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

pytest.importorskip("a2a")

from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import a2a_pb2 as a2a
from a2a.utils.constants import TransportProtocol
from a2a.utils.errors import UnsupportedOperationError

from pyrit.exceptions import EmptyResponseException
from pyrit.models import Conversation, Message, MessagePiece
from pyrit.prompt_target import A2ATarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.server.events.event_queue_v2 import EventQueue
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request

    from pyrit.memory import SQLiteMemory

pytestmark = pytest.mark.run_only_if_all_tests


def _agent_message(*, text: str, context_id: str) -> a2a.Message:
    return a2a.Message(
        role=a2a.ROLE_AGENT,
        message_id=str(uuid.uuid4()),
        context_id=context_id,
        parts=[a2a.Part(text=text)],
    )


class _TestExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.contexts: dict[str, str] = {}
        self.requests: list[a2a.Message] = []
        self.release_task = asyncio.Event()
        self.task_finished = asyncio.Event()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:  # pyrit-async-suffix-exempt
        assert context.message is not None
        assert context.context_id is not None
        assert context.task_id is not None
        message = a2a.Message()
        message.CopyFrom(context.message)
        self.requests.append(message)
        text = context.get_user_input()
        context_id = context.context_id

        if text in ("ask confirmation", "yes", "fail", "empty", "poll"):
            task = a2a.Task(
                id=context.task_id,
                context_id=context_id,
                status=a2a.TaskStatus(state=a2a.TASK_STATE_COMPLETED),
            )
            if text == "ask confirmation":
                task.status.state = a2a.TASK_STATE_INPUT_REQUIRED
                task.status.message.CopyFrom(_agent_message(text="Are you sure?", context_id=context_id))
                task.artifacts.append(a2a.Artifact(artifact_id="partial", parts=[a2a.Part(text="partial preview")]))
            elif text == "yes":
                assert context.current_task is not None
                assert context.current_task.status.state == a2a.TASK_STATE_INPUT_REQUIRED
                task.artifacts.append(a2a.Artifact(artifact_id="done", parts=[a2a.Part(text="Confirmed.")]))
            elif text == "fail":
                task.status.state = a2a.TASK_STATE_FAILED
                task.status.message.CopyFrom(_agent_message(text="Execution failed.", context_id=context_id))
            elif text == "poll":
                task.status.state = a2a.TASK_STATE_WORKING
                await event_queue.enqueue_event(task)
                await self.release_task.wait()
                await event_queue.enqueue_event(
                    a2a.TaskArtifactUpdateEvent(
                        task_id=context.task_id,
                        context_id=context_id,
                        artifact=a2a.Artifact(artifact_id="done", parts=[a2a.Part(text="Finished after polling.")]),
                    )
                )
                await event_queue.enqueue_event(
                    a2a.TaskStatusUpdateEvent(
                        task_id=context.task_id,
                        context_id=context_id,
                        status=a2a.TaskStatus(state=a2a.TASK_STATE_COMPLETED),
                    )
                )
                self.task_finished.set()
                return
            await event_queue.enqueue_event(task)
            self.task_finished.set()
            return

        if text.startswith("remember "):
            self.contexts[context_id] = text.removeprefix("remember ")
            reply = "Stored."
        elif text == "recall":
            reply = self.contexts[context_id]
        else:
            reply = f"Echo: {text}"
        await event_queue.enqueue_event(_agent_message(text=reply, context_id=context_id))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:  # pyrit-async-suffix-exempt
        raise UnsupportedOperationError("Cancellation is not used by this test agent.")


class _PollingHandler(DefaultRequestHandler):
    async def on_message_send(  # pyrit-async-suffix-exempt
        self, params: a2a.SendMessageRequest, context: ServerCallContext
    ) -> a2a.Task | a2a.Message:
        if params.message.parts[0].text == "poll":
            # Simulate an agent that returns a pending task even for a blocking request.
            params.configuration.return_immediately = True
        result = await super().on_message_send(params, context)
        assert isinstance(result, (a2a.Task, a2a.Message))
        return result


_TestServer = tuple[str, _TestExecutor, dict[str, int]]


@pytest.fixture(params=["0.3", "1.0"])
async def a2a_test_server(
    request: pytest.FixtureRequest,
) -> AsyncIterator[_TestServer]:
    executor = _TestExecutor()
    counts = {"polls": 0, "cards": 0}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        card = a2a.AgentCard(
            name="PyRIT integration agent",
            description="Local deterministic SDK agent",
            version="1.0",
            supported_interfaces=[
                a2a.AgentInterface(
                    url=url + "/",
                    protocol_binding=TransportProtocol.JSONRPC,
                    protocol_version=request.param,
                )
            ],
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            capabilities=a2a.AgentCapabilities(),
            skills=[a2a.AgentSkill(id="test", name="Test", description="Test agent", tags=["test"])],
        )
        handler = _PollingHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)

        async def card_modifier_async(card: a2a.AgentCard) -> a2a.AgentCard:
            counts["cards"] += 1
            return card

        app = Starlette(
            routes=[
                *create_jsonrpc_routes(handler, "/", enable_v0_3_compat=True),
                *create_agent_card_routes(card, card_modifier=card_modifier_async),
                *create_agent_card_routes(card, card_url="/agentCard/custom", card_modifier=card_modifier_async),
            ]
        )

        async def rate_limit_poll_async(request: Request, call_next: RequestResponseEndpoint) -> Response:
            if request.method == "POST":
                body = await request.json()
                if body["method"] in ("tasks/get", "GetTask"):
                    counts["polls"] += 1
                    if counts["polls"] == 1:
                        return Response("Poll rate limited", status_code=429)
                    executor.release_task.set()
                    await asyncio.wait_for(executor.task_finished.wait(), timeout=5)
            return await call_next(request)

        app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit_poll_async)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off", ws="none"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                        pytest.fail("SDK server stopped before startup.")
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient() as client:
                response = await client.get(url + "/.well-known/agent-card.json")
                response.raise_for_status()
            counts["cards"] = 0
            yield url, executor, counts
        finally:
            executor.release_task.set()
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
            await handler.aclose()


def _make_msg(*, text: str, conversation_id: str) -> Message:
    return MessagePiece(role="user", original_value=text, conversation_id=conversation_id).to_message()


def _persist_turn(*, memory: SQLiteMemory, target: A2ATarget, request: Message, response: Message) -> None:
    conversation_id = request.get_piece().conversation_id
    assert conversation_id is not None
    memory.add_conversation_to_memory(
        conversation=Conversation(
            conversation_id=conversation_id,
            target_identifier=target.get_identifier(),
        )
    )
    memory.add_message_to_memory(request=request)
    memory.add_message_to_memory(request=response)


@pytest.mark.parametrize("protocol_version", ["0.3", "1.0", "auto"])
async def test_a2a_http_multi_turn_continuity(
    sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer, protocol_version: Literal["0.3", "1.0", "auto"]
) -> None:
    url, executor, counts = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version=protocol_version)
    cid = str(uuid.uuid4())
    request = _make_msg(text="remember avocado-42", conversation_id=cid)
    first = await target.send_prompt_async(message=request)
    assert first[0].get_value() == "Stored."
    _persist_turn(memory=sqlite_instance, target=target, request=request, response=first[0])
    assert len(sqlite_instance.get_conversation_messages(conversation_id=cid)) == 2
    second = await target.send_prompt_async(message=_make_msg(text="recall", conversation_id=cid))
    assert second[0].get_value() == "avocado-42"
    assert executor.requests[0].context_id == executor.requests[1].context_id
    assert counts["cards"] == (2 if protocol_version == "auto" else 0)


async def test_a2a_http_custom_card_discovery(sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer) -> None:
    url, executor, counts = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="auto", agent_card_path="/agentCard/custom")
    result = await target.send_prompt_async(message=_make_msg(text="hello", conversation_id=str(uuid.uuid4())))
    assert result[0].get_value() == "Echo: hello"
    assert counts["cards"] == 1


async def test_a2a_http_input_required_continuation(
    sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer
) -> None:
    url, executor, _ = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="auto")
    cid = str(uuid.uuid4())
    request = _make_msg(text="ask confirmation", conversation_id=cid)
    first = await target.send_prompt_async(message=request)
    assert first[0].get_value() == "Are you sure?"
    _persist_turn(memory=sqlite_instance, target=target, request=request, response=first[0])
    second = await target.send_prompt_async(message=_make_msg(text="yes", conversation_id=cid))
    assert second[0].get_value() == "Confirmed."
    assert executor.requests[1].task_id == executor.requests[0].task_id
    assert executor.requests[1].context_id == executor.requests[0].context_id
    assert target._conversations[cid].open_task_id is None


async def test_a2a_http_failed_task(sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer) -> None:
    url, _, _ = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="auto")
    responses = await target.send_prompt_async(message=_make_msg(text="fail", conversation_id=str(uuid.uuid4())))
    piece = responses[0].get_piece()
    assert piece.converted_value_data_type == "error"
    assert piece.response_error == "unknown"
    assert piece.converted_value == "Execution failed."


async def test_a2a_http_polling_rate_limit_no_resubmission(
    sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer
) -> None:
    url, executor, counts = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="auto", poll_interval_seconds=0.01, task_timeout_seconds=5)
    responses = await target.send_prompt_async(message=_make_msg(text="poll", conversation_id=str(uuid.uuid4())))
    assert responses[0].get_value() == "Finished after polling."
    assert counts["polls"] >= 2
    assert len(executor.requests) == 1


async def test_a2a_http_empty_task_no_resubmission(sqlite_instance: SQLiteMemory, a2a_test_server: _TestServer) -> None:
    url, executor, _ = a2a_test_server
    target = A2ATarget(endpoint=url, protocol_version="auto")
    with pytest.raises(EmptyResponseException):
        await target.send_prompt_async(message=_make_msg(text="empty", conversation_id=str(uuid.uuid4())))
    assert len(executor.requests) == 1


@pytest.mark.skipif(not os.getenv("A2A_FOUNDRY_ENDPOINT"), reason="A2A_FOUNDRY_ENDPOINT is not set")
async def test_a2a_foundry_live_integration(sqlite_instance: SQLiteMemory) -> None:
    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    async with AsyncExitStack() as stack:
        token = os.environ.get("A2A_FOUNDRY_AUTH_TOKEN")
        provider = None
        if not token:
            credential = await stack.enter_async_context(DefaultAzureCredential())
            provider = get_bearer_token_provider(credential, "https://ai.azure.com/.default")
        target = A2ATarget(
            endpoint=os.environ["A2A_FOUNDRY_ENDPOINT"],
            auth_token=token or provider,
            protocol_version="auto",
            agent_card_path=os.getenv("A2A_FOUNDRY_CARD_PATH", "agentCard/v1.0"),
        )
        responses = await target.send_prompt_async(
            message=_make_msg(text="Reply with a short greeting.", conversation_id=str(uuid.uuid4()))
        )
    piece = responses[0].get_piece()
    assert piece.response_error == "none"
    assert piece.converted_value_data_type == "text"
    assert piece.converted_value.strip()
