# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the shared OpenAI Chat Completions wire-format helpers."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import EmptyResponseException, PyritException
from pyrit.models import JsonResponseConfig, Message, MessagePiece
from pyrit.prompt_target.common.chat_completions_message_builder import (
    build_multimodal_chat_messages_async,
    build_response_format,
    build_text_chat_messages,
    build_text_content_entry,
    is_text_only_conversation,
    should_skip_audio_piece,
)
from pyrit.prompt_target.common.chat_completions_response_parser import (
    _build_audio_pieces_async,
    build_content_filter_message,
    build_response_pieces_async,
    capture_token_usage,
    extract_partial_content,
    is_content_filter_response,
    save_audio_response_async,
    token_usage_from_chat_completion,
    validate_chat_completion_response,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _text_message(text="hi", role="user"):
    return MessagePiece(
        role=role, conversation_id="c", original_value=text, original_value_data_type="text"
    ).to_message()


def _request_piece(text="ask"):
    return MessagePiece(role="user", conversation_id="c", original_value=text, original_value_data_type="text")


def _mock_response(content="hello", finish_reason="stop", tool_calls=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = finish_reason
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls
    resp.choices[0].message.audio = None
    resp.model = "some-model"
    resp.model_dump_json = MagicMock(return_value=json.dumps({"finish_reason": finish_reason}))
    return resp


# ---------------------------------------------------------------------------
# message builder
# ---------------------------------------------------------------------------


def test_is_text_only_conversation_true():
    assert is_text_only_conversation([_text_message("a"), _text_message("b", role="assistant")]) is True


def test_is_text_only_conversation_false_for_multi_piece():
    text_piece = MessagePiece(role="user", conversation_id="c", original_value="a", original_value_data_type="text")
    image_piece = MessagePiece(
        role="user", conversation_id="c", original_value="x.png", original_value_data_type="image_path"
    )
    message = Message(message_pieces=[text_piece, image_piece])
    assert is_text_only_conversation([message]) is False


def test_build_text_chat_messages_preserves_roles():
    messages = [_text_message("hello", "user"), _text_message("hi", "assistant")]
    result = build_text_chat_messages(messages)
    assert result[0] == {"role": "user", "content": "hello"}
    assert result[1] == {"role": "assistant", "content": "hi"}


def test_build_text_content_entry():
    piece = _request_piece("describe this")
    assert build_text_content_entry(message_piece=piece) == {"type": "text", "text": "describe this"}


def test_build_response_format_disabled_returns_none():
    config = JsonResponseConfig.from_metadata(metadata={})
    assert build_response_format(json_config=config) is None


def test_build_response_format_json_object():
    config = JsonResponseConfig.from_metadata(metadata={"response_format": "json"})
    assert build_response_format(json_config=config) == {"type": "json_object"}


def test_build_response_format_json_schema():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    config = JsonResponseConfig.from_metadata(metadata={"response_format": "json", "json_schema": json.dumps(schema)})
    result = build_response_format(json_config=config)
    assert result is not None
    assert result["type"] == "json_schema"
    assert result["json_schema"]["schema"] == schema


# ---------------------------------------------------------------------------
# response parser
# ---------------------------------------------------------------------------


def test_validate_response_no_choices_raises():
    resp = MagicMock()
    resp.choices = []
    with pytest.raises(PyritException, match="No choices"):
        validate_chat_completion_response(response=resp)


def test_validate_response_unknown_finish_reason_raises():
    with pytest.raises(PyritException, match="Unknown finish_reason"):
        validate_chat_completion_response(response=_mock_response(finish_reason="banana"))


def test_validate_response_empty_raises():
    resp = _mock_response(content=None)
    with pytest.raises(EmptyResponseException):
        validate_chat_completion_response(response=resp)


def test_validate_response_accepts_valid():
    for reason in ("stop", "length", "tool_calls", "content_filter"):
        validate_chat_completion_response(response=_mock_response(finish_reason=reason))


def test_capture_token_usage_populates_metadata():
    resp = _mock_response("ok")
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 4
    resp.usage.total_tokens = 7
    resp.usage.prompt_tokens_details.cached_tokens = 1
    resp.usage.completion_tokens_details.reasoning_tokens = 2
    pieces = [_request_piece("ok")]
    capture_token_usage(pieces=pieces, response=resp)
    metadata = pieces[0].prompt_metadata
    assert metadata["token_usage_input_tokens"] == 3
    assert metadata["token_usage_output_tokens"] == 4
    assert metadata["token_usage_total_tokens"] == 7
    assert metadata["token_usage_cached_tokens"] == 1
    assert metadata["token_usage_reasoning_tokens"] == 2
    assert "token_usage_model_name" not in metadata


def test_capture_token_usage_noop_without_usage():
    resp = _mock_response("ok")
    resp.usage = None
    pieces = [_request_piece("ok")]
    capture_token_usage(pieces=pieces, response=resp)
    assert "token_usage_total_tokens" not in pieces[0].prompt_metadata


# ---------------------------------------------------------------------------
# token_usage_from_chat_completion (Chat Completions usage parsing)
# ---------------------------------------------------------------------------


def _usage(**kwargs):
    """Build an attribute-style stand-in for a provider usage object."""
    return SimpleNamespace(**kwargs)


def test_token_usage_maps_prompt_completion_and_total():
    result = token_usage_from_chat_completion(_usage(prompt_tokens=10, completion_tokens=20, total_tokens=30))
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.total_tokens == 30
    assert result.cached_tokens is None
    assert result.reasoning_tokens is None
    assert result.extra == {}


def test_token_usage_derives_total_when_missing():
    result = token_usage_from_chat_completion(_usage(prompt_tokens=4, completion_tokens=6))
    assert result.total_tokens == 10


def test_token_usage_reads_nested_details():
    usage = _usage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_tokens_details=_usage(cached_tokens=40, audio_tokens=8),
        completion_tokens_details=_usage(
            reasoning_tokens=12, audio_tokens=3, accepted_prediction_tokens=2, rejected_prediction_tokens=1
        ),
    )
    result = token_usage_from_chat_completion(usage)
    assert result.cached_tokens == 40
    assert result.reasoning_tokens == 12
    assert result.extra == {
        "input_audio_tokens": 8,
        "output_audio_tokens": 3,
        "accepted_prediction_tokens": 2,
        "rejected_prediction_tokens": 1,
    }


def test_token_usage_accepts_mapping_payload():
    usage = {
        "prompt_tokens": 5,
        "completion_tokens": 7,
        "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 2},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }
    result = token_usage_from_chat_completion(usage)
    assert result.input_tokens == 5
    assert result.output_tokens == 7
    assert result.cached_tokens == 2
    assert result.reasoning_tokens == 3


def test_token_usage_reads_litellm_top_level_cache_fields():
    usage = _usage(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=15,
    )
    result = token_usage_from_chat_completion(usage)
    assert result.cached_tokens == 30
    assert result.extra == {"cache_write_tokens": 15}


def test_token_usage_prefers_nested_cached_over_top_level():
    usage = _usage(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=_usage(cached_tokens=40),
        cache_read_input_tokens=30,
    )
    result = token_usage_from_chat_completion(usage)
    assert result.cached_tokens == 40


def test_token_usage_preserves_zero_cached_tokens():
    usage = _usage(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=_usage(cached_tokens=0))
    result = token_usage_from_chat_completion(usage)
    assert result.cached_tokens == 0


def test_token_usage_ignores_non_int_and_bool():
    result = token_usage_from_chat_completion(_usage(prompt_tokens=True, completion_tokens="5", total_tokens=None))
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.total_tokens is None


def test_token_usage_handles_missing_details():
    result = token_usage_from_chat_completion(_usage(prompt_tokens=1, completion_tokens=2, total_tokens=3))
    assert result.cached_tokens is None
    assert result.reasoning_tokens is None
    assert result.extra == {}


def test_token_usage_ignores_responses_api_names():
    # The Responses API shape (input_tokens/output_tokens) is intentionally not parsed here.
    result = token_usage_from_chat_completion(_usage(input_tokens=7, output_tokens=3, total_tokens=10))
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.total_tokens == 10


def test_is_content_filter_response_true():
    assert is_content_filter_response(_mock_response(finish_reason="content_filter")) is True


def test_is_content_filter_response_false():
    assert is_content_filter_response(_mock_response(finish_reason="stop")) is False


def test_extract_partial_content_returns_text():
    assert extract_partial_content(_mock_response(content="partial")) == "partial"


def test_extract_partial_content_none_when_absent():
    assert extract_partial_content(_mock_response(content=None)) is None


def test_build_content_filter_message_creates_error_with_partial():
    resp = _mock_response(content="partial answer", finish_reason="content_filter")
    message = build_content_filter_message(response=resp, request=_request_piece(), partial_content="partial answer")
    piece = message.message_pieces[0]
    assert piece.converted_value_data_type == "error"
    assert piece.prompt_metadata["partial_content"] == "partial answer"


# ---------------------------------------------------------------------------
# audio helpers
# ---------------------------------------------------------------------------


def _audio_piece(role="user"):
    return MessagePiece(
        role=role, conversation_id="c", original_value="clip.wav", original_value_data_type="audio_path"
    )


def test_should_skip_audio_piece_non_audio_type_false():
    assert (
        should_skip_audio_piece(
            message_piece=_request_piece(),
            is_last_message=False,
            has_text_piece=True,
            prefer_transcript_for_history=True,
        )
        is False
    )


def test_should_skip_audio_piece_assistant_always_skipped():
    assert (
        should_skip_audio_piece(
            message_piece=_audio_piece(role="assistant"),
            is_last_message=True,
            has_text_piece=False,
            prefer_transcript_for_history=False,
        )
        is True
    )


def test_should_skip_audio_piece_user_history_with_transcript_skipped():
    assert (
        should_skip_audio_piece(
            message_piece=_audio_piece(),
            is_last_message=False,
            has_text_piece=True,
            prefer_transcript_for_history=True,
        )
        is True
    )


def test_should_skip_audio_piece_current_user_message_kept():
    assert (
        should_skip_audio_piece(
            message_piece=_audio_piece(),
            is_last_message=True,
            has_text_piece=True,
            prefer_transcript_for_history=True,
        )
        is False
    )


async def test_build_multimodal_chat_messages_includes_audio():
    text_piece = MessagePiece(role="user", conversation_id="c", original_value="hi", original_value_data_type="text")
    message = Message(message_pieces=[text_piece, _audio_piece()])
    with patch(
        "pyrit.prompt_target.common.chat_completions_message_builder.build_audio_content_entry_async",
        new=AsyncMock(return_value={"type": "input_audio", "input_audio": {"data": "x", "format": "wav"}}),
    ):
        result = await build_multimodal_chat_messages_async([message], prefer_transcript_for_history=False)
    content_types = [part["type"] for part in result[0]["content"]]
    assert content_types == ["text", "input_audio"]


async def test_save_audio_response_async_wav():
    with patch("pyrit.prompt_target.common.chat_completions_response_parser.data_serializer_factory") as mock_factory:
        serializer = MagicMock()
        serializer.value = "/path/audio.wav"
        serializer.save_data_async = AsyncMock()
        mock_factory.return_value = serializer

        result = await save_audio_response_async(
            audio_data_base64=base64.b64encode(b"abc").decode("utf-8"), audio_format="wav"
        )

    mock_factory.assert_called_once_with(category="prompt-memory-entries", data_type="audio_path", extension=".wav")
    serializer.save_data_async.assert_awaited_once_with(b"abc")
    assert result == "/path/audio.wav"


async def test_save_audio_response_async_pcm16_wraps_wav():
    with patch("pyrit.prompt_target.common.chat_completions_response_parser.data_serializer_factory") as mock_factory:
        serializer = MagicMock()
        serializer.value = "/path/audio.wav"
        serializer.save_formatted_audio_async = AsyncMock()
        mock_factory.return_value = serializer

        result = await save_audio_response_async(
            audio_data_base64=base64.b64encode(b"pcmdata").decode("utf-8"), audio_format="pcm16"
        )

    mock_factory.assert_called_once_with(category="prompt-memory-entries", data_type="audio_path", extension=".wav")
    serializer.save_formatted_audio_async.assert_awaited_once_with(
        data=b"pcmdata", num_channels=1, sample_width=2, sample_rate=24000
    )
    assert result == "/path/audio.wav"


async def test_build_audio_pieces_async_transcript_and_file():
    message = MagicMock()
    message.audio.transcript = "the transcript"
    message.audio.data = base64.b64encode(b"audio").decode("utf-8")
    with patch("pyrit.prompt_target.common.chat_completions_response_parser.data_serializer_factory") as mock_factory:
        serializer = MagicMock()
        serializer.value = "/path/audio.wav"
        serializer.save_data_async = AsyncMock()
        mock_factory.return_value = serializer

        pieces = await _build_audio_pieces_async(message=message, request=_request_piece(), audio_format="wav")

    assert [p.converted_value_data_type for p in pieces] == ["text", "audio_path"]
    assert pieces[0].converted_value == "the transcript"
    assert pieces[0].prompt_metadata.get("transcription") == "audio"
    assert pieces[1].converted_value == "/path/audio.wav"


async def test_build_response_pieces_async_orders_text_audio_tool():
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "fn"
    tool_call.function.arguments = "{}"
    resp = _mock_response(content="text answer", tool_calls=[tool_call])
    resp.choices[0].message.audio = MagicMock()
    resp.choices[0].message.audio.transcript = "spoken"
    resp.choices[0].message.audio.data = base64.b64encode(b"audio").decode("utf-8")
    with patch("pyrit.prompt_target.common.chat_completions_response_parser.data_serializer_factory") as mock_factory:
        serializer = MagicMock()
        serializer.value = "/path/audio.wav"
        serializer.save_data_async = AsyncMock()
        mock_factory.return_value = serializer

        pieces = await build_response_pieces_async(response=resp, request=_request_piece(), audio_format="wav")

    assert [p.converted_value_data_type for p in pieces] == ["text", "text", "audio_path", "function_call"]
