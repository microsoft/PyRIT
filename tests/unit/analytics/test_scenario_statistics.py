# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.analytics import (
    compute_outcome_statistics,
    compute_scenario_statistics,
)
from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    combine_execution_counts,
    resolve_execution_unit,
    select_execution_attack_result_ids,
)
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ScenarioObjectiveScorerAttribution,
    ScenarioObjectiveScorerScope,
    ScenarioProgressCounts,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    config_hash,
)
from unit.mocks import (
    judged_attack_identifier,
    make_judged_scenario_result,
    make_scenario_result,
    make_sequential_attack_results,
)

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _result(*, objective: str, outcome: AttackOutcome, seconds: int = 0, **attribution: str) -> AttackResult:
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
        timestamp=_T0 + timedelta(seconds=seconds),
        attribution_data={"parent_collection": "attack", **attribution} if attribution else None,
    )


def _plan() -> ScenarioRunPlan:
    return ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=["seed-a", "seed-b"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id="seed-a", objective_sha256=to_sha256("A"), objective="A"),
            ScenarioRunPlanSeedGroup(id="seed-b", objective_sha256=to_sha256("B"), objective="B"),
        ],
    )


def test_latest_attempt_decides_each_unit() -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.SUCCESS, seconds=0),
                _result(objective="A", outcome=AttackOutcome.ERROR, seconds=1),
                _result(objective="B", outcome=AttackOutcome.ERROR, seconds=2),
                _result(objective="B", outcome=AttackOutcome.SUCCESS, seconds=3),
            ]
        }
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.completed == 2
    assert statistics.overall.succeeded == 1
    assert statistics.overall.success_percentage == 50
    assert statistics.overall.errors == 2
    assert statistics.attempts == 4


def test_empty_result_has_no_success_percentage() -> None:
    statistics = compute_scenario_statistics(make_scenario_result(attack_results={"attack": []}))

    assert statistics.overall.completed == 0
    assert statistics.overall.success_percentage is None
    assert statistics.attempts == 0
    assert statistics.overall.outcomes == compute_outcome_statistics({})


def test_latest_outcomes_share_both_denominators_without_historical_errors() -> None:
    result = make_scenario_result(
        attack_results={
            "one": [
                _result(objective="A", outcome=AttackOutcome.ERROR),
                _result(objective="A", outcome=AttackOutcome.SUCCESS, seconds=1),
                _result(objective="B", outcome=AttackOutcome.FAILURE),
            ],
            "two": [
                _result(objective="C", outcome=AttackOutcome.ERROR),
                _result(objective="C", outcome=AttackOutcome.ERROR, seconds=1),
                _result(objective="D", outcome=AttackOutcome.UNDETERMINED),
            ],
        },
        display_group_map={"one": "group", "two": "group"},
    )
    statistics = compute_scenario_statistics(result)
    expected = compute_outcome_statistics(dict.fromkeys(AttackOutcome, 1))
    assert statistics.overall.outcomes == expected
    assert statistics.display_groups["group"].outcomes == expected
    assert combine_execution_counts(statistics.atomic_attacks.values()) == statistics.overall
    assert statistics.overall.success_percentage == 25
    assert statistics.overall.outcomes.success_rate == 0.5
    assert statistics.overall.outcomes.success_rate_all == 0.25
    assert statistics.overall.errors == 3
    assert statistics.overall.outcomes.errors == 1
    assert statistics.overall.retries == 2


@pytest.mark.parametrize("outcome", list(AttackOutcome))
def test_each_latest_outcome_uses_shared_statistics(outcome: AttackOutcome) -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.FAILURE),
                _result(objective="A", outcome=outcome, seconds=1),
            ]
        }
    )
    counts = compute_scenario_statistics(result).overall
    assert counts.outcomes == compute_outcome_statistics({outcome: 1})
    assert counts.completed == 1
    assert counts.success_percentage == (100 if outcome is AttackOutcome.SUCCESS else 0)


