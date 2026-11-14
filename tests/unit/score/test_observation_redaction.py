# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from pyrit.models import (
    Contains,
    ContentWritten,
    Equals,
    ScoreStatus,
    ScoringExpectation,
    SurfaceBaseline,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceMatch,
    SurfaceObservationPayload,
    SurfaceScorable,
)
from pyrit.score import (
    FileWriteScorer,
    LocalFileSurfaceSource,
    RedactionPolicy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.memory import SQLiteMemory
    from pyrit.models import TextMatcher

pytestmark = pytest.mark.usefixtures("patch_central_database")

_SECRET = "sk-" + "A" * 20
_SECRETS = RedactionPolicy(patterns=[r"sk-[A-Za-z0-9]{20}"])
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _file_expectation(matcher: TextMatcher | None) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ContentWritten(uri="/data/out.txt", matcher=matcher),))


def _write(root: Path, data: bytes | str, relative: str = "data/out.txt") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


def _pinned_surface_payload() -> SurfaceObservationPayload:
    entries = (
        SurfaceEntry(uri="a.txt", size_bytes=5, sha256="a" * 64, modified_at=_AT, content="hello"),
        SurfaceEntry(
            uri="b.txt", size_bytes=9, sha256="b" * 64, modified_at=_AT, content="hel", content_truncated=True
        ),
        SurfaceEntry(uri="c.png", size_bytes=4, sha256="c" * 64, modified_at=_AT, content=None),
    )
    return SurfaceObservationPayload(
        scope=SurfaceScorable(uri="*", match=SurfaceMatch.GLOB),
        entries=entries,
        coverage=SurfaceCoverage(complete=True),
        baseline=SurfaceBaseline(captured_at=_AT, entries=entries[:1], coverage=SurfaceCoverage(complete=True)),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_unredacted_payloads_keep_their_stored_form() -> None:
    # Digests computed from the same payload on main before redaction existed.
    surface = _pinned_surface_payload()

    assert (
        _digest(surface.model_dump(mode="json")) == "41bb6116d8843ce2fd042d956cb25a34340f09292195676fc00cbe1fd8537d9b"
    )
    assert hashlib.sha256(surface.model_dump_json().encode()).hexdigest() == (
        "17752e0c66cc7ee8112fc1efd8aec34d91d646168e8762a9a9da955d48bb2688"
    )


def test_sources_without_a_policy_keep_their_identity() -> None:
    # Hash computed on main; a policy is opt-in and changes identity only when set.
    surface = LocalFileSurfaceSource(root="/evidence")

    assert surface.get_identifier().hash == "202536450c4f0a4bb76b7da45f21eb28783d9525434094a94e3107b186cd93fc"
    redacting = LocalFileSurfaceSource(root="/evidence", redaction=_SECRETS).get_identifier()
    assert redacting.children["redaction"] == _SECRETS.get_identifier()
    assert redacting.hash != surface.get_identifier().hash


def test_policy_identifier_is_versioned_and_does_not_reveal_patterns() -> None:
    identifier = RedactionPolicy(patterns=[_SECRET], fields=["content"]).get_identifier()

    assert identifier.params["redaction_version"] == 1
    assert identifier.params["fields"] == ["content"]
    assert identifier.params["patterns_sha256"] == hashlib.sha256(json.dumps([_SECRET]).encode()).hexdigest()
    assert _SECRET not in identifier.model_dump_json()
    assert "patterns_sha256" not in RedactionPolicy(fields=["content"]).get_identifier().params
    assert identifier.hash != RedactionPolicy(patterns=["other"], fields=["content"]).get_identifier().hash


@pytest.mark.parametrize(
    ("policy", "field", "text", "complete", "expected"),
    [
        (_SECRETS, "content", f"key {_SECRET}", True, True),
        (_SECRETS, "content", "nothing sensitive", True, False),
        (_SECRETS, "content", "nothing sensitive", False, True),
        (_SECRETS, "content", "", True, False),
        (RedactionPolicy(fields=["content"]), "content", "", True, True),
        (RedactionPolicy(fields=["content"]), "content", "partial", False, True),
    ],
)
def test_policy_withholds_whole_fields(
    policy: RedactionPolicy, field: str, text: str, complete: bool, expected: bool
) -> None:
    assert policy.withholds(field=field, text=text, complete=complete) is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({}, "at least one"), ({"fields": ["stdout"]}, "Unknown"), ({"patterns": ["("]}, "Invalid")],
)
def test_invalid_policy_is_rejected(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        RedactionPolicy(**kwargs)  # type: ignore[arg-type]


def test_redacted_marker_serializes_only_when_set_and_must_not_retain_text() -> None:
    entry = SurfaceEntry(uri="a.txt", size_bytes=1, sha256="a" * 64, modified_at=_AT, content_redacted=True)

    assert entry.model_dump(mode="json")["content_redacted"] is True
    assert "content_redacted" not in entry.model_copy(update={"content_redacted": False}).model_dump(mode="json")
    assert SurfaceEntry.model_validate(entry.model_dump(mode="json")) == entry
    for kept in ({"content": "x"}, {"content": "x", "content_truncated": True}):
        with pytest.raises(ValidationError, match="redacted surface entry"):
            SurfaceEntry(uri="a.txt", size_bytes=1, sha256="a" * 64, modified_at=_AT, content_redacted=True, **kept)


async def test_withheld_file_text_is_undetermined_never_false_async(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    _write(tmp_path, f"token={_SECRET}\n")
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path, redaction=_SECRETS))
    scope = SurfaceScorable(uri="/data/out.txt")

    absent = (await scorer.score_async(scorable=scope, expectation=_file_expectation(Contains(value="nothing"))))[0]
    present = (await scorer.score_async(scorable=scope, expectation=_file_expectation(None)))[0]

    assert absent.status is ScoreStatus.UNDETERMINED
    assert present.score_value == "true"
    observation = (await sqlite_instance.get_observations_async(observation_ids=absent.observation_ids))[0]
    assert isinstance(observation.payload, SurfaceObservationPayload)
    entry = observation.payload.entries[0]
    assert (entry.content, entry.content_redacted, entry.size_bytes) == (None, True, len(f"token={_SECRET}\n"))
    assert entry.sha256 == hashlib.sha256(f"token={_SECRET}\n".encode()).hexdigest()
    assert _SECRET not in observation.model_dump_json()
    replay = (
        await scorer.score_observation_async(observation=observation, expectation=_file_expectation(Equals(value="x")))
    )[0]
    assert replay.status is ScoreStatus.UNDETERMINED


