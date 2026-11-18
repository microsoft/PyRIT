# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ASSERT analytics over saved results and the exact policy each score judged."""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.analytics import NotAssertPolicyRunError, compute_assert_policy_metrics_async, compute_scenario_statistics
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AssertBinaryRate,
    AssertCategoryJudgment,
    AssertDimensionResult,
    AssertGroupMetadata,
    AssertNodeJudgment,
    AssertPolicyMetrics,
    AssertSkipReason,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    MessageScorable,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
    Score,
    ScoreStatus,
    ScoringExpectation,
    ViolatesAssertPolicy,
    construct_response_from_request,
)
from pyrit.score import AssertJudgeScorer, AssertJudgmentMode, TrueFalseInverterScorer
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget, get_sample_conversations, make_scenario_identifier

pytestmark = pytest.mark.usefixtures("patch_central_database")

_MODULE = "pyrit.scenario.scenarios.foundry.assert_policy"


@pytest.fixture
def policy() -> ViolatesAssertPolicy:
    generated = make_assert_result(config=make_assert_config(group_count=1))
    metadata = AssertGroupMetadata.from_metadata(generated.seed_groups[0].objective.metadata)
    return ViolatesAssertPolicy(taxonomy=metadata.taxonomy, target_instructions="You help.")


@pytest.fixture
def legacy_judge_identifier() -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name="_AssertJudgedScorer",
        class_module=_MODULE,
        params={"skipped_target_replies": [reason.value for reason in AssertSkipReason]},
        children={
            "sub_scorers": [ComponentIdentifier(class_name="TrueFalseConversationScorer", class_module="pyrit.score")]
        },
    )


def _score(
    policy: ViolatesAssertPolicy | None,
    *,
    dimension: str | None = "policy_violation",
    value: bool | None = True,
    nodes: Sequence[tuple[str, bool]] | None = None,
    piece_id: uuid.UUID | None = None,
    not_applicable: bool = False,
    skip: AssertSkipReason | None = None,
    grade: int | str | None = None,
    scale: tuple[int, ...] | tuple[str, ...] | None = None,
) -> Score:
    metadata = AssertDimensionResult(
        dimension=dimension, not_applicable=not_applicable, skip_reason=skip, grade=grade, scale=scale
    ).to_metadata()
    if nodes is not None:
        metadata = AssertCategoryJudgment(
            node_judgments=tuple(
                AssertNodeJudgment(node_name=name, violated=violated, confidence="high", reasoning="Relevant.")
                for name, violated in nodes
            ),
            original_score_value=bool(value),
        ).merge_into(metadata)
    return Score(
        score_value=str(value).lower() if value is not None else None,
        score_type="true_false",
        score_category=[dimension or "policy_violation"],
        score_metadata=metadata,
        status=ScoreStatus.COMPLETE if value is not None else ScoreStatus.UNDETERMINED,
        message_piece_id=piece_id or uuid.uuid4(),
        scorer_class_identifier=ComponentIdentifier(
            class_name="AssertJudgeScorer", class_module="pyrit.score.true_false.assert_judge_scorer"
        ),
        scored_expectation=ScoringExpectation(objective="Judge this conversation", conditions=(policy,))
        if policy is not None
        else None,
    )


def _attack(
    score: Score | None,
    *,
    objective: str | None = None,
    timestamp: datetime | None = None,
    attribution_data: dict[str, str | int] | None = None,
) -> AttackResult:
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective or str(uuid.uuid4()),
        outcome=AttackOutcome.SUCCESS,
        automated_score=score,
        timestamp=timestamp or datetime.now(tz=UTC),
        attribution_data=attribution_data,
    )


def _run(
    results: dict[str, list[AttackResult]],
    *,
    module: str = _MODULE,
    registry_name: str | None = None,
    plan: ScenarioRunPlan | None = None,
) -> ScenarioResult:
    if plan is None and registry_name is not None:
        plan = ScenarioRunPlan(scenario_registry_name=registry_name, atomic_groups=[], seed_groups=[])
    return ScenarioResult(
        scenario_identifier=make_scenario_identifier(scenario_name="AssertPolicy", scenario_module=module),
        attack_results=results,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")} if plan is not None else {},
    )


