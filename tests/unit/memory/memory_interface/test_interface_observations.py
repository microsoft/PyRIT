# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.dialects import mssql
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.memory.memory_models import ObservationEntry, ObservationMessagePieceEntry, ScoreEntry, ScoreObservationEntry
from pyrit.memory.memory_session import _begin_sqlite_write, _lock_observations
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    JudgmentObservationPayload,
    MessagePiece,
    MessageScorable,
    Observation,
    Score,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)
from pyrit.models.score.observation import _message_piece_digest, _response_piece_digest


def _identifier() -> ComponentIdentifier:
    return ComponentIdentifier(class_name="TestScorer", class_module="tests.unit.memory")


def _observation(
    *,
    memory: MemoryInterface,
    scorable: MessageScorable,
    response_piece_id: uuid.UUID,
    expectation: ScoringExpectation,
) -> Observation:
    scored_piece = memory.get_message_pieces(prompt_ids=[scorable.message_piece_ids[0]])[0]
    response_piece = memory.get_message_pieces(prompt_ids=[response_piece_id])[0]
    return Observation(
        source_identifier=_identifier(),
        acquisition=Acquisition.COMPLETE,
        scorable=scorable,
        payload=JudgmentObservationPayload(
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


def test_observations_and_score_links_round_trip_in_order(sqlite_instance: MemoryInterface):
    pieces = [
        MessagePiece(role="assistant", original_value="first", conversation_id=str(uuid.uuid4())),
        MessagePiece(role="assistant", original_value="second", conversation_id=str(uuid.uuid4())),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    scorable = MessageScorable(message_piece_ids=(pieces[0].id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observations = [
        _observation(
            memory=sqlite_instance,
            scorable=scorable,
            response_piece_id=piece.id,
            expectation=expectation,
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

    sqlite_instance.add_scores_to_memory(scores=[score], observations=observations)

    stored_score = sqlite_instance.get_scores(score_ids=[score.id])[0]
    stored_observations = sqlite_instance.get_observations(observation_ids=stored_score.observation_ids)
    assert stored_score.observation_ids == [observations[1].id, observations[0].id]
    assert [observation.id for observation in stored_observations] == stored_score.observation_ids
    assert stored_score.scored_expectation == expectation
    entry = sqlite_instance._query_entries(ObservationEntry)[0]
    assert "score_links" in inspect(entry).unloaded
    assert entry.get_observation().id in {observation.id for observation in observations}


def test_existing_observation_can_support_another_score(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[piece])
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = _observation(
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
    sqlite_instance.add_scores_to_memory(scores=[first_score], observations=[observation])
    replay_score = first_score.model_copy(
        update={
            "id": uuid.uuid4(),
            "observation_ids": [observation.id],
        }
    )

    sqlite_instance.add_scores_to_memory(scores=[replay_score])

    assert sqlite_instance.get_scores(score_ids=[replay_score.id])[0].observation_ids == [observation.id]


def test_unreferenced_observation_is_not_persisted(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[piece])
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = _observation(
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
        sqlite_instance.add_scores_to_memory(scores=[score], observations=[observation])

    assert sqlite_instance._query_entries(ObservationEntry) == []
    assert sqlite_instance._query_entries(ScoreEntry) == []


def test_missing_observation_link_leaves_no_score(sqlite_instance: MemoryInterface):
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()))
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[piece])
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    score = Score(
        score_value="true",
        score_type="true_false",
        scorable=scorable,
        observation_ids=[uuid.uuid4()],
    )

    with pytest.raises(ValueError, match="not found in memory"):
        sqlite_instance.add_scores_to_memory(scores=[score])

    assert sqlite_instance._query_entries(ScoreEntry) == []


def test_duplicate_message_anchor_preserves_exact_observation_evidence(
    sqlite_instance: MemoryInterface,
):
    original = MessagePiece(
        role="assistant",
        original_value="input",
        conversation_id=str(uuid.uuid4()),
        sequence=0,
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[original])
    sqlite_instance.duplicate_conversation(conversation_id=original.conversation_id)
    duplicate = next(piece for piece in sqlite_instance.get_message_pieces() if piece.id != original.id)
    response = MessagePiece(
        role="assistant",
        original_value="judgment",
        conversation_id=str(uuid.uuid4()),
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[response])
    scorable = MessageScorable(message_piece_ids=(duplicate.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = _observation(
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

    sqlite_instance.add_scores_to_memory(scores=[score], observations=[observation])

    stored_score = sqlite_instance.get_scores(score_ids=[score.id])[0]
    stored_observation = sqlite_instance.get_observations(observation_ids=[observation.id])[0]
    assert stored_score.scorable == MessageScorable(message_piece_ids=(original.id,))
    assert stored_observation.scorable == MessageScorable(message_piece_ids=(duplicate.id,))
    assert stored_observation.payload.scored_piece_id == duplicate.id


def test_sqlite_protects_observation_message_references(
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
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[scored_piece, response_piece])
    scorable = MessageScorable(message_piece_ids=(scored_piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = _observation(
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
    sqlite_instance.add_scores_to_memory(
        scores=[score],
        observations=[observation],
    )

    with pytest.raises(SQLAlchemyError):
        sqlite_instance.delete_conversation_pieces_after_sequence(
            conversation_id=response_piece.conversation_id,
            sequence=-1,
        )
    with pytest.raises(SQLAlchemyError):
        sqlite_instance.delete_conversation_pieces_after_sequence(
            conversation_id=scored_piece.conversation_id,
            sequence=-1,
        )

    assert sqlite_instance.get_message_pieces(prompt_ids=[response_piece.id])


@pytest.fixture
def file_memory(tmp_path: Path) -> Iterator[SQLiteMemory]:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    memory.__init__(db_path=tmp_path / "observations.db", silent=True)
    try:
        yield memory
    finally:
        memory.dispose_engine()


def _score_and_observation(memory: MemoryInterface) -> tuple[Score, Observation, MessagePiece]:
    piece = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4()), sequence=0)
    memory.add_message_pieces_to_memory(message_pieces=[piece])
    scorable = MessageScorable(message_piece_ids=(piece.id,))
    expectation = ScoringExpectation(objective="Judge the response")
    observation = _observation(memory=memory, scorable=scorable, response_piece_id=piece.id, expectation=expectation)
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
def test_file_sqlite_locks_evidence_before_reads_until_commit(
    *, file_memory: SQLiteMemory, operation: str, boundary: str
) -> None:
    score, observation, piece = _score_and_observation(file_memory)
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
        file_memory.add_scores_to_memory(scores=[score], observations=[observation])

    assert checked == [True]
    with file_memory.engine.connect() as writer:
        with pytest.raises(SQLAlchemyError, match="immutable observation evidence"):
            writer.execute(text(statement), {"id": str(piece.id), "value": "tampered"})
    assert file_memory.get_observations(observation_ids=[observation.id]) == [observation]


@pytest.mark.parametrize("fail_commit", [False, True])
def test_failed_score_write_preserves_duplicate_anchor(
    *, file_memory: SQLiteMemory, fail_commit: bool, caplog: pytest.LogCaptureFixture
) -> None:
    score, observation, piece = _score_and_observation(file_memory)
    file_memory.duplicate_conversation(conversation_id=piece.conversation_id)
    duplicate = next(value for value in file_memory.get_message_pieces() if value.id != piece.id)
    score.message_piece_id = duplicate.id
    score.scorable = MessageScorable(message_piece_ids=(duplicate.id,))
    before = score.model_dump()
    with file_memory.get_session() as session:
        if fail_commit:
            failure = patch.object(session, "commit", side_effect=SQLAlchemyError("commit failed"))
        else:
            failure = patch.object(file_memory, "_validate_observation_evidence", side_effect=ValueError("invalid"))
        with patch.object(file_memory, "get_session", return_value=session), failure:
            with pytest.raises((SQLAlchemyError, ValueError), match="commit failed|invalid"):
                file_memory.add_scores_to_memory(scores=[score], observations=[observation])
    assert score.model_dump() == before
    assert file_memory._query_entries(ScoreEntry) == []
    assert file_memory.get_observations(observation_ids=[observation.id]) == []
    if fail_commit:
        assert any(record.message == "Error inserting scores" and record.exc_info for record in caplog.records)


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_orm_score_delete_cleans_last_observation_and_releases_prompt(
    *, file_memory: SQLiteMemory, foreign_keys: bool
) -> None:
    score, observation, piece = _score_and_observation(file_memory)
    file_memory.add_scores_to_memory(scores=[score], observations=[observation])
    with file_memory.get_session() as session:
        if foreign_keys:
            session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
            assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        session.delete(session.get(ScoreEntry, score.id))
        session.commit()
    assert file_memory.get_observations(observation_ids=[observation.id]) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []
    assert file_memory._query_entries(ScoreObservationEntry) == []
    file_memory.delete_conversation_pieces_after_sequence(conversation_id=piece.conversation_id, sequence=-1)
    assert file_memory.get_message_pieces(prompt_ids=[piece.id]) == []


def test_orm_score_delete_rollback_restores_observation_protection(file_memory: SQLiteMemory) -> None:
    score, observation, piece = _score_and_observation(file_memory)
    file_memory.add_scores_to_memory(scores=[score], observations=[observation])
    with file_memory.get_session() as session:
        session.delete(session.get(ScoreEntry, score.id))
        session.flush()
        assert session.get(ObservationEntry, observation.id) is None
        session.rollback()
    assert file_memory.get_scores(score_ids=[score.id])[0].observation_ids == [observation.id]
    assert file_memory.get_observations(observation_ids=[observation.id]) == [observation]
    assert len(file_memory._query_entries(ObservationMessagePieceEntry)) == 1
    with pytest.raises(SQLAlchemyError, match="immutable observation evidence"):
        file_memory.delete_conversation_pieces_after_sequence(conversation_id=piece.conversation_id, sequence=-1)


@pytest.mark.parametrize("delete_together", [False, True])
def test_orm_shared_observation_survives_until_final_score(*, file_memory: SQLiteMemory, delete_together: bool) -> None:
    score, observation, piece = _score_and_observation(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    file_memory.add_scores_to_memory(scores=[score, replay], observations=[observation])
    with file_memory.get_session() as session:
        session.delete(session.get(ScoreEntry, score.id))
        if delete_together:
            session.delete(session.get(ScoreEntry, replay.id))
        session.commit()
    if not delete_together:
        assert file_memory.get_observations(observation_ids=[observation.id]) == [observation]
        with pytest.raises(SQLAlchemyError):
            file_memory.delete_conversation_pieces_after_sequence(conversation_id=piece.conversation_id, sequence=-1)
        with file_memory.get_session() as session:
            session.delete(session.get(ScoreEntry, replay.id))
            session.commit()
    assert file_memory.get_observations(observation_ids=[observation.id]) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []


def test_concurrent_orm_score_deletions_clean_final_observation(file_memory: SQLiteMemory) -> None:
    score, observation, _ = _score_and_observation(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    file_memory.add_scores_to_memory(scores=[score, replay], observations=[observation])
    ready = Barrier(2)

    def _delete(score_id: uuid.UUID) -> None:
        with file_memory.get_session() as session:
            entry = session.get(ScoreEntry, score_id)
            ready.wait(timeout=5)
            session.delete(entry)
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_delete, value.id) for value in (score, replay)]
        for future in futures:
            future.result(timeout=10)
    assert file_memory._query_entries(ScoreEntry) == []
    assert file_memory.get_observations(observation_ids=[observation.id]) == []
    assert file_memory._query_entries(ObservationMessagePieceEntry) == []


@pytest.mark.parametrize("insert_first", [False, True])
@pytest.mark.parametrize("insert_via_orm", [False, True])
def test_concurrent_insert_and_final_delete_do_not_orphan_score(
    *, file_memory: SQLiteMemory, insert_first: bool, insert_via_orm: bool
) -> None:
    score, observation, _ = _score_and_observation(file_memory)
    replay = score.model_copy(update={"id": uuid.uuid4()})
    file_memory.add_scores_to_memory(scores=[score], observations=[observation])
    competing_write = Event()

    def _before_write(*, statement: str, **kwargs: Any) -> None:
        if statement == "BEGIN IMMEDIATE":
            competing_write.set()

    def _compete() -> None:
        if insert_first:
            with file_memory.get_session() as session:
                session.delete(session.get(ScoreEntry, score.id))
                session.commit()
        else:
            with pytest.raises(ValueError, match="not found in memory"):
                if insert_via_orm:
                    with file_memory.get_session() as session:
                        session.add(ScoreEntry(entry=replay))
                        session.add(
                            ScoreObservationEntry(score_id=replay.id, position=0, observation_id=observation.id)
                        )
                        session.commit()
                else:
                    file_memory.add_scores_to_memory(scores=[replay])

    with file_memory.get_session() as session, ThreadPoolExecutor(max_workers=1) as pool:
        if insert_first:
            session.add(ScoreEntry(entry=replay))
            session.add(ScoreObservationEntry(score_id=replay.id, position=0, observation_id=observation.id))
        else:
            session.delete(session.get(ScoreEntry, score.id))
        session.flush()
        event.listen(file_memory.engine, "before_cursor_execute", _before_write, named=True)
        try:
            future = pool.submit(_compete)
            assert competing_write.wait(timeout=5)
            assert not future.done()
            session.commit()
            future.result(timeout=10)
        finally:
            session.rollback()
            event.remove(file_memory.engine, "before_cursor_execute", _before_write)
    expected = [observation] if insert_first else []
    assert file_memory.get_observations(observation_ids=[observation.id]) == expected
    assert len(file_memory._query_entries(ScoreEntry)) == int(insert_first)


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

    with pytest.raises(TypeError, match="requires a sqlite3.Connection"):
        _begin_sqlite_write(session)

    session.connection.return_value.exec_driver_sql.assert_not_called()


def test_media_judgment_observation_rejection(file_memory: SQLiteMemory) -> None:
    score, observation, _ = _score_and_observation(file_memory)
    media = ContentScorable(value="image.png", data_type="image_path")
    observation = observation.model_copy(
        update={
            "scorable": media,
            "payload": observation.payload.model_copy(update={"scored_piece_id": None}),
        }
    )
    with pytest.raises(ValueError, match="Media judgment observations are deferred"):
        file_memory.add_scores_to_memory(scores=[score], observations=[observation])
