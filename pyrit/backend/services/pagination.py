# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared helpers for filter-bound keyset pagination."""

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class DecodedKeysetCursor:
    """A validated keyset cursor payload."""

    timestamp: datetime
    identifier: str


def normalize_label_filters(
    *,
    labels: Mapping[str, str | Sequence[str]] | None,
) -> dict[str, str | list[str]] | None:
    """
    Normalize label filters for querying and cursor fingerprints.

    Returns:
        dict[str, str | list[str]] | None: Canonical effective label filters.
    """
    normalized: dict[str, str | list[str]] = {}
    for key in sorted(labels or {}):
        raw_value = (labels or {})[key]
        if isinstance(raw_value, str):
            if raw_value:
                normalized[key] = raw_value
            continue
        values = sorted({str(value) for value in raw_value if str(value)})
        if values:
            normalized[key] = values
    return normalized or None


def fingerprint_filters(*, filters: Mapping[str, Any]) -> str:
    """
    Compute a stable fingerprint for pagination filters.

    Returns:
        str: A short digest stable across mapping and sequence ordering.
    """
    canonical = json.dumps(_canonicalize(filters), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_keyset_cursor(*, timestamp: datetime, identifier: str, fingerprint: str) -> str:
    """
    Encode a filter-bound keyset anchor as an opaque cursor.

    Returns:
        str: A base64url-encoded cursor.
    """
    payload = {
        "v": 1,
        "f": fingerprint,
        "t": timestamp.isoformat(),
        "i": identifier,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_keyset_cursor(*, cursor: str | None, fingerprint: str) -> DecodedKeysetCursor | None:
    """
    Decode a filter-bound keyset cursor.

    Malformed, stale, and legacy cursors restart pagination from the first page.

    Returns:
        DecodedKeysetCursor | None: The validated anchor, or None for the first page.
    """
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("v", 1) != 1 or payload.get("f") != fingerprint:
        return None
    try:
        timestamp = datetime.fromisoformat(payload["t"])
        identifier = str(uuid.UUID(payload["i"]))
    except (KeyError, TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        return None
    try:
        timestamp = timestamp.astimezone(timezone.utc)
    except (OverflowError, OSError):
        return None
    return DecodedKeysetCursor(timestamp=timestamp, identifier=identifier)


def _canonicalize(value: Any) -> Any:
    """
    Canonicalize nested filter values for stable serialization.

    Returns:
        Any: The canonicalized value.
    """
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [_canonicalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value
