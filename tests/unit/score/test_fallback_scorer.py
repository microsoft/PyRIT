# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget, store_message

from pyrit.memory import MemoryInterface
from pyrit.models import (
    ComponentIdentifier,
    ContentScorable,
    MatchesObjective,
    MessagePiece,
    MessageScorable,
    Scorable,
    Score,
    ScoreStatus,
    ScoreType,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.registry import ScorerRegistry
from pyrit.score import (
    FloatScaleFallbackScorer,
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    LikertScale,
    LikertScaleEntry,
    MessageScorer,
    Scorer,
    SelfAskLikertScorer,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseFallbackScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)
from pyrit.score.fallback_scorer import _FallbackScorer

pytestmark = pytest.mark.usefixtures("patch_central_database")


class _FloatScorer(FloatScaleScorer):
    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []


class _TrueFalseScorer(TrueFalseScorer):
    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []


class _ObjectiveFloatScorer(_FloatScorer):
    CONDITION_TYPE = MatchesObjective


class _ObjectiveTrueFalseScorer(_TrueFalseScorer):
    CONDITION_TYPE = MatchesObjective


class _ToolsScorer(_TrueFalseScorer):
    CONDITION_TYPE = ToolsCalled


@pytest.fixture(params=["float_scale", "true_false"])
def family(request: pytest.FixtureRequest) -> ScoreType:
    return "float_scale" if request.param == "float_scale" else "true_false"


@pytest.fixture
def pair(family: ScoreType) -> tuple[Scorer, Scorer, Scorer]:
    if family == "float_scale":
        primary = _FloatScorer()
        fallback = _FloatScorer()
        return FloatScaleFallbackScorer(scorer=primary, fallback_scorer=fallback), primary, fallback
    primary_tf = _TrueFalseScorer()
    fallback_tf = _TrueFalseScorer()
    return TrueFalseFallbackScorer(scorer=primary_tf, fallback_scorer=fallback_tf), primary_tf, fallback_tf


@pytest.fixture
def objective_pair(family: ScoreType) -> tuple[Scorer, Scorer, Scorer]:
    if family == "float_scale":
        primary = _ObjectiveFloatScorer()
        fallback = _ObjectiveFloatScorer()
        return FloatScaleFallbackScorer(scorer=primary, fallback_scorer=fallback), primary, fallback
    primary_tf = _ObjectiveTrueFalseScorer()
    fallback_tf = _ObjectiveTrueFalseScorer()
    return TrueFalseFallbackScorer(scorer=primary_tf, fallback_scorer=fallback_tf), primary_tf, fallback_tf


def _score(
    *,
    family: ScoreType,
    value: str | None,
    rationale: str = "reason",
    metadata: dict[str, str | int | float] | None = None,
) -> Score:
    return Score(
        score_type=family,
        score_value=value,
        status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
        score_category=["criterion"],
        score_value_description="description",
        score_rationale=rationale,
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="Child", class_module=__name__),
        scorable=ContentScorable(value="evidence"),
    )


@pytest.mark.parametrize("positive", [False, True])
async def test_complete_primary_skips_fallback_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, positive: bool, sqlite_instance: MemoryInterface
) -> None:
    wrapper, primary, fallback = pair
    value = str(float(positive)) if family == "float_scale" else str(positive)
    child_score = _score(family=family, value=value, metadata={"model": "primary", "resolved_by": "inner"})
    original = child_score.model_copy(deep=True)
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[child_score]),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock) as fallback_call,
    ):
        result = (await wrapper.score_async(scorable=ContentScorable(value="evidence")))[0]

    fallback_call.assert_not_awaited()
    assert result.get_value() == child_score.get_value()
    assert result.id != child_score.id
    assert result.scorer_class_identifier == wrapper.get_identifier()
    assert result.score_value_description == child_score.score_value_description
    assert result.score_category == child_score.score_category
    assert result.score_rationale == child_score.score_rationale
    assert result.score_metadata == {
        "resolved_by": "primary",
        "primary.model": "primary",
        "primary.resolved_by": "inner",
    }
    assert child_score == original
    assert [score.id for score in sqlite_instance.get_scores(score_type=family)] == [result.id]
    assert isinstance(wrapper, FloatScaleScorer if family == "float_scale" else TrueFalseScorer)


