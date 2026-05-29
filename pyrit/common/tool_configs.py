# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum
from typing import Any


class OpenAIToolType(str, Enum):
    """Enum defining available OpenAI tool types for the Responses API."""

    WEB_SEARCH_PREVIEW = "web_search_preview"
    CODE_INTERPRETER = "code_interpreter"
    FILE_SEARCH = "file_search"


def web_search_tool() -> dict[str, Any]:
    """Return the configuration for OpenAI's web search tool."""
    return {"type": OpenAIToolType.WEB_SEARCH_PREVIEW.value}


def code_interpreter_tool() -> dict[str, Any]:
    """Return the configuration for OpenAI's code interpreter tool."""
    return {"type": OpenAIToolType.CODE_INTERPRETER.value}


def file_search_tool() -> dict[str, Any]:
    """Return the configuration for OpenAI's file search tool."""
    return {"type": OpenAIToolType.FILE_SEARCH.value}
