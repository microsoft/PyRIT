# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock

import httpx
import pytest
from openai import BadRequestError

from pyrit.exceptions import CONTENT_FILTER_MARKERS
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget, OpenAITarget
from pyrit.prompt_target.openai.openai_error_handling import (
    SAFETY_MESSAGE_MARKERS,
    _extract_error_payload,
    _is_content_filter_error,
)


class _FakeBodyError(Exception):
    """A minimal exception stand-in exposing a ``body`` attribute like the OpenAI SDK's errors."""

    def __init__(self, body: object) -> None:
        super().__init__("fake error")
        self.body = body


@pytest.fixture(
    params=[
        ("bio_policy", "This content was flagged for possible biological risk."),
        ("cyber_policy", "This request has been flagged for possible cybersecurity risk."),
    ],
    ids=["bio_policy", "cyber_policy"],
)
def policy_error_payload(request: pytest.FixtureRequest) -> dict[str, object]:
    code, message = request.param
    return {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": code}}


# Tests for _is_content_filter_error helper


def test_content_filter_markers_contents():
    """Sanity-check the empirical marker set so accidental removals are caught."""
    assert {
        "content_filter",
        "content_safety_violation",
        "cyber_policy",
        "policy_violation",
        "moderation_blocked",
        "bio_policy",
    } <= CONTENT_FILTER_MARKERS


def test_safety_message_markers_contents():
    """Sanity-check the message-level safety markers used for invalid_prompt."""
    assert {"limited access", "safety", "usage policy"} <= SAFETY_MESSAGE_MARKERS


@pytest.mark.parametrize(
    "code",
    [
        "content_filter",
        "content_safety_violation",
        "cyber_policy",
        "moderation_blocked",
        "bio_policy",
    ],
)
def test_is_content_filter_error_explicit_code(code):
    """Each marker that appears as an exact error.code should be detected."""
    assert _is_content_filter_error({"error": {"code": code}}) is True


def test_is_content_filter_error_content_policy_violation_via_substring():
    """Azure's content_policy_violation code is detected via the policy_violation marker."""
    data = {"error": {"code": "content_policy_violation", "message": "Content blocked"}}
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_cyber_policy_payload():
    """Azure OpenAI's cybersecurity-policy rejection is treated as a provider block."""
    data = {
        "error": {
            "message": (
                "This content was flagged for possible cybersecurity risk. "
                "Please contact Microsoft if you believe this is an error."
            ),
            "type": "invalid_request_error",
            "param": "prompt",
            "code": "cyber_policy",
        }
    }
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_with_dict():
    """Dict input with a content_filter code is detected."""
    assert _is_content_filter_error({"error": {"code": "content_filter"}}) is True


def test_is_content_filter_error_with_string():
    """String input containing a marker is detected."""
    assert _is_content_filter_error('{"error": {"code": "content_filter"}}') is True


def test_is_content_filter_error_string_moderation_blocked():
    """String input containing moderation_blocked is detected."""
    assert _is_content_filter_error("error: moderation_blocked for prompt") is True


def test_is_content_filter_error_invalid_prompt_safety_block():
    """invalid_prompt + 'safety' / 'limited access' message is detected (CBRN block)."""
    data = {
        "error": {
            "code": "invalid_prompt",
            "message": "Invalid prompt: we've limited access to this content for safety reasons.",
        }
    }
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_invalid_prompt_usage_policy_message():
    """invalid_prompt + 'usage policy' message is detected (previously a hardcoded literal)."""
    data = {
        "error": {
            "code": "invalid_prompt",
            "message": "Invalid prompt: your prompt was flagged as potentially violating our usage policy.",
        }
    }
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_invalid_prompt_non_safety():
    """invalid_prompt without a safety-marker message is NOT treated as content filter."""
    data = {"error": {"code": "invalid_prompt", "message": "Invalid prompt: schema validation failed."}}
    assert _is_content_filter_error(data) is False


def test_is_content_filter_error_invalid_prompt_non_safety_with_content_filter_marker():
    """invalid_prompt with no safety message but a CONTENT_FILTER_MARKERS substring elsewhere is detected."""
    data = {
        "error": {
            "code": "invalid_prompt",
            "message": "Invalid prompt.",
            "inner_error": {"code": "content_filter"},
        }
    }
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_no_filter():
    """Unrelated errors return False."""
    assert _is_content_filter_error({"error": {"code": "rate_limit", "message": "Too many requests"}}) is False


def test_is_content_filter_error_string_no_filter():
    """String input without any marker returns False."""
    assert _is_content_filter_error("connection timed out") is False


# Tests for _extract_error_payload helper


def test_extract_error_payload_body_dict():
    """When exc.body is a dict, it's returned as-is with content-filter detection applied."""
    exc = _FakeBodyError({"error": {"code": "content_filter"}})
    payload, is_filter = _extract_error_payload(exc)
    assert payload == {"error": {"code": "content_filter"}}
    assert is_filter is True


def test_extract_error_payload_body_json_string_decodes_to_dict():
    """When exc.body is a JSON string that decodes to a dict, the parsed dict is returned."""
    exc = _FakeBodyError('{"error": {"code": "content_filter"}}')
    payload, is_filter = _extract_error_payload(exc)
    assert payload == {"error": {"code": "content_filter"}}
    assert is_filter is True


def test_extract_error_payload_body_json_string_decodes_to_non_dict():
    """When exc.body is a JSON string that decodes to a non-dict (e.g. a list), the original
    string body is returned rather than the parsed value."""
    exc = _FakeBodyError('["not", "a", "dict"]')
    payload, is_filter = _extract_error_payload(exc)
    assert payload == '["not", "a", "dict"]'
    assert is_filter is False


def test_extract_error_payload_body_non_json_string():
    """When exc.body is a non-JSON string, the original string is returned unparsed."""
    exc = _FakeBodyError("not json at all")
    payload, is_filter = _extract_error_payload(exc)
    assert payload == "not json at all"
    assert is_filter is False


def test_extract_error_payload_falls_back_to_str_of_exception():
    """When exc has neither a response nor a body, the payload falls back to str(exc)."""
    exc = Exception("plain failure")
    payload, is_filter = _extract_error_payload(exc)
    assert payload == "plain failure"
    assert is_filter is False


@pytest.mark.parametrize("json_response", [True, False])
def test_extract_error_payload_policy_response(*, policy_error_payload: dict[str, object], json_response: bool) -> None:
    request = httpx.Request("POST", "https://example.test/v1/responses")
    error_text = f"Error code: 400 - {policy_error_payload}"
    response = (
        httpx.Response(400, json=policy_error_payload, request=request)
        if json_response
        else httpx.Response(400, text=error_text, request=request)
    )
    exc = BadRequestError("Bad request", response=response, body=None)

    payload, is_filter = _extract_error_payload(exc)

    assert payload == (policy_error_payload if json_response else error_text)
    assert is_filter is True


@pytest.mark.parametrize("json_body", [True, False])
def test_extract_error_payload_policy_body(*, policy_error_payload: dict[str, object], json_body: bool) -> None:
    error_body = policy_error_payload["error"]
    exc = _FakeBodyError(json.dumps(error_body) if json_body else error_body)

    payload, is_filter = _extract_error_payload(exc)

    assert payload == error_body
    assert is_filter is True


def test_extract_error_payload_policy_exception_text(policy_error_payload: dict[str, object]) -> None:
    error_text = f"Error code: 400 - {policy_error_payload}"

    payload, is_filter = _extract_error_payload(Exception(error_text))

    assert payload == error_text
    assert is_filter is True


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
async def test_send_prompt_policy_block_preserves_error_without_retry_async(
    *, target_type: type[OpenAITarget], policy_error_payload: dict[str, object]
) -> None:
    http_handler = MagicMock(return_value=httpx.Response(400, json=policy_error_payload))
    request_piece = MessagePiece(role="user", original_value="hello", conversation_id="policy-block-test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(http_handler)) as http_client:
        target = target_type(
            model_name="test-model",
            endpoint="https://example.test/v1",
            api_key="test-key",
            httpx_client_kwargs={"http_client": http_client},
        )

        responses = await target.send_prompt_async(message=Message(message_pieces=[request_piece]))

    http_handler.assert_called_once()
    assert len(responses) == 1
    assert len(responses[0].message_pieces) == 1
    piece = responses[0].message_pieces[0]
    assert piece.role == "assistant"
    assert piece.conversation_id == request_piece.conversation_id
    assert piece.response_error == "blocked"
    assert piece.converted_value_data_type == "error"
    assert json.loads(piece.converted_value) == {"status_code": 400, "message": str(policy_error_payload)}
    assert piece.original_value == piece.converted_value


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("target_type", [OpenAIChatTarget, OpenAIResponseTarget])
@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("invalid_prompt", "Schema validation failed."),
        ("invalid_request_error", "Missing required parameter: messages."),
        ("invalid_parameter", "Invalid safety setting."),
        ("invalid_json", "Malformed JSON."),
    ],
)
async def test_send_prompt_non_policy_bad_request_raises_without_retry_async(
    *, target_type: type[OpenAITarget], code: str, message: str
) -> None:
    error_payload = {"error": {"code": code, "message": message, "type": "invalid_request_error"}}
    http_handler = MagicMock(return_value=httpx.Response(400, json=error_payload))
    async with httpx.AsyncClient(transport=httpx.MockTransport(http_handler)) as http_client:
        target = target_type(
            model_name="test-model",
            endpoint="https://example.test/v1",
            api_key="test-key",
            httpx_client_kwargs={"http_client": http_client},
        )

        with pytest.raises(BadRequestError) as exc_info:
            await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))

    http_handler.assert_called_once()
    assert exc_info.value.response.json() == error_payload
    assert exc_info.value.code == code
