# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import (
    Acquisition,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Contains,
    ContentScorable,
    ContentWritten,
    Equals,
    MessagePiece,
    MessageScorable,
    Observation,
    Regex,
    ScoreStatus,
    ScoringExpectation,
    SurfaceBaseline,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceMatch,
    SurfaceObservationPayload,
    SurfaceScorable,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.score import (
    FileWriteScorer,
    LocalFileSurfaceSource,
    NonReplayableObservationError,
    TrueFalseCompositeScorer,
    TrueFalseFallbackScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)
from pyrit.score.true_false.file_write_scorer import match_content_written

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.memory import SQLiteMemory
    from pyrit.models import TextMatcher
    from pyrit.prompt_target import HTTPTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _expectation(
    *,
    uri: str = "/data/out.txt",
    match: SurfaceMatch = SurfaceMatch.EXACT,
    matcher: TextMatcher | None = None,
    require_content_change: bool = False,
) -> ScoringExpectation:
    return ScoringExpectation(
        conditions=(
            ContentWritten(uri=uri, match=match, matcher=matcher, require_content_change=require_content_change),
        )
    )


def _write(*, root: Path, relative: str = "data/out.txt", data: bytes | str = "hello") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("contents", "matcher", "acquisition", "expected"),
    [
        (["hello"], None, Acquisition.COMPLETE, True),
        ([""], None, Acquisition.COMPLETE, False),
        ([], None, Acquisition.COMPLETE, False),
        ([], None, Acquisition.PARTIAL, None),
        (["the key is 42"], Contains(value="KEY"), Acquisition.COMPLETE, True),
        (["the key is 42"], Contains(value="KEY", case_sensitive=True), Acquisition.COMPLETE, False),
        (["nothing here"], Contains(value="key"), Acquisition.COMPLETE, False),
        (["nothing here"], Contains(value="key"), Acquisition.PARTIAL, None),
        (["key"], Contains(value="key"), Acquisition.PARTIAL, True),
        ([" Answer "], Equals(value="answer"), Acquisition.COMPLETE, True),
        ([" Answer "], Equals(value="answer", case_sensitive=True), Acquisition.COMPLETE, False),
        ([" Answer "], Equals(value="answer", ignore_whitespace=False), Acquisition.COMPLETE, False),
        (["key=42"], Regex(value=r"KEY=\d+$"), Acquisition.COMPLETE, True),
        (["key=42"], Regex(value=r"KEY=\d+$", case_sensitive=True), Acquisition.COMPLETE, False),
        ([""], Equals(value=""), Acquisition.COMPLETE, True),
        ([""], Contains(value=""), Acquisition.COMPLETE, False),
        ([], None, Acquisition.UNAVAILABLE, None),
        ([], None, Acquisition.ERROR, None),
    ],
)
def test_match_content_written(
    *, contents: list[str], matcher: TextMatcher | None, acquisition: Acquisition, expected: bool | None
) -> None:
    scope = SurfaceScorable(uri="/data/*", match=SurfaceMatch.GLOB)
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

    condition = ContentWritten(uri=scope.uri, match=scope.match, matcher=matcher)
    assert match_content_written(condition=condition, payload=payload, acquisition=acquisition) is expected


def test_scorer_identifier_retains_source_child(tmp_path: Path) -> None:
    source = LocalFileSurfaceSource(root=tmp_path)
    identifier = FileWriteScorer(source=source).get_identifier()

    assert identifier.children["source"] == source.get_identifier()
    assert identifier.params["matching_version"] == 3
    assert "surface" not in identifier.params
    assert identifier.hash != FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path / "x")).get_identifier().hash
    assert ComponentIdentifier.model_validate_json(identifier.model_dump_json()).children == identifier.children


@pytest.mark.parametrize(
    ("data", "matcher", "value"),
    [
        ("exfiltrated", None, True),
        ("API_KEY=EXAMPLE", Contains(value="api_key"), True),
        ("API_KEY=EXAMPLE", Contains(value="api_key", case_sensitive=True), False),
        ("harmless", Contains(value="api_key"), False),
        (" answer ", Equals(value="ANSWER"), True),
        ("key=42", Regex(value=r"key=\d+$"), True),
        ("", None, False),
        ("", Equals(value=""), True),
        (None, None, False),
        (b"\xff\xfe\x00", Contains(value="api_key"), None),
        (b"\xff\xfe\x00", None, True),
    ],
)
async def test_verdict_and_offline_replay_async(
    *,
    sqlite_instance: SQLiteMemory,
    tmp_path: Path,
    data: str | bytes | None,
    matcher: TextMatcher | None,
    value: bool | None,
) -> None:
    if data is not None:
        _write(root=tmp_path, data=data)
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    scorable = SurfaceScorable(uri="/data/out.txt")
    expectation = _expectation(matcher=matcher)

    score = (await scorer.score_async(scorable=scorable, expectation=expectation))[0]
    stored_score = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    assert score.status is (ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE)
    assert stored_score.scored_expectation == expectation
    assert stored_score.status == score.status
    assert stored_score.score_rationale == score.score_rationale
    assert stored_score.score_value_description == "The named location held the content when it was read."
    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert observation.scorable == scorable

    for path in tmp_path.rglob("*.txt"):
        path.unlink()
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replay.status == score.status
    assert replay.observation_ids == score.observation_ids
    if value is not None:
        assert score.get_value() is value
        assert stored_score.get_value() is value
        assert replay.get_value() is value


