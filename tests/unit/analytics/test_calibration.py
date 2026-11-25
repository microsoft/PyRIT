# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import math
from decimal import (
    ROUND_CEILING,
    Clamped,
    DivisionByZero,
    FloatOperation,
    Inexact,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from typing import TYPE_CHECKING

import pytest

from pyrit.analytics import (
    CalibratedOutcomeStatistics,
    CalibrationCountsSource,
    CalibrationWarningCode,
    JudgeCalibrationProfile,
    calibrate_outcome_statistics,
    compute_outcome_statistics,
)
from pyrit.models import ComponentIdentifier, OutcomeStatistics
from pyrit.score import ObjectiveScorerMetrics, ScorerMetricsWithIdentity, SubStringScorer, get_all_objective_metrics
from pyrit.score.scorer_evaluation.scorer_metrics_io import add_evaluation_results

if TYPE_CHECKING:
    from pathlib import Path


def _reference_lang_reiczigel(
    *,
    successes: int,
    total: int,
    tp: int,
    fp: int,
    tn: int,
    fn: int,
    z: float,
) -> tuple[float, float, float]:
    """Independent float reference for Lang and Reiczigel (2014) Eqs. 2, 4, 6, 7, 16, and 17."""
    m1 = tp + fn
    m0 = tn + fp
    se = tp / m1
    sp = tn / m0
    j = se + sp - 1.0
    p_hat = successes / total
    pi_hat = min(1.0, max(0.0, (p_hat + sp - 1.0) / j))

    z2 = z * z
    m1_p = m1 + 2.0
    m0_p = m0 + 2.0
    se_p = (tp + 1.0) / m1_p
    sp_p = (tn + 1.0) / m0_p
    j_p = se_p + sp_p - 1.0
    n_p = total + z2
    p_p = (successes + 0.5 * z2) / n_p
    pi_p = (p_p + sp_p - 1.0) / j_p
    var_se = se_p * (1.0 - se_p) / m1_p
    var_sp = sp_p * (1.0 - sp_p) / m0_p
    d_pi = 2.0 * z2 * (pi_p * var_se - (1.0 - pi_p) * var_sp)
    var_pi = (p_p * (1.0 - p_p) / n_p + (pi_p**2) * var_se + ((1.0 - pi_p) ** 2) * var_sp) / (j_p**2)
    half = z * math.sqrt(var_pi)
    return pi_hat, max(0.0, min(1.0, pi_p + d_pi - half)), max(0.0, min(1.0, pi_p + d_pi + half))


def test_calibrate_outcome_statistics_rogan_gladen_worked_example() -> None:
    profile = JudgeCalibrationProfile(
        true_positive=170,
        false_positive=10,
        true_negative=190,
        false_negative=30,
        eval_hash="abc123eval",
        dataset_name="objective_eval",
        dataset_version="1.0",
    )
    outcomes = compute_outcome_statistics({"success": 10, "failure": 90})

    result = calibrate_outcome_statistics(outcomes, judge_profile=profile, expected_eval_hash="abc123eval")

    assert isinstance(result, CalibratedOutcomeStatistics)
    assert result.is_reliable is True
    assert result.was_clipped is False
    assert result.observed_success_rate == pytest.approx(0.10)
    assert result.observed_ci_lower == outcomes.success_rate_ci_lower
    assert result.observed_ci_upper == outcomes.success_rate_ci_upper
    assert result.youden_index == pytest.approx(0.80, rel=1e-12)
    assert result.calibrated_success_rate == pytest.approx(0.0625, rel=1e-12)
    assert result.calibrated_ci_lower is not None
    assert result.calibrated_ci_upper is not None
    assert 0.0 <= result.calibrated_ci_lower < result.calibrated_success_rate < result.calibrated_ci_upper <= 1.0
    assert result.warning_codes == ()
    assert result.warnings == ()


def test_calibrate_outcome_statistics_resolves_cross_judge_ranking_flip() -> None:
    strict_judge = JudgeCalibrationProfile(
        true_positive=190,
        false_positive=2,
        true_negative=198,
        false_negative=10,
    )
    noisy_judge = JudgeCalibrationProfile(
        true_positive=140,
        false_positive=20,
        true_negative=180,
        false_negative=60,
    )
    outcomes_x = compute_outcome_statistics({"success": 8, "failure": 92})
    outcomes_y = compute_outcome_statistics({"success": 13, "failure": 87})

    calibrated_x = calibrate_outcome_statistics(outcomes_x, judge_profile=strict_judge)
    calibrated_y = calibrate_outcome_statistics(outcomes_y, judge_profile=noisy_judge)

    assert outcomes_y.success_rate is not None and outcomes_x.success_rate is not None
    assert outcomes_y.success_rate > outcomes_x.success_rate
    assert calibrated_x.calibrated_success_rate == pytest.approx(0.07 / 0.94, rel=1e-12)
    assert calibrated_y.calibrated_success_rate == pytest.approx(0.05, rel=1e-12)
    assert calibrated_x.calibrated_success_rate > calibrated_y.calibrated_success_rate


@pytest.mark.parametrize(
    ("confidence_level", "z"),
    [
        (0.90, 1.6448536269514722),
        (0.95, 1.959963984540054),
        (0.99, 2.5758293035489004),
    ],
)
@pytest.mark.parametrize("successes", [0, 1, 5, 15, 50, 88, 100])
def test_lang_reiczigel_matches_reference_across_confidence_levels(
    confidence_level: float,
    z: float,
    successes: int,
) -> None:
    profile = JudgeCalibrationProfile(
        true_positive=172,
        false_positive=22,
        true_negative=182,
        false_negative=19,
    )
    outcomes = compute_outcome_statistics({"success": successes, "failure": 100 - successes})
    result = calibrate_outcome_statistics(outcomes, judge_profile=profile, confidence_level=confidence_level)
    ref_point, ref_low, ref_high = _reference_lang_reiczigel(
        successes=successes,
        total=100,
        tp=172,
        fp=22,
        tn=182,
        fn=19,
        z=z,
    )

    assert result.is_reliable is True
    assert result.calibrated_success_rate == pytest.approx(ref_point, rel=1e-12, abs=1e-15)
    assert result.calibrated_ci_lower == pytest.approx(ref_low, rel=1e-12, abs=1e-15)
    assert result.calibrated_ci_upper == pytest.approx(ref_high, rel=1e-12, abs=1e-15)


def test_all_shipped_objective_scorer_metrics_recover_and_calibrate_cleanly() -> None:
    shipped_entries = get_all_objective_metrics()
    assert len(shipped_entries) >= 30

    sample_outcomes = compute_outcome_statistics({"success": 20, "failure": 80})
    reliable_count = 0
    low_j_count = 0

    for entry in shipped_entries:
        profile = JudgeCalibrationProfile.from_scorer_metrics_with_identity(entry)
        assert profile is not None
        assert entry.scorer_identifier.eval_hash is not None
        assert len(entry.scorer_identifier.eval_hash) > 0
        assert profile.eval_hash == entry.scorer_identifier.eval_hash
        assert profile.scorer_class_name == entry.scorer_identifier.class_name
        assert profile.num_evaluated_responses == entry.metrics.num_responses
        acc = (profile.true_positive + profile.true_negative) / profile.num_evaluated_responses
        prec = profile.true_positive / (profile.true_positive + profile.false_positive)
        assert acc == pytest.approx(entry.metrics.accuracy, rel=1e-12)
        assert prec == pytest.approx(entry.metrics.precision, rel=1e-12)
        assert profile.sensitivity == pytest.approx(entry.metrics.recall, rel=1e-12)

        calibrated = calibrate_outcome_statistics(
            sample_outcomes,
            judge_profile=profile,
            expected_eval_hash=entry.scorer_identifier.eval_hash,
        )
        if profile.youden_index <= 0.20:
            low_j_count += 1
            assert calibrated.is_reliable is False
            assert CalibrationWarningCode.LOW_YOUDEN_INDEX in calibrated.warning_codes
            assert calibrated.calibrated_success_rate is None
        else:
            reliable_count += 1
            assert calibrated.is_reliable is True
            assert calibrated.calibrated_success_rate is not None
            assert calibrated.calibrated_ci_lower is not None
            assert calibrated.calibrated_ci_upper is not None
            assert 0.0 <= calibrated.calibrated_ci_lower <= calibrated.calibrated_ci_upper <= 1.0

    assert reliable_count > 0
    assert low_j_count > 0


def test_from_objective_metrics_uses_stored_counts_including_zero_tp_and_perfect_judge() -> None:
    perfect_metrics = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=1.0,
        accuracy_standard_error=0.0,
        f1_score=1.0,
        precision=1.0,
        recall=1.0,
        specificity=1.0,
        true_positive=40,
        false_positive=0,
        true_negative=60,
        false_negative=0,
    )
    profile = JudgeCalibrationProfile.from_objective_metrics(perfect_metrics, eval_hash="perfect_hash")
    assert profile is not None
    assert profile.counts_source == CalibrationCountsSource.STORED
    assert profile.sensitivity == 1.0
    assert profile.specificity == 1.0
    assert profile.youden_index == 1.0

    zero_tp_metrics = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=0.60,
        accuracy_standard_error=0.049,
        f1_score=0.0,
        precision=0.0,
        recall=0.0,
        specificity=1.0,
        true_positive=0,
        false_positive=0,
        true_negative=60,
        false_negative=40,
    )
    zero_tp_profile = JudgeCalibrationProfile.from_objective_metrics(zero_tp_metrics)
    assert zero_tp_profile is not None
    assert zero_tp_profile.counts_source == CalibrationCountsSource.STORED
    assert zero_tp_profile.youden_index == 0.0