async def _metrics(
    results: dict[str, list[AttackResult]],
    *,
    extra: Sequence[Score] = (),
    plan: ScenarioRunPlan | None = None,
) -> AssertPolicyMetrics:
    scores = [result.automated_score for group in results.values() for result in group if result.automated_score]
    memory = MagicMock(spec=MemoryInterface)
    memory.get_prompt_scores_async = AsyncMock(return_value=[*scores, *extra])
    metrics = await compute_assert_policy_metrics_async(_run(results, plan=plan), memory=memory)
    memory.get_seed_groups_async.assert_not_called()
    return metrics


def _counts(rate: AssertBinaryRate | None) -> tuple[int, int, int] | None:
    return None if rate is None else (rate.flagged_count, rate.applicable_count, rate.not_applicable_count)


async def test_headlines_follow_relevant_categories_not_generated_assignments(policy: ViolatesAssertPolicy) -> None:
    scores = [
        _score(policy, value=False, nodes=[("public_policy", False)]),
        _score(policy, nodes=[("public_policy", True)]),
        _score(policy, nodes=[("private_disclosure", True), ("public_policy", False)]),
        _score(policy, value=False, nodes=[]),
    ]
    result = await _metrics({"prompt_sending": [_attack(score) for score in scores]})
    technique = result.techniques["prompt_sending"]

    assert technique.total == technique.scored_total == 4
    assert _counts(technique.policy_violation_on_not_permissible) == (1, 1, 3)
    assert _counts(technique.policy_violation_on_permissible) == (1, 3, 1)
    assert _counts(technique.dimensions["policy_violation"]) == (2, 4, 0)
    categories = {category.name: category for category in technique.categories}
    assert _counts(categories["public_policy"].node_violation) == (1, 3, 0)
    assert _counts(categories["private_disclosure"].node_violation) == (1, 1, 0)
    assert categories["public_policy"].permissible
    assert not categories["private_disclosure"].permissible
    assert result.judgment_mode == "category_validated"


async def test_saved_policy_survives_without_seed_datasets(
    policy: ViolatesAssertPolicy, sqlite_instance: SQLiteMemory
) -> None:
    pieces = [piece for message in get_sample_conversations() for piece in message.message_pieces]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)
    score = _score(policy, piece_id=pieces[0].id, nodes=[("private_disclosure", True)])
    await sqlite_instance.add_scores_to_memory_async(scores=[score])
    (stored,) = await sqlite_instance.get_prompt_scores_async(prompt_ids=[str(score.message_piece_id)])
    assert stored.scored_expectation == score.scored_expectation
    with patch.object(sqlite_instance, "get_seed_groups_async", side_effect=AssertionError("No current seed lookup")):
        metrics = await compute_assert_policy_metrics_async(_run({"t": [_attack(stored)]}), memory=sqlite_instance)

    assert metrics.techniques["t"].unresolved_policy == 0
    assert _counts(metrics.techniques["t"].policy_violation_on_not_permissible) == (1, 1, 0)


@pytest.mark.parametrize("difference", ["taxonomy", "application_context", "target_instructions"])
async def test_category_rates_keep_distinct_recorded_policies_apart(
    policy: ViolatesAssertPolicy, difference: str
) -> None:
    if difference == "taxonomy":
        taxonomy = policy.taxonomy.model_copy(
            update={
                "behavior_categories": tuple(
                    category.model_copy(update={"permissible": not category.permissible})
                    for category in policy.taxonomy.behavior_categories
                )
            }
        )
        other = policy.model_copy(update={"taxonomy": taxonomy})
    else:
        other = policy.model_copy(update={difference: "Another application"})
    scores = [_score(item, nodes=[("public_policy", True)]) for item in (policy, other)]
    technique = (await _metrics({"t": [_attack(score) for score in scores]})).techniques["t"]

    assert len({category.policy_id for category in technique.categories}) == 2
    assert sum(category.node_violation.applicable_count for category in technique.categories) == 2


async def test_duplicate_objectives_keep_each_saved_policy_and_named_technique(policy: ViolatesAssertPolicy) -> None:
    other = policy.model_copy(update={"application_context": "Another application"})
    attacks = [
        _attack(
            _score(item, nodes=[("public_policy", True)]),
            objective="Same objective",
            attribution_data={"seed_group_id": str(index)},
        )
        for index, item in enumerate((policy, other))
    ]
    name = f"my__technique__{'a' * 64}"
    technique = (await _metrics({name: attacks})).techniques["my__technique"]
    assert technique.total == 2
    assert technique.unresolved_policy == 0
    assert len({category.policy_id for category in technique.categories}) == 2