@pytest.mark.parametrize("fallback_abstains", [False, True])
async def test_fallback_preserves_both_attempts_without_mutating_children_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, fallback_abstains: bool
) -> None:
    wrapper, primary, fallback = pair
    primary_score = _score(family=family, value=None, rationale="uncertain", metadata={"model": "primary"})
    value = None if fallback_abstains else ("0.8" if family == "float_scale" else "True")
    fallback_score = _score(family=family, value=value, rationale="second opinion", metadata={"model": "fallback"})
    originals = [score.model_copy(deep=True) for score in (primary_score, fallback_score)]
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[primary_score]),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=[fallback_score]) as call,
    ):
        result = (await wrapper.score_async(scorable=ContentScorable(value="evidence")))[0]

    call.assert_awaited_once()
    assert result.status == fallback_score.status
    assert result.score_value == fallback_score.score_value
    assert result.id not in {primary_score.id, fallback_score.id}
    assert result.score_metadata == {
        "resolved_by": "fallback",
        "primary.model": "primary",
        "fallback.model": "fallback",
        "primary_rationale": "uncertain",
        "fallback_rationale": "second opinion",
        "fallback_status": fallback_score.status.value,
    }
    assert "uncertain" in result.score_rationale
    assert "second opinion" in result.score_rationale
    if fallback_abstains:
        assert "also returned an undetermined score" in result.score_rationale
    assert [primary_score, fallback_score] == originals


@pytest.mark.parametrize("empty_primary", [False, True])
async def test_non_applicable_child_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, empty_primary: bool
) -> None:
    wrapper, primary, fallback = pair
    primary_scores = [] if empty_primary else [_score(family=family, value=None)]
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=primary_scores),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=[]) as call,
    ):
        results = await wrapper.score_async(scorable=ContentScorable(value="evidence"))

    if empty_primary:
        assert results == []
        call.assert_not_awaited()
    else:
        call.assert_awaited_once()
        assert results[0].is_undetermined
        assert results[0].score_metadata["resolved_by"] == "primary"
        assert results[0].score_metadata["fallback_status"] == "not_applicable"
        assert "not applicable" in results[0].score_rationale


@pytest.mark.parametrize("failing_primary", [False, True])
async def test_child_errors_propagate_without_persisting_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, failing_primary: bool, sqlite_instance: MemoryInterface
) -> None:
    wrapper, primary, fallback = pair
    with (
        patch.object(
            primary,
            "_score_scorable_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("primary failed") if failing_primary else None,
            return_value=[_score(family=family, value=None)],
        ),
        patch.object(
            fallback, "_score_scorable_async", new_callable=AsyncMock, side_effect=RuntimeError("fallback failed")
        ) as fallback_call,
        pytest.raises(RuntimeError, match="primary failed" if failing_primary else "fallback failed"),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"))
    if failing_primary:
        fallback_call.assert_not_awaited()
    assert sqlite_instance.get_scores(score_type=family) == []


@pytest.mark.parametrize("multiple_primary", [False, True])
async def test_rejects_multiple_child_scores_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, multiple_primary: bool, sqlite_instance: MemoryInterface
) -> None:
    wrapper, primary, fallback = pair
    primary_scores = [_score(family=family, value=None) for _ in range(2 if multiple_primary else 1)]
    fallback_scores = [_score(family=family, value=None) for _ in range(1 if multiple_primary else 2)]
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=primary_scores),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=fallback_scores) as call,
        pytest.raises(RuntimeError, match="exactly one score"),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"))
    if multiple_primary:
        call.assert_not_awaited()
    assert sqlite_instance.get_scores(score_type=family) == []


@pytest.mark.parametrize("difference", ["content", "piece", "category"])
async def test_rejects_non_comparable_results_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, difference: str
) -> None:
    wrapper, primary, fallback = pair
    primary_score = _score(family=family, value=None)
    fallback_score = _score(family=family, value="0.8" if family == "float_scale" else "True")
    if difference == "content":
        fallback_score.scorable = ContentScorable(value="different evidence")
    elif difference == "piece":
        first, second = uuid.uuid4(), uuid.uuid4()
        primary_score.scorable = fallback_score.scorable = MessageScorable(message_piece_ids=(first, second))
        primary_score.message_piece_id, fallback_score.message_piece_id = first, second
    else:
        fallback_score.score_category = ["different criterion"]
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[primary_score]),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=[fallback_score]),
        pytest.raises(RuntimeError, match="same categories" if difference == "category" else "same evidence"),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"))


