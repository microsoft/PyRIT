# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.dialects import mssql
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.memory.memory_models import ObservationEntry, ObservationMessagePieceEntry, ScoreEntry, ScoreObservationEntry
from pyrit.memory.memory_session import MemorySession, _begin_sqlite_write, _lock_observations
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    MessagePiece,
    MessageScorable,
    Observation,
    Score,
    ScorerTargetResponsePayload,
    ScoringExpectation,
    TraceScorable,
    scoring_expectation_fingerprint,
)
from pyrit.models.score.observation import _message_piece_digest, _response_piece_digest


def _identifier() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.memory")


async def _observation_async(
    *,
    memory: MemoryInterface,
    scorable: MessageScorable,
    response_piece_id: uuid.UUID,
    expectation: ScoringExpectation,
) -> Observation:
    scored_piece = (await memory.get_message_pieces_async(prompt_ids=[scorable.message_piece_ids[0]]))[0]
    response_piece = (await memory.get_message_pieces_async(prompt_ids=[response_piece_id]))[0]
    return Observation(
        source_identifier=_identifier(),
        acquisition=Acquisition.COMPLETE,
        scorable=scorable,
        payload=ScorerTargetResponsePayload(
            scored_piece_id=scorable.message_piece_ids[0],
            message_piece_ids=(response_piece_id,),
            message_piece_digests=(_response_piece_digest(response_piece, include_id=True),),
            scored_evidence_digest=_message_piece_digest(
                scored_piece,
                include_id=False,
            ),
            expectation_fingerprint=scoring_expectation_fingerprint(expectation),
        ),
    )


async def test_observations_and_score_links_round_trip_in_order(sqlite_instance: MemoryInterface):
    pieces = [
        MessagePiece(role="assistant", original_value="first", conversation_id=str(uuid.uuid4())),
        MessagePiece(role="assistant", original_value="second", conversation_id=str(uuid.uuid4())),
    ]
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    scorable = MessageScorable(message_piece_ids=(pieces[0].id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observations = [
        (
            await _observation_async(
                memory=sqlite_instance,
                scorable=scorable,
                response_piece_id=piece.id,
                expectation=expectation,
            )
        )
        for piece in pieces
    ]
    score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        scored_expectation=expectation,
        observation_ids=[observations[1].id, observations[0].id],
    )

    (await sqlite_instance.add_scores_to_memory_async(scores=[score], observations=observations))

    stored_score = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
    stored_observations = await sqlite_instance.get_observations_async(observation_ids=stored_score.observation_ids)
    assert stored_score.observation_ids == [observations[1].id, observations[0].id]
    assert [observation.id for observation in stored_observations] == stored_score.observation_ids
    assert stored_score.scored_expectation == expectation
    entry = sqlite_instance._query_entries(ObservationEntry)[0]
    assert "score_links" in inspect(entry).unloaded
    assert entry.get_observation().id in {observation.id for observation in observations}


async def test_existing_observation_can_support_another_score(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece]))
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = await _observation_async(
        memory=sqlite_instance,
        scorable=scorable,
        response_piece_id=piece.id,
        expectation=expectation,
    )
    first_score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        scored_expectation=expectation,
        observation_ids=[observation.id],
    )
    (await sqlite_instance.add_scores_to_memory_async(scores=[first_score], observations=[observation]))
    replay_score = first_score.model_copy(
        update={
            "id": uuid.uuid4(),
            "observation_ids": [observation.id],
        }
    )

    (await sqlite_instance.add_scores_to_memory_async(scores=[replay_score]))

    assert (await sqlite_instance.get_scores_async(score_ids=[replay_score.id]))[0].observation_ids == [observation.id]


async def test_unreferenced_observation_is_not_persisted(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece]))
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = await _observation_async(
        memory=sqlite_instance,
        scorable=scorable,
        response_piece_id=piece.id,
        expectation=expectation,
    )
    score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        scored_expectation=expectation,
    )

    with pytest.raises(ValueError, match="not referenced"):
        (await sqlite_instance.add_scores_to_memory_async(scores=[score], observations=[observation]))

    assert sqlite_instance._query_entries(ObservationEntry) == []
    assert sqlite_instance._query_entries(ScoreEntry) == []


