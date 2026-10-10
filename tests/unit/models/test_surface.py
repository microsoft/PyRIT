# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Condition,
    Contains,
    ContentWritten,
    Equals,
    Observation,
    Regex,
    ScoringExpectation,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceMatch,
    SurfaceObservationPayload,
    SurfaceScorable,
    TraceScorable,
    scorable_from_dict,
)

if TYPE_CHECKING:
    from pyrit.models import TextMatcher

_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
_SOURCE = ComponentIdentifier(class_name="FakeSource", class_module="tests.fake")


def _entry(
    *,
    uri: str = "/data/out.txt",
    sha256: str | None = "a" * 64,
    content: str | None = "hello",
    content_truncated: bool = False,
) -> SurfaceEntry:
    return SurfaceEntry(
        uri=uri, size_bytes=5, sha256=sha256, modified_at=_NOW, content=content, content_truncated=content_truncated
    )


def test_surface_scorable_round_trips() -> None:
    scorable = SurfaceScorable(uri="/data/**", match=SurfaceMatch.GLOB)

    restored = scorable_from_dict(scorable.model_dump(mode="json"))

    assert restored == scorable
    assert isinstance(restored, SurfaceScorable)
    assert restored.match is SurfaceMatch.GLOB
    assert scorable.model_dump(mode="json") == {"scorable_type": "surface", "uri": "/data/**", "match": "glob"}


@pytest.mark.parametrize("uri", ["", "/data/\x00out.txt"])
def test_surface_scorable_rejects_unusable_locators(uri: str) -> None:
    with pytest.raises(ValidationError):
        SurfaceScorable(uri=uri)


@pytest.mark.parametrize("matcher", [None, Contains(value="secret"), Equals(value="answer"), Regex(value=r"key=\d+")])
def test_content_written_round_trips_through_condition_registry(matcher: TextMatcher | None) -> None:
    condition = ContentWritten(uri="/data/*", match=SurfaceMatch.GLOB, matcher=matcher)

    assert Condition.model_validate(condition.model_dump(mode="json")) == condition
    expectation = ScoringExpectation(conditions=(condition,))
    assert ScoringExpectation.model_validate_json(expectation.model_dump_json()) == expectation


@pytest.mark.parametrize("match", ["exact", "glob"])
def test_surface_match_accepts_serialized_values(match: str) -> None:
    scorable = SurfaceScorable.model_validate({"uri": "/data/out.txt", "match": match})
    condition = ContentWritten.model_validate({"uri": "/data/out.txt", "match": match})

    assert scorable.match is SurfaceMatch(match)
    assert condition.match is scorable.match


def test_surface_match_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError, match="match"):
        SurfaceScorable.model_validate({"uri": "/data/out.txt", "match": "unknown"})
    with pytest.raises(ValidationError, match="match"):
        ContentWritten.model_validate({"uri": "/data/out.txt", "match": "unknown"})


def test_surface_scorable_rejects_unused_surface_selector() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        SurfaceScorable.model_validate({"uri": "/data/out.txt", "surface": "file"})


def test_truncated_entry_requires_retained_text() -> None:
    with pytest.raises(ValidationError, match="must retain the text"):
        _entry(content=None, content_truncated=True)


def test_incomplete_read_cannot_claim_full_text() -> None:
    with pytest.raises(ValidationError, match="must be marked truncated"):
        _entry(sha256=None, content="prefix")


def test_incomplete_read_retains_a_replayable_prefix_without_a_digest() -> None:
    entry = _entry(sha256=None, content="pre", content_truncated=True)

    assert SurfaceEntry.model_validate_json(entry.model_dump_json()) == entry
    assert entry.sha256 is None


def test_exact_scope_payload_holds_only_its_location() -> None:
    scope = SurfaceScorable(uri="/data/out.txt")
    with pytest.raises(ValidationError, match="exact surface scope"):
        SurfaceObservationPayload(scope=scope, entries=(_entry(uri="/data/other.txt"),))


def test_payload_rejects_repeated_locations() -> None:
    scope = SurfaceScorable(uri="/data/*", match=SurfaceMatch.GLOB)
    with pytest.raises(ValidationError, match="each location once"):
        SurfaceObservationPayload(scope=scope, entries=(_entry(), _entry()))


def test_payload_rejects_coerced_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        SurfaceObservationPayload(scope=SurfaceScorable(uri="/a"), schema_version="1")  # type: ignore[arg-type]


def test_observation_round_trips_surface_payload() -> None:
    scope = SurfaceScorable(uri="/data/out.txt")
    observation = Observation(
        source_identifier=_SOURCE,
        acquisition=Acquisition.COMPLETE,
        scorable=scope,
        payload=SurfaceObservationPayload(scope=scope, entries=(_entry(),), coverage=SurfaceCoverage(complete=True)),
    )

    assert Observation.model_validate_json(observation.model_dump_json()) == observation
    observation.validate_evidence(message_pieces={})


@pytest.mark.parametrize(
    ("acquisition", "complete", "entries", "match"),
    [
        (Acquisition.COMPLETE, False, (), "completeness must agree"),
        (Acquisition.PARTIAL, True, (), "completeness must agree"),
        (Acquisition.UNAVAILABLE, False, (_entry(),), "cannot contain entries"),
        (Acquisition.ERROR, False, (_entry(),), "cannot contain entries"),
    ],
)
def test_observation_rejects_inconsistent_surface_acquisition(
    *, acquisition: Acquisition, complete: bool, entries: tuple[SurfaceEntry, ...], match: str
) -> None:
    scope = SurfaceScorable(uri="/data/out.txt")
    with pytest.raises(ValidationError, match=match):
        Observation(
            source_identifier=_SOURCE,
            acquisition=acquisition,
            scorable=scope,
            payload=SurfaceObservationPayload(
                scope=scope, entries=entries, coverage=SurfaceCoverage(complete=complete)
            ),
        )


def test_observation_requires_matching_surface_anchor() -> None:
    scope = SurfaceScorable(uri="/data/out.txt")
    with pytest.raises(ValidationError, match="SurfaceScorable matching"):
        Observation(
            source_identifier=_SOURCE,
            acquisition=Acquisition.COMPLETE,
            scorable=TraceScorable(trace_ids=("1" * 32,)),
            payload=SurfaceObservationPayload(scope=scope, coverage=SurfaceCoverage(complete=True)),
        )