async def test_rejects_wrong_result_family_async(pair: tuple[Scorer, Scorer, Scorer]) -> None:
    wrapper, primary, fallback = pair
    wrong_family = Score(
        score_type="unknown", status=ScoreStatus.UNDETERMINED, scorable=ContentScorable(value="evidence")
    )
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[wrong_family]),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock) as fallback_call,
        pytest.raises(RuntimeError, match="result from each child"),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"))
    fallback_call.assert_not_awaited()


def test_rejects_wrong_family_and_reused_child() -> None:
    float_child = _FloatScorer()
    bool_child = _TrueFalseScorer()
    for wrapper_type, child, other in (
        (FloatScaleFallbackScorer, float_child, bool_child),
        (TrueFalseFallbackScorer, bool_child, float_child),
    ):
        with pytest.raises(ValueError, match="Both scorers must be"):
            wrapper_type(scorer=child, fallback_scorer=other)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Both scorers must be"):
            wrapper_type(scorer=other, fallback_scorer=child)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="different scorer"):
            wrapper_type(scorer=child, fallback_scorer=child)  # type: ignore[arg-type]


@pytest.mark.parametrize("reverse", [False, True])
def test_rejects_different_condition_capabilities(*, family: ScoreType, reverse: bool) -> None:
    if family == "float_scale":
        float_children = [_FloatScorer(), _ObjectiveFloatScorer()]
        if reverse:
            float_children.reverse()
        with pytest.raises(ValueError, match="same condition types"):
            FloatScaleFallbackScorer(scorer=float_children[0], fallback_scorer=float_children[1])
    else:
        bool_children = [_TrueFalseScorer(), _ObjectiveTrueFalseScorer()]
        if reverse:
            bool_children.reverse()
        with pytest.raises(ValueError, match="same condition types"):
            TrueFalseFallbackScorer(scorer=bool_children[0], fallback_scorer=bool_children[1])


async def test_normalizes_objective_and_forwards_expectation_async(
    *, objective_pair: tuple[Scorer, Scorer, Scorer], family: ScoreType, sqlite_instance: MemoryInterface
) -> None:
    wrapper, primary, fallback = objective_pair
    expectation = ScoringExpectation(objective="criterion")
    normalized = ScoringExpectation(objective="criterion", conditions=(MatchesObjective(),))
    scorable = ContentScorable(value="evidence")
    with (
        patch.object(
            primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[_score(family=family, value=None)]
        ) as primary_call,
        patch.object(
            fallback,
            "_score_scorable_async",
            new_callable=AsyncMock,
            return_value=[_score(family=family, value="0.8" if family == "float_scale" else "True")],
        ) as fallback_call,
    ):
        result = (await wrapper.score_async(scorable=scorable, expectation=expectation))[0]

    assert wrapper.get_condition_types() == frozenset({MatchesObjective})
    for call in (primary_call, fallback_call):
        assert call.call_args.kwargs == {"scorable": scorable, "expectation": normalized}
    assert result.scored_expectation == normalized
    assert sqlite_instance.get_scores(score_type=family)[0].scored_expectation == normalized


async def test_preflight_validates_fallback_before_primary_execution_async(
    *, pair: tuple[Scorer, Scorer, Scorer]
) -> None:
    wrapper, primary, fallback = pair
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock) as primary_call,
        patch.object(fallback, "_validate_expectation", side_effect=ValueError("invalid fallback configuration")),
        pytest.raises(ValueError, match="invalid fallback configuration"),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"))
    primary_call.assert_not_awaited()


@pytest.mark.parametrize(
    "expectation",
    [
        None,
        ScoringExpectation(conditions=(MatchesObjective(), MatchesObjective())),
        ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="search"),)),)),
    ],
)
async def test_invalid_conditions_fail_before_scoring_async(
    *, objective_pair: tuple[Scorer, Scorer, Scorer], expectation: ScoringExpectation | None
) -> None:
    wrapper, primary, fallback = objective_pair
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock) as primary_call,
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock) as fallback_call,
        pytest.raises(ValueError),
    ):
        await wrapper.score_async(scorable=ContentScorable(value="evidence"), expectation=expectation)
    primary_call.assert_not_awaited()
    fallback_call.assert_not_awaited()