async def test_missing_observation_link_leaves_no_score(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece]))
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        observation_ids=[uuid.uuid4()],
    )

    with pytest.raises(ValueError, match="not found in memory"):
        (await sqlite_instance.add_scores_to_memory_async(scores=[score]))

    assert sqlite_instance._query_entries(ScoreEntry) == []


async def test_duplicate_message_anchor_preserves_exact_observation_evidence(
    sqlite_instance: MemoryInterface,
):
    original = MessagePiece(
        role="assistant",
        original_value="input",
        conversation_id=str(uuid.uuid4()),
        sequence=0,
    )
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[original]))
    (await sqlite_instance.duplicate_conversation_async(conversation_id=original.conversation_id))
    duplicate = next(piece for piece in (await sqlite_instance.get_message_pieces_async()) if piece.id != original.id)
    response = MessagePiece(
        role="assistant",
        original_value="judgment",
        conversation_id=str(uuid.uuid4()),
    )
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[response]))
    scorable = MessageScorable(message_piece_ids=(duplicate.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = await _observation_async(
        memory=sqlite_instance,
        scorable=scorable,
        response_piece_id=response.id,
        expectation=expectation,
    )
    score = Score(
        score_value="true",
        score_type="true_false",
        message_piece_id=duplicate.id,
        scorable=scorable,
        observation_ids=[observation.id],
    )

    (await sqlite_instance.add_scores_to_memory_async(scores=[score], observations=[observation]))

    stored_score = (await sqlite_instance.get_scores_async(score_ids=[score.id]))[0]
    stored_observation = (await sqlite_instance.get_observations_async(observation_ids=[observation.id]))[0]
    assert stored_score.scorable == MessageScorable(message_piece_ids=(original.id,))
    assert stored_observation.scorable == MessageScorable(message_piece_ids=(duplicate.id,))
    assert stored_observation.payload.scored_piece_id == duplicate.id


async def test_sqlite_protects_observation_message_references(
    sqlite_instance: MemoryInterface,
):
    scored_piece = MessagePiece(
        role="assistant",
        original_value="input",
        conversation_id=str(uuid.uuid4()),
        sequence=0,
    )
    response_piece = MessagePiece(
        role="assistant",
        original_value="judgment",
        conversation_id=str(uuid.uuid4()),
        sequence=0,
    )
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[scored_piece, response_piece]))
    scorable = MessageScorable(message_piece_ids=(scored_piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = await _observation_async(
        memory=sqlite_instance,
        scorable=scorable,
        response_piece_id=response_piece.id,
        expectation=expectation,
    )
    score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        observation_ids=[observation.id],
    )
    (
        await sqlite_instance.add_scores_to_memory_async(
            scores=[score],
            observations=[observation],
        )
    )

    with pytest.raises(SQLAlchemyError):
        (
            await sqlite_instance.delete_conversation_pieces_after_sequence_async(
                conversation_id=response_piece.conversation_id,
                sequence=-1,
            )
        )
    with pytest.raises(SQLAlchemyError):
        (
            await sqlite_instance.delete_conversation_pieces_after_sequence_async(
                conversation_id=scored_piece.conversation_id,
                sequence=-1,
            )
        )

    assert await sqlite_instance.get_message_pieces_async(prompt_ids=[response_piece.id])


@pytest.fixture
async def file_memory(tmp_path: Path) -> AsyncIterator[SQLiteMemory]:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    memory.__init__(db_path=tmp_path / "observations.db", silent=True)
    try:
        yield memory
    finally:
        await memory.dispose_engine_async()


async def _score_and_observation_async(memory: MemoryInterface) -> tuple[Score, Observation, MessagePiece]:
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()), sequence=0)
    (await memory.add_message_pieces_to_memory_async(message_pieces=[piece]))
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = await _observation_async(
        memory=memory, scorable=scorable, response_piece_id=piece.id, expectation=expectation
    )
    return (
        Score(
            score_value="true",
            score_type="true_false",
            scorable=scorable,
            observation_ids=[observation.id],
            scored_expectation=expectation,
        ),
        observation,
        piece,
    )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
