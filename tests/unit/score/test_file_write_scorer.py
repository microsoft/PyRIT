# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    Acquisition,
    AttackOutcome,
    ComponentIdentifier,
    ContentScorable,
    ContentWritten,
    MessageScorable,
    ScoringExpectation,
    ScoringScope,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceObservationPayload,
    SurfaceScorable,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.prompt_target import HTTPTarget
from pyrit.score import FileWriteScorer, LocalFileSurfaceSource, NonReplayableObservationError
from pyrit.score.true_false.file_write_scorer import match_content_written

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _expectation(
    uri: str = "/data/out.txt", *, match: str = "exact", contains: str | None = None
) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ContentWritten(uri=uri, match=match, contains=contains),))  # type: ignore[arg-type]


def _write(root: Path, relative: str, data: bytes | str = "hello") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _link(link: Path, target: Path) -> None:
    """Create a symbolic link, skipping where the platform does not allow it (Windows without privileges)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symbolic links unavailable: {error}")


def _age(path: Path, *, days: int) -> None:
    old = (datetime.now(tz=UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (old, old))


# --- source ---------------------------------------------------------------------------------


async def test_source_reads_exact_location(tmp_path: Path) -> None:
    _write(tmp_path, "data/out.txt", "hello")
    scorable = SurfaceScorable(uri="/data/out.txt")

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(scorable=scorable)

    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.scorable == scorable
    (entry,) = observation.payload.entries
    assert (entry.uri, entry.size_bytes, entry.content, entry.content_truncated) == ("/data/out.txt", 5, "hello", False)


async def test_source_reports_absence_as_complete(tmp_path: Path) -> None:
    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(scorable=SurfaceScorable(uri="/missing"))

    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.payload.entries == ()


async def test_source_without_root_is_unavailable(tmp_path: Path) -> None:
    source = LocalFileSurfaceSource(root=tmp_path / "absent")

    observation = await source.acquire_async(scorable=SurfaceScorable(uri="/data/out.txt"))

    assert observation.acquisition is Acquisition.UNAVAILABLE
    assert observation.payload.coverage.reasons == ("surface_root_unavailable",)


@pytest.mark.parametrize("uri", ["/../outside.txt", "/data/../../outside.txt", "/"])
async def test_source_rejects_locators_that_leave_the_root(tmp_path: Path, uri: str) -> None:
    with pytest.raises(ValueError, match="inside the root"):
        await LocalFileSurfaceSource(root=tmp_path).acquire_async(scorable=SurfaceScorable(uri=uri))


async def test_source_rejects_other_surfaces(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'file' surface"):
        await LocalFileSurfaceSource(root=tmp_path).acquire_async(scorable=SurfaceScorable(uri="/c/b", surface="blob"))


async def test_glob_covers_every_file_and_skips_directories(tmp_path: Path) -> None:
    _write(tmp_path, "data/a.txt", "one")
    _write(tmp_path, "data/nested/b.txt", "two")
    (tmp_path / "data" / "empty_dir").mkdir()

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/**/*", match="glob")
    )

    assert observation.acquisition is Acquisition.COMPLETE
    assert [entry.uri for entry in observation.payload.entries] == ["/data/a.txt", "/data/nested/b.txt"]


async def test_glob_over_file_limit_is_partial(tmp_path: Path) -> None:
    for index in range(3):
        _write(tmp_path, f"data/{index}.txt")

    observation = await LocalFileSurfaceSource(root=tmp_path, max_files=2).acquire_async(
        scorable=SurfaceScorable(uri="/data/*", match="glob")
    )

    assert observation.acquisition is Acquisition.PARTIAL
    assert len(observation.payload.entries) == 2
    assert "file_limit_exceeded" in observation.payload.coverage.reasons


async def test_link_outside_root_is_not_read(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    secret = _write(tmp_path, "host_secret.txt", "host credentials")
    _link(root / "data" / "out.txt", secret)

    observation = await LocalFileSurfaceSource(root=root).acquire_async(scorable=SurfaceScorable(uri="/data/out.txt"))

    assert observation.acquisition is Acquisition.PARTIAL
    assert observation.payload.entries == ()
    assert observation.payload.coverage.reasons == ("link_outside_root",)


async def test_link_inside_root_is_read_under_its_own_name(tmp_path: Path) -> None:
    target = _write(tmp_path, "real/out.txt", "inside")
    _link(tmp_path / "data" / "out.txt", target)

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/out.txt")
    )

    assert observation.acquisition is Acquisition.COMPLETE
    assert [(entry.uri, entry.content) for entry in observation.payload.entries] == [("/data/out.txt", "inside")]


async def test_dangling_link_is_a_coverage_gap(tmp_path: Path) -> None:
    _link(tmp_path / "data" / "out.txt", tmp_path / "gone.txt")

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/out.txt")
    )

    assert observation.acquisition is Acquisition.PARTIAL
    assert observation.payload.coverage.reasons == ("dangling_link",)


async def test_window_excludes_files_written_before_the_run(tmp_path: Path) -> None:
    _age(_write(tmp_path, "data/old.txt", "planted"), days=3)
    _write(tmp_path, "data/new.txt", "fresh")
    now = datetime.now(tz=UTC)
    scope = ScoringScope(window=(now - timedelta(hours=1), now + timedelta(minutes=1)))

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/*", match="glob", scope=scope)
    )

    assert observation.acquisition is Acquisition.COMPLETE
    assert [entry.uri for entry in observation.payload.entries] == ["/data/new.txt"]
    assert observation.payload.excluded_outside_scope == 1


async def test_correlation_keys_are_recorded_as_not_applied(tmp_path: Path) -> None:
    scope = ScoringScope(attempt_id="attack-1", labels={"session": "s"})

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/out.txt", scope=scope)
    )

    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.payload.coverage.reasons == ()
    assert observation.metadata == {"scope_keys_not_applied": "attempt_id,labels"}


async def test_binary_content_is_hashed_but_not_retained(tmp_path: Path) -> None:
    _write(tmp_path, "data/blob.bin", b"\xff\xfe\x00binary")

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
        scorable=SurfaceScorable(uri="/data/blob.bin")
    )

    (entry,) = observation.payload.entries
    assert entry.content is None
    assert entry.size_bytes == 9
    assert entry.content_truncated is False


async def test_truncation_keeps_a_prefix_cut_inside_a_multibyte_character(tmp_path: Path) -> None:
    _write(tmp_path, "data/out.txt", "ab\u00e9cd")  # U+00E9 is two bytes in UTF-8, so a 3-byte cut splits it.

    observation = await LocalFileSurfaceSource(root=tmp_path, max_content_bytes=3).acquire_async(
        scorable=SurfaceScorable(uri="/data/out.txt")
    )

    (entry,) = observation.payload.entries
    assert (entry.content, entry.content_truncated, entry.size_bytes) == ("ab", True, 6)


def test_source_rejects_non_positive_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        LocalFileSurfaceSource(root=tmp_path, max_files=0)


# --- matching ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contents", "contains", "acquisition", "expected"),
    [
        (["hello"], None, Acquisition.COMPLETE, True),
        ([""], None, Acquisition.COMPLETE, False),
        ([], None, Acquisition.COMPLETE, False),
        ([], None, Acquisition.PARTIAL, None),
        (["the key is 42"], "key", Acquisition.COMPLETE, True),
        (["nothing here"], "key", Acquisition.COMPLETE, False),
        (["nothing here"], "key", Acquisition.PARTIAL, None),
        (["key"], "key", Acquisition.PARTIAL, True),
        ([], None, Acquisition.UNAVAILABLE, None),
    ],
)
def test_match_content_written(
    contents: list[str], contains: str | None, acquisition: Acquisition, expected: bool | None
) -> None:
    scope = SurfaceScorable(uri="/data/*", match="glob")
    entries = tuple(
        SurfaceEntry(
            uri=f"/data/{index}.txt",
            size_bytes=len(text.encode()),
            sha256="0" * 64,
            modified_at=datetime.now(tz=UTC),
            content=text,
        )
        for index, text in enumerate(contents)
    )
    payload = SurfaceObservationPayload(
        scope=scope, entries=entries, coverage=SurfaceCoverage(complete=acquisition is Acquisition.COMPLETE)
    )

    condition = ContentWritten(uri="/data/*", match="glob", contains=contains)
    assert match_content_written(condition=condition, payload=payload, acquisition=acquisition) is expected


# --- scorer ---------------------------------------------------------------------------------


def test_scorer_identifier_retains_source_child(tmp_path: Path) -> None:
    source = LocalFileSurfaceSource(root=tmp_path)
    identifier = FileWriteScorer(source=source).get_identifier()

    assert identifier.children["source"] == source.get_identifier()
    assert identifier.params["matching_version"] == 1
    assert identifier.hash != FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path / "x")).get_identifier().hash
    assert ComponentIdentifier.model_validate_json(identifier.model_dump_json()).children == identifier.children


@pytest.mark.parametrize(
    ("data", "contains", "value"),
    [
        ("exfiltrated", None, True),
        ("api_key=XYZ", "api_key", True),
        ("harmless", "api_key", False),
        (None, None, False),
        (b"\xff\xfe\x00", "api_key", None),
    ],
)
async def test_verdict_and_offline_replay_async(
    sqlite_instance: SQLiteMemory, tmp_path: Path, data: str | bytes | None, contains: str | None, value: bool | None
) -> None:
    if data is not None:
        _write(tmp_path, "data/out.txt", data)
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    scorable = SurfaceScorable(uri="/data/out.txt")
    expectation = _expectation(contains=contains)

    score = (await scorer.score_async(scorable=scorable, expectation=expectation))[0]

    assert score.status.value == ("undetermined" if value is None else "complete")
    if value is not None:
        assert score.get_value() is value
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert observation.scorable == scorable

    # Replay judges the stored snapshot without reading the file system again.
    for path in tmp_path.rglob("*.txt"):
        path.unlink()
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replay.status == score.status
    assert replay.observation_ids == score.observation_ids
    if value is not None:
        assert replay.get_value() is value


async def test_replay_with_new_content_criterion_async(sqlite_instance: SQLiteMemory, tmp_path: Path) -> None:
    _write(tmp_path, "data/out.txt", "password=hunter2")
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    score = (await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=_expectation()))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    replay = await scorer.score_observation_async(observation=observation, expectation=_expectation(contains="hunter2"))
    miss = await scorer.score_observation_async(observation=observation, expectation=_expectation(contains="absent"))

    assert replay[0].get_value() is True
    assert miss[0].get_value() is False


async def test_replay_rejects_a_different_locator_async(sqlite_instance: SQLiteMemory, tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    score = (await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=_expectation()))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    with pytest.raises(NonReplayableObservationError, match="different location"):
        await scorer.score_observation_async(observation=observation, expectation=_expectation("/data/other.txt"))


async def test_scorable_must_match_condition_locator(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError, match="same location"):
        await scorer.score_async(scorable=SurfaceScorable(uri="/data/a.txt"), expectation=_expectation("/data/b.txt"))


async def test_unsupported_scorable_is_rejected(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError, match="MessageScorable or an explicit SurfaceScorable"):
        await scorer.score_async(scorable=ContentScorable(value="x"), expectation=_expectation())


async def test_missing_condition_is_rejected(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    wrong = ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="x"),)),))

    with pytest.raises((TypeError, ValueError, RuntimeError), match="ContentWritten"):
        await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=wrong)


def test_negative_clock_skew_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path), clock_skew_seconds=-1)


# --- through a real attack ---------------------------------------------------------------------


def _agent_target(root: Path, *, write: bool) -> HTTPTarget:
    """An agent stand-in that writes the prompt it receives to /data/out.txt under its workspace."""

    def respond(request: httpx.Request) -> httpx.Response:
        if write:
            _write(root, "data/out.txt", request.content.decode())
        return httpx.Response(200, text="done")

    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
    )


@pytest.mark.parametrize(
    ("write", "planted", "outcome"),
    [
        (True, False, AttackOutcome.SUCCESS),
        (False, False, AttackOutcome.FAILURE),
        # A file already there before the run started is not this run's write.
        (False, True, AttackOutcome.FAILURE),
    ],
)
async def test_attack_scores_file_write_and_scopes_it_to_the_run_async(
    sqlite_instance: SQLiteMemory, tmp_path: Path, write: bool, planted: bool, outcome: AttackOutcome
) -> None:
    if planted:
        _age(_write(tmp_path, "data/out.txt", "exfiltrated earlier"), days=1)
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    attack = PromptSendingAttack(
        objective_target=_agent_target(tmp_path, write=write),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )

    result = await attack.execute_async(objective="exfiltrated", expectation=_expectation(contains="exfiltrated"))

    assert result.outcome is outcome
    score = result.automated_score
    assert score is not None
    assert result.last_response is not None
    assert score.scorable == MessageScorable(message_piece_ids=(result.last_response.id,))
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert isinstance(observation.scorable, SurfaceScorable)
    scope = observation.scorable.scope
    assert scope is not None
    assert scope.attempt_id == result.attack_result_id
    assert scope.window is not None
    assert observation.payload.excluded_outside_scope == (1 if planted else 0)