def test_combining_count_only_legacy_payloads_does_not_infer_failures_from_historical_errors(caplog) -> None:
    legacy = ScenarioProgressCounts(completed=2, succeeded=1, errors=5, retries=4, success_percentage=50)
    combined = combine_execution_counts([legacy])
    assert combined.outcomes is None
    assert combined.success_percentage == 50
    assert combined.errors == 5
    assert "without an outcome breakdown" in caplog.text


def test_combining_an_empty_group_reports_no_planned_count() -> None:
    """An empty display group never learned a plan, so planned is unknown, not 0."""
    combined = combine_execution_counts([])
    assert combined.planned is None
    assert combined.completed == 0


def test_combining_unequal_scenario_groups_recomputes_both_denominators() -> None:
    result = make_scenario_result(
        attack_results={
            "one": [_result(objective="A", outcome=AttackOutcome.SUCCESS)],
            "two": [
                _result(objective=str(index), outcome=outcome)
                for index, outcome in enumerate([AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.ERROR])
            ],
        }
    )
    statistics = compute_scenario_statistics(result)
    combined = combine_execution_counts(statistics.atomic_attacks.values())
    assert combined.outcomes.success_rate == pytest.approx(2 / 3)
    assert combined.outcomes.success_rate_all == 0.5
    assert combined == statistics.overall


@pytest.mark.parametrize("field", ["completed", "succeeded", "success_percentage"])
def test_combining_revalidates_mutated_scenario_totals(field: str) -> None:
    counts = ScenarioProgressCounts(
        completed=2,
        succeeded=1,
        errors=1,
        retries=0,
        success_percentage=50,
        outcomes=compute_outcome_statistics({"success": 1, "error": 1}),
    )
    setattr(counts, field, 10)
    with pytest.raises(ValueError, match="completed|succeeded|success_percentage"):
        combine_execution_counts([counts])


@pytest.mark.parametrize("field", ["successes", "total_results", "success_rate_all"])
def test_combining_revalidates_mutated_nested_outcomes(field: str) -> None:
    counts = ScenarioProgressCounts(
        completed=2,
        succeeded=1,
        errors=1,
        retries=0,
        outcomes=compute_outcome_statistics({"success": 1, "error": 1}),
    )
    setattr(counts.outcomes, field, 10)
    with pytest.raises(ValueError):
        combine_execution_counts([counts])


def test_saved_plan_counts_planned_units_and_reports_unattributed_attempts() -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.SUCCESS, parent_eval_hash="eval", seed_group_id="seed-a"),
                _result(objective="Z", outcome=AttackOutcome.SUCCESS, parent_eval_hash="other-eval"),
            ]
        },
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.planned == 2
    assert statistics.overall.completed == 1
    assert statistics.overall.success_percentage == 100
    assert statistics.unattributed_attempts == 1
    assert statistics.display_groups["Attack"].planned == 2