@pytest.mark.parametrize("boundary", ["_resolve_score_message_anchors", "_persist_score_rows"])
async def test_file_sqlite_locks_evidence_before_reads_until_commit(
    *, file_memory: SQLiteMemory, operation: str, boundary: str
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    statement = (
        'UPDATE "PromptMemoryEntries" SET converted_value = :value WHERE id = :id'
        if operation == "UPDATE"
        else 'DELETE FROM "PromptMemoryEntries" WHERE id = :id'
    )
    original = getattr(file_memory, boundary)
    checked = []

    def _attempt_concurrent_write(*, session: Session, **kwargs: Any) -> Any:
        with file_memory.engine.connect() as writer:
            assert writer.connection.driver_connection is not session.connection().connection.driver_connection
            writer.exec_driver_sql("PRAGMA busy_timeout=1")
            with pytest.raises(OperationalError, match="database is locked"):
                writer.execute(text(statement), {"id": str(piece.id), "value": "tampered"})
        checked.append(True)
        return original(session=session, **kwargs)

    with patch.object(file_memory, boundary, side_effect=_attempt_concurrent_write):
        (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))

    assert checked == [True]
    with file_memory.engine.connect() as writer:
        with pytest.raises(SQLAlchemyError, match="immutable observation evidence"):
            writer.execute(text(statement), {"id": str(piece.id), "value": "tampered"})
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == [observation]


