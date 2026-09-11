# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest
from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion import Completion
from openai.types.completion_choice import CompletionChoice
from openai.types.completion_usage import CompletionUsage
from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputText

from pyrit.exceptions import PyritException
from pyrit.models import MessagePiece
from pyrit.prompt_target.openai._response_adapter import (
    ChatCompletionsResponseAdapter,
    CompletionsResponseAdapter,
    OpenAIResponseAdapter,
    ResponsesResponseAdapter,
)
from pyrit.prompt_target.openai.openai_chat_target import OpenAIChatTarget
from pyrit.prompt_target.openai.openai_completion_target import OpenAICompletionTarget
from pyrit.prompt_target.openai.openai_response_target import OpenAIResponseTarget


def _piece() -> MessagePiece:
    return MessagePiece(role="assistant", original_value="response")


def _chat_response(*, content: str | None, finish_reason: str) -> ChatCompletion:
    return ChatCompletion(
        id="chat-1",
        choices=[
            Choice(
                finish_reason=finish_reason,
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
            )
        ],
        created=0,
        model="gpt-4o",
        object="chat.completion",
        usage=CompletionUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
    )


def _responses_response(
    *,
    status: str,
    incomplete_reason: str | None = None,
    text: str = "partial",
) -> MagicMock:
    response = MagicMock(spec=Response)
    response.error = None
    response.status = status
    response.incomplete_details = MagicMock(reason=incomplete_reason) if incomplete_reason else None
    response.output = [
        ResponseOutputMessage(
            id="message-1",
            content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
            role="assistant",
            status="completed",
            type="message",
        )
    ]
    response.usage = MagicMock(
        input_tokens=5,
        output_tokens=3,
        total_tokens=8,
        input_tokens_details=None,
        output_tokens_details=None,
    )
    return response


def test_targets_select_explicit_response_format_adapters():
    assert isinstance(OpenAIChatTarget._response_adapter, ChatCompletionsResponseAdapter)
    assert isinstance(OpenAICompletionTarget._response_adapter, CompletionsResponseAdapter)
    assert isinstance(OpenAIResponseTarget._response_adapter, ResponsesResponseAdapter)


def test_base_adapter_uses_no_op_defaults():
    adapter = OpenAIResponseAdapter[object]()
    response = object()
    piece = _piece()

    assert adapter.is_content_filtered(response=response) is False
    assert adapter.extract_partial_content(response=response) is None
    assert adapter.is_truncated(response=response) is False
    adapter.capture_metadata(response=response, pieces=[piece])
    adapter.validate(response=response, is_truncated=False)
    assert piece.prompt_metadata == {}


def test_chat_completions_adapter_contract():
    adapter = ChatCompletionsResponseAdapter()
    filtered = _chat_response(content="partial", finish_reason="content_filter")
    truncated = _chat_response(content="", finish_reason="length")
    malformed = _chat_response(content="ignored", finish_reason="stop")
    malformed.choices = []

    assert adapter.is_content_filtered(response=filtered) is True
    assert adapter.extract_partial_content(response=filtered) == "partial"
    assert adapter.is_truncated(response=truncated) is True
    adapter.validate(response=truncated, is_truncated=adapter.is_truncated(response=truncated))
    with pytest.raises(PyritException, match="No choices returned"):
        adapter.validate(response=malformed, is_truncated=adapter.is_truncated(response=malformed))

    piece = _piece()
    adapter.capture_metadata(response=filtered, pieces=[piece])
    assert piece.prompt_metadata["finish_reason"] == "content_filter"
    assert piece.prompt_metadata["token_usage_total_tokens"] == 8


def test_completions_adapter_preserves_legacy_contract():
    adapter = CompletionsResponseAdapter()
    response = Completion(
        id="completion-1",
        object="text_completion",
        created=0,
        model="gpt-3.5-turbo-instruct",
        choices=[CompletionChoice(finish_reason="content_filter", index=0, text="partial")],
        usage=CompletionUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
    )

    assert adapter.is_content_filtered(response=response) is False
    assert adapter.extract_partial_content(response=response) is None
    assert adapter.is_truncated(response=response) is False
    adapter.validate(response=response, is_truncated=adapter.is_truncated(response=response))

    piece = _piece()
    adapter.capture_metadata(response=response, pieces=[piece])
    assert piece.prompt_metadata["finish_reason"] == "content_filter"
    assert piece.prompt_metadata["token_usage_total_tokens"] == 8


def test_responses_adapter_contract():
    adapter = ResponsesResponseAdapter()
    filtered = _responses_response(status="incomplete", incomplete_reason="content_filter")
    truncated = _responses_response(status="incomplete", incomplete_reason="max_output_tokens", text="")
    malformed = _responses_response(status="failed")

    assert adapter.is_content_filtered(response=filtered) is True
    assert adapter.extract_partial_content(response=filtered) == "partial"
    assert adapter.is_truncated(response=truncated) is True
    adapter.validate(response=truncated, is_truncated=adapter.is_truncated(response=truncated))
    with pytest.raises(PyritException, match="Unexpected status: failed"):
        adapter.validate(response=malformed, is_truncated=adapter.is_truncated(response=malformed))

    piece = _piece()
    adapter.capture_metadata(response=filtered, pieces=[piece])
    assert piece.prompt_metadata["status"] == "incomplete"
    assert piece.prompt_metadata["incomplete_reason"] == "content_filter"
    assert piece.prompt_metadata["token_usage_total_tokens"] == 8


def test_chat_target_validation_honors_truncation_override():
    target = object.__new__(OpenAIChatTarget)
    response = _chat_response(content="", finish_reason="stop")

    with patch.object(target, "_is_truncated_response", return_value=True):
        target._validate_response(response, _piece())


def test_responses_target_validation_honors_truncation_override():
    target = object.__new__(OpenAIResponseTarget)
    response = _responses_response(status="failed")

    with patch.object(target, "_is_truncated_response", return_value=True):
        target._validate_response(response, _piece())
