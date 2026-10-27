# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""TAP keeps one auxiliary score per auxiliary scorer, even when scorers share a class."""

import json

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, TreeOfAttacksWithPruningAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import Message, MessagePiece, Score
from pyrit.score import FloatScaleThresholdScorer, Scorer, SubStringScorer
from pyrit.score.float_scale.float_scale_scorer import MessageFloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class _AdversarialTarget(MockPromptTarget):
    """Reply with the JSON the TAP adversarial prompt asks for."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
        reply = json.dumps({"next_message": "attack prompt", "rationale": "r", "last_response_summary": "s"})
        return [MessagePiece(role="assistant", original_value=reply, conversation_id=conversation_id).to_message()]


class _FixedFloatScorer(MessageFloatScaleScorer):
    """Score every response with the same float."""

    def __init__(self, *, value: str) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))
        self._value = value

    def _build_identifier(self):
        return self._create_identifier(params={"value": self._value})

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value=self._value,
                score_type="float_scale",
                score_category=["test"],
                score_rationale="rationale",
                score_metadata=None,
                message_piece_id=message_piece.id,
                score_value_description="description",
                scorer_class_identifier=self.get_identifier(),
            )
        ]


async def _run_tap_async(*, auxiliary_scorers: list[Scorer]) -> dict[str, float]:
    attack = TreeOfAttacksWithPruningAttack(
        objective_target=MockPromptTarget(),
        attack_adversarial_config=AttackAdversarialConfig(target=_AdversarialTarget()),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=FloatScaleThresholdScorer(scorer=_FixedFloatScorer(value="0.9"), threshold=0.7),
            auxiliary_scorers=auxiliary_scorers,
        ),
        tree_width=1,
        tree_depth=1,
        branching_factor=1,
        on_topic_checking_enabled=False,
    )
    result = await attack.execute_async(objective="do X")
    return result.auxiliary_scores_summary


@pytest.mark.usefixtures("sqlite_instance")
async def test_auxiliary_scorers_of_the_same_class_keep_separate_scores_async() -> None:
    found = SubStringScorer(substring="default", categories=["found"])
    missing = SubStringScorer(substring="missing", categories=["missing"])

    summary = await _run_tap_async(auxiliary_scorers=[found, missing])

    assert summary == {
        found.get_identifier().unique_name: 1.0,
        missing.get_identifier().unique_name: 0.0,
    }


@pytest.mark.usefixtures("sqlite_instance")
async def test_auxiliary_scorers_of_different_classes_keep_class_names_async() -> None:
    summary = await _run_tap_async(
        auxiliary_scorers=[SubStringScorer(substring="default", categories=["found"]), _FixedFloatScorer(value="0.4")]
    )

    assert summary == {"SubStringScorer": 1.0, "_FixedFloatScorer": 0.4}


async def test_tap_result_without_auxiliary_scorers_has_an_empty_summary_async(
    *, sqlite_instance: SQLiteMemory
) -> None:
    assert await _run_tap_async(auxiliary_scorers=[]) == {}
