# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Condition,
    ContentWritten,
    Observation,
    ScoringScope,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceObservationPayload,
    SurfaceScorable,
    TraceScorable,
    scorable_from_dict,
)

_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
_SOURCE = ComponentIdentifier(class_name="FakeSource", class_module="tests.fake")


def _entry(uri: str = "/data/out.txt", **kwargs: object) -> SurfaceEntry:
    values: dict[str, object] = {
        "uri": uri,
        "size_bytes": 5,
        "sha256": "a" * 64,
        "modified_at": _NOW,
        "content": "hello",
    }
    values.update(kwargs)
    return SurfaceEntry(**values)  # type: ignore[arg-type]


def test_surface_scorable_round_trips_with_scope() -> None:
    scorable = SurfaceScorable(
        uri="/data/**",
        match="glob",
        scope=ScoringScope(window=(_NOW, _NOW + timedelta(minutes=1)), labels={"session": "s1"}, attempt_id="a1"),
    )

    restored = scorable_from_dict(scorable.model_dump(mode="json"))

    assert restored == scorable
    assert isinstance(restored, SurfaceScorable)


@pytest.mark.parametrize("uri", ["", "/data/\x00out.txt"])
def test_surface_scorable_rejects_unusable_locators(uri: str) -> None:
    with pytest.raises(ValidationError):
        SurfaceScorable(uri=uri)


def test_scope_rejects_window_that_ends_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="must not end before it starts"):
        ScoringScope(window=(_NOW, _NOW - timedelta(seconds=1)))


def test_scope_requires_aware_window() -> None:
    with pytest.raises(ValidationError):
        ScoringScope(window=(datetime(2026, 1, 1), datetime(2026, 1, 2)))  # noqa: DTZ001


def test_content_written_round_trips_through_condition_registry() -> None:
    condition = ContentWritten(uri="/data/*", match="glob", contains="secret")

    assert Condition.model_validate(condition.model_dump()) == condition


def test_content_written_rejects_empty_contains() -> None:
    with pytest.raises(ValidationError):
        ContentWritten(uri="/data/out.txt", contains="")


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
    scope = SurfaceScorable(uri="/data/*", match="glob")
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
    acquisition: Acquisition, complete: bool, entries: tuple[SurfaceEntry, ...], match: str
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
