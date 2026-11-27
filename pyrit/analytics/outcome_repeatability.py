# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Outcome agreement across separately executed runs of the same scenario configuration.

Ports the logic of ``compute_repeatability`` in ASSERT's ``assert_ai/analysis/stability.py``
(https://github.com/responsibleai/ASSERT, MIT license). Unlike upstream, errored and
undetermined outcomes are counted and kept out of the agreement denominator, per-run rates
cover only the units decided in every run, and the runs must share one configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.analytics.scenario_statistics import load_scenario_run_plan, select_effective_attack_results
from pyrit.models import AttackOutcome, ScenarioEvaluationIdentifier, ScenarioExecutionUnit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import ScenarioResult

_DECIDED = (AttackOutcome.SUCCESS, AttackOutcome.FAILURE)


@dataclass(frozen=True, slots=True)
class OutcomeRepeatabilityCounts:
    """
    Outcome agreement for one set of execution units across the compared runs.

    ``shared_units`` counts units planned in every run, and ``decided_units`` the shared units
    whose latest top-level outcome is success or failure in every run. ``agreement_rate`` and
    each run's success rate use ``decided_units`` as the denominator, so ``rate_range`` compares
    the same objectives; they are None when no unit is decided. ``missing_units``,
    ``error_units`` and ``undetermined_units`` count shared units with that outcome in at least
    one run, so they may overlap. ``partially_planned_units`` counts units planned in some runs
    only; they are not shared.
    """

    shared_units: int
    decided_units: int
    always_success: int
    always_failure: int
    mixed: int
    agreement_rate: float | None
    run_success_counts: tuple[int, ...]
    run_success_rates: tuple[float | None, ...]
    rate_range: float | None
    missing_units: int
    error_units: int
    undetermined_units: int
    partially_planned_units: int


@dataclass(frozen=True, slots=True)
class OutcomeRepeatability:
    """
    Outcome agreement across runs, overall and per technique label.

    ``run_count`` is the number of compared runs; ``run_success_counts`` and
    ``run_success_rates`` follow the order the runs were passed in. ``by_technique`` is keyed by
    each planned group's technique label, which names the baseline ``baseline``.
    """

    run_count: int
    overall: OutcomeRepeatabilityCounts
    by_technique: dict[str, OutcomeRepeatabilityCounts]


def compute_outcome_repeatability(scenario_results: Sequence[ScenarioResult]) -> OutcomeRepeatability:
    """
    Compare the outcomes of two or more separately executed runs of one scenario configuration.

    Units are matched across runs by their planned ``(atomic_group_id, seed_group_id)``, which
    are deterministic, and each run contributes the latest top-level outcome per unit, selected
    as ``compute_scenario_statistics`` does. A unit decided in every run counts as
    ``always_success``, ``always_failure`` or ``mixed``.

    Args:
        scenario_results (Sequence[ScenarioResult]): The runs to compare, each with its hydrated
            attack results.

    Returns:
        OutcomeRepeatability: Agreement counts and per-run rates, overall and per technique.

    Raises:
        ValueError: If fewer than two runs are given, a run is given twice, a run has no valid
            saved plan, or the runs do not share one scenario configuration.
    """
    if len(scenario_results) < 2:
        raise ValueError("Outcome repeatability needs at least two scenario runs.")
    run_ids = [str(result.id) for result in scenario_results]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("Each scenario run can be compared only once.")

    plans = []
    for result in scenario_results:
        plan = load_scenario_run_plan(result)
        if plan is None:
            raise ValueError(f"Scenario result {result.id} has no valid saved run plan to match units across runs.")
        plans.append(plan)
    eval_hashes = {ScenarioEvaluationIdentifier(result.scenario_identifier).eval_hash for result in scenario_results}
    if len(eval_hashes) > 1:
        raise ValueError(
            "Outcome repeatability compares runs of the same scenario configuration, but these runs "
            "differ in scenario, technique or dataset selection, target, or scorer."
        )

    planned_by_run: list[set[ScenarioExecutionUnit]] = []
    techniques: dict[ScenarioExecutionUnit, str] = {}
    for plan in plans:
        planned: set[ScenarioExecutionUnit] = set()
        for group in plan.atomic_groups:
            for seed in group.seed_group_ids:
                unit = ScenarioExecutionUnit(atomic_group_id=group.id, seed_group_id=seed)
                planned.add(unit)
                techniques.setdefault(unit, group.technique_label)
        planned_by_run.append(planned)
    outcomes_by_run = [
        {unit: result.outcome for unit, (_, result) in select_effective_attack_results(scenario, plan=plan).items()}
        for scenario, plan in zip(scenario_results, plans, strict=True)
    ]

    shared = set.intersection(*planned_by_run)
    partially_planned = set.union(*planned_by_run) - shared
    overall = _counts(units=shared, partially_planned=partially_planned, outcomes_by_run=outcomes_by_run)
    by_technique = {
        technique: _counts(
            units={unit for unit in shared if techniques[unit] == technique},
            partially_planned={unit for unit in partially_planned if techniques[unit] == technique},
            outcomes_by_run=outcomes_by_run,
        )
        for technique in sorted(set(techniques.values()))
    }
    return OutcomeRepeatability(run_count=len(scenario_results), overall=overall, by_technique=by_technique)


def _counts(
    *,
    units: set[ScenarioExecutionUnit],
    partially_planned: set[ScenarioExecutionUnit],
    outcomes_by_run: list[dict[ScenarioExecutionUnit, AttackOutcome]],
) -> OutcomeRepeatabilityCounts:
    decided = [unit for unit in units if all(run.get(unit) in _DECIDED for run in outcomes_by_run)]
    successes = [sum(run[unit] == AttackOutcome.SUCCESS for run in outcomes_by_run) for unit in decided]
    always_success = sum(count == len(outcomes_by_run) for count in successes)
    always_failure = sum(count == 0 for count in successes)
    run_success_counts = tuple(sum(run[unit] == AttackOutcome.SUCCESS for unit in decided) for run in outcomes_by_run)
    run_success_rates = tuple(count / len(decided) if decided else None for count in run_success_counts)
    return OutcomeRepeatabilityCounts(
        shared_units=len(units),
        decided_units=len(decided),
        always_success=always_success,
        always_failure=always_failure,
        mixed=len(decided) - always_success - always_failure,
        agreement_rate=(always_success + always_failure) / len(decided) if decided else None,
        run_success_counts=run_success_counts,
        run_success_rates=run_success_rates,
        rate_range=(max(run_success_counts) - min(run_success_counts)) / len(decided) if decided else None,
        missing_units=sum(any(unit not in run for run in outcomes_by_run) for unit in units),
        error_units=sum(any(run.get(unit) == AttackOutcome.ERROR for run in outcomes_by_run) for unit in units),
        undetermined_units=sum(
            any(run.get(unit) == AttackOutcome.UNDETERMINED for run in outcomes_by_run) for unit in units
        ),
        partially_planned_units=len(partially_planned),
    )