@pytest.mark.parametrize("read_limited", [False, True])
@pytest.mark.parametrize(
    ("matcher", "value"),
    [
        (None, True),
        (Contains(value="MARKER"), True),
        (Contains(value="tail"), None),
        (Contains(value="MARKER", case_sensitive=True), None),
        (Equals(value="marker"), None),
        (Equals(value="different"), None),
        (Regex(value="marker$"), None),
        (Regex(value="marker"), None),
    ],
)
async def test_truncated_text_preserves_uncertainty_and_replay_async(
    *,
    sqlite_instance: SQLiteMemory,
    tmp_path: Path,
    read_limited: bool,
    matcher: TextMatcher | None,
    value: bool | None,
) -> None:
    path = _write(root=tmp_path, data="marker-tail")
    scorer = FileWriteScorer(
        source=LocalFileSurfaceSource(root=tmp_path, max_content_bytes=6, max_read_bytes=6 if read_limited else 1000)
    )
    expectation = _expectation(matcher=matcher)
    score = (await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=expectation))[0]
    stored_score = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert observation.payload.entries[0].content == "marker"
    assert observation.payload.entries[0].content_truncated
    assert observation.acquisition is (Acquisition.PARTIAL if read_limited else Acquisition.COMPLETE)
    assert stored_score.scored_expectation == expectation
    path.unlink()
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    for result in (score, stored_score, replay):
        assert result.status is (ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE)
        if value is not None:
            assert result.get_value() is value


@pytest.mark.parametrize("uri", ["/data/**", "/data/**/*", "/data/**/**", "/data/**/**/out.txt"])
@pytest.mark.parametrize("expected", [True, False])
async def test_recursive_glob_verdict_and_replay_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, uri: str, expected: bool
) -> None:
    first = _write(root=tmp_path, data="harmless")
    nested = _write(root=tmp_path, relative="data/nested/out.txt", data="secret")
    (tmp_path / "data" / "empty").mkdir()
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    scorable = SurfaceScorable(uri=uri, match=SurfaceMatch.GLOB)
    expectation = _expectation(
        uri=uri, match=SurfaceMatch.GLOB, matcher=Contains(value="secret" if expected else "absent")
    )

    score = (await scorer.score_async(scorable=scorable, expectation=expectation))[0]
    stored_score = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    assert score.get_value() is expected
    assert stored_score.scored_expectation == expectation
    assert stored_score.get_value() is expected
    assert observation.acquisition is Acquisition.COMPLETE
    assert isinstance(observation.payload, SurfaceObservationPayload)
    assert [entry.uri for entry in observation.payload.entries] == ["/data/nested/out.txt", "/data/out.txt"]
    first.unlink()
    nested.unlink()
    replayed = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replayed.get_value() is expected


@pytest.mark.parametrize(
    ("matcher", "expected"),
    [
        (Contains(value="example-marker"), True),
        (Contains(value="absent"), False),
        (Equals(value="token=example-marker"), True),
        (Regex(value="token=.+$"), True),
    ],
)
async def test_replay_with_new_content_criterion_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, matcher: TextMatcher, expected: bool
) -> None:
    path = _write(root=tmp_path, data="token=example-marker")
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    score = (await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=_expectation()))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    path.unlink()

    expectation = _expectation(matcher=matcher)
    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replay.get_value() is expected
    assert replay.scored_expectation == expectation


async def test_replay_rejects_a_different_locator_async(*, sqlite_instance: SQLiteMemory, tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    score = (await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=_expectation()))[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    with pytest.raises(NonReplayableObservationError, match="different location"):
        await scorer.score_observation_async(observation=observation, expectation=_expectation(uri="/data/other.txt"))


async def test_scorable_must_match_condition_locator_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError, match="same location"):
        await scorer.score_async(
            scorable=SurfaceScorable(uri="/data/a.txt"), expectation=_expectation(uri="/data/b.txt")
        )


async def test_unsupported_scorable_is_rejected_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError, match="MessageScorable or an explicit SurfaceScorable"):
        await scorer.score_async(scorable=ContentScorable(value="x"), expectation=_expectation())


async def test_missing_condition_is_rejected_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    wrong = ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="x"),)),))

    with pytest.raises((TypeError, ValueError, RuntimeError), match="ContentWritten"):
        await scorer.score_async(scorable=SurfaceScorable(uri="/data/out.txt"), expectation=wrong)


