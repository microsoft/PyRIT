# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from pyrit.analytics.paired_outcome_statistics import compute_paired_outcome_statistics
from pyrit.common.utils import to_sha256
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.compound import SequenceCompletionPolicy, SequentialAttackResult
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
    Score,
    SeedObjective,
    TargetIdentifier,
)
from pyrit.models.score import MatchesObjective, ScoringExpectation
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget, make_scenario_result

_TIME = datetime(2026, 9, 1, tzinfo=UTC)


def _plan(
    baseline_ids: tuple[str, ...] = ("a", "b", "c", "d"),
    attack_ids: tuple[str, ...] = ("a", "b", "c", "d"),
) -> ScenarioRunPlan:
    return ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="base",
                atomic_attack_name="baseline",
                display_group="Baseline",
                technique_eval_hash="base-eval",
                kind=ScenarioRunPlanGroupKind.BASELINE,
                seed_group_ids=list(baseline_ids),
            ),
            ScenarioRunPlanAtomicGroup(
                id="attack",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="attack-eval",
                kind=ScenarioRunPlanGroupKind.ATTACK,
                seed_group_ids=list(attack_ids),
            ),
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id=seed, objective=seed, objective_sha256=to_sha256(seed))
            for seed in sorted(set(baseline_ids) | set(attack_ids))
        ],
    )


def _result(
    seed: str,
    outcome: AttackOutcome,
    *,
    seconds: int = 0,
    group: str | None = None,
    attempt_index: int | None = None,
    result_id: str | None = None,
    objective: str | None = None,
) -> AttackResult:
    attribution = {"seed_group_id": seed}
    if group is not None:
        attribution["parent_eval_hash"] = f"{group}-eval"
    if attempt_index is not None:
        attribution["attempt_index"] = attempt_index
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        attack_result_id=result_id or str(uuid.uuid4()),
        objective=objective or seed,
        outcome=outcome,
        timestamp=_TIME + timedelta(seconds=seconds),
        attribution_data=attribution,
    )


def _statistics(plan: ScenarioRunPlan, attack_results: dict[str, list[AttackResult]]):
    scenario = make_scenario_result(
        attack_results=attack_results,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    return compute_paired_outcome_statistics(scenario, baseline_group_id="base", attack_group_id="attack")


@pytest.mark.usefixtures("patch_central_database")
async def test_paired_outcomes_with_plan_built_by_real_scenario() -> None:
    class PairTechnique(ScenarioTechnique):
        ATTACK = ("attack", {"attack"})
        ALL = ("all", {"all"})

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    class PairScenario(Scenario):
        BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

        def __init__(self, *, atomic_attacks: list[AtomicAttack]) -> None:
            super().__init__(
                technique_class=PairTechnique,
                default_dataset_config=DatasetConfiguration(),
                objective_scorer=SubStringScorer(substring="success"),
                version=1,
            )
            self._paired_attacks = atomic_attacks

        async def _resolve_seed_groups_by_dataset_async(self, *, apply_sampling: bool = True):
            return {}

        async def _build_atomic_attacks_async(self, *, context):
            return self._paired_attacks

    target = MockPromptTarget()
    scorer = SubStringScorer(substring="success")
    seeds = [AttackSeedGroup(seeds=[SeedObjective(value="a")])]
    baseline = build_baseline_atomic_attack(objective_target=target, objective_scorer=scorer, seed_groups=seeds)
    attack = AtomicAttack(
        atomic_attack_name="paired-attack",
        technique_name="attack",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=seeds,
    )
    scenario = PairScenario(atomic_attacks=[baseline, attack])
    scenario.set_params_from_args(args={"objective_target": target})
    await scenario.initialize_async()
    plan = scenario._build_run_plan()
    baseline_group = next(group for group in plan.atomic_groups if group.kind == ScenarioRunPlanGroupKind.BASELINE)
    attack_group = next(group for group in plan.atomic_groups if group.kind == ScenarioRunPlanGroupKind.ATTACK)
    assert baseline_group.seed_group_ids == attack_group.seed_group_ids

    saved = make_scenario_result(
        attack_results={
            baseline_group.atomic_attack_name: [_result("a", AttackOutcome.FAILURE)],
            attack_group.atomic_attack_name: [_result("a", AttackOutcome.SUCCESS)],
        },
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    for name, group in (
        (baseline_group.atomic_attack_name, baseline_group),
        (attack_group.atomic_attack_name, attack_group),
    ):
        result = saved.attack_results[name][0]
        result.attribution_data = {
            "parent_eval_hash": group.technique_eval_hash,
            "seed_group_id": group.seed_group_ids[0],
        }
    stats = compute_paired_outcome_statistics(
        saved, baseline_group_id=baseline_group.id, attack_group_id=attack_group.id
    )
    assert (stats.planned_pairs, stats.decided_pairs, stats.failure_to_success) == (1, 1, 1)


def test_paired_outcomes_show_transitions_even_when_marginal_rates_are_equal() -> None:
    plan = _plan()
    result = _statistics(
        plan,
        {
            "baseline": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.SUCCESS),
                _result("c", AttackOutcome.FAILURE),
                _result("d", AttackOutcome.FAILURE),
            ],
            "attack": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.FAILURE),
                _result("c", AttackOutcome.SUCCESS),
                _result("d", AttackOutcome.FAILURE),
            ],
        },
    )

    assert (result.failure_to_success, result.success_to_failure) == (1, 1)
    assert (result.failure_to_failure, result.success_to_success) == (1, 1)
    assert (result.planned_pairs, result.decided_pairs, result.excluded_pairs) == (4, 4, 0)
    assert result.baseline_success_rate == result.attack_success_rate == 0.5
    assert result.paired_difference == 0.0


