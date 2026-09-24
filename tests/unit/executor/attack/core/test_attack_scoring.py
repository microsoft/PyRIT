# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.executor.attack.core.attack_scoring import prepare_attack_scoring, score_attack_response_async
from pyrit.models import (
    ComponentIdentifier,
    Condition,
    MessagePiece,
    Scorable,
    ScorableUnion,
    Score,
    ScoringExpectation,
)
from pyrit.score import TrueFalseCompositeScorer, TrueFalseScoreAggregator, TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface
    from pyrit.models import Message
    from pyrit.score import Scorer


class _FirstCondition(Condition):
    condition_type: Literal["test_attack_scoring_first"] = "test_attack_scoring_first"


class _SecondCondition(Condition):
    condition_type: Literal["test_attack_scoring_second"] = "test_attack_scoring_second"


class _FirstScorer(TrueFalseScorer):
    CONDITION_TYPE: type[Condition] | None = _FirstCondition

    def __init__(self) -> None:
        super().__init__()
        self.expectations: list[ScoringExpectation | None] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        self.expectations.append(expectation)
        return [
            Score(
                score_value="true",
                score_type="true_false",
                scorable=cast("ScorableUnion", scorable),
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _SecondScorer(_FirstScorer):
    CONDITION_TYPE = _SecondCondition


class _ConfiguredScorer(_FirstScorer):
    CONDITION_TYPE = None


def _expectation() -> ScoringExpectation:
    return ScoringExpectation(objective="context", conditions=(_FirstCondition(), _SecondCondition()))


async def _stored_response_async(memory: MemoryInterface) -> Message:
    response = MessagePiece(role="assistant", original_value="response", conversation_id=str(uuid.uuid4())).to_message()
    (await memory.add_message_to_memory_async(request=response))
    return response


def _subset(expectation: ScoringExpectation, *conditions: Condition) -> ScoringExpectation:
    return expectation.model_copy(update={"conditions": conditions})


def test_conditions_require_objective_scorer() -> None:
    with pytest.raises(ValueError, match="objective scorer is required"):
        prepare_attack_scoring(objective_scorer=None, auxiliary_scorers=[_FirstScorer()], expectation=_expectation())


def test_objective_scorer_must_cover_every_condition() -> None:
    with pytest.raises(ValueError, match="does not support"):
        prepare_attack_scoring(objective_scorer=_FirstScorer(), auxiliary_scorers=[], expectation=_expectation())


@pytest.mark.parametrize("kind", ["configured", "matching", "missing", "incomplete-composite"])
def test_auxiliary_receives_its_subset_or_is_skipped(kind: str) -> None:
    auxiliary: Scorer
    if kind == "configured":
        auxiliary = _ConfiguredScorer()
    elif kind == "matching":
        auxiliary = _FirstScorer()
    elif kind == "missing":
        auxiliary = _SecondScorer()
    else:
        auxiliary = TrueFalseCompositeScorer(
            scorers=[_FirstScorer(), _SecondScorer()], aggregator=TrueFalseScoreAggregator.OR
        )
    expectation = ScoringExpectation(objective="context", conditions=(_FirstCondition(),))

    prepared = prepare_attack_scoring(
        objective_scorer=_FirstScorer(), auxiliary_scorers=[auxiliary], expectation=expectation
    )

    assert prepared.objective_expectation == expectation
    if kind in ("missing", "incomplete-composite"):
        assert prepared.auxiliary_scorers == prepared.auxiliary_expectations == []
    else:
        assert prepared.auxiliary_scorers == [auxiliary]
        assert prepared.auxiliary_expectations == [expectation if kind == "matching" else _subset(expectation)]


@pytest.mark.parametrize("expectation", [None, ScoringExpectation(), ScoringExpectation(objective="context")])
def test_absent_criteria_skip_typed_but_not_configured_auxiliary(expectation: ScoringExpectation | None) -> None:
    configured = _ConfiguredScorer()
    prepared = prepare_attack_scoring(
        objective_scorer=None, auxiliary_scorers=[_FirstScorer(), configured], expectation=expectation
    )
    assert prepared.auxiliary_scorers == [configured]


def test_selected_auxiliary_validation_error_is_not_a_skip() -> None:
    auxiliary = _FirstScorer()
    with (
        patch.object(auxiliary, "_validate_expectation", side_effect=ValueError("invalid diagnostic context")),
        pytest.raises(ValueError, match="invalid diagnostic context"),
    ):
        prepare_attack_scoring(
            objective_scorer=_FirstScorer(),
            auxiliary_scorers=[auxiliary],
            expectation=ScoringExpectation(conditions=(_FirstCondition(),)),
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_objective_and_auxiliary_receive_their_own_inputs_async(sqlite_instance: MemoryInterface) -> None:
    first, second, auxiliary = _FirstScorer(), _SecondScorer(), _SecondScorer()
    objective = TrueFalseCompositeScorer(scorers=[first, second], aggregator=TrueFalseScoreAggregator.AND)
    expectation = _expectation()

    results = await score_attack_response_async(
        response=(await _stored_response_async(sqlite_instance)),
        objective_scorer=objective,
        auxiliary_scorers=[auxiliary],
        expectation=expectation,
    )

    selected = _subset(expectation, _SecondCondition())
    assert first.expectations == [_subset(expectation, _FirstCondition())]
    assert second.expectations == auxiliary.expectations == [selected]
    assert results["objective_scores"][0].scored_expectation == expectation
    assert results["auxiliary_scores"][0].scored_expectation == selected


@pytest.mark.usefixtures("patch_central_database")
async def test_selected_auxiliary_failure_is_not_suppressed_async(sqlite_instance: MemoryInterface) -> None:
    auxiliary = _FirstScorer()
    with (
        patch.object(auxiliary, "_score_scorable_async", side_effect=ValueError("diagnostic failed")),
        pytest.raises(Exception, match="diagnostic failed"),
    ):
        await score_attack_response_async(
            response=(await _stored_response_async(sqlite_instance)),
            objective_scorer=_FirstScorer(),
            auxiliary_scorers=[auxiliary],
            expectation=ScoringExpectation(conditions=(_FirstCondition(),)),
        )


@pytest.mark.usefixtures("patch_central_database")
def test_attack_warns_once_before_execution_when_auxiliary_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    attack = PromptSendingAttack(
        objective_target=MockPromptTarget(),
        attack_scoring_config=AttackScoringConfig(objective_scorer=_FirstScorer(), auxiliary_scorers=[_SecondScorer()]),
    )
    context = MagicMock(expectation=ScoringExpectation(conditions=(_FirstCondition(),)))
    with caplog.at_level(logging.WARNING):
        attack._validate_scoring_expectation(context=context)
    assert "_SecondScorer will not run" in caplog.text