def test_use_saved_plan_false_counts_a_legacy_run() -> None:
    result = make_scenario_result(
        attack_results={"attack": [_result(objective="A", outcome=AttackOutcome.SUCCESS)]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    statistics = compute_scenario_statistics(result, use_saved_plan=False)

    assert statistics.overall.planned is None
    assert statistics.overall.completed == 1


def test_invalid_saved_plan_counts_as_legacy_run(caplog) -> None:
    result = make_scenario_result(
        attack_results={"attack": [_result(objective="A", outcome=AttackOutcome.SUCCESS)]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: {"atomic_groups": "invalid"}},
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.planned is None
    assert statistics.overall.success_percentage == 100
    assert "invalid saved run plan" in caplog.text


def test_resolve_execution_unit_precedence() -> None:
    lookup = ScenarioPlanLookup.from_plan(plan=_plan())

    def resolve(**kwargs):
        defaults = {
            "atomic_attack_name": "attack",
            "technique_eval_hash": "eval",
            "attributed_seed_group_id": None,
            "atomic_attack_identifier": None,
            "objective": "A",
            "objective_sha256": None,
            "plan_lookup": lookup,
        }
        return resolve_execution_unit(**{**defaults, **kwargs})

    # Attribution wins, then a unique objective match in the planned group, then an objective hash.
    assert resolve(attributed_seed_group_id="seed-b").seed_group_id == "seed-b"
    assert resolve().seed_group_id == "seed-a"
    assert resolve(objective="unplanned").seed_group_id == config_hash({"objective": "unplanned"})
    # Planned groups keep their plan ID; unplanned configurations get a name-and-hash identity.
    assert resolve().atomic_group_id == "group"
    assert resolve(technique_eval_hash="other").atomic_group_id == config_hash(
        {"atomic_attack_name": "attack", "technique_eval_hash": "other"}
    )


@pytest.mark.parametrize("with_result_roles", [True, False], ids=["recorded-roles", "pre-role-rows"])
@pytest.mark.parametrize("saved_plan", [None, _plan()], ids=["legacy", "saved-plan"])
@pytest.mark.parametrize(
    ("envelope_outcome", "child_outcomes", "envelope_selected"),
    [
        (AttackOutcome.SUCCESS, [AttackOutcome.FAILURE, AttackOutcome.SUCCESS], False),
        (AttackOutcome.ERROR, [AttackOutcome.FAILURE, AttackOutcome.ERROR], False),
        (AttackOutcome.ERROR, [AttackOutcome.FAILURE], True),
    ],
)
def test_execution_attack_result_ids_select_an_envelope_only_for_its_own_error(
    envelope_outcome: AttackOutcome,
    child_outcomes: list[AttackOutcome],
    envelope_selected: bool,
    saved_plan: ScenarioRunPlan | None,
    with_result_roles: bool,
) -> None:
    envelope, children = make_sequential_attack_results(
        atomic_attack_name="attack",
        envelope_outcome=envelope_outcome,
        child_outcomes=child_outcomes,
        objective="a",
        with_result_roles=with_result_roles,
    )
    result = make_scenario_result(
        attack_results={"attack": [*children, envelope]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: saved_plan.model_dump(mode="json")} if saved_plan else {},
    )

    selected = select_execution_attack_result_ids(result)

    expected = {child.attack_result_id for child in children}
    if envelope_selected:
        expected.add(envelope.attack_result_id)
    assert selected == expected
    assert len(selected) == compute_scenario_statistics(result).attempts


@pytest.mark.parametrize(
    ("plan", "use_saved_plan", "envelope_selected"),
    [(None, True, False), (None, False, True), (_plan(), False, False)],
    ids=["saved-plan", "legacy", "explicit-plan"],
)
def test_execution_attack_result_ids_resolve_units_with_the_requested_plan(
    plan: ScenarioRunPlan | None, use_saved_plan: bool, envelope_selected: bool
) -> None:
    envelope, children = make_sequential_attack_results(
        atomic_attack_name="attack",
        envelope_outcome=AttackOutcome.SUCCESS,
        child_outcomes=[AttackOutcome.SUCCESS],
        objective="a",
    )
    # Without its eval hash, the envelope joins its children's unit only through the plan's unique-name match.
    envelope.attribution_data = {
        key: value for key, value in (envelope.attribution_data or {}).items() if key != "parent_eval_hash"
    }
    result = make_scenario_result(
        attack_results={"attack": [*children, envelope]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    selected = select_execution_attack_result_ids(result, plan=plan, use_saved_plan=use_saved_plan)

    expected = {child.attack_result_id for child in children}
    if envelope_selected:
        expected.add(envelope.attack_result_id)
    assert selected == expected
    assert len(selected) == compute_scenario_statistics(result, plan=plan, use_saved_plan=use_saved_plan).attempts


def test_execution_attack_result_ids_keep_an_envelope_without_its_children() -> None:
    envelope, _ = make_sequential_attack_results(
        atomic_attack_name="attack", envelope_outcome=AttackOutcome.SUCCESS, child_outcomes=[AttackOutcome.SUCCESS]
    )
    result = make_scenario_result(attack_results={"attack": [envelope]})

    assert select_execution_attack_result_ids(result) == {envelope.attack_result_id}


_JUDGE_A = ComponentIdentifier(class_name="JudgeA", class_module="tests.scorers")
_JUDGE_B = ComponentIdentifier(class_name="JudgeB", class_module="tests.scorers")


def test_pool_results_recorded_by_two_judges_are_split_and_mixed() -> None:
    result = make_judged_scenario_result(
        groups=[
            ("attack_a", _JUDGE_A, _JUDGE_A, [AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED]),
            ("attack_b", _JUDGE_B, _JUDGE_B, [AttackOutcome.FAILURE, AttackOutcome.FAILURE]),
        ]
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.MIXED_SCORERS
    assert [
        (
            objective_scorer.attribution,
            objective_scorer.scorer_hash,
            objective_scorer.candidate_index,
            list(objective_scorer.atomic_attacks),
        )
        for objective_scorer in statistics.objective_scorers
    ] == [
        (ScenarioObjectiveScorerAttribution.RECORDED, _JUDGE_A.hash, 0, ["attack_a"]),
        (ScenarioObjectiveScorerAttribution.RECORDED, _JUDGE_B.hash, 1, ["attack_b"]),
    ]
    judge_a = statistics.objective_scorers[0].overall
    assert (judge_a.completed, judge_a.planned, judge_a.succeeded) == (2, 2, 1)
    assert judge_a.outcomes.success_rate_decided == 1.0
    assert judge_a.outcomes.success_rate_all == 0.5
    assert statistics.objective_scorers[0].scorer_identifier == _JUDGE_A


def test_pool_binding_attributes_unrecorded_results_and_ignores_unstarted_judges() -> None:
    result = make_judged_scenario_result(
        groups=[
            ("attack_a", _JUDGE_A, None, [AttackOutcome.SUCCESS]),
            ("attack_b", _JUDGE_B, None, []),
        ],
        planned_seed_groups=1,
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.SINGLE_SCORER
    assert [
        (objective_scorer.attribution, objective_scorer.scorer_hash, objective_scorer.overall.completed)
        for objective_scorer in statistics.objective_scorers
    ] == [
        (ScenarioObjectiveScorerAttribution.PLANNED, _JUDGE_A.hash, 1),
        (ScenarioObjectiveScorerAttribution.PLANNED, _JUDGE_B.hash, 0),
    ]
    assert statistics.objective_scorers[0].scorer_identifier is None


def test_recorded_scorer_that_contradicts_the_pool_binding_is_a_conflict() -> None:
    result = make_judged_scenario_result(groups=[("attack_a", _JUDGE_A, _JUDGE_B, [AttackOutcome.SUCCESS])])

    statistics = compute_scenario_statistics(result)

    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.UNKNOWN_SCORER
    [objective_scorer] = statistics.objective_scorers
    assert (objective_scorer.attribution, objective_scorer.scorer_hash, objective_scorer.scorer_identifier) == (
        ScenarioObjectiveScorerAttribution.CONFLICT,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("run_scorer", "attribution", "scope"),
    [
        (_JUDGE_A, ScenarioObjectiveScorerAttribution.RUN, ScenarioObjectiveScorerScope.SINGLE_SCORER),
        (None, ScenarioObjectiveScorerAttribution.UNKNOWN, ScenarioObjectiveScorerScope.UNKNOWN_SCORER),
    ],
)
def test_legacy_results_without_a_recorded_scorer_use_the_run_scorer(run_scorer, attribution, scope) -> None:
    result = make_scenario_result(
        objective_scorer_identifier=run_scorer,
        attack_results={"attack": [_result(objective="A", outcome=AttackOutcome.SUCCESS)]},
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.objective_scorer_scope == scope
    [objective_scorer] = statistics.objective_scorers
    assert objective_scorer.attribution == attribution
    assert objective_scorer.scorer_hash == (run_scorer.hash if run_scorer is not None else None)


def test_latest_completion_decides_the_judge_of_a_resumed_unit() -> None:
    first, second = (
        make_judged_scenario_result(groups=[("attack", None, objective_scorer, [outcome])]).attack_results["attack"][0]
        for objective_scorer, outcome in ((_JUDGE_A, AttackOutcome.FAILURE), (_JUDGE_B, AttackOutcome.SUCCESS))
    )
    second.timestamp = first.timestamp + timedelta(seconds=1)
    result = make_judged_scenario_result(groups=[("attack", None, None, [AttackOutcome.SUCCESS])])
    result.attack_results["attack"] = [first, second]

    statistics = compute_scenario_statistics(result)

    assert [
        (objective_scorer.scorer_hash, objective_scorer.overall.completed)
        for objective_scorer in statistics.objective_scorers
    ] == [(_JUDGE_B.hash, 1)]
    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.SINGLE_SCORER


@pytest.mark.parametrize(
    ("bound", "attribution", "scorer_hash"),
    [
        (_JUDGE_B, ScenarioObjectiveScorerAttribution.PLANNED, _JUDGE_B.hash),
        (None, ScenarioObjectiveScorerAttribution.UNKNOWN, None),
    ],
)
def test_unrecorded_latest_completion_ignores_an_earlier_execution_judge(bound, attribution, scorer_hash) -> None:
    [earlier] = make_judged_scenario_result(
        groups=[("attack", None, _JUDGE_A, [AttackOutcome.FAILURE])]
    ).attack_results["attack"]
    result = make_judged_scenario_result(groups=[("attack", bound, None, [AttackOutcome.SUCCESS])])
    [latest] = result.attack_results["attack"]
    latest.timestamp = earlier.timestamp + timedelta(seconds=1)
    result.attack_results["attack"] = [earlier, latest]

    [objective_scorer] = compute_scenario_statistics(result).objective_scorers

    assert (objective_scorer.attribution, objective_scorer.scorer_hash) == (attribution, scorer_hash)


def test_envelope_takes_the_judge_its_linked_child_recorded() -> None:
    envelope, children = make_sequential_attack_results(
        atomic_attack_name="attack", envelope_outcome=AttackOutcome.SUCCESS, child_outcomes=[AttackOutcome.SUCCESS]
    )
    children[0].atomic_attack_identifier = judged_attack_identifier(_JUDGE_A)
    result = make_scenario_result(attack_results={"attack": [envelope, *children]})

    [objective_scorer] = compute_scenario_statistics(result).objective_scorers

    assert (objective_scorer.attribution, objective_scorer.scorer_hash) == (
        ScenarioObjectiveScorerAttribution.RECORDED,
        _JUDGE_A.hash,
    )


@pytest.mark.parametrize(
    ("bound", "attribution"),
    [
        (None, ScenarioObjectiveScorerAttribution.UNKNOWN),
        (_JUDGE_A, ScenarioObjectiveScorerAttribution.CONFLICT),
        (_JUDGE_B, ScenarioObjectiveScorerAttribution.CONFLICT),
    ],
)
def test_mixed_scorer_compound_does_not_credit_the_last_child(
    bound: ComponentIdentifier | None, attribution: ScenarioObjectiveScorerAttribution
) -> None:
    envelope, children = make_sequential_attack_results(
        atomic_attack_name="attack",
        objective="objective-0",
        envelope_outcome=AttackOutcome.SUCCESS,
        child_outcomes=[AttackOutcome.SUCCESS, AttackOutcome.FAILURE],
    )
    envelope.metadata["completion_policy"] = "exhaustive"
    for child, scorer in zip(children, [_JUDGE_A, _JUDGE_B], strict=True):
        child.atomic_attack_identifier = judged_attack_identifier(scorer)
    for attack in [*children, envelope]:
        attack.attribution_data.update(parent_eval_hash="eval-attack", seed_group_id="seed-0")
    result = make_judged_scenario_result(groups=[("attack", bound, None, [AttackOutcome.SUCCESS])])
    result.attack_results["attack"] = [*children, envelope]

    statistics = compute_scenario_statistics(result)
    [scorer_statistics] = statistics.objective_scorers

    assert scorer_statistics.attribution == attribution
    assert scorer_statistics.scorer_hash is None
    assert scorer_statistics.scorer_identifier is None
    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.UNKNOWN_SCORER
    assert (statistics.overall.completed, statistics.overall.succeeded) == (1, 1)
    assert (scorer_statistics.overall.completed, scorer_statistics.overall.succeeded) == (1, 1)


@pytest.mark.parametrize("unidentified_child", ["nested-compound", "missing-result"])
@pytest.mark.parametrize(
    ("bound", "attribution"),
    [
        (None, ScenarioObjectiveScorerAttribution.UNKNOWN),
        (_JUDGE_A, ScenarioObjectiveScorerAttribution.UNKNOWN),
        (_JUDGE_B, ScenarioObjectiveScorerAttribution.CONFLICT),
    ],
)
def test_compound_with_an_unidentified_child_scorer_is_not_credited_to_a_recorded_child(
    bound: ComponentIdentifier | None, attribution: ScenarioObjectiveScorerAttribution, unidentified_child: str
) -> None:
    # FIRST_SUCCESS: A's child fails, then an inner compound succeeds through a child that B judged.
    envelope, [child_a] = make_sequential_attack_results(
        atomic_attack_name="attack",
        objective="objective-0",
        envelope_outcome=AttackOutcome.SUCCESS,
        child_outcomes=[AttackOutcome.FAILURE],
    )
    inner_envelope, [child_b] = make_sequential_attack_results(
        atomic_attack_name="attack",
        objective="objective-0",
        envelope_outcome=AttackOutcome.SUCCESS,
        child_outcomes=[AttackOutcome.SUCCESS],
    )
    inner_envelope.attribution_data["attempt_index"] = 2
    child_a.atomic_attack_identifier = judged_attack_identifier(_JUDGE_A)
    child_b.atomic_attack_identifier = judged_attack_identifier(_JUDGE_B)
    envelope.metadata["child_attack_result_ids"].append(inner_envelope.attack_result_id)
    attacks = [child_a, inner_envelope, child_b, envelope]
    if unidentified_child == "missing-result":
        attacks = [child_a, envelope]
    for attack in attacks:
        attack.attribution_data.update(parent_eval_hash="eval-attack", seed_group_id="seed-0")
    result = make_judged_scenario_result(groups=[("attack", bound, None, [AttackOutcome.SUCCESS])])
    result.attack_results["attack"] = attacks

    statistics = compute_scenario_statistics(result)
    [scorer_statistics] = statistics.objective_scorers

    assert scorer_statistics.attribution == attribution
    assert scorer_statistics.scorer_hash is None
    assert scorer_statistics.scorer_identifier is None
    assert statistics.objective_scorer_scope == ScenarioObjectiveScorerScope.UNKNOWN_SCORER


@pytest.mark.parametrize("loaded_children", [False, True])
def test_bound_compound_without_child_scorer_evidence_is_unknown_not_planned(loaded_children: bool) -> None:
    envelope, children = make_sequential_attack_results(
        atomic_attack_name="attack",
        objective="objective-0",
        envelope_outcome=AttackOutcome.SUCCESS,
        child_outcomes=[AttackOutcome.SUCCESS],
    )
    attacks = [*children, envelope] if loaded_children else [envelope]
    for attack in attacks:
        attack.attribution_data.update(parent_eval_hash="eval-attack", seed_group_id="seed-0")
    result = make_judged_scenario_result(groups=[("attack", _JUDGE_A, None, [AttackOutcome.SUCCESS])])
    result.attack_results["attack"] = attacks

    [scorer_statistics] = compute_scenario_statistics(result).objective_scorers

    assert scorer_statistics.attribution == ScenarioObjectiveScorerAttribution.UNKNOWN
    assert scorer_statistics.scorer_hash is None
    assert scorer_statistics.scorer_identifier is None
