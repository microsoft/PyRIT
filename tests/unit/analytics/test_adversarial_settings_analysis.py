# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for adversarial sampling-sweep statistics. All records here are synthetic fixtures."""

import json
import math

import pytest

from pyrit.analytics.adversarial_settings_analysis import (
    AdversarialTrialRecord,
    TokenPrice,
    build_experiment_report,
    format_report_markdown,
    minimum_detectable_difference,
    read_trial_records,
    wilson_interval,
    write_trial_records,
)
from pyrit.models import AttackOutcome


def _record(
    *,
    generator: str = "gen_a",
    value: float = 1.2,
    repetition: int = 0,
    objective: str = "obj_0",
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    **extra: object,
) -> AdversarialTrialRecord:
    return AdversarialTrialRecord(
        experiment_id="exp",
        trial_id=f"{generator}|{value}|{repetition}|{objective}",
        cell_id=f"{generator}|{value}|{repetition}",
        generator_name=generator,
        technique="crescendo_simulated",
        parameter="temperature",
        value=value,
        repetition=repetition,
        objective_id=objective,
        outcome=outcome.value,
        **extra,
    )


def _cell(
    *, generator: str, value: float, successes_by_objective: dict[str, int], repetitions: int = 3
) -> list[AdversarialTrialRecord]:
    """Build a cell where objective ``k`` succeeds in its first ``successes_by_objective[k]`` repetitions."""
    return [
        _record(
            generator=generator,
            value=value,
            repetition=repetition,
            objective=objective,
            outcome=AttackOutcome.SUCCESS if repetition < successes else AttackOutcome.FAILURE,
        )
        for objective, successes in successes_by_objective.items()
        for repetition in range(repetitions)
    ]


def test_wilson_interval_matches_reference_value():
    interval = wilson_interval(successes=5, total=10)
    assert interval is not None
    low, high = interval
    assert low == pytest.approx(0.2366, abs=1e-4)
    assert high == pytest.approx(0.7634, abs=1e-4)


def test_wilson_interval_stays_inside_unit_interval_at_extremes():
    assert wilson_interval(successes=0, total=4) == (0.0, pytest.approx(0.4899, abs=1e-4))
    assert wilson_interval(successes=4, total=4) == (pytest.approx(0.5101, abs=1e-4), 1.0)


def test_wilson_interval_is_none_for_empty_population():
    assert wilson_interval(successes=0, total=0) is None


@pytest.mark.parametrize(("successes", "total"), [(-1, 3), (4, 3), (0, -1)])
def test_wilson_interval_rejects_invalid_counts(successes: int, total: int):
    with pytest.raises(ValueError, match="Counts"):
        wilson_interval(successes=successes, total=total)


def test_record_rejects_unknown_outcome():
    with pytest.raises(ValueError):
        AdversarialTrialRecord(
            experiment_id="exp",
            trial_id="t",
            cell_id="c",
            generator_name="g",
            technique="crescendo_simulated",
            parameter="temperature",
            value=0.2,
            repetition=0,
            objective_id="o",
            outcome="refused",
        )


def test_record_rejects_negative_repetition():
    with pytest.raises(ValueError, match="repetition"):
        _record(repetition=-1)


def test_cell_asr_uses_decided_denominator_by_default():
    records = [
        _record(objective="o1", outcome=AttackOutcome.SUCCESS),
        _record(objective="o2", outcome=AttackOutcome.FAILURE),
        _record(objective="o3", outcome=AttackOutcome.ERROR),
        _record(objective="o4", outcome=AttackOutcome.UNDETERMINED),
    ]
    cell = build_experiment_report(records=records, baseline_value=1.2).cells[0]
    assert (cell.successes, cell.failures, cell.errors, cell.undetermined) == (1, 1, 1, 1)
    assert cell.trials == 4
    assert cell.asr == pytest.approx(0.5)
    assert cell.error_rate == pytest.approx(0.25)
    assert cell.undetermined_rate == pytest.approx(0.25)


def test_cell_asr_all_denominator_counts_errors_and_undetermined_as_non_success():
    records = [
        _record(objective="o1", outcome=AttackOutcome.SUCCESS),
        _record(objective="o2", outcome=AttackOutcome.FAILURE),
        _record(objective="o3", outcome=AttackOutcome.ERROR),
        _record(objective="o4", outcome=AttackOutcome.UNDETERMINED),
    ]
    cell = build_experiment_report(records=records, baseline_value=1.2, denominator="all").cells[0]
    assert cell.asr == pytest.approx(0.25)
    interval = wilson_interval(successes=1, total=4)
    assert interval is not None
    low, high = interval
    assert (cell.asr_ci_low, cell.asr_ci_high) == pytest.approx((low, high))