async def _stored_piece_async(*, memory: SQLiteMemory, conversation_id: str) -> MessagePiece:
    piece = MessagePiece(role="assistant", original_value="done", conversation_id=conversation_id)
    await memory.add_message_to_memory_async(request=piece.to_message())
    return piece


async def test_message_reference_with_a_missing_piece_is_rejected_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    stored = await _stored_piece_async(memory=sqlite_instance, conversation_id=str(uuid.uuid4()))
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError):
        await scorer.score_async(
            scorable=MessageScorable(message_piece_ids=(stored.id, uuid.uuid4())), expectation=_expectation()
        )


async def test_message_reference_spanning_two_runs_is_rejected_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    first = await _stored_piece_async(memory=sqlite_instance, conversation_id=str(uuid.uuid4()))
    second = await _stored_piece_async(memory=sqlite_instance, conversation_id=str(uuid.uuid4()))
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    with pytest.raises(RuntimeError):
        await scorer.score_async(
            scorable=MessageScorable(message_piece_ids=(first.id, second.id)), expectation=_expectation()
        )


@pytest.mark.parametrize("latest", [True, False])
async def test_message_is_judged_only_without_a_later_turn_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, latest: bool
) -> None:
    _write(root=tmp_path, data="exfiltrated")
    conversation_id = str(uuid.uuid4())
    earlier = await _stored_piece_async(memory=sqlite_instance, conversation_id=conversation_id)
    later = await _stored_piece_async(memory=sqlite_instance, conversation_id=conversation_id)
    source = LocalFileSurfaceSource(root=tmp_path)
    scorer = FileWriteScorer(source=source)
    scorable = MessageScorable(message_piece_ids=((later if latest else earlier).id,))

    with patch.object(source, "acquire_async", wraps=source.acquire_async) as acquire:
        score = (await scorer.score_async(scorable=scorable, expectation=_expectation()))[0]

    assert score.status is (ScoreStatus.COMPLETE if latest else ScoreStatus.UNDETERMINED)
    assert score.scorable == scorable
    assert bool(score.observation_ids) is latest
    assert acquire.call_count == int(latest)
    if latest:
        assert score.get_value() is True
    else:
        assert "conversation continued" in score.score_rationale


async def test_message_snapshot_replays_after_a_later_turn_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    path = _write(root=tmp_path, data="marker")
    conversation_id = str(uuid.uuid4())
    piece = await _stored_piece_async(memory=sqlite_instance, conversation_id=conversation_id)
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    expectation = _expectation(matcher=Contains(value="marker"))
    score = (
        await scorer.score_async(scorable=MessageScorable(message_piece_ids=(piece.id,)), expectation=expectation)
    )[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    await _stored_piece_async(memory=sqlite_instance, conversation_id=conversation_id)
    path.unlink()

    replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]
    assert replay.get_value() is True
    assert replay.observation_ids == score.observation_ids


def _agent_target(*, root: Path, write: bool, preserve_days: int | None = None) -> HTTPTarget:
    """
    Return an agent stand-in that writes the prompt it receives to /data/out.txt under its root.

    With ``preserve_days`` the write keeps an old timestamp, as a copy that preserves times does.
    """
    from pyrit.prompt_target import HTTPTarget

    def respond(request: httpx.Request) -> httpx.Response:
        if write:
            path = _write(root=root, data=request.content.decode())
            if preserve_days is not None:
                _age(path, days=preserve_days)
        return httpx.Response(200, text="done")

    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
    )


@pytest.mark.parametrize(("write", "outcome"), [(True, AttackOutcome.SUCCESS), (False, AttackOutcome.FAILURE)])
async def test_attack_scores_file_write_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, write: bool, outcome: AttackOutcome
) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=tmp_path, write=write),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )
    expectation = _expectation(matcher=Contains(value="exfiltrated"))
    result = await attack.execute_async(objective="exfiltrated", expectation=expectation)

    assert result.outcome is outcome
    score = result.automated_score
    assert score is not None
    assert result.last_response is not None
    assert score.scorable == MessageScorable(message_piece_ids=(result.last_response.id,))
    assert score.scored_expectation == ScoringExpectation(objective="exfiltrated", conditions=expectation.conditions)
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.scorable == SurfaceScorable(uri="/data/out.txt")


# --- observation lifecycle ------------------------------------------------------------------


def _change_expectation(*, matcher: TextMatcher | None = None) -> ScoringExpectation:
    return _expectation(matcher=matcher, require_content_change=True)


def _age(path: Path, *, days: int) -> None:
    stamp = (datetime.now(tz=UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


class _SnapshotOnlySource:
    """A surface source without lifecycle support."""

    def __init__(self, root: Path) -> None:
        self._inner = LocalFileSurfaceSource(root=root)

    def get_identifier(self) -> ComponentIdentifier:
        return self._inner.get_identifier()

    async def acquire_async(self, *, scorable: SurfaceScorable) -> Observation:
        return await self._inner.acquire_async(scorable=scorable)


async def _run_attack(
    *,
    root: Path,
    source: object,
    write: bool,
    preserve_days: int | None = None,
    require_content_change: bool = True,
) -> AttackResult:
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=root, write=write, preserve_days=preserve_days),
        attack_scoring_config=AttackScoringConfig(objective_scorer=FileWriteScorer(source=source)),  # type: ignore[arg-type]
        max_attempts_on_failure=0,
    )
    return await attack.execute_async(
        objective="exfiltrated",
        expectation=_expectation(matcher=Contains(value="exfiltrated"), require_content_change=require_content_change),
    )


