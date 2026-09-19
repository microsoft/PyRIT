# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import ComponentIdentifier, Condition, MatchesObjective, MessagePiece, Score, ScoringExpectation
from pyrit.score import (
    AudioFloatScaleScorer,
    AudioTrueFalseScorer,
    FloatScaleThresholdScorer,
    MessageFloatScaleScorer,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorer,
    ScorerPromptValidator,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
    VideoFloatScaleScorer,
    VideoTrueFalseScorer,
    create_conversation_scorer,
)


class _ObjectiveTrueFalseScorer(MessageTrueFalseScorer):
    _DEFAULT_VALIDATOR = ScorerPromptValidator(
        is_objective_required=True, supported_data_types=["text", "image_path", "audio_path"]
    )

    def __init__(self) -> None:
        super().__init__(validator=self._DEFAULT_VALIDATOR)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="true",
                score_type="true_false",
                message_piece_id=message_piece.id,
                objective=objective,
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _ObjectiveFloatScaleScorer(MessageFloatScaleScorer):
    _DEFAULT_VALIDATOR = ScorerPromptValidator(
        is_objective_required=True, supported_data_types=["text", "image_path", "audio_path"]
    )

    def __init__(self) -> None:
        super().__init__(validator=self._DEFAULT_VALIDATOR)

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [
            Score(
                score_value="0.9",
                score_type="float_scale",
                message_piece_id=message_piece.id,
                objective=objective,
                scorer_class_identifier=self.get_identifier(),
            )
        ]


class _SiblingCondition(Condition):
    condition_type: Literal["test_wrapper_sibling"] = "test_wrapper_sibling"


class _SiblingScorer(_ObjectiveTrueFalseScorer):
    _DEFAULT_VALIDATOR = ScorerPromptValidator()
    MATCHED_CONDITIONS = frozenset({_SiblingCondition})
    REQUIRED_CONDITIONS = MATCHED_CONDITIONS


@pytest.fixture(params=["inverter", "threshold"])
def outcome_pair(request: pytest.FixtureRequest) -> tuple[TrueFalseScorer, MessageScorer]:
    if request.param == "inverter":
        true_false_child = _ObjectiveTrueFalseScorer()
        return TrueFalseInverterScorer(scorer=true_false_child), true_false_child
    float_child = _ObjectiveFloatScaleScorer()
    return FloatScaleThresholdScorer(scorer=float_child, threshold=0.5), float_child


@pytest.fixture(
    params=[
        pytest.param((TrueFalseInverterScorer, _ObjectiveTrueFalseScorer, "scorer", {}), id="inverter"),
        pytest.param(
            (FloatScaleThresholdScorer, _ObjectiveFloatScaleScorer, "scorer", {"threshold": 0.5}), id="threshold"
        ),
        pytest.param(
            (create_conversation_scorer, _ObjectiveTrueFalseScorer, "scorer", {}), id="conversation-true-false"
        ),
        pytest.param(
            (create_conversation_scorer, _ObjectiveFloatScaleScorer, "scorer", {}), id="conversation-float-scale"
        ),
        pytest.param(
            (AudioTrueFalseScorer, _ObjectiveTrueFalseScorer, "text_capable_scorer", {}), id="audio-true-false"
        ),
        pytest.param(
            (AudioFloatScaleScorer, _ObjectiveFloatScaleScorer, "text_capable_scorer", {}), id="audio-float-scale"
        ),
        pytest.param(
            (VideoTrueFalseScorer, _ObjectiveTrueFalseScorer, "image_capable_scorer", {}), id="video-image-true-false"
        ),
        pytest.param(
            (VideoFloatScaleScorer, _ObjectiveFloatScaleScorer, "image_capable_scorer", {}),
            id="video-image-float-scale",
        ),
    ]
)
def wrapper_pair(request: pytest.FixtureRequest) -> tuple[Scorer, MessageScorer]:
    wrapper, leaf_type, child_argument, kwargs = request.param
    child = leaf_type()
    root = wrapper(**{child_argument: child}, **kwargs)
    assert isinstance(root, Scorer)
    assert isinstance(child, MessageScorer)
    return root, child


def test_wrapper_preflight_calls_child_validation(wrapper_pair: tuple[Scorer, MessageScorer]) -> None:
    wrapper, child = wrapper_pair
    expectation = ScoringExpectation(objective="valid objective", conditions=(MatchesObjective(),))
    with (
        patch.object(child, "_validate_expectation", side_effect=ValueError("child-specific rejection")) as validate,
        pytest.raises(ValueError, match="child-specific rejection"),
    ):
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=expectation)
    validate.assert_called_once()
    assert validate.call_args.kwargs["expectation"] is expectation


def test_wrapper_preflight_preserves_sibling_conditions(wrapper_pair: tuple[Scorer, MessageScorer]) -> None:
    wrapper, child = wrapper_pair
    expectation = ScoringExpectation(objective="valid objective", conditions=(MatchesObjective(), _SiblingCondition()))
    with patch.object(child, "_validate_expectation", wraps=child._validate_expectation) as validate:
        Scorer.validate_expectation_for_scorers(scorers=[wrapper, _SiblingScorer()], expectation=expectation)
    validate.assert_called_once()
    assert validate.call_args.kwargs["expectation"] is expectation