async def test_nested_composition_routes_only_supported_conditions_async(
    *, objective_pair: tuple[Scorer, Scorer, Scorer], family: ScoreType
) -> None:
    wrapper, primary, fallback = objective_pair
    if isinstance(wrapper, FloatScaleScorer):
        wrapper = FloatScaleThresholdScorer(scorer=wrapper, threshold=0.5)
    assert isinstance(wrapper, TrueFalseScorer)
    sibling = _ToolsScorer()
    tools = ToolsCalled(tools=(ToolCallRequirement(name="search"),))
    expectation = ScoringExpectation(objective="criterion", conditions=(MatchesObjective(), tools))
    composite = TrueFalseCompositeScorer(scorers=[wrapper, sibling], aggregator=TrueFalseScoreAggregator.AND)
    with (
        patch.object(
            primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[_score(family=family, value=None)]
        ) as primary_call,
        patch.object(
            fallback,
            "_score_scorable_async",
            new_callable=AsyncMock,
            return_value=[_score(family=family, value="0.8" if family == "float_scale" else "True")],
        ) as fallback_call,
        patch.object(
            sibling,
            "_score_scorable_async",
            new_callable=AsyncMock,
            return_value=[_score(family="true_false", value="True")],
        ) as sibling_call,
    ):
        result = (await composite.score_async(scorable=ContentScorable(value="evidence"), expectation=expectation))[0]
    assert result.get_value() is True
    assert result.scored_expectation == expectation
    for call in (primary_call, fallback_call):
        assert call.call_args.kwargs["expectation"] == ScoringExpectation(
            objective="criterion", conditions=(MatchesObjective(),)
        )
    assert sibling_call.call_args.kwargs["expectation"] == ScoringExpectation(
        objective="criterion", conditions=(tools,)
    )


def test_identifier_preserves_child_order_and_chat_target_selection(pair: tuple[Scorer, Scorer, Scorer]) -> None:
    wrapper, primary, fallback = pair
    identifiers = [ComponentIdentifier(class_name=name, class_module=__name__) for name in ("Primary", "Fallback")]
    target = MockPromptTarget()
    with (
        patch.object(primary, "get_identifier", return_value=identifiers[0]),
        patch.object(fallback, "get_identifier", return_value=identifiers[1]),
    ):
        assert wrapper.get_identifier().children["sub_scorers"] == identifiers
    with (
        patch.object(primary, "get_chat_target", return_value=None),
        patch.object(fallback, "get_chat_target", return_value=target),
    ):
        assert wrapper.get_chat_target() is target
    with (
        patch.object(primary, "get_chat_target", return_value=target),
        patch.object(fallback, "get_chat_target") as fallback_target,
    ):
        assert wrapper.get_chat_target() is target
        fallback_target.assert_not_called()


def test_registry_discovers_and_builds_typed_wrappers(pair: tuple[Scorer, Scorer, Scorer]) -> None:
    wrapper, primary, fallback = pair
    registry = ScorerRegistry()
    assert inspect.isabstract(_FallbackScorer)
    assert "_FallbackScorer" not in registry.get_class_names()
    assert registry.get_class(type(wrapper).__name__) is type(wrapper)
    built = registry.create_instance(type(wrapper).__name__, scorer=primary, fallback_scorer=fallback)
    assert type(built) is type(wrapper)
    assert built.get_identifier() == wrapper.get_identifier()


async def test_observation_links_are_merged_without_mutating_children_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType
) -> None:
    wrapper, primary, fallback = pair
    primary_score = _score(family=family, value=None)
    fallback_score = _score(family=family, value="0.8" if family == "float_scale" else "True")
    first, shared, last = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    primary_score.observation_ids = [first, shared]
    fallback_score.observation_ids = [shared, last]
    with (
        patch.object(primary, "_score_scorable_async", new_callable=AsyncMock, return_value=[primary_score]),
        patch.object(fallback, "_score_scorable_async", new_callable=AsyncMock, return_value=[fallback_score]),
    ):
        result = (await wrapper._score_nested_async(scorable=ContentScorable(value="evidence"), expectation=None))[0]
    assert result.observation_ids == [first, shared, last]
    assert primary_score.observation_ids == [first, shared]
    assert fallback_score.observation_ids == [shared, last]