async def test_attack_records_a_baseline_and_finalizes_against_it_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    result = await _run_attack(root=tmp_path, source=LocalFileSurfaceSource(root=tmp_path), write=True)

    assert result.outcome is AttackOutcome.SUCCESS
    score = result.automated_score
    assert score is not None
    assert "has changed content" in score.score_rationale
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    baseline = observation.payload.baseline
    assert baseline is not None
    assert baseline.entries == ()
    assert baseline.coverage.complete


async def test_baseline_rules_out_content_already_in_a_shared_workspace_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    # Presence and content change are different predicates over the same final content.
    _write(root=tmp_path, data="exfiltrated by someone else")

    snapshot = await _run_attack(
        root=tmp_path, source=_SnapshotOnlySource(tmp_path), write=False, require_content_change=False
    )
    observed = await _run_attack(root=tmp_path, source=LocalFileSurfaceSource(root=tmp_path), write=False)

    assert snapshot.outcome is AttackOutcome.SUCCESS
    assert observed.outcome is AttackOutcome.FAILURE
    score = observed.automated_score
    assert score is not None
    assert "does not establish whether writes occurred" in score.score_rationale
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.payload.baseline is not None


async def test_baseline_detects_changed_content_with_an_old_timestamp_async(tmp_path: Path) -> None:
    _write(root=tmp_path, data="nothing yet")
    _age(tmp_path / "data" / "out.txt", days=3)

    observed = await _run_attack(
        root=tmp_path, source=LocalFileSurfaceSource(root=tmp_path), write=True, preserve_days=3
    )

    assert observed.outcome is AttackOutcome.SUCCESS


async def test_source_without_lifecycle_keeps_the_snapshot_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    result = await _run_attack(
        root=tmp_path, source=_SnapshotOnlySource(tmp_path), write=True, require_content_change=False
    )

    assert result.outcome is AttackOutcome.SUCCESS
    score = result.automated_score
    assert score is not None
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.payload.baseline is None


async def test_source_without_lifecycle_records_no_baseline_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=_SnapshotOnlySource(tmp_path))  # type: ignore[arg-type]

    await scorer.start_observing_async(attempt_id="attempt", expectation=_change_expectation())

    assert scorer._baselines == {}


async def test_baseline_is_released_when_the_attempt_ends_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    started: list[str] = []
    stopped: list[str] = []
    start, stop = scorer.start_observing_async, scorer.stop_observing

    async def recording_start(*, attempt_id: str, expectation: ScoringExpectation) -> None:
        started.append(attempt_id)
        await start(attempt_id=attempt_id, expectation=expectation)
        assert scorer._baselines

    def recording_stop(*, attempt_id: str) -> None:
        stopped.append(attempt_id)
        stop(attempt_id=attempt_id)

    scorer.start_observing_async = recording_start  # type: ignore[method-assign]
    scorer.stop_observing = recording_stop  # type: ignore[method-assign]
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=tmp_path, write=False),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )

    result = await attack.execute_async(
        objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
    )

    assert started == stopped == [result.attack_result_id]
    assert scorer._baselines == {}


async def test_baseline_is_released_when_the_attempt_fails_async(tmp_path: Path) -> None:
    from pyrit.prompt_target import HTTPTarget

    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("agent unreachable", request=request)

    attack = PromptSendingAttack(
        objective_target=HTTPTarget(
            http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}", transport=httpx.MockTransport(refuse)
        ),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )

    with pytest.raises(Exception):  # noqa: B017, PT011
        await attack.execute_async(
            objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
        )

    assert scorer._baselines == {}


async def test_start_observing_reads_the_locations_in_full_async(tmp_path: Path) -> None:
    _write(root=tmp_path, data="before")

    baseline = await LocalFileSurfaceSource(root=tmp_path).start_observing_async(
        scorable=SurfaceScorable(uri="/data/out.txt")
    )

    assert baseline.coverage.complete
    (entry,) = baseline.entries
    assert (entry.uri, entry.content) == ("/data/out.txt", "before")
    assert entry.sha256 is not None


def _entry(uri: str = "/data/out.txt", content: str = "", **fields: object) -> SurfaceEntry:
    data = content.encode()
    values: dict[str, object] = {
        "uri": uri,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "modified_at": datetime(2026, 1, 1, tzinfo=UTC),
        "content": content,
    }
    values.update(fields)
    return SurfaceEntry(**values)  # type: ignore[arg-type]