def test_paired_outcomes_use_latest_top_level_completion_once() -> None:
    plan = _plan(("a",), ("a",))
    stats = _statistics(
        plan,
        {
            "baseline": [_result("a", AttackOutcome.FAILURE)],
            "attack": [
                _result("a", AttackOutcome.FAILURE, seconds=-3),
                _result("a", AttackOutcome.ERROR, seconds=-2),
                _result("a", AttackOutcome.SUCCESS, seconds=0),
                _result("a", AttackOutcome.FAILURE, seconds=2, attempt_index=1),
            ],
        },
    )

    assert stats.failure_to_success == 1
    assert stats.decided_pairs == 1
    assert stats.attack_errors == 0
    assert stats.paired_difference == 1.0


def test_paired_outcomes_account_for_exclusions_without_rate_denominator_drift() -> None:
    stats = _statistics(
        _plan(("a", "b", "c", "d"), ("a", "b", "c", "e")),
        {
            "baseline": [
                _result("a", AttackOutcome.ERROR),
                _result("b", AttackOutcome.UNDETERMINED),
                _result("c", AttackOutcome.FAILURE),
            ],
            "attack": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.ERROR),
                _result("e", AttackOutcome.SUCCESS),
            ],
        },
    )

    assert (stats.planned_pairs, stats.decided_pairs, stats.excluded_pairs) == (3, 0, 3)
    assert (stats.unmatched_baseline_seeds, stats.unmatched_attack_seeds) == (1, 1)
    assert (stats.missing_baseline, stats.missing_attack, stats.missing_both) == (0, 1, 0)
    assert (stats.baseline_errors, stats.attack_errors) == (1, 1)
    assert (stats.baseline_undetermined, stats.attack_undetermined) == (1, 0)
    assert stats.baseline_success_rate is stats.attack_success_rate is stats.paired_difference is None


def test_paired_outcomes_account_for_both_missing_and_one_sided_missing() -> None:
    stats = _statistics(
        _plan(("a", "b", "c"), ("a", "b", "c")),
        {"baseline": [_result("b", AttackOutcome.SUCCESS)], "attack": [_result("c", AttackOutcome.FAILURE)]},
    )

    assert stats.missing_both == 1
    assert stats.missing_attack == 1
    assert stats.missing_baseline == 1
    assert stats.excluded_pairs == 3