@pytest.mark.parametrize("expectation", [None, ScoringExpectation(objective="")])
def test_wrapper_group_preflight_keeps_empty_condition_compatibility(
    *, wrapper_pair: tuple[Scorer, MessageScorer], expectation: ScoringExpectation | None
) -> None:
    wrapper, child = wrapper_pair
    with patch.object(child, "_validate_expectation", wraps=child._validate_expectation) as validate:
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=expectation)
    validate.assert_not_called()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested-composite"])
@pytest.mark.parametrize("objective", [None, ""])
def test_wrapped_objective_validation_rejects_missing_context(
    *, outcome_pair: tuple[TrueFalseScorer, MessageScorer], nested: bool, objective: str | None
) -> None:
    wrapper, _ = outcome_pair
    if nested:
        wrapper = TrueFalseInverterScorer(
            scorer=TrueFalseCompositeScorer(
                scorers=[_SiblingScorer(), wrapper], aggregator=TrueFalseScoreAggregator.AND
            )
        )
    expectation = ScoringExpectation(objective=objective, conditions=(MatchesObjective(), _SiblingCondition()))
    with pytest.raises(ValueError, match="MatchesObjective requires the expectation to carry an objective"):
        Scorer.validate_expectation_for_scorers(scorers=[wrapper, _SiblingScorer()], expectation=expectation)


@pytest.mark.parametrize("float_scale", [False, True], ids=["true-false", "float-scale"])
def test_video_preflight_recursively_validates_audio_child(float_scale: bool) -> None:
    child: MessageScorer
    video: Scorer
    if float_scale:
        float_child = _ObjectiveFloatScaleScorer()
        child = float_child
        video = VideoFloatScaleScorer(
            image_capable_scorer=_ObjectiveFloatScaleScorer(),
            audio_scorer=AudioFloatScaleScorer(text_capable_scorer=float_child),
        )
    else:
        true_false_child = _ObjectiveTrueFalseScorer()
        child = true_false_child
        video = VideoTrueFalseScorer(
            image_capable_scorer=_ObjectiveTrueFalseScorer(),
            audio_scorer=AudioTrueFalseScorer(text_capable_scorer=true_false_child),
        )
    expectation = ScoringExpectation(objective="valid objective", conditions=(MatchesObjective(),))
    with (
        patch.object(child, "_validate_expectation", side_effect=ValueError("audio child rejected")),
        pytest.raises(ValueError, match="audio child rejected"),
    ):
        Scorer.validate_expectation_for_scorers(scorers=[video], expectation=expectation)


@pytest.mark.usefixtures("patch_central_database")
class TestWrapperAttackPreflight:
    @pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested-composite"])
    async def test_invalid_child_expectation_fails_before_target_send_async(
        self, *, outcome_pair: tuple[TrueFalseScorer, MessageScorer], nested: bool
    ) -> None:
        wrapper, child = outcome_pair
        if nested:
            wrapper = TrueFalseCompositeScorer(
                scorers=[_SiblingScorer(), wrapper], aggregator=TrueFalseScoreAggregator.AND
            )
        target = MockPromptTarget()
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=wrapper, auxiliary_scorers=[_SiblingScorer()]),
        )
        expectation = ScoringExpectation(objective="", conditions=(MatchesObjective(), _SiblingCondition()))
        with (
            patch.object(target, "send_prompt_async", new_callable=AsyncMock) as send,
            patch.object(child, "_score_piece_async", new_callable=AsyncMock) as score,
            pytest.raises(ValueError, match="MatchesObjective requires the expectation to carry an objective"),
        ):
            await attack.execute_async(objective="attack objective", expectation=expectation)
        send.assert_not_awaited()
        score.assert_not_awaited()

    async def test_valid_nested_wrapper_retains_complete_expectation_async(self) -> None:
        child = _ObjectiveFloatScaleScorer()
        sibling = _SiblingScorer()
        wrapper = TrueFalseInverterScorer(
            scorer=TrueFalseCompositeScorer(
                scorers=[FloatScaleThresholdScorer(scorer=child, threshold=0.5), sibling],
                aggregator=TrueFalseScoreAggregator.AND,
            )
        )
        target = MockPromptTarget()
        attack = PromptSendingAttack(
            objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=wrapper)
        )
        expectation = ScoringExpectation(
            objective="scoring objective", conditions=(MatchesObjective(), _SiblingCondition())
        )
        with (
            patch.object(child, "_validate_expectation", wraps=child._validate_expectation) as child_validate,
            patch.object(sibling, "_validate_expectation", wraps=sibling._validate_expectation) as sibling_validate,
        ):
            result = await attack.execute_async(objective="attack objective", expectation=expectation)
        assert target.prompt_sent == ["attack objective"]
        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == expectation
        for validate in (child_validate, sibling_validate):
            assert validate.call_args_list
            assert all(call.kwargs["expectation"] is expectation for call in validate.call_args_list)