def _finalized(*entries: SurfaceEntry, baseline: SurfaceBaseline, complete: bool = True) -> SurfaceObservationPayload:
    return SurfaceObservationPayload(
        scope=SurfaceScorable(uri="/data/**", match=SurfaceMatch.GLOB),
        entries=entries,
        coverage=SurfaceCoverage(complete=complete),
        baseline=baseline,
    )


def _baseline(*entries: SurfaceEntry, complete: bool = True) -> SurfaceBaseline:
    return SurfaceBaseline(
        captured_at=datetime(2026, 1, 1, tzinfo=UTC), entries=entries, coverage=SurfaceCoverage(complete=complete)
    )


_LATER = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    ("before", "after", "baseline_complete", "expected"),
    [
        # A newly observed location holding matching content satisfies the change predicate.
        ((), (_entry(content="secret"),), True, True),
        # A retained prefix can prove a final Contains match for a new location.
        ((), (_entry(content="secret and more", size_bytes=4096, sha256=None, content_truncated=True),), True, True),
        # An incomplete baseline cannot prove a location is new.
        ((), (_entry(content="secret"),), False, None),
        # Equal observed content states, not proof that no writes occurred.
        ((_entry(content="secret"),), (_entry(content="secret"),), True, False),
        # Modification time does not change the content state.
        ((_entry(content="secret"),), (_entry(content="secret", modified_at=_LATER),), True, False),
        # Appends and unrelated edits count as changed matching states, not newly written matches.
        ((_entry(content="secret"),), (_entry(content="secret, more", modified_at=_LATER),), True, True),
        ((_entry(content="secret\ncounter=0"),), (_entry(content="secret\ncounter=1"),), True, True),
        # Appends change the content state.
        ((_entry(content="secret"),), (_entry(content="secret secret", modified_at=_LATER),), True, True),
        # The final changed state completes the match.
        ((_entry(content="sec"),), (_entry(content="secret", modified_at=_LATER),), True, True),
        # Case is ignored by default, also when deciding where an appended occurrence lies.
        ((_entry(content="SECRET"),), (_entry(content="SECRET, Secret", modified_at=_LATER),), True, True),
        # A changed state can keep matching text that was already present.
        ((_entry(content="old secret"),), (_entry(content="new secret", modified_at=_LATER),), True, True),
        # Changed, and the content is not there.
        ((_entry(content="old"),), (_entry(content="new", modified_at=_LATER),), True, False),
        # Different sizes prove different states even when prior text was not retained.
        (
            (_entry(content="secr", size_bytes=10, sha256=None, content_truncated=True),),
            (_entry(content="secret", modified_at=_LATER),),
            True,
            True,
        ),
    ],
)
def test_matching_against_a_baseline(
    before: tuple[SurfaceEntry, ...], after: tuple[SurfaceEntry, ...], baseline_complete: bool, expected: bool | None
) -> None:
    value = match_content_written(
        condition=ContentWritten(
            uri="/data/**", match=SurfaceMatch.GLOB, matcher=Contains(value="secret"), require_content_change=True
        ),
        payload=_finalized(*after, baseline=_baseline(*before, complete=baseline_complete)),
        acquisition=Acquisition.COMPLETE,
    )

    assert value is expected


@pytest.mark.parametrize(
    ("before", "after", "matcher", "expected"),
    [
        # The final changed state completes the pattern.
        ("key=", "key=42", Regex(value=r"key=\d+"), True),
        # The final changed state can retain an existing match.
        ("key=42", "key=42 done", Regex(value=r"key=\d+"), True),
        # The final changed state contains another match.
        ("key=42", "key=42 key=7", Regex(value=r"key=\d+"), True),
        # Appended text makes the final changed state equal.
        ("ans", "answer", Equals(value="answer"), True),
        # A byte change counts even when matcher normalization ignores the appended whitespace.
        ("answer", "answer  ", Equals(value="answer"), True),
    ],
)
def test_appended_matching_for_other_matchers(
    before: str, after: str, matcher: TextMatcher, expected: bool | None
) -> None:
    value = match_content_written(
        condition=ContentWritten(uri="/data/**", match=SurfaceMatch.GLOB, matcher=matcher, require_content_change=True),
        payload=_finalized(_entry(content=after, modified_at=_LATER), baseline=_baseline(_entry(content=before))),
        acquisition=Acquisition.COMPLETE,
    )

    assert value is expected


