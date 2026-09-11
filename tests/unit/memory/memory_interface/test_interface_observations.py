# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ObservationEntry, ScoreEntry
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    LlmJudgmentObservationPayload,
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
        payload=LlmJudgmentObservationPayload(
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
