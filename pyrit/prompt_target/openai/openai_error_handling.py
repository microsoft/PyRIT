# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared error handling utilities for OpenAI SDK interactions.

This module provides defensive error parsing, request ID extraction, and retry-after
hint extraction for consistent error handling across OpenAI-based prompt targets.
"""

import json
import logging

from pyrit.exceptions import CONTENT_FILTER_MARKERS

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


def _as_string_keyed_dict(value: object) -> dict[str, object] | None:
    """Return a string-keyed dictionary, or None for an invalid payload."""
    if not isinstance(value, dict):
        return None

    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        result[key] = item

    return result


def _extract_error_payload(exc: Exception) -> tuple[dict[str, object] | str, bool]:
    """
    Extract error payload and detect content filtering from an OpenAI SDK exception.

    Tries response JSON, then exception body, then exception text.

    Args:
        exc: An exception from the OpenAI SDK.

    Returns:
        A tuple containing the extracted dictionary or string payload and a
        boolean indicating whether content filtering was detected.
    """
    # Strategy 1: Try response JSON
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            json_payload = _as_string_keyed_dict(data)
            if json_payload is not None:
                return json_payload, _is_content_filter_error(json_payload)
        except Exception:
            pass

        # Try text fallback from response
        try:
            text = resp.text
            if text and isinstance(text, str):
                return text, _is_content_filter_error(text)
        except Exception:
            pass

    # Strategy 2: Try exception body
    body = getattr(exc, "body", None)
    if body is not None:
        body_payload = _as_string_keyed_dict(body)
        if body_payload is not None:
            return body_payload, _is_content_filter_error(body_payload)

        if isinstance(body, str):
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return body, _is_content_filter_error(body)

            parsed_payload = _as_string_keyed_dict(data)
            if parsed_payload is not None:
                return parsed_payload, _is_content_filter_error(parsed_payload)

            return body, _is_content_filter_error(body)

    # Strategy 3: Fall back to exception text
    text = str(exc)
    return text, _is_content_filter_error(text)