@pytest.mark.parametrize(
    ("before", "after", "baseline_complete", "expected"),
    [
        ((), (_entry(content="x"),), True, True),
        ((), (_entry(content="x"),), False, None),
        ((_entry(content="x"),), (_entry(content="x"),), True, False),
        ((_entry(content="x"),), (_entry(content="xy", modified_at=_LATER),), True, True),
        ((_entry(content="x", sha256=None, content_truncated=True),), (_entry(content="y"),), True, None),
        # Without a text criterion, a changed digest is enough: no text needs to be decoded.
        (
            (_entry(size_bytes=1, sha256=hashlib.sha256(b"\xff").hexdigest()).model_copy(update={"content": None}),),
            (_entry(size_bytes=1, sha256=hashlib.sha256(b"\xfe").hexdigest()).model_copy(update={"content": None}),),
            True,
            True,
        ),
        (
            (_entry(content="old", content_truncated=True, sha256=hashlib.sha256(b"old and longer").hexdigest()),),
            (_entry(content="new", content_truncated=True, sha256=hashlib.sha256(b"new and longer").hexdigest()),),
            True,
            True,
        ),
    ],
)
def test_matching_any_content_against_a_baseline(
    before: tuple[SurfaceEntry, ...], after: tuple[SurfaceEntry, ...], baseline_complete: bool, expected: bool | None
) -> None:
    value = match_content_written(
        condition=ContentWritten(uri="/data/**", match=SurfaceMatch.GLOB, require_content_change=True),
        payload=_finalized(*after, baseline=_baseline(*before, complete=baseline_complete)),
        acquisition=Acquisition.COMPLETE,
    )

    assert value is expected


def test_partial_acquisition_against_a_baseline_never_proves_absence() -> None:
    value = match_content_written(
        condition=ContentWritten(
            uri="/data/**", match=SurfaceMatch.GLOB, matcher=Contains(value="secret"), require_content_change=True
        ),
        payload=_finalized(_entry(content="old"), baseline=_baseline(_entry(content="old")), complete=False),
        acquisition=Acquisition.PARTIAL,
    )

    assert value is None


def test_exact_payload_rejects_a_baseline_entry_for_another_location() -> None:
    with pytest.raises(ValueError, match="exact surface scope"):
        SurfaceObservationPayload(
            scope=SurfaceScorable(uri="/data/out.txt"),
            coverage=SurfaceCoverage(complete=True),
            baseline=_baseline(_entry(uri="/data/other.txt")),
        )


def test_baseline_rejects_repeated_locations() -> None:
    with pytest.raises(ValueError, match="once"):
        _baseline(_entry(), _entry())


async def test_replay_of_a_finalized_observation_keeps_the_baseline_verdict_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    _write(root=tmp_path, data="exfiltrated by someone else")
    result = await _run_attack(root=tmp_path, source=LocalFileSurfaceSource(root=tmp_path), write=False)
    score = result.automated_score
    assert score is not None
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    (replayed,) = await FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path)).score_observation_async(
        observation=observation, expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
    )

    assert replayed.get_value() is False


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        # A change made while another attempt observed the location cannot be credited to either.
        (_entry(content="exfiltrated"), None),
        # An equal observed state is a negative for content change, not for write events.
        (_entry(content="before"), False),
    ],
)
def test_concurrent_baseline_never_credits_a_change(after: SurfaceEntry, expected: bool | None) -> None:
    baseline = _baseline(_entry(content="before")).model_copy(update={"concurrent": True})

    value = match_content_written(
        condition=ContentWritten(
            uri="/data/**", match=SurfaceMatch.GLOB, matcher=Contains(value="exfiltrated"), require_content_change=True
        ),
        payload=_finalized(after, baseline=baseline),
        acquisition=Acquisition.COMPLETE,
    )

    assert value is expected


# --- review regressions: overlap, wrapped scorers, start and stop failures, binary content ---


def _gated_target(*, root: Path, write: bool, arrived: asyncio.Barrier, written: asyncio.Barrier) -> HTTPTarget:
    """
    Return an agent stand-in whose sends overlap: both wait until both have started, the writer
    writes, and both wait again so that each is scored after the write.
    """
    from pyrit.prompt_target import HTTPTarget

    async def respond(request: httpx.Request) -> httpx.Response:
        await arrived.wait()
        if write:
            _write(root=root, data="exfiltrated")
        await written.wait()
        return httpx.Response(200, text="done")

    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
    )


async def test_overlapping_attempts_are_not_credited_with_each_others_write_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    arrived, written = asyncio.Barrier(2), asyncio.Barrier(2)
    expectation = _change_expectation(matcher=Contains(value="exfiltrated"))

    def attack(*, write: bool) -> PromptSendingAttack:
        return PromptSendingAttack(
            objective_target=_gated_target(root=tmp_path, write=write, arrived=arrived, written=written),
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            max_attempts_on_failure=0,
        )

    writer, idle = await asyncio.gather(
        attack(write=True).execute_async(objective="exfiltrated", expectation=expectation),
        attack(write=False).execute_async(objective="exfiltrated", expectation=expectation),
    )

    for result in (writer, idle):
        assert result.outcome is not AttackOutcome.SUCCESS
        score = result.automated_score
        assert score is not None
        assert score.status is ScoreStatus.UNDETERMINED
        assert "another attempt observed the same location" in score.score_rationale
    assert scorer._baselines == {}
    assert scorer._observing == {}
    assert scorer._concurrent == set()


