# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared error handling utilities for OpenAI SDK interactions.

This module provides defensive error parsing, request ID extraction, and retry-after
hint extraction for consistent error handling across OpenAI-based prompt targets.
"""

import json
import logging

from pyrit.exceptions.exception_classes import CONTENT_FILTER_MARKERS

logger = logging.getLogger(__name__)


# OpenAI uses ``error.code == "invalid_prompt"`` for both model-level safety blocks
# (e.g. CBRN topics) and unrelated failures (e.g. schema validation errors), so the
# code alone is too generic to treat as a content-filter signal. Only treat
# ``invalid_prompt`` as content filtering when the message text contains one of these
# safety markers.
#
#   - "limited access"             - "we've limited access to this content for safety..."
#   - "safety"                     - generic safety-system wording.
#   - "usage policy"               - "your prompt was flagged as potentially violating
#                                    our usage policy."
SAFETY_MESSAGE_MARKERS = frozenset(
    {
        "limited access",
        "safety",
        "usage policy",
    }
)


def _extract_request_id_from_exception(exc: Exception) -> str | None:
    """
    Extract the x-request-id from an OpenAI SDK exception for logging/telemetry.

    Args:
        exc: An exception from the OpenAI SDK (e.g., BadRequestError, RateLimitError).

    Returns:
        The request ID string if found, otherwise None.
    """
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            # Try both common header name variants
            request_id = resp.headers.get("x-request-id") or resp.headers.get("X-Request-Id")
            return str(request_id) if request_id is not None else None
    except Exception:
        pass
    return None


def _extract_retry_after_from_exception(exc: Exception) -> float | None:
    """
    Extract the Retry-After header from a rate-limit exception for intelligent backoff.

    Args:
        exc: A rate-limit exception from the OpenAI SDK.

    Returns:
        The retry-after value in seconds as a float, or None if not present.
    """
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            if ra is not None:
                try:
                    return float(ra)
                except ValueError:
                    # Retry-After can be an HTTP date string; ignore for now
                    return None
    except Exception:
        pass
    return None


def _is_content_filter_error(data: dict[str, object] | str) -> bool:
    """
    Check if error data indicates content filtering.

    Performs a substring scan over the payload (JSON-dumped for dicts, ``str()`` for
    strings) against ``CONTENT_FILTER_MARKERS``. The ``invalid_prompt`` code is
    handled separately because it requires inspecting both the code and the message.

    Args:
        data: Either a dict (parsed JSON) or string (error text).

    Returns:
        True if content filtering is detected, False otherwise.
    """
    if isinstance(data, dict):
        error_obj = data.get("error")
        if isinstance(error_obj, dict) and error_obj.get("code") == "invalid_prompt":
            message = str(error_obj.get("message", "")).lower()
            if any(marker in message for marker in SAFETY_MESSAGE_MARKERS):
                return True
        haystack = json.dumps(data).lower()
    else:
        haystack = str(data).lower()
    return any(marker in haystack for marker in CONTENT_FILTER_MARKERS)


def _extract_error_payload(exc: Exception) -> tuple[dict[str, object] | str, bool]:
    """
    Extract error payload and detect content filter from an OpenAI SDK exception.

    This function tries multiple strategies to parse error information:
    1. Try response.json() if response object exists
    2. Fall back to e.body attribute
    3. Fall back to str(e)

    It also attempts to detect whether the error is due to content filtering by
    delegating to ``_is_content_filter_error``, which scans the payload for the
    markers in ``CONTENT_FILTER_MARKERS`` (or, for ``invalid_prompt`` errors,
    inspects the message for ``SAFETY_MESSAGE_MARKERS``).

    Args:
        exc: An exception from the OpenAI SDK (typically BadRequestError).

    Returns:
        A tuple of (payload, is_content_filter) where:
        - payload is either a dict (if JSON) or a string
        - is_content_filter is True if the error appears to be content policy related
    """
    # Strategy 1: Try response JSON
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            # Validate that we got actual data, not a mock
            if isinstance(data, dict):
                return data, _is_content_filter_error(data)
        except Exception:
            pass
        # Try text fallback from response
        try:
            text = resp.text
            if text and isinstance(text, str):
                return text, _is_content_filter_error(text)
        except Exception:
            pass

    # Strategy 2: Try e.body attribute
    body = getattr(exc, "body", None)
    if body is not None:
        if isinstance(body, dict):
            return body, _is_content_filter_error(body)
        if isinstance(body, str):
            try:
                data = json.loads(body)
                return data, _is_content_filter_error(data)
            except json.JSONDecodeError:
                return body, _is_content_filter_error(body)

    # Strategy 3: Fall back to str(e)
    text = str(exc)
    return text, _is_content_filter_error(text)
