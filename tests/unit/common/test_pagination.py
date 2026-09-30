# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pyrit.common.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)


@pytest.mark.parametrize("field", ["t", "i"])
@pytest.mark.parametrize("invalid", [None, 123, True, [], {}])
def test_decode_keyset_cursor_rejects_non_string_fields(*, field: str, invalid: object) -> None:
    payload = {
        "v": 1,
        "f": "cohort",
        "t": "2026-09-21T12:00:00Z",
        "i": "00000000-0000-0000-0000-000000000001",
        field: invalid,
    }
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    assert decode_keyset_cursor(cursor=cursor, fingerprint="cohort") is None


def test_decode_keyset_cursor_preserves_valid_identity_and_normalizes_utc() -> None:
    identifier = "00000000-0000-0000-0000-000000000001"
    cursor = encode_keyset_cursor(
        timestamp=datetime(2026, 9, 21, 5, tzinfo=timezone(timedelta(hours=-7))),
        identifier=identifier,
        fingerprint="cohort",
    )
    decoded = decode_keyset_cursor(cursor=cursor, fingerprint="cohort")
    assert decoded is not None
    assert decoded.identifier == identifier
    assert decoded.timestamp == datetime(2026, 9, 21, 12, tzinfo=UTC)


def test_decode_keyset_cursor_rejects_a_different_cohort() -> None:
    cursor = encode_keyset_cursor(
        timestamp=datetime(2026, 9, 21, tzinfo=UTC),
        identifier="00000000-0000-0000-0000-000000000001",
        fingerprint="original",
    )
    assert decode_keyset_cursor(cursor=cursor, fingerprint="changed") is None


@pytest.mark.parametrize("cursor", [None, "", "ar-attack-1", "deadbeef.40", "not-base64!!!", "\u2603"])
def test_decode_keyset_cursor_rejects_absent_or_legacy_tokens(cursor: str | None) -> None:
    assert decode_keyset_cursor(cursor=cursor, fingerprint="cohort") is None


def test_encode_keyset_cursor_preserves_history_wire_format() -> None:
    cursor = encode_keyset_cursor(
        timestamp=datetime(2026, 9, 21, tzinfo=UTC),
        identifier="00000000-0000-0000-0000-000000000001",
        fingerprint="cohort",
    )
    assert not cursor.endswith("=")
    assert json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))) == {
        "v": 1,
        "f": "cohort",
        "t": "2026-09-21T00:00:00+00:00",
        "i": "00000000-0000-0000-0000-000000000001",
    }


def test_fingerprint_filters_preserves_history_default_digest() -> None:
    assert fingerprint_filters(filters={}) == "44136fa355b3678a"
    assert fingerprint_filters(filters={}, length=64) == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert fingerprint_filters(filters={}, length=1) == "4"


@pytest.mark.parametrize("length", [0, 65])
def test_fingerprint_filters_rejects_invalid_digest_length(length: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 64"):
        fingerprint_filters(filters={}, length=length)


def test_fingerprint_filters_canonicalizes_nested_unordered_values() -> None:
    original = {"labels": {"team": ["blue", "red"], "operation": "campaign"}, "outcomes": ["success", "failure"]}
    reordered = {"outcomes": ["failure", "success"], "labels": {"operation": "campaign", "team": ["red", "blue"]}}
    assert fingerprint_filters(filters=original) == fingerprint_filters(filters=reordered)
    assert fingerprint_filters(filters=original) != fingerprint_filters(filters={**original, "outcomes": ["success"]})


def test_fingerprint_filters_keeps_separate_and_predicates() -> None:
    first = {"dimension": {"name": "converter_type"}, "values": [{"value": "A"}, {"value": "B"}]}
    second = {"dimension": {"name": "converter_type"}, "values": [{"value": "C"}]}
    separate = fingerprint_filters(filters={"dimensions": [first, second]})
    assert separate == fingerprint_filters(filters={"dimensions": [second, first]})
    combined = {**first, "values": [*first["values"], *second["values"]]}
    assert separate != fingerprint_filters(filters={"dimensions": [combined]})


def test_normalize_label_filters_preserves_history_semantics_without_mutating_input() -> None:
    labels = {"team": ["red", "", "blue", "red"], "empty": "", "omitted": [], "operation": "campaign"}
    assert normalize_label_filters(labels=labels) == {"operation": "campaign", "team": ["blue", "red"]}
    assert labels == {"team": ["red", "", "blue", "red"], "empty": "", "omitted": [], "operation": "campaign"}
    assert normalize_label_filters(labels={"empty": "", "omitted": []}) is None
    assert normalize_label_filters(labels=None) is None