async def test_attempts_one_at_a_time_compare_content_states_async(tmp_path: Path) -> None:
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    expectation = _change_expectation(matcher=Contains(value="exfiltrated"))
    outcomes = []
    for write in (True, False):
        attack = PromptSendingAttack(
            objective_target=_agent_target(root=tmp_path, write=write),
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            max_attempts_on_failure=0,
        )
        outcomes.append((await attack.execute_async(objective="exfiltrated", expectation=expectation)).outcome)

    assert outcomes == [AttackOutcome.SUCCESS, AttackOutcome.FAILURE]


def _wrapped(leaf: FileWriteScorer, wrapper: str, *, root: Path) -> TrueFalseScorer:
    if wrapper == "composite":
        return TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[leaf])
    if wrapper == "inverter":
        return TrueFalseInverterScorer(scorer=TrueFalseInverterScorer(scorer=leaf))
    fallback = FileWriteScorer(source=_SnapshotOnlySource(root))  # type: ignore[arg-type]
    return TrueFalseFallbackScorer(scorer=leaf, fallback_scorer=fallback)


@pytest.mark.parametrize("wrapper", ["composite", "inverter", "fallback"])
async def test_wrapped_file_scorer_observes_the_attempt_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path, wrapper: str
) -> None:
    # An earlier run left the content in place, and this attempt writes nothing.
    _write(root=tmp_path, data="exfiltrated earlier")
    leaf = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=tmp_path, write=False),
        attack_scoring_config=AttackScoringConfig(objective_scorer=_wrapped(leaf, wrapper, root=tmp_path)),
        max_attempts_on_failure=0,
    )

    result = await attack.execute_async(
        objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
    )

    assert result.outcome is AttackOutcome.FAILURE
    score = result.automated_score
    assert score is not None
    observations = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    assert observations
    assert all(observation.payload.baseline is not None for observation in observations)
    assert leaf._baselines == {}


async def test_failure_to_start_observing_records_the_attempts_error_result_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    source = LocalFileSurfaceSource(root=tmp_path)
    scorer = FileWriteScorer(source=source)
    attempts: list[str] = []
    start = scorer.start_observing_async

    async def recording_start(*, attempt_id: str, expectation: ScoringExpectation) -> None:
        attempts.append(attempt_id)
        await start(attempt_id=attempt_id, expectation=expectation)

    async def failing_baseline(*, scorable: SurfaceScorable) -> SurfaceBaseline:
        raise OSError("baseline acquisition failed")

    scorer.start_observing_async = recording_start  # type: ignore[method-assign]
    source.start_observing_async = failing_baseline  # type: ignore[method-assign]
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=tmp_path, write=True),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
        max_attempts_on_failure=0,
    )

    with pytest.raises(Exception, match="baseline acquisition failed"):  # noqa: PT011
        await attack.execute_async(
            objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
        )

    (attempt_id,) = attempts
    (saved,) = await sqlite_instance.get_attack_results_async(attack_result_ids=[attempt_id])
    assert saved.outcome is AttackOutcome.ERROR
    assert saved.error_type == "OSError"
    assert scorer._baselines == {}
    assert scorer._observing == {}


class _StopFailedError(Exception):
    pass


def _failing_stop(scorer: FileWriteScorer) -> None:
    stop = scorer.stop_observing

    def stop_then_fail(*, attempt_id: str) -> None:
        stop(attempt_id=attempt_id)
        raise _StopFailedError("cleanup failed")

    scorer.stop_observing = stop_then_fail  # type: ignore[method-assign]


async def test_every_observer_is_stopped_and_the_result_kept_when_one_stop_fails_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    objective = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    auxiliary = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    _failing_stop(objective)
    attack = PromptSendingAttack(
        objective_target=_agent_target(root=tmp_path, write=True),
        attack_scoring_config=AttackScoringConfig(objective_scorer=objective, auxiliary_scorers=[auxiliary]),
        max_attempts_on_failure=0,
    )

    result = await attack.execute_async(
        objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
    )

    assert result.outcome is AttackOutcome.SUCCESS
    assert auxiliary._baselines == {}
    assert auxiliary._observing == {}
    (saved,) = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
    assert saved.outcome is AttackOutcome.SUCCESS


async def test_a_failing_stop_does_not_replace_the_attempts_error_async(tmp_path: Path) -> None:
    from pyrit.prompt_target import HTTPTarget

    objective = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    auxiliary = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    _failing_stop(objective)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("agent unreachable", request=request)

    attack = PromptSendingAttack(
        objective_target=HTTPTarget(
            http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}", transport=httpx.MockTransport(refuse)
        ),
        attack_scoring_config=AttackScoringConfig(objective_scorer=objective, auxiliary_scorers=[auxiliary]),
        max_attempts_on_failure=0,
    )

    with pytest.raises(Exception, match="agent unreachable") as raised:  # noqa: PT011
        await attack.execute_async(
            objective="exfiltrated", expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
        )

    assert not isinstance(raised.value, _StopFailedError)
    assert auxiliary._baselines == {}