def test_paired_outcomes_report_unmatched_seeds_separately_from_pair_exclusions() -> None:
    stats = _statistics(
        _plan(("a", "b", "c", "d"), ("a", "b", "c")),
        {
            "baseline": [_result(seed, AttackOutcome.FAILURE) for seed in ("a", "b", "c", "d")],
            "attack": [_result(seed, AttackOutcome.SUCCESS) for seed in ("a", "b", "c")],
        },
    )

    assert (stats.planned_pairs, stats.decided_pairs, stats.excluded_pairs) == (3, 3, 0)
    assert (stats.unmatched_baseline_seeds, stats.unmatched_attack_seeds) == (1, 0)
    assert stats.failure_to_success == 3


def test_paired_outcomes_allow_empty_planned_groups() -> None:
    stats = _statistics(_plan((), ()), {})
    assert stats.planned_pairs == stats.decided_pairs == stats.excluded_pairs == 0
    assert stats.paired_difference is None
    assert stats.baseline_success_rate is None


def test_paired_outcomes_reject_disjoint_seed_groups() -> None:
    with pytest.raises(ValueError, match="no shared logical seed-group"):
        _statistics(_plan(("a",), ("b",)), {})


def test_paired_outcomes_require_valid_plan_and_explicit_kinds() -> None:
    scenario = make_scenario_result(attack_results={})
    with pytest.raises(ValueError, match="valid saved scenario run plan"):
        compute_paired_outcome_statistics(scenario, baseline_group_id="base", attack_group_id="attack")

    plan = _plan(("a",), ("a",))
    plan.atomic_groups[0].kind = None
    with pytest.raises(ValueError, match="explicit baseline and attack"):
        _statistics(plan, {})


def test_paired_outcomes_reject_ambiguous_group_configuration() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="duplicate",
            atomic_attack_name="baseline",
            display_group="Other",
            technique_eval_hash="base-eval",
            kind=ScenarioRunPlanGroupKind.BASELINE,
            seed_group_ids=["a"],
        )
    )
    with pytest.raises(ValueError, match="ambiguous name/configuration"):
        _statistics(plan, {})


def test_paired_outcomes_reject_duplicate_results() -> None:
    duplicate = _result("a", AttackOutcome.SUCCESS)
    with pytest.raises(ValueError, match="Duplicate attack result IDs"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [duplicate, duplicate]})


def test_paired_outcomes_reject_objective_drift_in_attributed_result() -> None:
    with pytest.raises(ValueError, match="objective disagrees"):
        _statistics(
            _plan(("a",), ("a",)),
            {"attack": [_result("a", AttackOutcome.SUCCESS, objective="a different objective")]},
        )


def test_paired_outcomes_reject_ambiguous_attempt_attribution() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="other",
            atomic_attack_name="attack",
            display_group="Another config",
            technique_eval_hash="other-eval",
            kind=ScenarioRunPlanGroupKind.ATTACK,
            seed_group_ids=["a"],
        )
    )
    with pytest.raises(ValueError, match="cannot distinguish multiple planned configurations"):
        _statistics(plan, {"attack": [_result("a", AttackOutcome.SUCCESS)]})


def _score(scorer: str, *, expectation: ScoringExpectation | None = None) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        scorer_class_identifier=ComponentIdentifier(class_name=scorer, class_module="tests"),
        scored_expectation=expectation,
    )


def _with_target(result: AttackResult, *, model_name: str, endpoint: str = "https://example.com") -> AttackResult:
    result.atomic_attack_identifier = AtomicAttackIdentifier.build(
        attack_identifier=AttackIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending",
            objective_target=TargetIdentifier(
                class_name="MockPromptTarget",
                class_module="tests",
                model_name=model_name,
                endpoint=endpoint,
            ),
        )
    )
    return result


