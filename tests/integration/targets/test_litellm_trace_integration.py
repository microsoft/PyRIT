# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Verify trace headers through real LiteLLM adapters against a loopback server."""

import hashlib
import json
import os
import threading
from collections.abc import Callable, Iterator
from email.message import Message as HTTPHeaders
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from pyrit.models import Message, MessagePiece, RequestTraceContext
from pyrit.prompt_target import LiteLLMChatTarget, TargetTraceConfig

if TYPE_CHECKING:
    import tiktoken

pytestmark = [pytest.mark.run_only_if_all_tests, pytest.mark.usefixtures("patch_central_database")]

_TOKENIZER_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
_TOKENIZER_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
_OPENAI_REPLY = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
_ANTHROPIC_REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "hello"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
_WIRE_PROVIDERS = pytest.mark.parametrize(
    ("model_name", "base_path", "request_path"),
    [
        ("openai/gpt-4o", "/v1", "/v1/chat/completions"),
        ("anthropic/claude-sonnet-4-6", "", "/v1/messages"),
    ],
)


@pytest.fixture
def official_cl100k_encoding(tmp_path: Path) -> Iterator["tiktoken.Encoding"]:
    cache_dir = tmp_path / "official-tokenizer"
    with patch.dict(
        os.environ,
        {
            "TIKTOKEN_CACHE_DIR": str(cache_dir),
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS": "true",
        },
    ):
        pytest.importorskip("litellm")
        import tiktoken
        import tiktoken.registry

        source = os.environ.get("PYRIT_TEST_TIKTOKEN_ASSET")
        if not source:
            raise RuntimeError(
                "Set PYRIT_TEST_TIKTOKEN_ASSET to the official cl100k_base.tiktoken file. "
                "See doc/contributing/6_integration_tests.md for offline preparation."
            )
        content = Path(source).read_bytes()
        if hashlib.sha256(content).hexdigest() != _TOKENIZER_SHA256:
            raise ValueError(f"Official cl100k_base asset SHA-256 mismatch: {source}")
        cache_dir.mkdir()
        (cache_dir / hashlib.sha1(_TOKENIZER_URL.encode("utf-8")).hexdigest()).write_bytes(content)
        with patch.dict(tiktoken.registry.ENCODINGS, {}, clear=True):
            yield tiktoken.get_encoding("cl100k_base")


@pytest.fixture
def chat_endpoint(
    official_cl100k_encoding: "tiktoken.Encoding",
) -> Iterator[tuple[str, list[tuple[str, HTTPHeaders]]]]:
    received: list[tuple[str, HTTPHeaders]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.path, self.headers))
            reply = _ANTHROPIC_REPLY if self.path.endswith("/v1/messages") else _OPENAI_REPLY
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _user_message() -> Message:
    return MessagePiece(role="user", conversation_id="convo", original_value="test prompt").to_message()


@_WIRE_PROVIDERS
async def test_recorded_trace_context_reaches_wire_through_litellm_async(
    *,
    chat_endpoint: tuple[str, list[tuple[str, HTTPHeaders]]],
    model_name: str,
    base_path: str,
    request_path: str,
) -> None:
    url, received = chat_endpoint
    provider = model_name.split("/")[0]
    target = LiteLLMChatTarget(
        model_name=model_name,
        endpoint=url + base_path,
        api_key="test-key",
        extra_body_parameters={
            "provider_specific_header": {"custom_llm_provider": provider, "extra_headers": {"X-Route": "a"}}
        },
        trace_config=TargetTraceConfig(enabled=True),
    )
    request = _user_message()
    responses = await target.send_prompt_async(message=request)

    link = RequestTraceContext.from_metadata(request.get_piece().prompt_metadata)
    assert link is not None
    assert len(received) == 1
    path, headers = received[0]
    assert path == request_path
    assert headers.get_all("traceparent") == [link.traceparent]
    assert headers.get_all("tracestate") is None
    assert headers["X-Route"] == "a"
    assert responses[0].get_value() == "hello"


def _single_scope(provider: str) -> object:
    return {"custom_llm_provider": provider, "extra_headers": {"TraceParent": f"00-{'3' * 32}-{'4' * 16}-01"}}


def _list_scope(provider: str) -> object:
    return [
        {"custom_llm_provider": "bedrock", "extra_headers": {"X-Other": "1"}},
        {"custom_llm_provider": f"bedrock, {provider}", "extra_headers": {"tracestate": "vendor=manual"}},
    ]


@_WIRE_PROVIDERS
@pytest.mark.parametrize("scoped_headers", [_single_scope, _list_scope])
async def test_provider_specific_trace_header_is_rejected_through_litellm_async(
    *,
    chat_endpoint: tuple[str, list[tuple[str, HTTPHeaders]]],
    model_name: str,
    base_path: str,
    request_path: str,
    scoped_headers: Callable[[str], object],
) -> None:
    url, received = chat_endpoint
    target = LiteLLMChatTarget(
        model_name=model_name,
        endpoint=url + base_path,
        api_key="test-key",
        extra_body_parameters={"provider_specific_header": scoped_headers(model_name.split("/")[0])},
        trace_config=TargetTraceConfig(enabled=True),
    )
    with pytest.raises(ValueError, match="Manual trace headers"):
        await target.send_prompt_async(message=_user_message())
    assert received == []
