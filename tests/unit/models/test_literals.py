# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal, get_args, get_origin

from pyrit.models import ChatMessageRole, PromptDataType, PromptResponseError


def test_prompt_data_type():
    assert get_origin(PromptDataType) is Literal

    expected_literals = {
        "text",
        "image_path",
        "audio_path",
        "video_path",
        "binary_path",
        "url",
        "error",
        "reasoning",
        "function_call",
        "tool_call",
        "function_call_output",
    }
    assert set(get_args(PromptDataType)) == expected_literals


def test_prompt_response_error():
    assert get_origin(PromptResponseError) is Literal

    expected_literals = {"blocked", "none", "processing", "unknown", "empty"}
    assert set(get_args(PromptResponseError)) == expected_literals


def test_chat_message_role():
    assert get_origin(ChatMessageRole) is Literal

    expected_literals = {"system", "user", "assistant", "simulated_assistant", "tool", "developer"}
    assert set(get_args(ChatMessageRole)) == expected_literals