@pytest.mark.parametrize(
    ("baseline_model", "attack_model", "attack_endpoint"),
    [("model-A", "model-B", "https://example.com"), ("model-A", "model-A", "https://other.example.com")],
)
def test_paired_outcomes_reject_different_recorded_objective_targets(
    baseline_model: str, attack_model: str, attack_endpoint: str
) -> None:
    baseline = _with_target(_result("a", AttackOutcome.FAILURE), model_name=baseline_model)
    attack = _with_target(_result("a", AttackOutcome.SUCCESS), model_name=attack_model, endpoint=attack_endpoint)

    with pytest.raises(ValueError, match="different objective targets"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_reject_target_drift_across_decided_pairs() -> None:
    baseline = [
        _with_target(_result(seed, AttackOutcome.FAILURE), model_name=model)
        for seed, model in (("a", "model-A"), ("b", "model-B"))
    ]
    attack = [
        _with_target(_result(seed, AttackOutcome.SUCCESS), model_name=model)
        for seed, model in (("a", "model-A"), ("b", "model-B"))
    ]

    with pytest.raises(ValueError, match="incompatible objective targets"):
        _statistics(_plan(("a", "b"), ("a", "b")), {"baseline": baseline, "attack": attack})


def _compound_outcome(
    *, scorer: str, expectation: ScoringExpectation | None = None
) -> tuple[SequentialAttackResult, AttackResult]:
    child = _result("a", AttackOutcome.SUCCESS, group="attack", attempt_index=1)
    child.automated_score = _score(scorer, expectation=expectation)
    envelope = SequentialAttackResult.model_validate(_result("a", AttackOutcome.SUCCESS, group="attack").model_dump())
    envelope.completion_policy = SequenceCompletionPolicy.LAST_RESULT
    envelope.metadata["completion_policy"] = SequenceCompletionPolicy.LAST_RESULT.value
    envelope.metadata["child_attack_result_ids"] = [child.attack_result_id]
    assert envelope.last_score is None
    return envelope, child


def test_paired_outcomes_validate_compound_child_scorer_evidence() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("ScorerA")
    envelope, child = _compound_outcome(scorer="ScorerB")

    with pytest.raises(ValueError, match="different scorers"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope, child]})


def test_paired_outcomes_validate_live_compound_child_scorer_evidence() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("ScorerA")
    envelope, child = _compound_outcome(scorer="ScorerB")
    envelope.child_attack_results = [child]

    with pytest.raises(ValueError, match="different scorers"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope]})


def _nested_compound_outcome() -> tuple[SequentialAttackResult, SequentialAttackResult, AttackResult]:
    inner, leaf = _compound_outcome(scorer="SharedScorer", expectation=ScoringExpectation(objective="context"))
    outer = inner.model_copy(deep=True)
    outer.attack_result_id = str(uuid.uuid4())
    outer.metadata["child_attack_result_ids"] = [inner.attack_result_id]
    outer.child_attack_results = [inner]
    assert inner.attribution_data is not None
    inner.attribution_data["attempt_index"] = 1
    inner.child_attack_results = [leaf]
    return outer, inner, leaf


def _saved_compound_results(results: list[AttackResult]) -> list[AttackResult]:
    return [
        AttackResult.model_validate({name: getattr(result, name) for name in AttackResult.model_fields})
        for result in results
    ]


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        ("scorer", "different scorers"),
        ("target", "different objective targets"),
        ("score_type", "incompatible score types"),
        ("expectation", "incompatible scoring expectations"),
    ],
)
def test_paired_outcomes_validate_nested_compound_evidence(live: bool, mismatch: str, message: str) -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    expectation = ScoringExpectation(objective="context")
    baseline.automated_score = _score("SharedScorer", expectation=expectation)
    outer, inner, leaf = _nested_compound_outcome()
    if mismatch == "scorer":
        leaf.automated_score = _score("OtherScorer", expectation=expectation)
    elif mismatch == "target":
        _with_target(baseline, model_name="model-A")
        _with_target(leaf, model_name="model-B")
    elif mismatch == "score_type":
        leaf.automated_score = Score(
            score_type="float_scale",
            score_value="0.7",
            scorer_class_identifier=baseline.automated_score.scorer_class_identifier,
            scored_expectation=expectation,
        )
    else:
        leaf.automated_score = _score("SharedScorer", expectation=ScoringExpectation(objective="different context"))
    results = [outer] if live else _saved_compound_results([outer, inner, leaf])

    with pytest.raises(ValueError, match=message):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": results})


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("shared_leaf", [False, True])
def test_paired_outcomes_count_compatible_nested_compound_once(live: bool, shared_leaf: bool) -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("SharedScorer", expectation=ScoringExpectation(objective="context"))
    outer, inner, leaf = _nested_compound_outcome()
    if shared_leaf:
        outer.metadata["child_attack_result_ids"].append(leaf.attack_result_id)
        outer.child_attack_results.append(leaf)
    results = [outer] if live else _saved_compound_results([outer, inner, leaf])

    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": results})

    assert (stats.planned_pairs, stats.decided_pairs, stats.failure_to_success) == (1, 1, 1)
    assert stats.paired_difference == 1.0