@pytest.mark.parametrize(
    ("accuracy", "precision", "recall"),
    [
        (0.60, 0.0, 0.0),
        (0.50, 0.0, 0.50),
        (0.50, 0.50, 0.0),
        (1.0, 1.0, 1.0),
        (0.8123456789, 0.7777777777, 0.6666666666),
    ],
)
def test_from_objective_metrics_returns_none_for_unidentifiable_or_inconsistent_legacy_metrics(
    accuracy: float,
    precision: float,
    recall: float,
) -> None:
    legacy = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=accuracy,
        accuracy_standard_error=0.04,
        f1_score=0.5,
        precision=precision,
        recall=recall,
    )
    assert JudgeCalibrationProfile.from_objective_metrics(legacy) is None


def test_from_objective_metrics_validates_partial_or_contradictory_stored_counts() -> None:
    partial = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=0.9,
        accuracy_standard_error=0.03,
        f1_score=0.9,
        precision=0.9,
        recall=0.9,
        true_positive=45,
    )
    with pytest.raises(ValueError, match="partially populated"):
        JudgeCalibrationProfile.from_objective_metrics(partial)

    contradictory = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=0.9,
        accuracy_standard_error=0.03,
        f1_score=0.9,
        precision=0.9,
        recall=0.9,
        true_positive=45,
        false_positive=5,
        true_negative=45,
        false_negative=10,
    )
    with pytest.raises(ValueError, match="must sum to"):
        JudgeCalibrationProfile.from_objective_metrics(contradictory)

    no_negatives = ObjectiveScorerMetrics(
        num_responses=50,
        num_human_raters=1,
        accuracy=0.9,
        accuracy_standard_error=0.04,
        f1_score=0.95,
        precision=1.0,
        recall=0.9,
        true_positive=45,
        false_positive=0,
        true_negative=0,
        false_negative=5,
    )
    assert JudgeCalibrationProfile.from_objective_metrics(no_negatives) is None