async def test_nested_fallback_preserves_inner_metadata_async(
    *, pair: tuple[Scorer, Scorer, Scorer], family: ScoreType
) -> None:
    inner, primary, fallback = pair
    outer: Scorer
    last: Scorer
    if isinstance(inner, FloatScaleScorer):
        last = _FloatScorer()
        outer = FloatScaleFallbackScorer(scorer=inner, fallback_scorer=last)
    else:
        assert isinstance(inner, TrueFalseScorer)
        last = _TrueFalseScorer()
        outer = TrueFalseFallbackScorer(scorer=inner, fallback_scorer=last)
    with (
        patch.object(
            primary,
            "_score_scorable_async",
            new_callable=AsyncMock,
            return_value=[_score(family=family, value=None, metadata={"model": "first"})],
        ),
        patch.object(
            fallback,
            "_score_scorable_async",
            new_callable=AsyncMock,
            return_value=[_score(family=family, value="0.8" if family == "float_scale" else "True")],
        ),
        patch.object(last, "_score_scorable_async", new_callable=AsyncMock) as last_call,
    ):
        result = (await outer.score_async(scorable=ContentScorable(value="evidence")))[0]
    last_call.assert_not_awaited()
    assert result.score_metadata["resolved_by"] == "primary"
    assert result.score_metadata["primary.resolved_by"] == "fallback"
    assert result.score_metadata["primary.primary.model"] == "first"


@pytest.mark.parametrize("stored_message", [False, True])
async def test_real_judges_persist_both_observations_and_only_wrapper_score_async(
    *, sqlite_instance: MemoryInterface, family: ScoreType, stored_message: bool
) -> None:
    primary_target, fallback_target = MockPromptTarget(), MockPromptTarget()
    primary: MessageScorer
    fallback: MessageScorer
    wrapper: Scorer
    if family == "float_scale":
        scale = LikertScale(
            category="criterion",
            entries=(
                LikertScaleEntry(score_value=0, description="absent"),
                LikertScaleEntry(score_value=1, description="present"),
            ),
        )
        primary = SelfAskLikertScorer.from_likert_scale(chat_target=primary_target, likert_scale=scale)
        fallback = SelfAskLikertScorer.from_likert_scale(chat_target=fallback_target, likert_scale=scale)
        wrapper = FloatScaleFallbackScorer(scorer=primary, fallback_scorer=fallback)
    else:
        primary = SelfAskTrueFalseScorer(chat_target=primary_target)
        fallback = SelfAskTrueFalseScorer(chat_target=fallback_target)
        wrapper = TrueFalseFallbackScorer(scorer=primary, fallback_scorer=fallback)
    primary.raise_if_scorer_blocks = False
    blocked = MessagePiece(
        role="assistant", original_value="", original_value_data_type="error", response_error="blocked"
    ).to_message()
    value = "1" if family == "float_scale" else "true"
    judged = MessagePiece(
        role="assistant",
        original_value=(
            f'{{"score_value":"{value}","description":"matched","rationale":"second opinion","metadata":"test"}}'
        ),
    ).to_message()
    expectation = ScoringExpectation(objective="criterion")
    scorable: Scorable = ContentScorable(value="evidence")
    if stored_message:
        message = store_message(MessagePiece(role="assistant", original_value="evidence").to_message())
        scorable = MessageScorable.from_message(message)
    with (
        patch.object(primary_target, "send_prompt_async", new_callable=AsyncMock, return_value=[blocked]),
        patch.object(fallback_target, "send_prompt_async", new_callable=AsyncMock, return_value=[judged]),
    ):
        result = (await wrapper.score_async(scorable=scorable, expectation=expectation))[0]
    stored = sqlite_instance.get_scores(score_type=family)
    assert len(stored) == 1
    assert stored[0].id == result.id
    assert stored[0].get_value() == (1.0 if family == "float_scale" else True)
    assert stored[0].scored_expectation == expectation
    assert stored[0].score_metadata == result.score_metadata
    assert stored[0].scorer_class_identifier == wrapper.get_identifier()
    assert len(result.observation_ids) == 2
    observations = sqlite_instance.get_observations(observation_ids=result.observation_ids)
    assert len(observations) == 2
    assert {observation.scorable for observation in observations} == {result.scorable}
    if isinstance(scorable, MessageScorable):
        assert stored[0].message_piece_id == scorable.message_piece_ids[0]
