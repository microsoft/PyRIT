# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
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


class Modality(str, Enum):
    """
    Canonical high-level modalities for dataset content and target capabilities.

    Inherits from ``str`` so members compare equal to their string values
    (e.g. ``Modality.TEXT == "text"``) and JSON-serialize as plain strings.
    This keeps the enum interoperable with existing string-typed APIs while
    giving authors autocomplete and lint-time typo protection.

    Used by:
      * ``SeedDatasetMetadata.modalities`` and dataset loader class attributes
        to describe what content a dataset produces.

    Not yet used by ``TargetCapabilities`` (which still uses ``PromptDataType``
    with finer-grained ``image_path``/``audio_path``/``video_path`` storage
    tokens). Unifying the two is tracked as a follow-up — see PR #1780.
    """

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