@pytest.mark.parametrize("live", [False, True])
def test_paired_outcomes_reject_missing_nested_compound_evidence(live: bool) -> None:
    outer, inner, _ = _nested_compound_outcome()
    inner.child_attack_results = []
    results = [outer] if live else _saved_compound_results([outer, inner])

    with pytest.raises(ValueError, match="missing child attack result"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE, group="base")], "attack": results},
        )


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("self_reference", [False, True])
def test_paired_outcomes_reject_compound_cycles(live: bool, self_reference: bool) -> None:
    outer, inner, _ = _nested_compound_outcome()
    linked = outer if self_reference else inner
    linked.metadata["child_attack_result_ids"] = [outer.attack_result_id]
    linked.child_attack_results = [outer]
    results = [outer] if live else _saved_compound_results([outer, inner])

    with pytest.raises(ValueError, match="cycle"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE, group="base")], "attack": results},
        )


def test_paired_outcomes_handle_deep_saved_compounds_without_recursion_limit() -> None:
    leaf = _result("a", AttackOutcome.SUCCESS, group="attack", attempt_index=1)
    leaf.automated_score = _score("SharedScorer")
    results = [leaf]
    for _ in range(1500):
        parent = _result("a", AttackOutcome.SUCCESS, group="attack", attempt_index=1)
        parent.metadata["child_attack_result_ids"] = [results[-1].attack_result_id]
        results.append(parent)
    assert results[-1].attribution_data is not None
    results[-1].attribution_data.pop("attempt_index")
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("SharedScorer")

    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": results})

    assert (stats.decided_pairs, stats.failure_to_success) == (1, 1)


def test_paired_outcomes_reject_compound_child_target_mismatch() -> None:
    baseline = _with_target(_result("a", AttackOutcome.FAILURE, group="base"), model_name="model-A")
    envelope, child = _compound_outcome(scorer="SharedScorer")
    _with_target(child, model_name="model-B")

    with pytest.raises(ValueError, match="different objective targets"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope, child]})


def test_paired_outcomes_validate_compound_child_scoring_expectation() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("SharedScorer", expectation=ScoringExpectation(objective="first"))
    envelope, child = _compound_outcome(scorer="SharedScorer", expectation=ScoringExpectation(objective="second"))

    with pytest.raises(ValueError, match="scoring expectations"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope, child]})


def test_paired_outcomes_accept_matching_compound_child_scoring_evidence() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    baseline.automated_score = _score("SharedScorer")
    envelope, child = _compound_outcome(scorer="SharedScorer")
    unrelated = _result("a", AttackOutcome.FAILURE, group="attack", attempt_index=2)
    unrelated.automated_score = _score("OtherScorer")

    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope, child, unrelated]})
    assert (stats.decided_pairs, stats.failure_to_success) == (1, 1)


def test_paired_outcomes_reject_unavailable_compound_child_evidence() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    envelope, _ = _compound_outcome(scorer="SharedScorer")

    with pytest.raises(ValueError, match="missing child attack result"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [envelope]})


def test_paired_outcomes_exclude_unpaired_compound_without_child_evidence() -> None:
    envelope, _ = _compound_outcome(scorer="SharedScorer")
    stats = _statistics(_plan(("a",), ("a",)), {"attack": [envelope]})

    assert (stats.decided_pairs, stats.missing_baseline, stats.excluded_pairs) == (0, 1, 1)


