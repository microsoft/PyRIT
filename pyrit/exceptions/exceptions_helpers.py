# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import re
import time

from tenacity import RetryCallState

from pyrit.exceptions.exception_context import get_execution_context
from pyrit.exceptions.retry_collector import get_retry_collector

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
            # e.g. "objective scorer. TrueFalseScorer::_score_value_with_llm_async"
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
    collector = get_retry_collector()
    if collector:
        collector.record(retry_state=retry_state)


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

    Returns the ONLY top-level JSON literal in the string. When the string
    contains multiple distinct top-level JSON literals, the reply is ambiguous
    (e.g. a judge quoting an embedded JSON object before its own verdict) and
    no single literal can be trusted as the response, so the original string is
    returned unchanged (making the caller fail as invalid JSON and retry).

    Equivalent repeated literals (a model stutter) are accepted, ignoring
    whitespace and object key order but preserving JSON value types.
    Decoder-limit failures leave the original string unchanged.

    Args:
        response_msg (str): The response message to check.

    Returns:
        str: The extracted JSON string if found, otherwise the original string.

    """
    try:
        candidates = _top_level_json_candidates(response_msg)
        if not candidates:
            return response_msg
        if len(candidates) == 1:
            return candidates[0]
        normalized = [json.dumps(json.loads(candidate), sort_keys=True) for candidate in candidates]
    except (ValueError, RecursionError):
        return response_msg
    if all(candidate == normalized[0] for candidate in normalized[1:]):
        return candidates[0]
    return response_msg


def _top_level_json_candidates(response_msg: str) -> list[str]:
    """
    Scan left-to-right for non-overlapping top-level JSON literals.

    Args:
        response_msg (str): The response message to scan.

    Returns:
        list[str]: The top-level JSON literal substrings, outermost-first at each position.
    """
    decoder = json.JSONDecoder()
    found: list[str] = []
    index, length = 0, len(response_msg)
    while index < length:
        if response_msg[index] in "[{":
            try:
                _, end_index = decoder.raw_decode(response_msg, idx=index)
                found.append(response_msg[index:end_index])
                index = end_index
                continue
            except json.JSONDecodeError:
                pass
        index += 1
    return found


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
    except (ValueError, RecursionError):
        response_msg = extract_json_from_string(response_msg)
        try:
            json.loads(response_msg)
            return response_msg
        except (ValueError, RecursionError):
            return f"Invalid JSON response: {response_msg}"
