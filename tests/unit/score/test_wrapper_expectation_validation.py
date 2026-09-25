# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import (
    ComponentIdentifier,
    Condition,
    ContentScorable,
    MatchesObjective,
    MessagePiece,
    Score,
    ScoringExpectation,
)
from pyrit.score import (
    AudioFloatScaleScorer,
    AudioTrueFalseScorer,
    FloatScaleScorer,
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
    CONDITION_TYPE = _SiblingCondition

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        assert expectation is not None
        assert any(isinstance(condition, _SiblingCondition) for condition in expectation.conditions)
        return await self._score_piece_async(message_piece, objective=expectation.objective)


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
    expected = expectation
    if isinstance(wrapper, (VideoTrueFalseScorer, VideoFloatScaleScorer)):
        expected = expectation.model_copy(
            update={
                "objective": (
                    "The objective is: valid objective\n\nThis request includes an image from the resultant video."
                )
            }
        )
    assert validate.call_args.kwargs["expectation"] == expected


def test_wrapper_preflight_selects_child_conditions(wrapper_pair: tuple[Scorer, MessageScorer]) -> None:
    wrapper, child = wrapper_pair
    expectation = ScoringExpectation(objective="valid objective", conditions=(MatchesObjective(), _SiblingCondition()))
    child_expectation = expectation.model_copy(update={"conditions": (MatchesObjective(),)})
    if isinstance(wrapper, (VideoTrueFalseScorer, VideoFloatScaleScorer)):
        child_expectation = child_expectation.model_copy(
            update={
                "objective": (
                    "The objective is: valid objective\n\nThis request includes an image from the resultant video."
                )
            }
        )
    if isinstance(wrapper, FloatScaleScorer):
        wrapper = FloatScaleThresholdScorer(scorer=wrapper, threshold=0.5)
    assert isinstance(wrapper, TrueFalseScorer)
    root = TrueFalseCompositeScorer(scorers=[wrapper, _SiblingScorer()], aggregator=TrueFalseScoreAggregator.AND)
    with patch.object(child, "_validate_expectation", wraps=child._validate_expectation) as validate:
        root.prepare_expectation(expectation=expectation)
    validate.assert_called_once()
    assert validate.call_args.kwargs["expectation"] == child_expectation


@pytest.mark.parametrize("expectation", [None, ScoringExpectation(objective="")])
def test_wrapper_group_preflight_rejects_missing_objective(
    *, wrapper_pair: tuple[Scorer, MessageScorer], expectation: ScoringExpectation | None
) -> None:
    wrapper, child = wrapper_pair
    with (
        patch.object(child, "_validate_expectation", wraps=child._validate_expectation) as validate,
        pytest.raises(ValueError, match="MatchesObjective requires"),
    ):
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=expectation)
    validate.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("expectation", [None, ScoringExpectation(objective="")])
def test_wrapper_preflight_forwards_opt_in_empty_criteria_validation(
    *, wrapper_pair: tuple[Scorer, MessageScorer], expectation: ScoringExpectation | None
) -> None:
    wrapper, child = wrapper_pair
    with (
        patch.object(child, "_validate_expectation", side_effect=ValueError("typed criteria required")) as validate,
        pytest.raises(ValueError, match="typed criteria required"),
    ):
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=expectation)
    validate.assert_called_once()


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
    expectation = ScoringExpectation(
        objective=objective, conditions=(MatchesObjective(), _SiblingCondition()) if nested else (MatchesObjective(),)
    )
    with pytest.raises(ValueError, match="MatchesObjective requires the expectation to carry an objective"):
        Scorer.validate_expectation_for_scorers(scorers=[wrapper], expectation=expectation)


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
        expectation = ScoringExpectation(
            objective="", conditions=(MatchesObjective(), _SiblingCondition()) if nested else (MatchesObjective(),)
        )
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
        for validate, conditions in (
            (child_validate, (MatchesObjective(),)),
            (sibling_validate, (_SiblingCondition(),)),
        ):
            assert validate.call_args_list
            assert all(
                call.kwargs["expectation"] == expectation.model_copy(update={"conditions": conditions})
                for call in validate.call_args_list
            )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("nested", [False, True])
async def test_objective_only_default_is_used_for_judgment_and_persistence_async(*, nested: bool) -> None:
    leaf = _ObjectiveTrueFalseScorer()
    scorer = (
        TrueFalseInverterScorer(
            scorer=TrueFalseCompositeScorer(scorers=[leaf], aggregator=TrueFalseScoreAggregator.AND)
        )
        if nested
        else leaf
    )
    original = ScoringExpectation(objective="original objective")
    normalized = original.model_copy(update={"conditions": (MatchesObjective(),)})
    with patch.object(
        leaf, "_score_piece_with_expectation_async", wraps=leaf._score_piece_with_expectation_async
    ) as judge:
        scores = await scorer.score_async(scorable=ContentScorable(value="response"), expectation=original)
    assert judge.call_args.kwargs["expectation"] == normalized
    assert scores[0].scored_expectation == normalized
    assert original.conditions == ()


@pytest.mark.usefixtures("patch_central_database")
async def test_explicit_conditions_do_not_default_missing_objective_after_projection_async() -> None:
    objective, sibling = _ObjectiveTrueFalseScorer(), _SiblingScorer()
    scorer = TrueFalseCompositeScorer(scorers=[objective, sibling], aggregator=TrueFalseScoreAggregator.AND)
    with (
        patch.object(objective, "_score_piece_async", new_callable=AsyncMock) as judge,
        pytest.raises(ValueError, match="requires one MatchesObjective"),
    ):
        await scorer.score_async(
            scorable=ContentScorable(value="response"),
            expectation=ScoringExpectation(objective="context", conditions=(_SiblingCondition(),)),
        )
    judge.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("float_scale", [False, True])
def test_video_validates_transformed_audio_context_before_acquisition(*, float_scale: bool) -> None:
    child = _ObjectiveTrueFalseScorer()
    video = (
        VideoFloatScaleScorer(image_capable_scorer=_ObjectiveFloatScaleScorer(), audio_scorer=child)
        if float_scale
        else VideoTrueFalseScorer(image_capable_scorer=_ObjectiveTrueFalseScorer(), audio_scorer=child)
    )
    with (
        patch.object(video._video_helper, "_extract_frames") as extract,
        pytest.raises(ValueError, match="MatchesObjective requires"),
    ):
        video.prepare_expectation(expectation=ScoringExpectation(objective="context"))
    extract.assert_not_called()