async def test_missing_policy_is_reported_without_guessing_from_scorer_or_seeds() -> None:
    score = _score(None, nodes=[("public_policy", True)])
    technique = (await _metrics({"t": [_attack(score)]})).techniques["t"]
    assert technique.unresolved_policy == 1
    assert technique.policy_violation_on_permissible is None
    assert technique.categories == []
    assert _counts(technique.dimensions["policy_violation"]) == (1, 1, 0)


async def test_unmarked_supplied_objective_is_not_a_policy_judge(policy: ViolatesAssertPolicy) -> None:
    score = _score(policy, dimension=None)
    technique = (await _metrics({"t": [_attack(score)]})).techniques["t"]
    assert list(technique.dimensions) == ["objective"]
    assert technique.categories == []


async def test_legacy_native_judge_keeps_owned_dimensions_without_a_recorded_policy(
    legacy_judge_identifier: ComponentIdentifier,
) -> None:
    objective = _score(None).model_copy(update={"scorer_class_identifier": legacy_judge_identifier})
    auxiliary = _score(None, dimension="overrefusal", value=False, piece_id=objective.message_piece_id).model_copy(
        update={"scorer_class_identifier": legacy_judge_identifier}
    )
    technique = (await _metrics({"t": [_attack(objective)]}, extra=[auxiliary])).techniques["t"]
    assert _counts(technique.dimensions["policy_violation"]) == (1, 1, 0)
    assert _counts(technique.dimensions["overrefusal"]) == (0, 1, 0)
    assert "objective" not in technique.dimensions
    assert technique.unresolved_policy == 1
    assert technique.policy_violation_on_permissible is None


@pytest.mark.parametrize("reason", list(AssertSkipReason))
async def test_legacy_native_judge_keeps_owned_target_skips(
    legacy_judge_identifier: ComponentIdentifier, reason: AssertSkipReason
) -> None:
    objective = _score(None, value=None, skip=reason).model_copy(
        update={"scorer_class_identifier": legacy_judge_identifier}
    )
    technique = (await _metrics({"t": [_attack(objective)]})).techniques["t"]
    assert getattr(technique.not_judged, reason.value) == 1
    assert technique.not_judged.judge_no_verdict == 0


@pytest.mark.parametrize("mode", [AssertJudgmentMode.DIRECT, AssertJudgmentMode.CATEGORY_VALIDATED])
async def test_transformed_objective_keeps_the_original_persisted_judge_dimension(
    policy: ViolatesAssertPolicy, sqlite_instance: SQLiteMemory, mode: AssertJudgmentMode
) -> None:
    conversation_id = str(uuid.uuid4())
    request = MessagePiece(role="user", original_value="Explain returns.", conversation_id=conversation_id)
    reply = MessagePiece(role="assistant", original_value="Returns are not allowed.", conversation_id=conversation_id)
    await sqlite_instance.add_message_to_memory_async(request=request.to_message())
    await sqlite_instance.add_message_to_memory_async(request=reply.to_message())
    judge = MockPromptTarget()

    async def judge_async(*, normalized_conversation: list[Message]) -> list[Message]:
        payload: dict[str, object] = {
            "score_value": True,
            "description": "Failure observed.",
            "rationale": "A relevant category was violated.",
            "metadata": "Recorded evidence.",
        }
        if mode == AssertJudgmentMode.CATEGORY_VALIDATED:
            payload["node_judgments"] = [
                {"node_name": "public_policy", "violated": True, "confidence": "high", "reasoning": "Relevant."}
            ]
        return [
            construct_response_from_request(
                request=normalized_conversation[-1].get_piece(), response_text_pieces=[json.dumps(payload)]
            )
        ]

    scorer = TrueFalseInverterScorer(scorer=AssertJudgeScorer(chat_target=judge, judgment_mode=mode))
    with patch.object(judge, "_send_prompt_to_target_async", side_effect=judge_async):
        (objective,) = await scorer.score_async(
            scorable=MessageScorable.from_message(reply.to_message()),
            expectation=ScoringExpectation(conditions=(policy,)),
        )
    assert objective.get_value() is False
    result = AttackResult(
        conversation_id=conversation_id,
        objective="Explain returns.",
        outcome=AttackOutcome.FAILURE,
        automated_score=objective,
    )
    metrics = await compute_assert_policy_metrics_async(_run({"t": [result]}), memory=sqlite_instance)
    technique = metrics.techniques["t"]
    assert _counts(technique.dimensions["objective"]) == (0, 1, 0)
    assert _counts(technique.dimensions["policy_violation"]) == (1, 1, 0)
    if mode == AssertJudgmentMode.CATEGORY_VALIDATED:
        assert _counts(technique.policy_violation_on_permissible) == (1, 1, 0)