@pytest.mark.parametrize("fail_commit", [False, True])
async def test_failed_score_write_preserves_duplicate_anchor(
    *, file_memory: SQLiteMemory, fail_commit: bool, caplog: pytest.LogCaptureFixture
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    (await file_memory.duplicate_conversation_async(conversation_id=piece.conversation_id))
    duplicate = next(value for value in (await file_memory.get_message_pieces_async()) if value.id != piece.id)
    score.message_piece_id = duplicate.id
    score.scorable = MessageScorable(message_piece_ids=(duplicate.id,))
    before = score.model_dump()
    with file_memory.get_session() as session:
        if fail_commit:
            failure = patch.object(session, "commit", side_effect=SQLAlchemyError("commit failed"))
        else:
            failure = patch.object(file_memory, "_validate_observation_evidence", side_effect=ValueError("invalid"))
        with patch.object(file_memory, "_get_session", return_value=session), failure:
            with pytest.raises((SQLAlchemyError, ValueError), match="commit failed|invalid"):
                (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    assert score.model_dump() == before
    assert file_memory._query_entries(ScoreEntry) == []
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []
    if fail_commit:
        assert any(record.message == "Error inserting scores" and record.exc_info for record in caplog.records)


@pytest.mark.parametrize("foreign_keys", [False, True])
@pytest.mark.parametrize("clear_links", [False, True])
async def test_orm_score_delete_cleans_last_observation_and_releases_prompt(
    *, file_memory: SQLiteMemory, foreign_keys: bool, clear_links: bool
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    with file_memory.get_session() as session:
        if foreign_keys:
            session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
            assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        if clear_links:
            entry.observation_links.clear()
        session.delete(entry)
        session.commit()
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []
    assert file_memory._query_entries(ScoreObservationEntry) == []
    (
        await file_memory.delete_conversation_pieces_after_sequence_async(
            conversation_id=piece.conversation_id, sequence=-1
        )
    )
    assert (await file_memory.get_message_pieces_async(prompt_ids=[piece.id])) == []


@pytest.mark.parametrize("clear_links", [False, True])
async def test_orm_score_delete_rollback_restores_observation_protection(
    *, file_memory: SQLiteMemory, clear_links: bool
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    with file_memory.get_session() as session:
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        if clear_links:
            entry.observation_links.clear()
        session.delete(entry)
        session.flush()
        assert session.get(ObservationEntry, observation.id) is None
        session.rollback()
    assert (await file_memory.get_scores_async(score_ids=[score.id]))[0].observation_ids == [observation.id]
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == [observation]
    assert len(file_memory._query_entries(ObservationMessagePieceEntry)) == 1
    with pytest.raises(SQLAlchemyError, match="immutable observation evidence"):
        (
            await file_memory.delete_conversation_pieces_after_sequence_async(
                conversation_id=piece.conversation_id, sequence=-1
            )
        )


@pytest.mark.parametrize("delete_together", [False, True])
@pytest.mark.parametrize("clear_links", [False, True])
async def test_orm_shared_observation_survives_until_final_score(
    *, file_memory: SQLiteMemory, delete_together: bool, clear_links: bool
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    (await file_memory.add_scores_to_memory_async(scores=[score, replay], observations=[observation]))
    with file_memory.get_session() as session:
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        if clear_links:
            entry.observation_links.clear()
        session.delete(entry)
        if delete_together:
            session.delete(session.get(ScoreEntry, replay.id))
        session.commit()
    if not delete_together:
        assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == [observation]
        with pytest.raises(SQLAlchemyError):
            (
                await file_memory.delete_conversation_pieces_after_sequence_async(
                    conversation_id=piece.conversation_id, sequence=-1
                )
            )
        with file_memory.get_session() as session:
            session.delete(session.get(ScoreEntry, replay.id))
            session.commit()
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []


@pytest.mark.parametrize("remove_directly", [False, True])
async def test_orm_link_removal_cleans_observation_before_score_deletion(
    *, file_memory: SQLiteMemory, remove_directly: bool
) -> None:
    score, observation, piece = await _score_and_observation_async(file_memory)
    (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    with file_memory.get_session() as session:
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        if remove_directly:
            session.delete(entry.observation_links[0])
        else:
            entry.observation_links.clear()
        session.flush()
        assert session.get(ObservationEntry, observation.id) is None
        session.expire(entry, ["observation_links"])
        session.delete(entry)
        session.commit()
    assert file_memory._query_entries(ScoreObservationEntry) == []
    (
        await file_memory.delete_conversation_pieces_after_sequence_async(
            conversation_id=piece.conversation_id, sequence=-1
        )
    )
    assert (await file_memory.get_message_pieces_async(prompt_ids=[piece.id])) == []


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.parametrize("foreign_keys", [False, True])
async def test_orm_score_delete_finds_links_missing_from_cached_collection(
    *, file_memory: SQLiteMemory, shared: bool, rollback: bool, foreign_keys: bool
) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    other_score, other_observation, piece = await _score_and_observation_async(file_memory)
    (
        await file_memory.add_scores_to_memory_async(
            scores=[score, other_score], observations=[observation, other_observation]
        )
    )
    with file_memory.get_session() as session:
        if foreign_keys:
            session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        assert [link.observation_id for link in entry.observation_links] == [observation.id]
        with file_memory.get_session() as writer:
            writer.add(ScoreObservationEntry(score_id=score.id, position=1, observation_id=other_observation.id))
            if not shared:
                writer.delete(writer.get(ScoreEntry, other_score.id))
            writer.commit()
        assert [link.observation_id for link in entry.observation_links] == [observation.id]
        session.delete(entry)
        session.flush()
        if rollback:
            session.rollback()
        else:
            session.commit()
    retained = shared or rollback
    assert (await file_memory.get_observations_async(observation_ids=[other_observation.id])) == (
        [other_observation] if retained else []
    )
    if rollback:
        assert (await file_memory.get_scores_async(score_ids=[score.id]))[0].observation_ids == [
            observation.id,
            other_observation.id,
        ]
    else:
        assert all(link.score_id != score.id for link in file_memory._query_entries(ScoreObservationEntry))
    if retained:
        with pytest.raises(SQLAlchemyError, match="immutable observation evidence"):
            (
                await file_memory.delete_conversation_pieces_after_sequence_async(
                    conversation_id=piece.conversation_id, sequence=-1
                )
            )
    else:
        (
            await file_memory.delete_conversation_pieces_after_sequence_async(
                conversation_id=piece.conversation_id, sequence=-1
            )
        )
        assert (await file_memory.get_message_pieces_async(prompt_ids=[piece.id])) == []


async def test_orm_clearing_stale_links_cleans_the_persisted_observation(file_memory: SQLiteMemory) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    shared_score = score.model_copy(update={"id": uuid.uuid4()})
    other_score, other_observation, piece = await _score_and_observation_async(file_memory)
    (
        await file_memory.add_scores_to_memory_async(
            scores=[score, shared_score, other_score], observations=[observation, other_observation]
        )
    )
    with file_memory.get_session() as session:
        entry = session.get(ScoreEntry, score.id)
        assert entry is not None
        assert entry.observation_links[0].observation_id == observation.id
        with file_memory.get_session() as writer:
            updated = writer.get(ScoreEntry, score.id)
            assert updated is not None
            updated.observation_links[0].observation_id = other_observation.id
            writer.delete(writer.get(ScoreEntry, other_score.id))
            writer.commit()
        entry.observation_links.clear()
        session.flush()
        assert session.get(ObservationEntry, other_observation.id) is None
        session.delete(entry)
        session.commit()
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == [observation]
    (
        await file_memory.delete_conversation_pieces_after_sequence_async(
            conversation_id=piece.conversation_id, sequence=-1
        )
    )
    assert (await file_memory.get_message_pieces_async(prompt_ids=[piece.id])) == []


async def test_orm_moving_link_before_score_deletion_preserves_observation(file_memory: SQLiteMemory) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    other_score = score.model_copy(update={"id": uuid.uuid4(), "observation_ids": []})
    (await file_memory.add_scores_to_memory_async(scores=[score, other_score], observations=[observation]))
    with file_memory.get_session() as session:
        entry = session.get(ScoreEntry, score.id)
        other = session.get(ScoreEntry, other_score.id)
        assert entry is not None and other is not None
        link = entry.observation_links.pop()
        other.observation_links.append(link)
        session.delete(entry)
        session.commit()
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == [observation]
    assert (await file_memory.get_scores_async(score_ids=[other_score.id]))[0].observation_ids == [observation.id]
    with file_memory.get_session() as session:
        session.delete(session.get(ScoreEntry, other_score.id))
        session.commit()
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []


async def test_concurrent_orm_score_deletions_clean_final_observation(file_memory: SQLiteMemory) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    (await file_memory.add_scores_to_memory_async(scores=[score, replay], observations=[observation]))
    ready = asyncio.Barrier(2)

    async def delete_async(score_id: uuid.UUID) -> None:
        async with await file_memory.get_session_async() as session:
            entry = await session.get(ScoreEntry, score_id)
            await asyncio.wait_for(ready.wait(), timeout=5)
            await session.delete(entry)
            await session.commit()

    await asyncio.wait_for(asyncio.gather(*(delete_async(value.id) for value in (score, replay))), timeout=10)
    assert file_memory._query_entries(ScoreEntry) == []
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []


@pytest.mark.parametrize("insert_first", [False, True])
@pytest.mark.parametrize("insert_via_orm", [False, True])
async def test_concurrent_insert_and_final_delete_do_not_orphan_score(
    *, file_memory: SQLiteMemory, insert_first: bool, insert_via_orm: bool
) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    competing_write = asyncio.Event()

    def _before_write(*, statement: str, **kwargs: Any) -> None:
        if statement == "BEGIN IMMEDIATE":
            competing_write.set()

    async def compete_async() -> None:
        if insert_first:
            async with await file_memory.get_session_async() as session:
                await session.delete(await session.get(ScoreEntry, score.id))
                await session.commit()
        else:
            with pytest.raises(ValueError, match="not found in memory"):
                if insert_via_orm:
                    async with await file_memory.get_session_async() as session:
                        session.add(ScoreEntry(entry=replay))
                        session.add(
                            ScoreObservationEntry(score_id=replay.id, position=0, observation_id=observation.id)
                        )
                        await session.commit()
                else:
                    (await file_memory.add_scores_to_memory_async(scores=[replay]))

    engine = file_memory._get_async_engine().sync_engine
    async with await file_memory.get_session_async() as session:
        if insert_first:
            session.add(ScoreEntry(entry=replay))
            session.add(ScoreObservationEntry(score_id=replay.id, position=0, observation_id=observation.id))
        else:
            await session.delete(await session.get(ScoreEntry, score.id))
        await session.flush()
        event.listen(engine, "before_cursor_execute", _before_write, named=True)
        task = asyncio.create_task(compete_async())
        try:
            await asyncio.wait_for(competing_write.wait(), timeout=5)
            assert not task.done()
            await session.commit()
            await asyncio.wait_for(task, timeout=10)
        finally:
            await session.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            event.remove(engine, "before_cursor_execute", _before_write)
    expected = [observation] if insert_first else []
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == expected
    assert len(file_memory._query_entries(ScoreEntry)) == int(insert_first)


@pytest.mark.parametrize("dialect", ["sqlite", "mssql"])
def test_removed_score_link_lookup_batches_and_uses_persisted_ids(dialect: str) -> None:
    session = MagicMock(spec=MemorySession)
    session._MAX_BIND_VARS = 1
    session.get_bind.return_value.dialect.name = dialect
    score_ids = [uuid.uuid4() for _ in range(2)]
    persisted_ids = [uuid.uuid4() for _ in range(2)]
    session.scalars.side_effect = [[observation_id] for observation_id in persisted_ids]

    candidates = MemorySession._get_persisted_score_observation_ids(session, score_ids)

    assert candidates == set(persisted_ids)
    assert session.scalars.call_count == 2
    for call, score_id in zip(session.scalars.call_args_list, score_ids, strict=True):
        compiled = call.args[0].compile(dialect=mssql.dialect(), compile_kwargs={"render_postcompile": True})
        assert list(compiled.params.values()) == [score_id]
        assert ("WITH (UPDLOCK, HOLDLOCK)" in str(compiled)) == (dialect == "mssql")


def test_sql_server_observation_reference_changes_use_exclusive_range_locks() -> None:
    session = MagicMock(spec=Session)
    session.get_bind.return_value.dialect.name = "mssql"
    observation_id = uuid.uuid4()
    session.scalars.return_value = [observation_id]

    assert _lock_observations(session=session, observation_ids=[observation_id]) == {str(observation_id)}

    statement = session.scalars.call_args.args[0]
    compiled = str(statement.compile(dialect=mssql.dialect()))
    assert "WITH (XLOCK, HOLDLOCK)" in compiled


def test_sqlite_observation_lookups_are_batched() -> None:
    session = MagicMock(spec=Session)
    session.get_bind.return_value.dialect.name = "sqlite"
    observation_ids = sorted({str(uuid.uuid4()) for _ in range(1001)})
    session.scalars.side_effect = [observation_ids[:500], observation_ids[500:1000], observation_ids[1000:]]

    assert _lock_observations(session=session, observation_ids=observation_ids * 2) == set(observation_ids)
    assert session.scalars.call_count == 3
    for call in session.scalars.call_args_list:
        assert len(call.args[0].compile().params["id_1"]) <= 500


def test_sqlite_write_requires_a_real_driver_connection() -> None:
    session = MagicMock(spec=Session)
    session.get_bind.return_value.dialect.name = "sqlite"
    session.connection.return_value.connection.driver_connection = None

    with pytest.raises(TypeError, match="requires a sqlite3 or aiosqlite connection"):
        _begin_sqlite_write(session)

    session.connection.return_value.exec_driver_sql.assert_not_called()


@pytest.mark.parametrize("direct_orm", [False, True])
@pytest.mark.parametrize(
    ("scorable", "error"),
    [
        (ContentScorable(value="image.png", data_type="image_path"), "Media scorer target response"),
        (TraceScorable(trace_ids=("1" * 32,)), "message or content evidence"),
    ],
)
async def test_unchecked_observation_rejected(
    *, file_memory: SQLiteMemory, direct_orm: bool, scorable: object, error: str
) -> None:
    score, observation, _ = await _score_and_observation_async(file_memory)
    observation = observation.model_copy(update={"scorable": scorable})
    with pytest.raises(ValueError, match=error):
        if direct_orm:
            ObservationEntry(entry=observation)
        else:
            (await file_memory.add_scores_to_memory_async(scores=[score], observations=[observation]))
    assert (await file_memory.get_scores_async(score_ids=[score.id])) == []
    assert (await file_memory.get_observations_async(observation_ids=[observation.id])) == []
