# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import httpx2
import openai
import pytest
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from unit.mocks import openai_chat_response_json_dict, openai_response_json_dict
from websockets.asyncio.client import ClientConnection

from pyrit.embedding import OpenAITextEmbedding
from pyrit.exceptions import PyritException, RateLimitException
from pyrit.models import Message
from pyrit.prompt_target import (
    LiteLLMChatTarget,
    OpenAIChatTarget,
    OpenAICompletionTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    RealtimeTarget,
)


@pytest.fixture(autouse=True)
def bounded_retries() -> Iterator[None]:
    with patch.dict("os.environ", {"RETRY_MAX_NUM_ATTEMPTS": "1"}):
        yield


@pytest.fixture(params=["native", "legacy"])
def http_module(request: pytest.FixtureRequest) -> ModuleType:
    if request.param == "native" and int(openai.__version__.split(".")[0]) >= 3:
        return httpx2
    return httpx


def _response_payload(target_type: type[OpenAITarget]) -> dict[str, Any]:
    if target_type is OpenAIResponseTarget:
        response = openai_response_json_dict()
        response["usage"] = {
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    else:
        response = openai_chat_response_json_dict()
        response["usage"] = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
        if target_type is OpenAICompletionTarget:
            response["object"] = "text_completion"
            response["choices"] = [{"index": 0, "text": "hi", "finish_reason": "stop", "logprobs": None}]
    return response


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("target_type", "api_path"),
    [
        (OpenAIChatTarget, "chat/completions"),
        (OpenAIResponseTarget, "responses"),
        (OpenAICompletionTarget, "completions"),
    ],
)
@pytest.mark.parametrize(
    "endpoint",
    ["https://example.test/v1", "https://test.openai.azure.com/openai/v1", "https://compatible.test/custom"],
)
async def test_sdk_request_and_response_async(
    *, http_module: ModuleType, target_type: type[OpenAITarget], api_path: str, endpoint: str
) -> None:
    requests: list[Any] = []

    def respond(request: Any) -> Any:
        requests.append(request)
        assert str(request.url) == f"{endpoint}/{api_path}?api-version=test-version"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["x-test-header"] == "test-value"
        assert json.loads(request.content)["model"] == "test-model"
        return http_module.Response(200, json=_response_payload(target_type))

    async def token_provider_async() -> str:
        return "test-token"

    async with http_module.AsyncClient(transport=http_module.MockTransport(respond)) as client:
        target = target_type(
            model_name="test-model",
            endpoint=endpoint,
            api_key=token_provider_async,
            headers='{"x-test-header": "test-value"}',
            httpx_client_kwargs={
                "http_client": client,
                "default_query": {"api-version": "test-version"},
                "timeout": httpx.Timeout(3, read=5),
                "max_retries": 0,
            },
        )
        responses = await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))

    piece = responses[0].get_piece()
    assert piece.converted_value == "hi"
    assert piece.response_error == "none"
    assert piece.prompt_metadata["token_usage_total_tokens"] == 5
    assert len(requests) == 1


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
@pytest.mark.parametrize(
    ("scenario", "expected_exception"),
    [
        ("policy", None),
        ("bad_request", openai.BadRequestError),
        ("authentication", openai.AuthenticationError),
        ("rate_limit", RateLimitException),
        ("server", openai.InternalServerError),
        ("timeout", openai.APITimeoutError),
        ("connection", openai.APIConnectionError),
    ],
)
async def test_sdk_errors_async(
    *,
    http_module: ModuleType,
    target_type: type[OpenAITarget],
    scenario: str,
    expected_exception: type[Exception] | None,
) -> None:
    calls = 0

    def respond(request: Any) -> Any:
        nonlocal calls
        calls += 1
        if scenario == "timeout":
            raise http_module.ReadTimeout("Synthetic timeout", request=request)
        if scenario == "connection":
            raise http_module.ConnectError("Synthetic connection failure", request=request)
        status = {"policy": 400, "bad_request": 400, "authentication": 401, "rate_limit": 429, "server": 503}
        return http_module.Response(
            status[scenario],
            headers={"retry-after": "0", "x-request-id": "test-request"},
            json={
                "error": {"code": "content_filter" if scenario == "policy" else "invalid_request", "message": "Test"}
            },
        )

    async with http_module.AsyncClient(transport=http_module.MockTransport(respond)) as client:
        target = target_type(
            model_name="test-model",
            endpoint="https://example.test/v1",
            api_key="test-key",
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        request = Message.from_prompt(prompt="hello", role="user")
        if expected_exception is None:
            responses = await target.send_prompt_async(message=request)
            assert responses[0].get_piece().response_error == "blocked"
        else:
            with pytest.raises(expected_exception):
                await target.send_prompt_async(message=request)
    assert calls == 1


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("media", ["image", "tts", "video"])
async def test_sdk_binary_responses_async(*, http_module: ModuleType, media: str) -> None:
    data = b"synthetic media bytes"
    paths: list[str] = []

    def respond(request: Any) -> Any:
        paths.append(request.url.path)
        if media == "tts" or request.url.path.endswith("/content"):
            return http_module.Response(200, content=data)
        if media == "image":
            return http_module.Response(
                200, json={"created": 123, "data": [{"b64_json": base64.b64encode(data).decode()}]}
            )
        return http_module.Response(
            200,
            json={
                "id": "video_test",
                "object": "video",
                "created_at": 123,
                "status": "completed",
                "model": "sora-2",
                "progress": 100,
                "seconds": "4",
                "size": "1280x720",
                "remixed_from_video_id": None,
            },
        )

    target_type = {"image": OpenAIImageTarget, "tts": OpenAITTSTarget, "video": OpenAIVideoTarget}[media]
    async with http_module.AsyncClient(transport=http_module.MockTransport(respond)) as client:
        target = target_type(
            model_name="test-model",
            endpoint="https://example.test/v1",
            api_key="test-key",
            httpx_client_kwargs={"http_client": client, "max_retries": 0},
        )
        responses = await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))

    piece = responses[0].get_piece()
    assert piece.converted_value_data_type == {"image": "image_path", "tts": "audio_path", "video": "video_path"}[media]
    assert await asyncio.to_thread(Path(piece.converted_value).read_bytes) == data
    assert len(paths) == (3 if media == "video" else 1)


