# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.prompt_target.openai.openai_error_handling import (
    _is_content_filter_error,
)

# Tests for _is_content_filter_error helper


def test_is_content_filter_error_with_dict():
    """Test detection with dict input"""
    data = {"error": {"code": "content_filter"}}
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_with_string():
    """Test detection with string input containing content_filter"""
    error_str = '{"error": {"code": "content_filter"}}'
    assert _is_content_filter_error(error_str) is True


def test_is_content_filter_error_invalid_prompt_safety_block():
    """Test detection with invalid_prompt code and safety-related message (CBRN block)"""
    data = {
        "error": {
            "code": "invalid_prompt",
            "message": "Invalid prompt: we've limited access to this content for safety reasons.",
        }
    }
    assert _is_content_filter_error(data) is True


def test_is_content_filter_error_invalid_prompt_non_safety():
    """Test that invalid_prompt without a safety message is NOT treated as a content filter"""
    data = {"error": {"code": "invalid_prompt", "message": "Invalid prompt: schema validation failed."}}
    assert _is_content_filter_error(data) is False


def test_is_content_filter_error_no_filter():
    """Test detection returns False when no content_filter"""
    error_dict = {"error": {"code": "rate_limit", "message": "Too many requests"}}
    assert _is_content_filter_error(error_dict) is False