async def test_text_without_a_match_is_kept_async(tmp_path: Path, sqlite_instance: SQLiteMemory) -> None:
    _write(tmp_path, "harmless")
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path, redaction=_SECRETS))

    score = (
        await scorer.score_async(
            scorable=SurfaceScorable(uri="/data/out.txt"), expectation=_file_expectation(Contains(value="other"))
        )
    )[0]

    assert score.score_value == "false"
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert observation.payload.entries[0].content == "harmless"
    assert "content_redacted" not in observation.payload.model_dump(mode="json")["entries"][0]


async def test_text_cut_by_the_retention_limit_is_withheld_when_patterns_apply_async(tmp_path: Path) -> None:
    # The cut can split a secret, so the pattern cannot be checked against the retained prefix.
    _write(tmp_path, "a" * 8 + _SECRET)
    source = LocalFileSurfaceSource(root=tmp_path, max_content_bytes=12, redaction=_SECRETS)

    observation = await source.acquire_async(scorable=SurfaceScorable(uri="/data/out.txt"))

    assert isinstance(observation.payload, SurfaceObservationPayload)
    entry = observation.payload.entries[0]
    assert (entry.content, entry.content_truncated, entry.content_redacted) == (None, False, True)


async def test_binary_media_is_referenced_by_digest_not_stored_raw_async(tmp_path: Path) -> None:
    image = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    _write(tmp_path, image, relative="data/out.png")
    for redaction in (None, _SECRETS):
        source = LocalFileSurfaceSource(root=tmp_path, redaction=redaction)

        observation = await source.acquire_async(scorable=SurfaceScorable(uri="/data/out.png"))

        assert isinstance(observation.payload, SurfaceObservationPayload)
        entry = observation.payload.entries[0]
        assert (entry.content, entry.content_redacted) == (None, False)
        assert (entry.size_bytes, entry.sha256) == (len(image), hashlib.sha256(image).hexdigest())


async def test_baseline_entries_are_withheld_too_async(tmp_path: Path) -> None:
    _write(tmp_path, f"before {_SECRET}")
    source = LocalFileSurfaceSource(root=tmp_path, redaction=_SECRETS)
    scope = SurfaceScorable(uri="/data/out.txt")
    baseline = await source.start_observing_async(scorable=scope)
    _write(tmp_path, "after")
    unredacted = baseline.model_copy(
        update={
            "entries": (
                baseline.entries[0].model_copy(update={"content": f"leak {_SECRET}", "content_redacted": False}),
            )
        }
    )

    observation = await source.finalize_async(scorable=scope, baseline=unredacted)

    assert baseline.entries[0].content_redacted
    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert observation.payload.baseline is not None
    assert observation.payload.baseline.entries[0].content is None
    assert observation.payload.entries[0].content == "after"