async def test_sdk_embedding_response_async() -> None:
    http = httpx2 if int(openai.__version__.split(".")[0]) >= 3 else httpx

    def respond(request: Any) -> Any:
        assert request.url.path == "/v1/embeddings"
        return http.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.5]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async with http.AsyncClient(transport=http.MockTransport(respond)) as client:
        sdk = openai.AsyncOpenAI(api_key="test-key", base_url="https://example.test/v1", http_client=client)
        with patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI", return_value=sdk):
            target = OpenAITextEmbedding(
                api_key="test-key", endpoint="https://example.test/v1", model_name="test-model"
            )
        response = await target.generate_text_embedding_async(text="hello", encoding_format="float")
    assert response.data[0].embedding == [0.25, 0.5]
    assert response.usage.total_tokens == 2


@pytest.mark.usefixtures("patch_central_database")
async def test_sdk_realtime_events_async() -> None:
    socket = MagicMock(spec=ClientConnection)
    socket.send = AsyncMock()
    socket.close = AsyncMock()
    socket.recv = AsyncMock(
        side_effect=[
            json.dumps(event).encode()
            for event in [
                {"type": "response.created", "event_id": "e1", "response": {"id": "r1", "status": "in_progress"}},
                {
                    "type": "response.output_audio.delta",
                    "event_id": "e2",
                    "response_id": "r1",
                    "item_id": "i1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": base64.b64encode(b"\0\0" * 24).decode(),
                },
                {
                    "type": "response.output_audio_transcript.delta",
                    "event_id": "e3",
                    "response_id": "r1",
                    "item_id": "i1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "hello",
                },
                {
                    "type": "response.done",
                    "event_id": "e4",
                    "response": {"id": "r1", "status": "completed", "output": []},
                },
            ]
        ]
    )
    connection = AsyncRealtimeConnection(connection=socket)
    target = RealtimeTarget(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key="test-key",
        existing_convo={"test": connection},
    )
    try:
        await target.send_response_create_async("test")
        assert json.loads(socket.send.call_args.args[0])["type"] == "response.create"
        result = await target.receive_events_async("test")
        assert result.audio_bytes == b"\0\0" * 24
        assert result.transcripts == ["hello"]
    finally:
        await target.cleanup_target_async()
        await target._client.close()


