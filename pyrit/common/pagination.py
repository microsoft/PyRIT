# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lightweight shared helpers for filter-bound keyset pagination."""

import base64
import binascii
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class DecodedKeysetCursor:
    """A UTC timestamp/UUID anchor for descending recency pagination."""

    timestamp: datetime
    identifier: str


def normalize_label_filters(
    *,
    labels: Mapping[str, str | Sequence[str]] | None,
) -> dict[str, str | list[str]] | None:
    """
    Normalize label filters for querying and cursor fingerprints.

    Empty strings and empty selections follow the existing History convention of
    applying no filter. Sequence values are de-duplicated and sorted; label names
    remain separate AND predicates rather than being merged into one value list.

    Args:
        labels (Mapping[str, str | Sequence[str]] | None): Caller-owned label selections.

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


def fingerprint_filters(*, filters: Mapping[str, Any], length: int = 16) -> str:
    """
    Compute a stable fingerprint for pagination filters.

    Mappings and sequence order are canonicalized because these callers use
    unordered filter values, not ordered pipelines. This is an identity check,
    not a signature or authorization mechanism.

    Args:
        filters (Mapping[str, Any]): JSON-serializable query identity.
        length (int): Number of SHA256 hex characters retained; 16 preserves
            History cursor compatibility. Callers can request all 64 characters
            when a full-length query fingerprint is needed.

    Returns:
        str: A short digest stable across mapping and sequence ordering.

    Raises:
        ValueError: If the requested SHA256 digest length is outside 1 through 64.
    """
    if not 1 <= length <= 64:
        raise ValueError("Fingerprint length must be between 1 and 64")
    canonical = json.dumps(_canonicalize(filters), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def encode_keyset_cursor(*, timestamp: datetime, identifier: str, fingerprint: str) -> str:
    """
    Encode a filter-bound keyset anchor as an opaque cursor.

    Args:
        timestamp (datetime): A timezone-aware recency key from the last returned row.
        identifier (str): That row's UUID, breaking ties at an identical timestamp.
        fingerprint (str): The effective filters and result-selection policy.

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

    An absent, malformed, or mismatched token returns no usable anchor. Callers
    choose the error policy: History restarts its first page. Callers requiring
    strict continuation must reject a supplied token when no anchor is returned.

    Args:
        cursor (str | None): An opaque token from a preceding response.
        fingerprint (str): The effective request identity the token must match.

    Returns:
        DecodedKeysetCursor | None: A UTC anchor, or None when no valid anchor is available.
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
    timestamp_value = payload.get("t")
    identifier_value = payload.get("i")
    if not isinstance(timestamp_value, str) or not isinstance(identifier_value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(timestamp_value)
        identifier = str(uuid.UUID(identifier_value))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    try:
        timestamp = timestamp.astimezone(UTC)
    except (OverflowError, OSError):
        return None
    return DecodedKeysetCursor(timestamp=timestamp, identifier=identifier)


def _canonicalize(value: Any) -> Any:
    """
    Make unordered JSON filter structures deterministic for fingerprinting.

    Strings and bytes are scalar values, not sortable sequences. Other sequences
    are recursively sorted by their canonical JSON representation, so reordering
    selected values does not invalidate pagination.

    Returns:
        Any: A serialization-ready value with stable mapping and sequence order.
    """
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [_canonicalize(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value