def test_paired_outcomes_reject_incompatible_persisted_scorers() -> None:
    baseline = _result("a", AttackOutcome.SUCCESS, group="base")
    attack = _result("a", AttackOutcome.SUCCESS, group="attack")
    baseline.automated_score = _score("ScorerA")
    attack.automated_score = _score("ScorerB")

    with pytest.raises(ValueError, match="different scorers"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_use_human_override_score_identity() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    attack = _result("a", AttackOutcome.SUCCESS, group="attack")
    baseline.automated_score = _score("ScorerA")
    attack.automated_score = _score("OldScorer")
    attack.human_score = _score("ScorerA")
    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})

    assert stats.failure_to_success == 1


def test_paired_outcomes_include_reused_persisted_completion() -> None:
    # Reused completions can precede the live scenario and still carry valid
    # plan attribution. Their timestamp must not prevent matching.
    cached_baseline = _result("a", AttackOutcome.FAILURE, seconds=-3600, group="base")
    cached_attack = _result("a", AttackOutcome.SUCCESS, seconds=-3600, group="attack")
    stats = _statistics(
        _plan(("a",), ("a",)),
        {"baseline": [cached_baseline], "attack": [cached_attack]},
    )

    assert (stats.planned_pairs, stats.decided_pairs, stats.failure_to_success) == (1, 1, 1)


def test_paired_outcomes_resolve_unique_seed_from_objective_without_explicit_attribution() -> None:
    # Legacy records may omit the seed ID if the objective uniquely identifies a planned seed.
    attack = _result("a", AttackOutcome.SUCCESS)
    attack.attribution_data = None

    stats = _statistics(
        _plan(("a",), ("a",)),
        {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": [attack]},
    )

    assert stats.failure_to_success == stats.decided_pairs == 1
    assert stats.unattributed_attempts == 0


def test_paired_outcomes_reject_ambiguous_objective_only_seed_attribution() -> None:
    plan = _plan(("a", "b"), ("a", "b"))
    # Distinct logical seeds are allowed to have the same objective. Without a
    # persisted seed ID, an outcome cannot safely be assigned to either seed.
    plan.seed_groups[1].objective = "a"
    plan.seed_groups[1].objective_sha256 = to_sha256("a")
    ambiguous = _result("a", AttackOutcome.SUCCESS, group="attack")
    ambiguous.attribution_data = {"parent_eval_hash": "attack-eval"}

    with pytest.raises(ValueError, match="ambiguous|seed"):
        _statistics(plan, {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": [ambiguous]})


def test_paired_outcomes_do_not_count_other_configuration_with_same_atomic_name() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="other",
            atomic_attack_name="attack",
            display_group="Other attack configuration",
            technique_eval_hash="other-eval",
            kind=ScenarioRunPlanGroupKind.ATTACK,
            seed_group_ids=["a"],
        )
    )
    unrelated = _result("a", AttackOutcome.FAILURE, seconds=10)
    unrelated.attribution_data = {"seed_group_id": "a", "parent_eval_hash": "other-eval"}

    stats = _statistics(
        plan,
        {
            "baseline": [_result("a", AttackOutcome.FAILURE, group="base")],
            "attack": [_result("a", AttackOutcome.SUCCESS, group="attack"), unrelated],
        },
    )

    assert (stats.decided_pairs, stats.failure_to_success, stats.unattributed_attempts) == (1, 1, 1)


def test_paired_outcomes_ignore_malformed_records_from_known_unselected_configuration() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="other",
            atomic_attack_name="attack",
            display_group="Other attack configuration",
            technique_eval_hash="other-eval",
            kind=ScenarioRunPlanGroupKind.ATTACK,
            seed_group_ids=["a"],
        )
    )
    unrelated = _result("a", AttackOutcome.FAILURE, group="other")
    unrelated.timestamp = None  # type: ignore[assignment]
    unrelated.metadata["child_attack_result_ids"] = None

    stats = _statistics(
        plan,
        {
            "baseline": [_result("a", AttackOutcome.FAILURE, group="base")],
            "attack": [_result("a", AttackOutcome.SUCCESS, group="attack"), unrelated],
        },
    )

    assert (stats.decided_pairs, stats.failure_to_success, stats.unattributed_attempts) == (1, 1, 1)


