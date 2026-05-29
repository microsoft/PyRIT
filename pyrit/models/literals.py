# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

PromptDataType = Literal[
    "text",
    "image_path",
    "audio_path",
    "video_path",
    "binary_path",
    "url",
    "reasoning",
    "error",
    "function_call",
    "tool_call",
    "function_call_output",
]

"""
The type of the error in the prompt response
blocked: blocked by an external filter e.g. Azure Filters
none: no exception is raised
processing: there is an exception thrown unrelated to the query
unknown: the type of error is unknown
"""
PromptResponseError = Literal["blocked", "none", "processing", "empty", "unknown"]

ChatMessageRole = Literal["system", "user", "assistant", "simulated_assistant", "tool", "developer"]

SeedType = Literal["prompt", "objective", "simulated_conversation"]