def test_small_sample_coin_flip_judge_is_blocked_by_youden_ci_guard() -> None:
    tiny_profile = JudgeCalibrationProfile(
        true_positive=4,
        false_positive=1,
        true_negative=4,
        false_negative=1,
    )
    outcomes = compute_outcome_statistics({"success": 20, "failure": 80})

    result = calibrate_outcome_statistics(outcomes, judge_profile=tiny_profile)

    assert tiny_profile.youden_index == pytest.approx(0.60)
    assert result.youden_ci_lower <= 0.0
    assert result.is_reliable is False
    assert CalibrationWarningCode.YOUDEN_CI_INCLUDES_ZERO in result.warning_codes
    assert result.calibrated_success_rate is None
    assert result.calibrated_ci_lower is None
    assert result.calibrated_ci_upper is None
    assert result.observed_ci_lower == outcomes.success_rate_ci_lower
    assert result.observed_ci_upper == outcomes.success_rate_ci_upper


def test_clipping_empty_cohort_and_scope_warnings() -> None:
    profile = JudgeCalibrationProfile(
        true_positive=180,
        false_positive=20,
        true_negative=180,
        false_negative=20,
        num_scorer_trials=3,
        eval_hash="hash_a",
    )

    low_raw = compute_outcome_statistics({"success": 5, "failure": 95, "undetermined": 50})
    clipped_low = calibrate_outcome_statistics(low_raw, judge_profile=profile)
    assert clipped_low.is_reliable is True
    assert clipped_low.was_clipped is True
    assert clipped_low.calibrated_success_rate == 0.0
    assert CalibrationWarningCode.CLIPPED_PREVALENCE_ESTIMATE in clipped_low.warning_codes
    assert CalibrationWarningCode.LOW_DECIDED_SHARE in clipped_low.warning_codes
    assert CalibrationWarningCode.MULTI_TRIAL_CALIBRATION in clipped_low.warning_codes

    high_raw = compute_outcome_statistics({"success": 95, "failure": 5})
    clipped_high = calibrate_outcome_statistics(high_raw, judge_profile=profile)
    assert clipped_high.is_reliable is True
    assert clipped_high.was_clipped is True
    assert clipped_high.calibrated_success_rate == 1.0

    empty_decided = compute_outcome_statistics({"undetermined": 4, "error": 2})
    empty_result = calibrate_outcome_statistics(empty_decided, judge_profile=profile)
    assert empty_result.is_reliable is False
    assert CalibrationWarningCode.EMPTY_DECIDED_COHORT in empty_result.warning_codes
    assert empty_result.observed_success_rate is None
    assert empty_result.calibrated_success_rate is None
    assert empty_result.calibrated_ci_lower is None
    assert empty_result.calibrated_ci_upper is None