def test_paired_outcomes_reject_conflicting_persisted_parent_collection() -> None:
    mismatched = _result("a", AttackOutcome.SUCCESS, group="attack")
    assert mismatched.attribution_data is not None
    mismatched.attribution_data["parent_collection"] = "different_atomic_attack"

    with pytest.raises(ValueError, match="parent_collection|collection"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE, group="base")], "attack": [mismatched]},
        )


def test_paired_outcomes_distinguish_selected_groups_sharing_an_atomic_name() -> None:
    plan = _plan(("a",), ("a",))
    for group in plan.atomic_groups:
        group.atomic_attack_name = "shared"

    stats = _statistics(
        plan,
        {
            "shared": [
                _result("a", AttackOutcome.FAILURE, group="base"),
                _result("a", AttackOutcome.SUCCESS, group="attack"),
            ]
        },
    )

    assert (stats.decided_pairs, stats.failure_to_success, stats.unattributed_attempts) == (1, 1, 0)


def test_paired_outcomes_reject_duplicate_ids_across_selected_groups() -> None:
    with pytest.raises(ValueError, match="Duplicate attack result IDs"):
        _statistics(
            _plan(("a",), ("a",)),
            {
                "baseline": [_result("a", AttackOutcome.FAILURE, result_id="reused-id")],
                "attack": [_result("a", AttackOutcome.SUCCESS, result_id="reused-id")],
            },
        )


def test_paired_outcomes_reject_mixed_scorers_across_decided_pairs() -> None:
    # Every pair is internally compatible, but the aggregated success rates
    # silently combine evaluations from different scorer implementations.
    baseline = [_result(seed, AttackOutcome.FAILURE) for seed in ("a", "b")]
    attack = [_result(seed, AttackOutcome.SUCCESS) for seed in ("a", "b")]
    for scorer, before, after in zip(("ScorerA", "ScorerB"), baseline, attack, strict=True):
        before.automated_score = _score(scorer)
        after.automated_score = _score(scorer)

    with pytest.raises(ValueError, match="scorer|incompatible"):
        _statistics(_plan(("a", "b"), ("a", "b")), {"baseline": baseline, "attack": attack})


def test_paired_outcomes_reject_different_score_types_from_same_scorer() -> None:
    baseline = _result("a", AttackOutcome.FAILURE)
    attack = _result("a", AttackOutcome.SUCCESS)
    baseline.automated_score = _score("SharedScorer")
    attack.automated_score = Score(
        score_type="float_scale",
        score_value="0.7",
        scorer_class_identifier=ComponentIdentifier(class_name="SharedScorer", class_module="tests"),
    )

    with pytest.raises(ValueError, match="score|incompatible|type"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_reject_incompatible_scoring_objectives_within_pair() -> None:
    baseline = _result("a", AttackOutcome.FAILURE)
    attack = _result("a", AttackOutcome.SUCCESS)
    baseline.automated_score = _score(
        "SharedScorer", expectation=ScoringExpectation(objective="expected scoring context")
    )
    attack.automated_score = Score(
        score_type="true_false",
        score_value="true",
        scorer_class_identifier=ComponentIdentifier(class_name="SharedScorer", class_module="tests"),
        objective="unrelated objective",
    )

    with pytest.raises(ValueError, match="scoring expectations"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_allow_matching_scoring_objective_distinct_from_attack_objective() -> None:
    baseline = _result("a", AttackOutcome.FAILURE)
    attack = _result("a", AttackOutcome.SUCCESS)
    expectation = ScoringExpectation(objective="scoring context distinct from the attack objective")
    baseline.automated_score = _score("SharedScorer", expectation=expectation)
    attack.automated_score = _score("SharedScorer", expectation=expectation)

    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})
    assert stats.decided_pairs == stats.failure_to_success == 1


