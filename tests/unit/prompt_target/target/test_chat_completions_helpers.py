# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the shared OpenAI Chat Completions wire-format helpers."""

import base64
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import EmptyResponseException, PyritException
from pyrit.models import JsonResponseConfig, Message, MessagePiece, TokenUsage
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
    capture_usage_and_finish_reason,
    extract_partial_content,
    get_finish_reason,
    is_content_filter_response,
    save_audio_response_async,
    token_usage_from_chat_completion,
    validate_chat_completion_response,
)
from pyrit.prompt_target.common.utils import RESERVED_RESPONSE_METADATA_KEYS, set_response_metadata

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


def test_get_finish_reason_returns_first_choice_reason():
    assert get_finish_reason(response=_mock_response(finish_reason="length")) == "length"


def test_get_finish_reason_returns_none_without_choices():
    resp = MagicMock()
    resp.choices = []
    assert get_finish_reason(response=resp) is None


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


def test_capture_token_usage_writes_nothing_without_usage():
    resp = _mock_response("ok")
    resp.usage = None
    pieces = [_request_piece("ok")]
    capture_token_usage(pieces=pieces, response=resp)
    assert "token_usage_total_tokens" not in pieces[0].prompt_metadata


# ---------------------------------------------------------------------------
# response metadata capture: token usage plus the stop reason
# ---------------------------------------------------------------------------


class _SyntheticContentFilterResponse:
    """Mirrors ``OpenAITarget``'s synthetic stand-in: no ``usage``, no ``choices``."""

    def model_dump_json(self) -> str:
        return "{}"


def test_capture_usage_and_finish_reason_captures_usage_and_finish_reason():
    resp = _mock_response("ok", finish_reason="length")
    resp.usage.prompt_tokens = 3
    resp.usage.completion_tokens = 4
    resp.usage.total_tokens = 7
    resp.usage.prompt_tokens_details.cached_tokens = 1
    resp.usage.completion_tokens_details.reasoning_tokens = 2
    pieces = [_request_piece("ok")]

    capture_usage_and_finish_reason(pieces=pieces, response=resp)

    metadata = pieces[0].prompt_metadata
    assert metadata["finish_reason"] == "length"
    assert metadata["token_usage_input_tokens"] == 3
    assert metadata["token_usage_output_tokens"] == 4
    assert metadata["token_usage_reasoning_tokens"] == 2


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter", "tool_calls"])
def test_capture_usage_and_finish_reason_records_each_finish_reason(finish_reason):
    pieces = [_request_piece("ok")]
    capture_usage_and_finish_reason(pieces=pieces, response=_mock_response("ok", finish_reason=finish_reason))
    assert pieces[0].prompt_metadata["finish_reason"] == finish_reason


def test_capture_usage_and_finish_reason_stores_finish_reason_as_string():
    """``prompt_metadata`` is persisted as JSON and queried as a string."""
    pieces = [_request_piece("ok")]
    capture_usage_and_finish_reason(pieces=pieces, response=_mock_response("ok"))
    assert isinstance(pieces[0].prompt_metadata["finish_reason"], str)


def test_capture_usage_and_finish_reason_writes_only_to_first_piece():
    resp = _mock_response("ok")
    resp.usage = None
    pieces = [_request_piece("a"), _request_piece("b")]
    capture_usage_and_finish_reason(pieces=pieces, response=resp)
    assert pieces[0].prompt_metadata["finish_reason"] == "stop"
    assert "finish_reason" not in pieces[1].prompt_metadata


def test_capture_usage_and_finish_reason_clears_stale_metadata_from_every_piece():
    """Request metadata is merged into every piece, so a stale value must not survive on any of them."""
    stale = {"finish_reason": "caller_supplied", "status": "caller_supplied", "token_usage_input_tokens": 999999}
    pieces = [_request_piece("a"), _request_piece("b")]
    for piece in pieces:
        piece.prompt_metadata.update(stale)
    resp = _mock_response("ok", finish_reason="length")
    resp.usage = _usage(prompt_tokens=3, completion_tokens=4, total_tokens=7)

    capture_usage_and_finish_reason(pieces=pieces, response=resp)

    assert pieces[0].prompt_metadata["finish_reason"] == "length"
    assert pieces[0].prompt_metadata["token_usage_input_tokens"] == 3
    assert "status" not in pieces[0].prompt_metadata
    assert not any(key in pieces[1].prompt_metadata for key in stale)


def test_capture_usage_and_finish_reason_tolerates_response_without_choices():
    """The SDK-raised content-filter path passes an object with neither usage nor choices."""
    pieces = [_request_piece("ok")]
    capture_usage_and_finish_reason(pieces=pieces, response=_SyntheticContentFilterResponse())
    assert pieces[0].prompt_metadata == {}