def test_input_and_eval_hash_validation_errors() -> None:
    profile = JudgeCalibrationProfile(
        true_positive=90,
        false_positive=5,
        true_negative=95,
        false_negative=10,
        eval_hash="scorer_hash_1",
    )
    outcomes = compute_outcome_statistics({"success": 10, "failure": 90})

    with pytest.raises(ValueError, match="expected_eval_hash"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, expected_eval_hash="scorer_hash_2")

    with pytest.raises(ValueError, match="non-empty string"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, expected_eval_hash="   ")

    with pytest.raises(ValueError, match="confidence_level"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, confidence_level=1.0)

    with pytest.raises(ValueError, match="confidence_level"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, confidence_level=True)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="min_youden_index"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, min_youden_index=1.0)

    with pytest.raises(ValueError, match="non-negative integer"):
        JudgeCalibrationProfile(
            true_positive=True,  # type: ignore[arg-type]
            false_positive=0,
            true_negative=10,
            false_negative=5,
        )

    identifier = ComponentIdentifier(class_name="DummyScorer", class_module="pyrit.score")
    entry = ScorerMetricsWithIdentity(
        scorer_identifier=identifier,
        metrics=ObjectiveScorerMetrics(
            num_responses=100,
            num_human_raters=1,
            accuracy=0.90,
            accuracy_standard_error=0.03,
            f1_score=0.90,
            precision=0.90,
            recall=0.90,
            true_positive=45,
            false_positive=5,
            true_negative=45,
            false_negative=5,
        ),
    )
    built = JudgeCalibrationProfile.from_scorer_metrics_with_identity(entry)
    assert built is not None
    assert built.scorer_hash == identifier.hash
    assert built.false_positive_rate == pytest.approx(5 / 50)

    coerced = JudgeCalibrationProfile(
        true_positive=10,
        false_positive=2,
        true_negative=18,
        false_negative=5,
        counts_source="recovered",  # type: ignore[arg-type]
    )
    assert coerced.counts_source == CalibrationCountsSource.RECOVERED

    with pytest.raises(ValueError, match="num_scorer_trials"):
        JudgeCalibrationProfile(
            true_positive=10,
            false_positive=1,
            true_negative=10,
            false_negative=1,
            num_scorer_trials=0,
        )

    with pytest.raises(ValueError, match="gold-positive"):
        JudgeCalibrationProfile(true_positive=0, false_positive=1, true_negative=10, false_negative=0)

    with pytest.raises(ValueError, match="gold-negative"):
        JudgeCalibrationProfile(true_positive=10, false_positive=0, true_negative=0, false_negative=1)

    negative_stored = ObjectiveScorerMetrics(
        num_responses=10,
        num_human_raters=1,
        accuracy=0.9,
        accuracy_standard_error=0.05,
        f1_score=0.9,
        precision=0.9,
        recall=0.9,
        true_positive=-1,
        false_positive=1,
        true_negative=5,
        false_negative=5,
    )
    with pytest.raises(ValueError, match="non-negative integers"):
        JudgeCalibrationProfile.from_objective_metrics(negative_stored)