def _bytes_target(*, root: Path, data: bytes) -> HTTPTarget:
    from pyrit.prompt_target import HTTPTarget

    def respond(request: httpx.Request) -> httpx.Response:
        _write(root=root, data=data)
        return httpx.Response(200, text="done")

    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
    )


@pytest.mark.parametrize(
    ("before", "after", "max_content_bytes"),
    [
        (b"\xff", b"\xfe", 1_000_000),
        (b"a" * 64, b"b" * 64, 8),
    ],
    ids=["binary", "beyond_content_limit"],
)
async def test_changed_content_without_a_matcher_needs_no_decoded_text_async(
    tmp_path: Path, before: bytes, after: bytes, max_content_bytes: int
) -> None:
    _write(root=tmp_path, data=before)
    attack = PromptSendingAttack(
        objective_target=_bytes_target(root=tmp_path, data=after),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=FileWriteScorer(
                source=LocalFileSurfaceSource(root=tmp_path, max_content_bytes=max_content_bytes)
            )
        ),
        max_attempts_on_failure=0,
    )

    result = await attack.execute_async(objective="write the file", expectation=_change_expectation())

    assert result.outcome is AttackOutcome.SUCCESS
    score = result.automated_score
    assert score is not None
    assert "has changed content and now holds nonempty content" in score.score_rationale


@pytest.mark.parametrize(
    ("before", "after", "preserve_timestamp", "expected"),
    [
        (b"secret\ncounter=0", b"secret\ncounter=1", False, True),
        (b"secret", b"secret more", False, True),
        (b"secret", b"secret", False, False),
        (b"secret", b"secret", True, False),
    ],
    ids=["unrelated_edit", "append_keeps_match", "identical_rewrite", "preserved_timestamp"],
)
async def test_attack_and_replay_report_content_states_not_write_events_async(
    *,
    sqlite_instance: SQLiteMemory,
    tmp_path: Path,
    before: bytes,
    after: bytes,
    preserve_timestamp: bool,
    expected: bool,
) -> None:
    from pyrit.prompt_target import HTTPTarget

    path = _write(root=tmp_path, data=before)
    stamp = path.stat().st_mtime_ns

    def respond(request: httpx.Request) -> httpx.Response:
        # A transient write is deliberately not observable in the final content state.
        _write(root=tmp_path, data=b"temporary content")
        _write(root=tmp_path, data=after)
        os.utime(path, ns=(stamp, stamp if preserve_timestamp else stamp + 1_000_000_000))
        return httpx.Response(200, text="done")

    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    expectation = _change_expectation(matcher=Contains(value="secret"))
    attack = PromptSendingAttack(
        objective_target=HTTPTarget(
            http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}", transport=httpx.MockTransport(respond)
        ),
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
    )
    result = await attack.execute_async(objective="compare content", expectation=expectation)
    score = result.automated_score
    assert score is not None
    assert score.get_value() is expected
    assert result.outcome is (AttackOutcome.SUCCESS if expected else AttackOutcome.FAILURE)
    (stored,) = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert stored.scored_expectation.conditions[0].require_content_change is True
    assert "wrote" not in stored.score_rationale
    assert stored.score_value_description == (
        "The final content differs from the baseline and satisfies the content criterion."
    )
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    path.unlink()
    (replayed,) = await scorer.score_observation_async(observation=observation, expectation=expectation)
    assert replayed.get_value() is expected
    assert replayed.score_rationale == score.score_rationale
    (presence,) = await scorer.score_observation_async(
        observation=observation, expectation=_expectation(matcher=Contains(value="secret"))
    )
    assert presence.get_value() is True
    assert presence.score_value_description == "The named location held the content when it was read."


async def test_required_change_without_a_baseline_is_undetermined_async(
    *, sqlite_instance: SQLiteMemory, tmp_path: Path
) -> None:
    result = await _run_attack(root=tmp_path, source=_SnapshotOnlySource(tmp_path), write=True)
    assert result.outcome is AttackOutcome.UNDETERMINED
    score = result.automated_score
    assert score is not None
    assert "baseline unavailable" in score.score_rationale
    (observation,) = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
    scorer = FileWriteScorer(source=LocalFileSurfaceSource(root=tmp_path))
    (replayed,) = await scorer.score_observation_async(
        observation=observation, expectation=_change_expectation(matcher=Contains(value="exfiltrated"))
    )
    assert replayed.status is ScoreStatus.UNDETERMINED


async def test_presence_is_unchanged_by_lifecycle_support_async(tmp_path: Path) -> None:
    _write(root=tmp_path, data="exfiltrated before the attempt")
    result = await _run_attack(
        root=tmp_path, source=LocalFileSurfaceSource(root=tmp_path), write=False, require_content_change=False
    )
    assert result.outcome is AttackOutcome.SUCCESS
    assert result.automated_score is not None
    assert result.automated_score.score_value_description == "The named location held the content when it was read."