def test_cell_with_only_errors_has_no_decided_asr():
    records = [_record(objective=f"o{index}", outcome=AttackOutcome.ERROR) for index in range(3)]
    cell = build_experiment_report(records=records, baseline_value=1.2).cells[0]
    assert cell.asr is None
    assert cell.asr_ci_low is None
    assert cell.error_rate == 1.0


def test_repetition_variability_is_reported():
    # Repetition 0 succeeds on both objectives, repetitions 1 and 2 on none.
    records = _cell(generator="gen_a", value=1.2, successes_by_objective={"o1": 1, "o2": 1})
    cell = build_experiment_report(records=records, baseline_value=1.2).cells[0]
    assert cell.repetition_asr_mean == pytest.approx(1 / 3)
    assert cell.repetition_asr_stdev == pytest.approx(math.sqrt(1 / 3))


def test_operational_measurements_and_cost_are_aggregated():
    records = [
        _record(
            objective="o1",
            executed_turns=2,
            backtrack_count=1,
            execution_time_ms=100,
            adversarial_input_tokens=1_000,
            adversarial_output_tokens=500,
        ),
        _record(
            objective="o2",
            executed_turns=4,
            backtrack_count=3,
            execution_time_ms=300,
            adversarial_input_tokens=3_000,
            adversarial_output_tokens=1_500,
        ),
    ]
    report = build_experiment_report(
        records=records,
        baseline_value=1.2,
        prices={"gen_a": TokenPrice(input_per_million=2.0, output_per_million=10.0)},
    )
    cell = report.cells[0]
    assert cell.mean_turns == 3
    assert cell.mean_backtracks == 2
    assert cell.mean_execution_time_ms == 200
    assert (cell.adversarial_input_tokens, cell.adversarial_output_tokens) == (4_000, 2_000)
    assert cell.estimated_cost == pytest.approx((4_000 * 2.0 + 2_000 * 10.0) / 1_000_000)


def test_missing_token_counts_stay_none_instead_of_zero():
    cell = build_experiment_report(
        records=[_record()], baseline_value=1.2, prices={"gen_a": TokenPrice(1.0, 1.0)}
    ).cells[0]
    assert cell.adversarial_input_tokens is None
    assert cell.estimated_cost is None


def test_baseline_comparison_reports_delta_and_paired_interval():
    objectives = {f"o{index}": 0 for index in range(12)}
    treatment = dict.fromkeys(objectives, 3)  # always succeeds
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective=objectives),
        *_cell(generator="gen_a", value=0.2, successes_by_objective=treatment),
    ]
    report = build_experiment_report(records=records, baseline_value=1.2, bootstrap_samples=200)
    (comparison,) = report.comparisons
    assert comparison.value == 0.2
    assert comparison.delta == pytest.approx(1.0)
    assert comparison.paired_objectives == 12
    assert comparison.delta_ci_low == pytest.approx(1.0)
    assert comparison.delta_ci_high == pytest.approx(1.0)


def test_no_effect_interval_covers_zero():
    pattern = {f"o{index}": index % 4 for index in range(12)}
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective=pattern),
        *_cell(generator="gen_a", value=0.7, successes_by_objective=pattern),
    ]
    (comparison,) = build_experiment_report(records=records, baseline_value=1.2, bootstrap_samples=300).comparisons
    assert comparison.delta == pytest.approx(0.0)
    assert comparison.delta_ci_low <= 0.0 <= comparison.delta_ci_high


def test_bootstrap_is_reproducible_for_a_fixed_seed():
    pattern_a = {f"o{index}": index % 4 for index in range(12)}
    pattern_b = {f"o{index}": (index + 1) % 4 for index in range(12)}
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective=pattern_a),
        *_cell(generator="gen_a", value=0.2, successes_by_objective=pattern_b),
    ]
    first = build_experiment_report(records=records, baseline_value=1.2, bootstrap_seed=7).comparisons
    second = build_experiment_report(records=records, baseline_value=1.2, bootstrap_seed=7).comparisons
    assert first == second


def test_comparison_without_baseline_cell_reports_no_delta():
    records = _cell(generator="gen_a", value=0.2, successes_by_objective={"o1": 1})
    (comparison,) = build_experiment_report(records=records, baseline_value=1.2).comparisons
    assert comparison.delta is None
    assert comparison.paired_objectives == 0


def test_comparison_pairs_only_shared_objectives():
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective={"o1": 0, "o2": 0}),
        *_cell(generator="gen_a", value=0.2, successes_by_objective={"o2": 3, "o3": 3}),
    ]
    (comparison,) = build_experiment_report(records=records, baseline_value=1.2, bootstrap_samples=50).comparisons
    assert comparison.paired_objectives == 1
    assert comparison.delta == pytest.approx(1.0)