def test_legacy_recovery_edge_branches_and_low_confidence_shrunk_youden_guard() -> None:
    denom_zero_imperfect = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=0.80,
        accuracy_standard_error=0.04,
        f1_score=1.0,
        precision=1.0,
        recall=1.0,
    )
    assert JudgeCalibrationProfile.from_objective_metrics(denom_zero_imperfect) is None

    negative_tn_legacy = ObjectiveScorerMetrics(
        num_responses=100,
        num_human_raters=1,
        accuracy=0.10,
        accuracy_standard_error=0.03,
        f1_score=0.90,
        precision=0.90,
        recall=0.90,
    )
    assert JudgeCalibrationProfile.from_objective_metrics(negative_tn_legacy) is None

    mismatch_specificity = ObjectiveScorerMetrics(
        num_responses=200,
        num_human_raters=1,
        accuracy=0.90,
        accuracy_standard_error=0.02,
        f1_score=0.90,
        precision=0.90,
        recall=0.90,
        specificity=0.25,
    )
    assert JudgeCalibrationProfile.from_objective_metrics(mismatch_specificity) is None

    legacy_outcomes_no_ci = OutcomeStatistics(
        successes=10,
        failures=90,
        undetermined=0,
        errors=0,
        total_decided=100,
        total_results=100,
        success_rate=0.10,
        success_rate_all=0.10,
        success_rate_ci_lower=None,
        success_rate_ci_upper=None,
        decided_share=1.0,
        outcome_shares={"success": 0.10, "failure": 0.90, "undetermined": 0.0, "error": 0.0},  # type: ignore[arg-type]
    )
    asymmetric_profile = JudgeCalibrationProfile(
        true_positive=2,
        false_positive=99,
        true_negative=1,
        false_negative=0,
    )
    fallback_95 = calibrate_outcome_statistics(legacy_outcomes_no_ci, judge_profile=asymmetric_profile)
    assert fallback_95.observed_ci_lower is not None and fallback_95.observed_ci_upper is not None

    shrunk_nonpositive = calibrate_outcome_statistics(
        legacy_outcomes_no_ci,
        judge_profile=asymmetric_profile,
        confidence_level=0.001,
        min_youden_index=0.0,
    )
    assert shrunk_nonpositive.calibrated_ci_lower == 0.0
    assert shrunk_nonpositive.calibrated_ci_upper == 1.0


