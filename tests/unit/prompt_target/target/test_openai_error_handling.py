# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.exceptions import CONTENT_FILTER_MARKERS
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


# Tests for _is_content_filter_error helper


def test_content_filter_markers_contents():
    """Sanity-check the empirical marker set so accidental removals are caught."""
    assert {
        "content_filter",
        "content_safety_violation",
        "policy_violation",
        "moderation_blocked",
    } <= CONTENT_FILTER_MARKERS


def test_safety_message_markers_contents():
    """Sanity-check the message-level safety markers used for invalid_prompt."""
    assert {"limited access", "safety", "usage policy"} <= SAFETY_MESSAGE_MARKERS


@pytest.mark.parametrize(
    "code",
    [
        "content_filter",
        "content_safety_violation",
        "moderation_blocked",
    ],
)
def test_is_content_filter_error_explicit_code(code):
    """Each marker that appears as an exact error.code should be detected."""
    assert _is_content_filter_error({"error": {"code": code}}) is True


def test_is_content_filter_error_content_policy_violation_via_substring():
    """Azure's content_policy_violation code is detected via the policy_violation marker."""
    data = {"error": {"code": "content_policy_violation", "message": "Content blocked"}}
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