def test_paired_outcomes_reject_different_scoring_criteria_within_pair() -> None:
    baseline = _result("a", AttackOutcome.FAILURE)
    attack = _result("a", AttackOutcome.SUCCESS)
    baseline.automated_score = _score(
        "SharedScorer", expectation=ScoringExpectation(objective="context", conditions=(MatchesObjective(),))
    )
    attack.automated_score = _score("SharedScorer", expectation=ScoringExpectation(objective="context", conditions=()))

    with pytest.raises(ValueError, match="scoring expectations"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_allow_distinct_scoring_contexts_across_matched_seeds() -> None:
    baseline = [_result(seed, AttackOutcome.FAILURE) for seed in ("a", "b")]
    attack = [_result(seed, AttackOutcome.SUCCESS) for seed in ("a", "b")]
    for seed, before, after in zip(("a", "b"), baseline, attack, strict=True):
        expectation = ScoringExpectation(objective=f"scoring context for {seed}")
        before.automated_score = _score("SharedScorer", expectation=expectation)
        after.automated_score = _score("SharedScorer", expectation=expectation)

    stats = _statistics(_plan(("a", "b"), ("a", "b")), {"baseline": baseline, "attack": attack})
    assert (stats.decided_pairs, stats.failure_to_success) == (2, 2)


@pytest.mark.parametrize("invalid_index", [0, -1, "1", True])
def test_paired_outcomes_reject_invalid_child_attempt_index(invalid_index: object) -> None:
    # Invalid but present child metadata must not silently promote a child to
    # top-level status and let it supersede a completed parent result.
    child = _result("a", AttackOutcome.FAILURE, seconds=10)
    assert child.attribution_data is not None
    child.attribution_data["attempt_index"] = invalid_index

    with pytest.raises(ValueError, match="attempt_index|malformed|invalid"):
        _statistics(
            _plan(("a",), ("a",)),
            {
                "baseline": [_result("a", AttackOutcome.FAILURE)],
                "attack": [_result("a", AttackOutcome.SUCCESS), child],
            },
        )


def test_paired_outcomes_use_result_id_as_tie_breaker_independent_of_input_order() -> None:
    # Two top-level completions can share a persisted timestamp. The higher
    # result ID determines the winner regardless of result-list ordering.
    older_id = _result("a", AttackOutcome.FAILURE, result_id="a-result")
    newer_id = _result("a", AttackOutcome.SUCCESS, result_id="z-result")

    for results in ([newer_id, older_id], [older_id, newer_id]):
        stats = _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": results},
        )
        assert (stats.decided_pairs, stats.failure_to_success, stats.paired_difference) == (1, 1, 1.0)


def test_paired_outcomes_exclude_pair_when_latest_top_level_retry_errors() -> None:
    stats = _statistics(
        _plan(("a",), ("a",)),
        {
            "baseline": [_result("a", AttackOutcome.FAILURE)],
            "attack": [
                _result("a", AttackOutcome.SUCCESS, seconds=-1),
                _result("a", AttackOutcome.ERROR, seconds=1),
            ],
        },
    )

    assert (stats.decided_pairs, stats.excluded_pairs, stats.attack_errors) == (0, 1, 1)
    assert stats.failure_to_success == 0
    assert stats.paired_difference is None


def test_paired_outcomes_reject_malformed_timestamp_in_candidate_completion() -> None:
    result = _result("a", AttackOutcome.SUCCESS)
    # Hydrated result models are mutable; a damaged persisted timestamp makes
    # chronological latest-result selection unverifiable.
    result.timestamp = None  # type: ignore[assignment]

    with pytest.raises(ValueError, match="timestamp"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": [result]},
        )


def test_paired_outcomes_reject_unrecognized_persisted_outcome() -> None:
    result = _result("a", AttackOutcome.SUCCESS)
    result.outcome = "not_a_valid_outcome"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="outcome"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": [result]},
        )


def test_paired_outcomes_reject_malformed_child_result_ids() -> None:
    result = _result("a", AttackOutcome.SUCCESS)
    result.metadata["child_attack_result_ids"] = None

    with pytest.raises(ValueError, match="child_attack_result_ids|metadata|invalid"):
        _statistics(
            _plan(("a",), ("a",)),
            {"baseline": [_result("a", AttackOutcome.FAILURE)], "attack": [result]},
        )