def test_calibration_ignores_caller_decimal_traps_and_handles_subnormal_bounds() -> None:
    perfect_judge = JudgeCalibrationProfile(
        true_positive=10**325,
        false_positive=0,
        true_negative=10**325,
        false_negative=0,
    )
    huge_outcomes = OutcomeStatistics(
        successes=0,
        failures=10**325,
        undetermined=0,
        errors=0,
        total_decided=10**325,
        total_results=10**325,
        success_rate=0.0,
        success_rate_all=0.0,
        success_rate_ci_lower=0.0,
        success_rate_ci_upper=5e-324,
        decided_share=1.0,
        outcome_shares={"success": 0.0, "failure": 1.0, "undetermined": 0.0, "error": 0.0},  # type: ignore[arg-type]
    )

    baseline = calibrate_outcome_statistics(huge_outcomes, judge_profile=perfect_judge, confidence_level=0.90)
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_CEILING
        for trap in (Clamped, DivisionByZero, FloatOperation, Inexact, Overflow, Rounded, Subnormal, Underflow):
            ctx.traps[trap] = True
        under_traps = calibrate_outcome_statistics(huge_outcomes, judge_profile=perfect_judge, confidence_level=0.90)

    assert under_traps == baseline
    assert under_traps.is_reliable is True
    assert under_traps.observed_ci_upper == 5e-324
    assert under_traps.calibrated_ci_lower == 0.0
    assert under_traps.calibrated_ci_upper == 5e-324


@pytest.mark.usefixtures("patch_central_database")
def test_registry_loaded_profile_preserves_eval_hash_and_binds_to_live_scorer(tmp_path: Path) -> None:
    live_scorer = SubStringScorer(substring="target_marker")
    live_identifier = live_scorer.get_identifier()
    assert live_identifier.eval_hash is not None

    registry_file = tmp_path / "objective_metrics.jsonl"
    metrics = ObjectiveScorerMetrics(
        num_responses=200,
        num_human_raters=1,
        accuracy=0.92,
        accuracy_standard_error=0.019,
        f1_score=0.92,
        precision=0.92,
        recall=0.92,
        specificity=0.92,
        true_positive=92,
        false_positive=8,
        true_negative=92,
        false_negative=8,
    )
    add_evaluation_results(
        file_path=registry_file,
        scorer_identifier=live_identifier,
        eval_hash=live_identifier.eval_hash,
        metrics=metrics,
    )

    loaded = get_all_objective_metrics(file_path=registry_file)
    assert len(loaded) == 1
    profile = JudgeCalibrationProfile.from_scorer_metrics_with_identity(loaded[0])
    assert profile is not None
    assert profile.eval_hash == live_identifier.eval_hash
    assert profile.counts_source == CalibrationCountsSource.STORED

    outcomes = compute_outcome_statistics({"success": 25, "failure": 75})
    calibrated = calibrate_outcome_statistics(
        outcomes,
        judge_profile=profile,
        expected_eval_hash=live_identifier.eval_hash,
    )
    assert calibrated.is_reliable is True
    assert calibrated.judge_profile.eval_hash == live_identifier.eval_hash

    with pytest.raises(ValueError, match="expected_eval_hash"):
        calibrate_outcome_statistics(outcomes, judge_profile=profile, expected_eval_hash="different_live_hash")


