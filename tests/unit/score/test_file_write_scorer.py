# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import (
    Acquisition,
    AttackOutcome,
    ComponentIdentifier,
    Contains,
    ContentScorable,
    ContentWritten,
    Equals,
    MessagePiece,
    MessageScorable,
    Regex,
    ScoreStatus,
    ScoringExpectation,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceMatch,
    SurfaceObservationPayload,
    SurfaceScorable,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.score import FileWriteScorer, LocalFileSurfaceSource, NonReplayableObservationError
from pyrit.score.true_false.file_write_scorer import match_content_written

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.memory import SQLiteMemory
    from pyrit.models import TextMatcher
    from pyrit.prompt_target import HTTPTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _expectation(
    *, uri: str = "/data/out.txt", match: SurfaceMatch = SurfaceMatch.EXACT, matcher: TextMatcher | None = None
) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ContentWritten(uri=uri, match=match, matcher=matcher),))


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
    assert identifier.params["matching_version"] == 2
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


def _agent_target(*, root: Path, write: bool) -> HTTPTarget:
    from pyrit.prompt_target import HTTPTarget

    def respond(request: httpx.Request) -> httpx.Response:
        if write:
            _write(root=root, data=request.content.decode())
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