@pytest.mark.usefixtures("patch_central_database")
async def test_sdk_realtime_url_async() -> None:
    socket = MagicMock(spec=ClientConnection)
    socket.close = AsyncMock()
    target = RealtimeTarget(
        model_name="test-model", endpoint="https://test.openai.azure.com/openai/v1", api_key="test-key"
    )
    try:
        transport = (
            "openai.lib._websocket._WebSocketConnect"
            if importlib.util.find_spec("openai.lib._websocket") is not None
            else "websockets.asyncio.client.connect"
        )
        with patch(transport, new_callable=AsyncMock, return_value=socket) as connect:
            target._existing_conversation["test"] = await target._connect_async(conversation_id="test")
        url = httpx.URL(connect.call_args.args[0])
        assert url.scheme == "wss"
        assert url.host == "test.openai.azure.com"
        assert url.path == "/openai/v1/realtime"
        assert url.params["model"] == "test-model"
    finally:
        await target.cleanup_target_async()
        await target._client.close()


@pytest.mark.usefixtures("patch_central_database")
async def test_sdk_default_transport_and_legacy_timeout_async() -> None:
    http = httpx2 if int(openai.__version__.split(".")[0]) >= 3 else httpx
    target = OpenAIChatTarget(
        model_name="test-model",
        endpoint="https://example.test/v1",
        api_key="test-key",
        httpx_client_kwargs={"timeout": httpx.Timeout(3, read=5), "max_retries": 0},
    )

    def respond(request: Any, **kwargs: Any) -> Any:
        assert request.extensions["timeout"]["read"] == 5
        return http.Response(200, request=request, json=_response_payload(OpenAIChatTarget))

    try:
        assert isinstance(target._client._client, http.AsyncClient)
        with patch.object(target._client._client, "send", new_callable=AsyncMock, side_effect=respond) as send:
            response = await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))
        assert response[0].get_piece().converted_value == "hi"
        send.assert_awaited_once()
    finally:
        await target._client.close()


@pytest.fixture(scope="module")
def litellm_sdk() -> None:
    with (
        patch.dict("os.environ", {"LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_TELEMETRY": "False"}),
        patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected network access")),
    ):
        pytest.importorskip("litellm")


@pytest.mark.usefixtures("patch_central_database", "litellm_sdk")
@pytest.mark.parametrize(
    ("status", "expected_exception"),
    [(200, None), (400, None), (401, PyritException), (429, RateLimitException)],
)
async def test_litellm_real_sdk_route_async(
    *, http_module: ModuleType, status: int, expected_exception: type[Exception] | None
) -> None:
    calls = 0

    def respond(request: Any) -> Any:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/chat/completions"
        if status == 200:
            return http_module.Response(200, json=_response_payload(OpenAIChatTarget))
        code, message = {
            400: ("content_filter", "Request blocked by content policy"),
            401: ("invalid_api_key", "Invalid API key"),
            429: ("rate_limit", "Too many requests"),
        }[status]
        return http_module.Response(status, json={"error": {"code": code, "message": message}})

    async with http_module.AsyncClient(transport=http_module.MockTransport(respond)) as client:
        sdk = openai.AsyncOpenAI(
            api_key="test-key", base_url="https://example.test/v1", http_client=client, max_retries=0
        )
        with patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected network access")):
            target = LiteLLMChatTarget(
                model_name="openai/gpt-4o",
                endpoint="https://example.test/v1",
                api_key="test-key",
                extra_body_parameters={"client": sdk, "num_retries": 0},
            )
            request = Message.from_prompt(prompt="hello", role="user")
            if expected_exception is None:
                responses = await target.send_prompt_async(message=request)
                assert responses[0].get_piece().response_error == ("blocked" if status == 400 else "none")
            else:
                with pytest.raises(expected_exception):
                    await target.send_prompt_async(message=request)
    assert calls == 1