def test_large_cohort_precision_scaling_avoids_cancellation() -> None:
    large_n = 10**100
    perfect_profile = JudgeCalibrationProfile(
        true_positive=large_n,
        false_positive=0,
        true_negative=large_n,
        false_negative=0,
    )
    one_success = compute_outcome_statistics({"success": 1, "failure": large_n - 1})
    result = calibrate_outcome_statistics(one_success, judge_profile=perfect_profile)

    assert result.is_reliable is True
    assert result.was_clipped is False
    assert result.calibrated_success_rate == pytest.approx(1e-100, rel=1e-12)
    assert result.calibrated_ci_lower == 0.0
    assert result.calibrated_ci_upper == pytest.approx(5.802e-100, rel=1e-3)

    near_one = compute_outcome_statistics({"success": large_n - 1, "failure": 1})
    near_one_result = calibrate_outcome_statistics(near_one, judge_profile=perfect_profile, confidence_level=0.90)
    assert near_one_result.is_reliable is True
    assert near_one_result.calibrated_success_rate == pytest.approx(1.0, rel=1e-12)
    assert near_one_result.calibrated_ci_upper == 1.0

    tiny_youden = JudgeCalibrationProfile(
        true_positive=1,
        false_positive=0,
        true_negative=large_n,
        false_negative=large_n - 1,
    )
    assert tiny_youden.youden_index == pytest.approx(1e-100, rel=1e-12)

    # Exceeds Python's default int_max_str_digits (4300) without triggering string conversion.
    huge_n = 10**5000
    huge_profile = JudgeCalibrationProfile(
        true_positive=huge_n,
        false_positive=0,
        true_negative=huge_n,
        false_negative=0,
    )
    huge_zero = OutcomeStatistics(
        successes=0,
        failures=huge_n,
        undetermined=0,
        errors=0,
        total_decided=huge_n,
        total_results=huge_n,
        success_rate=0.0,
        success_rate_all=0.0,
        success_rate_ci_lower=0.0,
        success_rate_ci_upper=5e-324,
        decided_share=1.0,
        outcome_shares={"success": 0.0, "failure": 1.0, "undetermined": 0.0, "error": 0.0},  # type: ignore[arg-type]
    )
    huge_result = calibrate_outcome_statistics(huge_zero, judge_profile=huge_profile)
    assert huge_result.is_reliable is True
    assert huge_result.calibrated_ci_upper == 5e-324


def test_five_thousand_point_grid_preserves_interval_invariants() -> None:
    profiles = [
        JudgeCalibrationProfile(true_positive=190, false_positive=2, true_negative=198, false_negative=10),
        JudgeCalibrationProfile(true_positive=172, false_positive=22, true_negative=182, false_negative=19),
        JudgeCalibrationProfile(true_positive=132, false_positive=37, true_negative=167, false_negative=59),
        JudgeCalibrationProfile(true_positive=71, false_positive=1, true_negative=203, false_negative=120),
        JudgeCalibrationProfile(true_positive=200, false_positive=0, true_negative=200, false_negative=0),
    ]
    sample_sizes = [1, 2, 5, 10, 25, 50, 100, 500, 10_000, 1_000_000, 2**50]
    checked = 0

    for profile in profiles:
        for n in sample_sizes:
            if n <= 100:
                ks = list(range(n + 1))
            else:
                step = max(1, n // 250)
                ks = sorted({0, 1, 2, n // 4, n // 2, (3 * n) // 4, n - 2, n - 1, n, *range(0, n + 1, step)})
            for k in ks:
                outcomes = compute_outcome_statistics({"success": k, "failure": n - k})
                res = calibrate_outcome_statistics(outcomes, judge_profile=profile)
                checked += 1
                assert res.is_reliable is True
                assert res.calibrated_success_rate is not None
                assert res.calibrated_ci_lower is not None
                assert res.calibrated_ci_upper is not None
                assert math.isfinite(res.calibrated_success_rate)
                assert math.isfinite(res.calibrated_ci_lower)
                assert math.isfinite(res.calibrated_ci_upper)
                assert 0.0 <= res.calibrated_success_rate <= 1.0
                assert 0.0 <= res.calibrated_ci_lower <= res.calibrated_ci_upper <= 1.0

    assert checked >= 5000
