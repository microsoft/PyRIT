# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import re
import time

from tenacity import RetryCallState

from pyrit.exceptions.exception_context import get_execution_context

logger = logging.getLogger(__name__)


def log_exception(retry_state: RetryCallState) -> None:
    """
    Log each retry attempt with exception details at ERROR level.

    If an execution context is set (via exception_context module), includes
    component role and endpoint information for easier debugging.

    Args:
        retry_state: The tenacity retry state containing attempt information.

    """
    # Validate retry_state has required attributes before proceeding
    if not retry_state:
        logger.error("Retry callback invoked with no retry state")
        return

    # Safely extract values with defaults
    call_count = getattr(retry_state, "attempt_number", None) or 0
    start_time = getattr(retry_state, "start_time", None)
    elapsed_time = (time.monotonic() - start_time) if start_time is not None else 0.0

    outcome = getattr(retry_state, "outcome", None)
    if not outcome or not getattr(outcome, "failed", False):
        return

    exception = outcome.exception() if hasattr(outcome, "exception") else None

    # Get function name safely
    fn = getattr(retry_state, "fn", None)
    fn_name = getattr(fn, "__name__", "unknown") if fn else "unknown"

    # Build the "for X" part of the message based on execution context
    for_clause = fn_name
    endpoint_clause = ""
    try:
        exec_context = get_execution_context()
        if exec_context:
            # e.g. "objective scorer. TrueFalseScorer::_score_value_with_llm"
            role_display = exec_context.component_role.value.replace("_", " ")
            if exec_context.component_name:
                for_clause = f"{role_display}. {exec_context.component_name}::{fn_name}"
            else:
                for_clause = f"{role_display}. {fn_name}"
            if exec_context.endpoint:
                endpoint_clause = f" Endpoint: {exec_context.endpoint}."
    except Exception:
        # Don't let context retrieval errors break retry logging
        pass

    logger.error(
        f"Retry attempt {call_count} for {for_clause} "
        f"failed with exception: {exception}.{endpoint_clause} "
        f"Elapsed time: {elapsed_time} seconds. Total calls: {call_count}"
    )


def remove_start_md_json(response_msg: str) -> str:
    """
    Check the message for supported start markers and remove them if present.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The response message without the start marker (if one was present).

    """
    start_pattern = re.compile(r"^(```json\n|`json\n|```\n|`\n|```json|`json|```|`|json|json\n)")
    match = start_pattern.match(response_msg)
    if match:
        response_msg = response_msg[match.end() :]

    return response_msg


def remove_end_md_json(response_msg: str) -> str:
    """
    Check the message for supported end markers and remove them if present.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The response message without the end marker (if one was present).

    """
    end_pattern = re.compile(r"(\n```|\n`|```|`)$")
    match = end_pattern.search(response_msg)
    if match:
        response_msg = response_msg[: match.start()]

    return response_msg


def extract_json_from_string(response_msg: str) -> str:
    """
    Extract JSON (object or array) from within a larger string.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The extracted JSON string if found, otherwise the original string.

    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(response_msg):
        if char not in "[{":
            continue

        try:
            _, end_index = decoder.raw_decode(response_msg, idx=index)
            return response_msg[index:end_index]
        except json.JSONDecodeError:
            continue

    return response_msg


def remove_markdown_json(response_msg: str) -> str:
    """
    Remove markdown wrappers and return a JSON payload when possible.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The response message without Markdown formatting if present.

    """
    response_msg = remove_start_md_json(response_msg)
    response_msg = remove_end_md_json(response_msg)

    # Validate if the remaining response message is valid JSON. If it's still not valid
    # after removing the markdown notation, try to extract JSON from within the string.
    try:
        json.loads(response_msg)
        return response_msg
    except json.JSONDecodeError:
        response_msg = extract_json_from_string(response_msg)
        try:
            json.loads(response_msg)
            return response_msg
        except json.JSONDecodeError:
            return f"Invalid JSON response: {response_msg}"
