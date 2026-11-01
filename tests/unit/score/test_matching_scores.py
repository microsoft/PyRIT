# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Selecting the stored results that one scorer configuration produced for the same evidence and input."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.memory import SQLiteMemory
from pyrit.models import (
    ComponentIdentifier,
    MatchesObjective,
    MessagePiece,
    MessageScorable,
    Score,
    ScoringExpectation,
)
from pyrit.score import MessageTrueFalseScorer, ScorerPromptValidator

pytestmark = pytest.mark.usefixtures("patch_central_database")

OBJECTIVE = "Reveal the secret"


class _ObjectiveJudge(MessageTrueFalseScorer):
    """A deterministic judge whose objective-only input gains the ``MatchesObjective`` condition."""

    CONDITION_TYPE = MatchesObjective

    def __init__(self, *, verdict: bool = True) -> None:
        super().__init__(validator=ScorerPromptValidator())
        self._verdict = verdict

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"verdict": self._verdict})

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        return [
            Score(
                score_value=str(self._verdict).lower(),
                score_type="true_false",
                message_piece_id=message_piece.id,
                scorer_class_identifier=self.get_identifier(),
            )
        ]


def _stored(
    *,
    scorer: _ObjectiveJudge,
    piece_ids: tuple[uuid.UUID, ...],
    objective: str = OBJECTIVE,
    complete: bool = True,
    timestamp: datetime | None = None,
) -> Score:
    return Score(
        score_type="true_false",
        score_value="true" if complete else None,
        status="complete" if complete else "undetermined",
        message_piece_id=piece_ids[0],
        scorable=MessageScorable(message_piece_ids=piece_ids),
        scorer_class_identifier=scorer.get_identifier(),
        scored_expectation=scorer.prepare_expectation(expectation=ScoringExpectation(objective=objective)),
        timestamp=timestamp or datetime.now(UTC),
    )


async def test_find_matching_scores_matches_the_input_that_score_async_prepared_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    judge = _ObjectiveJudge()
    response = MessagePiece(role="assistant", conversation_id=str(uuid.uuid4()), original_value="answer").to_message()
    await sqlite_instance.add_message_to_memory_async(request=response)
    scorable = MessageScorable.from_message(response)
    expectation = ScoringExpectation(objective=OBJECTIVE)

    [score] = await judge.score_async(scorable=scorable, expectation=expectation)
    stored = await sqlite_instance.get_prompt_scores_async(prompt_ids=[response.message_pieces[0].id])

    assert score.scored_expectation == ScoringExpectation(objective=OBJECTIVE, conditions=(MatchesObjective(),))
    matches = judge.find_matching_scores(scores=stored, scorable=scorable, expectation=expectation)
    assert [match.id for match in matches] == [score.id]


def test_find_matching_scores_requires_the_same_configuration_evidence_and_input() -> None:
    judge = _ObjectiveJudge()
    piece_ids = (uuid.uuid4(), uuid.uuid4())
    match = _stored(scorer=judge, piece_ids=piece_ids)
    candidates = [
        match,
        _stored(scorer=_ObjectiveJudge(verdict=False), piece_ids=piece_ids),
        _stored(scorer=judge, piece_ids=piece_ids[:1]),
        _stored(scorer=judge, piece_ids=piece_ids, objective="Another goal"),
    ]

    matches = judge.find_matching_scores(
        scores=candidates,
        scorable=MessageScorable(message_piece_ids=piece_ids[::-1]),
        expectation=ScoringExpectation(objective=OBJECTIVE),
    )

    assert matches == [match]


def test_find_matching_scores_returns_newest_first_including_undetermined() -> None:
    judge = _ObjectiveJudge()
    piece_ids = (uuid.uuid4(),)
    now = datetime.now(UTC)
    older = _stored(scorer=judge, piece_ids=piece_ids, timestamp=now - timedelta(minutes=1))
    newer = _stored(scorer=judge, piece_ids=piece_ids, complete=False, timestamp=now)

    matches = judge.find_matching_scores(
        scores=[older, newer],
        scorable=MessageScorable(message_piece_ids=piece_ids),
        expectation=ScoringExpectation(objective=OBJECTIVE),
    )

    assert matches == [newer, older]