async def test_auxiliary_policy_verdict_survives_an_undetermined_objective(policy: ViolatesAssertPolicy) -> None:
    objective = _score(policy, dimension="quality", value=None, not_applicable=True)
    policy_score = _score(policy, piece_id=objective.message_piece_id, nodes=[("private_disclosure", True)])
    technique = (await _metrics({"t": [_attack(objective)]}, extra=[policy_score])).techniques["t"]
    assert technique.scored_total == 1
    assert _counts(technique.dimensions["quality"]) == (0, 0, 1)
    assert _counts(technique.policy_violation_on_not_permissible) == (1, 1, 0)


async def test_each_dimension_accounts_for_applicable_na_and_no_verdict(policy: ViolatesAssertPolicy) -> None:
    objectives = [_score(policy, nodes=[("public_policy", True)]) for _ in range(3)]
    auxiliary = [
        _score(policy, dimension="quality", piece_id=objectives[0].message_piece_id),
        _score(policy, dimension="quality", piece_id=objectives[1].message_piece_id, value=None, not_applicable=True),
        _score(policy, dimension="quality", piece_id=objectives[2].message_piece_id, value=None),
    ]
    technique = (await _metrics({"t": [_attack(score) for score in objectives]}, extra=auxiliary)).techniques["t"]
    assert _counts(technique.dimensions["quality"]) == (1, 1, 1)
    assert technique.dimension_no_verdict["quality"] == 1
    for name, rate in technique.dimensions.items():
        assert (
            rate.applicable_count + rate.not_applicable_count + technique.dimension_no_verdict[name]
            == technique.scored_total
        )


@pytest.mark.parametrize("skip", list(AssertSkipReason))
async def test_target_skips_exclude_the_conversation(policy: ViolatesAssertPolicy, skip: AssertSkipReason) -> None:
    score = _score(policy, value=None, skip=skip)
    technique = (await _metrics({"t": [_attack(score)]})).techniques["t"]
    assert technique.scored_total == 0
    assert getattr(technique.not_judged, skip.value) == 1


@pytest.mark.parametrize("kind", ["attack_error", "not_scored", "judge_no_verdict"])
async def test_unjudged_conversations_are_counted_not_rated(policy: ViolatesAssertPolicy, kind: str) -> None:
    score = _score(policy, value=None) if kind != "not_scored" else None
    attack = _attack(score)
    if kind == "attack_error":
        attack.outcome = AttackOutcome.ERROR
    technique = (await _metrics({"t": [attack]})).techniques["t"]
    assert technique.total == 1
    assert technique.scored_total == 0
    assert getattr(technique.not_judged, kind) == 1


@pytest.mark.parametrize("reverse", [False, True])
async def test_rejudged_dimensions_break_timestamp_ties_by_id(policy: ViolatesAssertPolicy, reverse: bool) -> None:
    objective = _score(policy)
    first = _score(policy, dimension="quality", value=False, piece_id=objective.message_piece_id)
    first.id = uuid.UUID(int=0)
    second = first.model_copy(update={"id": uuid.UUID(int=1), "score_value": "true"})
    scores = [first, second]
    technique = (await _metrics({"t": [_attack(objective)]}, extra=scores[::-1] if reverse else scores)).techniques["t"]
    assert _counts(technique.dimensions["quality"]) == (1, 1, 0)