def test_capture_usage_and_finish_reason_noop_without_pieces():
    capture_usage_and_finish_reason(pieces=[], response=_mock_response("ok"))


def test_capture_usage_and_finish_reason_clears_caller_supplied_finish_reason():
    """``finish_reason`` is reserved for the provider, so an inherited value must not survive."""
    piece = _request_piece("ok")
    piece.prompt_metadata["finish_reason"] = "caller_supplied"
    capture_usage_and_finish_reason(pieces=[piece], response=_SyntheticContentFilterResponse())
    assert "finish_reason" not in piece.prompt_metadata


def test_capture_usage_and_finish_reason_overwrites_caller_supplied_finish_reason():
    piece = _request_piece("ok")
    piece.prompt_metadata["finish_reason"] = "caller_supplied"
    capture_usage_and_finish_reason(pieces=[piece], response=_mock_response("ok", finish_reason="length"))
    assert piece.prompt_metadata["finish_reason"] == "length"


@pytest.mark.parametrize("value", ["", None, 0, MagicMock()])
def test_set_response_metadata_ignores_unreported_values(value):
    """``prompt_metadata`` is JSON-serialized, so anything but a non-empty string is not reported."""
    piece = _request_piece("ok")
    set_response_metadata(pieces=[piece], status=value)
    assert "status" not in piece.prompt_metadata


@pytest.mark.parametrize("reserved_key", sorted(RESERVED_RESPONSE_METADATA_KEYS))
def test_set_response_metadata_clears_every_reserved_key(reserved_key):
    """A target only writes the keys its own API reports, so all of them must be cleared."""
    piece = _request_piece("ok")
    piece.prompt_metadata[reserved_key] = "caller_supplied"

    set_response_metadata(pieces=[piece], finish_reason="stop")

    assert piece.prompt_metadata.get(reserved_key) == ("stop" if reserved_key == "finish_reason" else None)


def test_set_response_metadata_keeps_all_reported_values():
    """Clearing runs once up front, so a second reported key must not wipe the first."""
    piece = _request_piece("ok")

    set_response_metadata(pieces=[piece], status="incomplete", incomplete_reason="max_output_tokens")

    assert piece.prompt_metadata["status"] == "incomplete"
    assert piece.prompt_metadata["incomplete_reason"] == "max_output_tokens"


def test_set_response_metadata_leaves_unreserved_caller_metadata_untouched():
    piece = _request_piece("ok")
    piece.prompt_metadata["video_id"] = "caller_supplied"

    set_response_metadata(pieces=[piece], finish_reason="stop")

    assert piece.prompt_metadata["video_id"] == "caller_supplied"


def test_reserved_response_metadata_keys_are_the_stop_reason_keys():
    """Pinned explicitly: parametrizing over the set lets a dropped key delete its own test case."""
    assert {"finish_reason", "status", "incomplete_reason"} == RESERVED_RESPONSE_METADATA_KEYS


def test_capture_token_usage_clears_caller_supplied_counts_when_none_reported():
    """The whole prefix is reserved, so a guess must not read back as what the provider charged."""
    piece = _request_piece("ok")
    piece.prompt_metadata.update({"token_usage_input_tokens": 999999, "token_usage_bogus": 777})

    capture_token_usage(pieces=[piece], response=_SyntheticContentFilterResponse())

    assert TokenUsage.from_metadata(piece.prompt_metadata) is None


def test_capture_token_usage_replaces_stale_counts_the_provider_did_not_report():
    """A reported payload must replace the caller's leftovers, not merge into them."""
    piece = _request_piece("ok")
    piece.prompt_metadata["token_usage_reasoning_tokens"] = 999999
    resp = _mock_response("ok")
    resp.usage = _usage(prompt_tokens=3, completion_tokens=4, total_tokens=7)

    capture_token_usage(pieces=[piece], response=resp)

    assert "token_usage_reasoning_tokens" not in piece.prompt_metadata
    assert piece.prompt_metadata["token_usage_input_tokens"] == 3


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


def test_set_response_metadata_records_every_reserved_key():
    """Every reserved key must be reachable: named in the signature and written back to the piece."""
    piece = _request_piece("ok")
    reported = {key: f"reported_{key}" for key in RESERVED_RESPONSE_METADATA_KEYS}
    parameters = inspect.signature(set_response_metadata).parameters
    keyword_only = {name for name, p in parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY}

    assert keyword_only - {"pieces"} == RESERVED_RESPONSE_METADATA_KEYS

    set_response_metadata(pieces=[piece], **reported)

    assert piece.prompt_metadata == reported


def test_set_response_metadata_rejects_an_unreserved_key():
    """An unreserved key is drift or a typo, so it fails at the call site instead of being persisted."""
    piece = _request_piece("ok")

    with pytest.raises(TypeError, match="reasoning_status"):
        set_response_metadata(pieces=[piece], reasoning_status="not_reserved")