def test_interaction_detects_model_specific_effect():
    objectives = [f"o{index}" for index in range(12)]
    zeros = dict.fromkeys(objectives, 0)
    always = dict.fromkeys(objectives, 3)
    records = [
        # gen_a improves at 0.2; gen_b does not change.
        *_cell(generator="gen_a", value=1.2, successes_by_objective=zeros),
        *_cell(generator="gen_a", value=0.2, successes_by_objective=always),
        *_cell(generator="gen_b", value=1.2, successes_by_objective=zeros),
        *_cell(generator="gen_b", value=0.2, successes_by_objective=zeros),
    ]
    (interaction,) = build_experiment_report(records=records, baseline_value=1.2, bootstrap_samples=100).interactions
    assert (interaction.generator_a, interaction.generator_b) == ("gen_a", "gen_b")
    assert interaction.delta_a == pytest.approx(1.0)
    assert interaction.delta_b == pytest.approx(0.0)
    assert interaction.interaction == pytest.approx(1.0)
    assert interaction.ci_low > 0


def test_interaction_without_all_four_cells_is_empty():
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective={"o1": 1}),
        *_cell(generator="gen_a", value=0.2, successes_by_objective={"o1": 1}),
        *_cell(generator="gen_b", value=1.2, successes_by_objective={"o1": 1}),
    ]
    (interaction,) = build_experiment_report(records=records, baseline_value=1.2).interactions
    assert interaction.interaction is None
    assert interaction.paired_objectives == 0


def test_report_rejects_mixed_parameters():
    records = [_record(), AdversarialTrialRecord(**{**_record(objective="o2").__dict__, "parameter": "top_p"})]
    with pytest.raises(ValueError, match="single parameter"):
        build_experiment_report(records=records, baseline_value=1.2)


def test_report_rejects_mixed_techniques():
    records = [_record(), AdversarialTrialRecord(**{**_record(objective="o2").__dict__, "technique": "red_teaming"})]
    with pytest.raises(ValueError, match="single technique"):
        build_experiment_report(records=records, baseline_value=1.2)


def test_report_rejects_mixed_experiments():
    records = [_record(), AdversarialTrialRecord(**{**_record(objective="o2").__dict__, "experiment_id": "other"})]
    with pytest.raises(ValueError, match="single experiment"):
        build_experiment_report(records=records, baseline_value=1.2)


def test_report_rejects_duplicate_trials():
    with pytest.raises(ValueError, match="duplicate"):
        build_experiment_report(records=[_record(), _record()], baseline_value=1.2)


def test_records_round_trip_through_jsonl(tmp_path):
    path = tmp_path / "records.jsonl"
    records = [_record(objective="o1", executed_turns=3), _record(objective="o2", outcome=AttackOutcome.ERROR)]
    write_trial_records(records=records[:1], path=path)
    write_trial_records(records=records[1:], path=path)
    assert read_trial_records(path=path) == records


def test_records_overwrite_when_not_appending(tmp_path):
    path = tmp_path / "records.jsonl"
    write_trial_records(records=[_record(objective="o1")], path=path)
    write_trial_records(records=[_record(objective="o2")], path=path, append=False)
    assert [record.objective_id for record in read_trial_records(path=path)] == ["o2"]


def test_reading_missing_file_returns_no_records(tmp_path):
    assert read_trial_records(path=tmp_path / "absent.jsonl") == []


def test_serialized_records_do_not_contain_objective_text(tmp_path):
    path = tmp_path / "records.jsonl"
    write_trial_records(records=[_record(objective="harmbench_behavior_id")], path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "objective" not in data
    assert data["objective_id"] == "harmbench_behavior_id"


def test_reading_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "records.jsonl"
    write_trial_records(records=[_record()], path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema version"):
        read_trial_records(path=path)


def test_minimum_detectable_difference_shrinks_with_more_trials():
    small = minimum_detectable_difference(trials_per_arm=36, baseline_asr=0.3)
    large = minimum_detectable_difference(trials_per_arm=360, baseline_asr=0.3)
    assert small == pytest.approx(0.30, abs=0.01)
    assert large < small


def test_minimum_detectable_difference_rejects_invalid_design():
    with pytest.raises(ValueError):
        minimum_detectable_difference(trials_per_arm=0, baseline_asr=0.3)


def test_markdown_report_labels_synthetic_data():
    records = [
        *_cell(generator="gen_a", value=1.2, successes_by_objective={"o1": 1, "o2": 2}),
        *_cell(generator="gen_a", value=0.2, successes_by_objective={"o1": 2, "o2": 3}),
    ]
    text = format_report_markdown(
        report=build_experiment_report(records=records, baseline_value=1.2, bootstrap_samples=50), synthetic=True
    )
    assert text.startswith("> **SYNTHETIC FIXTURE DATA")
    assert "| gen_a | 0.2 vs 1.2 |" in text


def test_report_to_dict_is_json_serializable():
    records = _cell(generator="gen_a", value=1.2, successes_by_objective={"o1": 1})
    json.dumps(build_experiment_report(records=records, baseline_value=1.2).to_dict())