@pytest.mark.parametrize(
    ("scale", "values", "median"),
    [
        ((1, 2, 3), (3, 1, 2, 3), 2.5),
        (("good", "poor", "bad"), ("bad", "good", "poor", "bad"), "poor"),
    ],
)
async def test_grade_counts_and_median_follow_each_declared_scale(
    policy: ViolatesAssertPolicy,
    scale: tuple[int, ...] | tuple[str, ...],
    values: tuple[int | str, ...],
    median: int | float | str,
) -> None:
    scores = [_score(policy, dimension="quality", grade=value, scale=scale) for value in values]
    technique = (await _metrics({"t": [_attack(score) for score in scores]})).techniques["t"]
    (distribution,) = technique.dimension_grades["quality"]
    assert distribution.scale == list(scale)
    assert distribution.counts == {str(grade): values.count(grade) for grade in scale}
    assert distribution.median == median


@pytest.mark.parametrize(
    ("scales", "values"),
    [
        ((("good", "bad"), ("fine", "awful")), ("bad", "fine")),
        (((1, 2), (3, 4)), (2, 3)),
        ((("good", "bad"), ("bad", "good")), ("bad", "good")),
    ],
)
async def test_incompatible_scales_have_separate_complete_distributions(
    policy: ViolatesAssertPolicy,
    scales: tuple[tuple[int, ...] | tuple[str, ...], ...],
    values: tuple[int | str, ...],
) -> None:
    scores = [
        _score(policy, dimension="quality", grade=value, scale=scale)
        for value, scale in zip(values, scales, strict=True)
    ]
    technique = (await _metrics({"t": [_attack(score) for score in scores]})).techniques["t"]
    distributions = technique.dimension_grades["quality"]
    assert [distribution.scale for distribution in distributions] == [list(scale) for scale in scales]
    assert [distribution.median for distribution in distributions] == list(values)
    assert [sum(distribution.counts.values()) for distribution in distributions] == [1, 1]


async def test_all_na_grades_preserve_scale_and_exclusions(policy: ViolatesAssertPolicy) -> None:
    score = _score(policy, dimension="quality", value=None, not_applicable=True, scale=(1, 2))
    technique = (await _metrics({"t": [_attack(score)]})).techniques["t"]
    assert _counts(technique.dimensions["quality"]) == (0, 0, 1)
    (distribution,) = technique.dimension_grades["quality"]
    assert distribution.counts == {"1": 0, "2": 0}
    assert distribution.median is None


@pytest.mark.parametrize(
    ("flagged", "total", "lower", "upper"),
    [
        (3, 10, 0.12686685286055388, 0.5583218666642386),
        (0, 7, 0.0, 0.2787984782647891),
        (7, 7, 0.7212015217352108, 1.0),
    ],
)
async def test_rates_match_assert_wilson_intervals(
    policy: ViolatesAssertPolicy, flagged: int, total: int, lower: float, upper: float
) -> None:
    scores = [_score(policy, value=index < flagged) for index in range(total)]
    rate = (await _metrics({"t": [_attack(score) for score in scores]})).techniques["t"].dimensions["policy_violation"]
    assert rate.rate == flagged / total
    assert rate.ci_lower == pytest.approx(lower)
    assert rate.ci_upper == pytest.approx(upper)
    assert rate.sufficient_support


async def test_no_relevant_categories_has_no_rate_and_low_support(policy: ViolatesAssertPolicy) -> None:
    technique = (await _metrics({"t": [_attack(_score(policy, value=False, nodes=[]))]})).techniques["t"]
    rate = technique.policy_violation_on_permissible
    assert rate is not None
    assert rate.rate is rate.ci_lower is rate.ci_upper is None
    assert not rate.sufficient_support
    assert rate.not_applicable_count == 1


async def test_latest_top_level_attempts_match_scenario_statistics(policy: ViolatesAssertPolicy) -> None:
    old = _attack(_score(policy, value=False), objective="Same")
    latest = _attack(_score(policy), objective="Same", timestamp=old.timestamp + timedelta(seconds=1))
    child = _attack(
        _score(policy, value=False),
        objective="Same",
        timestamp=old.timestamp + timedelta(seconds=2),
        attribution_data={"attempt_index": 1},
    )
    run = _run({"t": [old, latest, child]})
    metrics = await _metrics(run.attack_results)
    assert metrics.total == compute_scenario_statistics(run).overall.completed == 1
    assert _counts(metrics.techniques["t"].dimensions["policy_violation"]) == (1, 1, 0)


async def test_plan_separates_baseline_and_excludes_unplanned_units(policy: ViolatesAssertPolicy) -> None:
    plan = ScenarioRunPlan(
        scenario_registry_name="foundry.assert_policy",
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed", objective="Same", objective_sha256="hash")],
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=name,
                atomic_attack_name=name,
                display_group=name,
                technique_name="my__technique" if name == "attack" else None,
                technique_eval_hash="config",
                seed_group_ids=["seed"],
                kind=ScenarioRunPlanGroupKind.BASELINE if name == "baseline" else ScenarioRunPlanGroupKind.ATTACK,
            )
            for name in ("baseline", "attack")
        ],
    )
    results = {
        name: [
            _attack(
                _score(policy),
                objective="Same",
                attribution_data={"seed_group_id": "seed", "parent_eval_hash": "config"},
            )
        ]
        for name in ("baseline", "attack", "unplanned")
    }
    metrics = await _metrics(results, plan=plan)
    assert list(metrics.techniques) == ["baseline", "my__technique"]
    assert metrics.total == 2


async def test_direct_and_mixed_judgments_are_explicit(policy: ViolatesAssertPolicy) -> None:
    direct = _attack(_score(policy))
    metrics = await _metrics({"t": [direct]})
    assert metrics.judgment_mode == "direct"
    assert metrics.split_unavailable_reason == "node_judgments_unavailable"
    metrics = await _metrics({"t": [direct, _attack(_score(policy, nodes=[("public_policy", True)]))]})
    assert metrics.judgment_mode == "mixed"


@pytest.mark.parametrize(
    "metadata",
    [
        {"assert_not_applicable": "maybe"},
        {"assert_node_judgments": "not json"},
        {"assert_judgment_mode": "category_validated", "assert_node_judgments": "[]"},
        {"assert_grade": '"great"', "assert_scale": '["good", "poor"]'},
    ],
)
async def test_malformed_judgment_metadata_is_not_silently_discarded(
    policy: ViolatesAssertPolicy, metadata: dict[str, str]
) -> None:
    score = _score(policy)
    score.score_metadata = {**(score.score_metadata or {}), **metadata}
    with pytest.raises(ValueError, match="malformed ASSERT"):
        await _metrics({"t": [_attack(score)]})


async def test_policy_verdict_must_agree_with_category_judgments(policy: ViolatesAssertPolicy) -> None:
    score = _score(policy, nodes=[("public_policy", False)])
    with pytest.raises(ValueError, match="disagrees with its ASSERT category judgments"):
        await _metrics({"t": [_attack(score)]})


async def test_multiple_recorded_policies_are_rejected_instead_of_selected_arbitrarily(
    policy: ViolatesAssertPolicy,
) -> None:
    score = _score(policy)
    score = score.model_copy(
        update={
            "scored_expectation": ScoringExpectation(
                conditions=(policy, policy.model_copy(update={"application_context": "Another application"}))
            )
        }
    )
    with pytest.raises(ValueError, match="multiple ASSERT policies"):
        await _metrics({"t": [_attack(score)]})


@pytest.mark.parametrize(
    ("module", "registry_name"), [("custom.scenarios", None), (_MODULE, "custom.scan"), ("", None)]
)
async def test_other_scenarios_are_rejected(module: str, registry_name: str | None) -> None:
    with pytest.raises(NotAssertPolicyRunError, match="came from"):
        await compute_assert_policy_metrics_async(_run({}, module=module, registry_name=registry_name))


@pytest.mark.parametrize(
    ("module", "registry_name"),
    [(_MODULE, None), ("pyrit.scenario.foundry.assert_policy", None), ("custom.module", "foundry.assert_policy")],
)
async def test_empty_assert_run_roundtrips_with_explicit_no_data(module: str, registry_name: str | None) -> None:
    metrics = await compute_assert_policy_metrics_async(_run({}, module=module, registry_name=registry_name))
    assert metrics.total == metrics.scored_total == 0
    assert metrics.techniques == {}
    assert metrics.split_unavailable_reason == "no_judged_results"
    assert AssertPolicyMetrics.model_validate_json(metrics.model_dump_json()) == metrics
