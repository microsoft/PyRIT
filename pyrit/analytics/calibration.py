# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in calibration of decided attack success rates using objective scorer evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import Enum
from math import isclose, isfinite, nextafter
from statistics import NormalDist
from typing import TYPE_CHECKING, Self

from pyrit.models.analytics import compute_wilson_ci

if TYPE_CHECKING:
    from pyrit.models import OutcomeStatistics
    from pyrit.score import ObjectiveScorerMetrics, ScorerMetricsWithIdentity

_CALIBRATION_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    Emin=MIN_EMIN,
    Emax=MAX_EMAX,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[],
)
_Z_95 = Decimal("1.959963984540054")
_DECIDED_SHARE_WARNING_THRESHOLD = 0.80


class CalibrationCountsSource(str, Enum):
    """How confusion-matrix counts in ``JudgeCalibrationProfile`` were obtained."""

    #: Loaded directly from persisted confusion-matrix integer fields on ``ObjectiveScorerMetrics``.
    STORED = "stored"
    #: Algebraically recovered from ``num_responses``, ``accuracy``, ``precision``, and ``recall``.
    RECOVERED = "recovered"


class CalibrationWarningCode(str, Enum):
    """Diagnostic codes attached to ``CalibratedOutcomeStatistics``."""

    #: ``total_decided`` is zero, so neither observed nor calibrated decided rates exist.
    EMPTY_DECIDED_COHORT = "empty_decided_cohort"
    #: Empirical Youden's index falls at or below ``min_youden_index``.
    LOW_YOUDEN_INDEX = "low_youden_index"
    #: Lower confidence bound on Youden's index is at or below zero.
    YOUDEN_CI_INCLUDES_ZERO = "youden_ci_includes_zero"
    #: Less than 80% of total outcomes reached a decided verdict (potential selection bias).
    LOW_DECIDED_SHARE = "low_decided_share"
    #: Calibration metrics used majority voting across multiple scorer trials.
    MULTI_TRIAL_CALIBRATION = "multi_trial_calibration"
    #: Unclipped Rogan-Gladen point estimate fell outside ``[0, 1]`` and was clipped to the boundary.
    CLIPPED_PREVALENCE_ESTIMATE = "clipped_prevalence_estimate"


@dataclass(frozen=True, slots=True, kw_only=True)
class JudgeCalibrationProfile:
    """
    Confusion-matrix error profile and provenance for an objective binary scorer.

    Attributes:
        true_positive (int): Gold-positive evaluation items scored positive.
        false_positive (int): Gold-negative evaluation items scored positive.
        true_negative (int): Gold-negative evaluation items scored negative.
        false_negative (int): Gold-positive evaluation items scored negative.
        eval_hash (str | None): Behavioral evaluation hash of the scorer, when known.
        scorer_hash (str | None): Full component configuration hash of the scorer, when known.
        scorer_class_name (str | None): Scorer class name for display provenance.
        dataset_name (str | None): Human-labeled dataset name used to evaluate the scorer.
        dataset_version (str | None): Version of the human-labeled evaluation dataset.
        num_scorer_trials (int): Number of scoring trials aggregated during evaluation.
        counts_source (CalibrationCountsSource): Whether counts were stored directly or recovered.
    """

    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    eval_hash: str | None = None
    scorer_hash: str | None = None
    scorer_class_name: str | None = None
    dataset_name: str | None = None
    dataset_version: str | None = None
    num_scorer_trials: int = 1
    counts_source: CalibrationCountsSource = CalibrationCountsSource.STORED

    def __post_init__(self) -> None:
        """
        Validate confusion counts, class denominators, and trial count.

        Raises:
            ValueError: If any count or ``num_scorer_trials`` is invalid or a gold class is empty.
        """
        for field_name in ("true_positive", "false_positive", "true_negative", "false_negative"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer.")
        if type(self.num_scorer_trials) is not int or self.num_scorer_trials < 1:
            raise ValueError("num_scorer_trials must be a positive integer.")
        if not isinstance(self.counts_source, CalibrationCountsSource):
            object.__setattr__(self, "counts_source", CalibrationCountsSource(self.counts_source))
        if self.true_positive + self.false_negative == 0:
            raise ValueError("JudgeCalibrationProfile requires at least one gold-positive evaluation example.")
        if self.true_negative + self.false_positive == 0:
            raise ValueError("JudgeCalibrationProfile requires at least one gold-negative evaluation example.")

    @property
    def num_gold_positive(self) -> int:
        """Number of gold-positive evaluation examples (``true_positive + false_negative``)."""
        return self.true_positive + self.false_negative

    @property
    def num_gold_negative(self) -> int:
        """Number of gold-negative evaluation examples (``true_negative + false_positive``)."""
        return self.true_negative + self.false_positive

    @property
    def num_evaluated_responses(self) -> int:
        """Total number of decided evaluation examples across both gold classes."""
        return self.num_gold_positive + self.num_gold_negative

    @property
    def sensitivity(self) -> float:
        """Empirical true positive rate ``TP / (TP + FN)``."""
        return self.true_positive / self.num_gold_positive

    @property
    def specificity(self) -> float:
        """Empirical true negative rate ``TN / (TN + FP)``."""
        return self.true_negative / self.num_gold_negative

    @property
    def false_positive_rate(self) -> float:
        """Empirical false positive rate ``FP / (TN + FP)``."""
        return self.false_positive / self.num_gold_negative

    @property
    def youden_index(self) -> float:
        """Empirical Youden's J statistic ``sensitivity + specificity - 1``."""
        with localcontext(_CALIBRATION_DECIMAL_CONTEXT):
            se = Decimal(self.true_positive) / Decimal(self.num_gold_positive)
            sp = Decimal(self.true_negative) / Decimal(self.num_gold_negative)
            return float(se + sp - Decimal(1))

    @classmethod
    def from_objective_metrics(
        cls,
        metrics: ObjectiveScorerMetrics,
        *,
        eval_hash: str | None = None,
        scorer_hash: str | None = None,
        scorer_class_name: str | None = None,
    ) -> Self | None:
        """
        Build a ``JudgeCalibrationProfile`` from ``ObjectiveScorerMetrics``.

        Uses persisted confusion-matrix counts when present. For metrics recorded before
        confusion counts were stored, algebraically recovers exact integer counts from
        ``num_responses``, ``accuracy``, ``precision``, and ``recall`` when the system has
        full rank and round-trips within ``1e-12``.

        Args:
            metrics (ObjectiveScorerMetrics): Objective scorer evaluation metrics.
            eval_hash (str | None): Optional behavioral evaluation hash of the scorer.
            scorer_hash (str | None): Optional component configuration hash of the scorer.
            scorer_class_name (str | None): Optional scorer class name for provenance.

        Returns:
            Self | None: Validated calibration profile, or ``None`` when counts are absent and
                cannot be uniquely recovered or lack both gold classes.

        Raises:
            ValueError: If persisted confusion counts are only partially populated or contradict
                ``num_responses``.
        """
        stored = (metrics.true_positive, metrics.false_positive, metrics.true_negative, metrics.false_negative)
        if all(count is not None for count in stored):
            if any(type(count) is not int or count < 0 for count in stored):
                raise ValueError("Persisted confusion counts on ObjectiveScorerMetrics must be non-negative integers.")
            assert stored[0] is not None and stored[1] is not None and stored[2] is not None and stored[3] is not None
            tp, fp, tn, fn = stored[0], stored[1], stored[2], stored[3]
            if metrics.num_responses != tp + fp + tn + fn:
                raise ValueError("Persisted confusion counts must sum to ObjectiveScorerMetrics.num_responses.")
            if tp + fn == 0 or tn + fp == 0:
                return None
            return cls(
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
                eval_hash=eval_hash,
                scorer_hash=scorer_hash,
                scorer_class_name=scorer_class_name,
                dataset_name=metrics.dataset_name,
                dataset_version=metrics.dataset_version,
                num_scorer_trials=metrics.num_scorer_trials,
                counts_source=CalibrationCountsSource.STORED,
            )

        if any(count is not None for count in stored):
            raise ValueError("ObjectiveScorerMetrics has partially populated confusion counts.")

        recovered = _recover_confusion_counts(metrics)
        if recovered is None:
            return None
        tp, fp, tn, fn = recovered
        return cls(
            true_positive=tp,
            false_positive=fp,
            true_negative=tn,
            false_negative=fn,
            eval_hash=eval_hash,
            scorer_hash=scorer_hash,
            scorer_class_name=scorer_class_name,
            dataset_name=metrics.dataset_name,
            dataset_version=metrics.dataset_version,
            num_scorer_trials=metrics.num_scorer_trials,
            counts_source=CalibrationCountsSource.RECOVERED,
        )

    @classmethod
    def from_scorer_metrics_with_identity(
        cls,
        entry: ScorerMetricsWithIdentity[ObjectiveScorerMetrics],
    ) -> Self | None:
        """
        Build a ``JudgeCalibrationProfile`` carrying identity hashes from ``ScorerMetricsWithIdentity``.

        Args:
            entry (ScorerMetricsWithIdentity[ObjectiveScorerMetrics]): Metrics paired with scorer identity.

        Returns:
            Self | None: Validated calibration profile with scorer provenance, or ``None`` if unrecoverable.
        """
        identifier = entry.scorer_identifier
        return cls.from_objective_metrics(
            entry.metrics,
            eval_hash=identifier.eval_hash,
            scorer_hash=identifier.hash,
            scorer_class_name=identifier.class_name,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CalibratedOutcomeStatistics:
    """
    Opt-in judge-calibrated estimate for the decided-outcome attack success rate.

    Calibration applies only to ``success_rate_decided`` under the assumption that the
    scorer's evaluation sensitivity and specificity transfer non-differentially to single
    judged outcomes in the target cohort. Observed counts and Wilson bounds are always
    preserved alongside the calibrated result.

    Attributes:
        total_decided (int): Decided attack outcomes (``successes + failures``).
        total_results (int): All selected attack outcomes including undetermined and errors.
        decided_share (float | None): Share of ``total_results`` that reached a decided outcome.
        observed_success_rate (float | None): Unadjusted ``success_rate_decided``.
        observed_ci_lower (float | None): Lower Wilson confidence bound for ``observed_success_rate``.
        observed_ci_upper (float | None): Upper Wilson confidence bound for ``observed_success_rate``.
        calibrated_success_rate (float | None): Clipped Rogan-Gladen prevalence estimate, or ``None``
            when ``total_decided == 0`` or ``is_reliable`` is ``False``.
        calibrated_ci_lower (float | None): Lower Lang-Reiczigel confidence bound, or ``None`` when
            ``total_decided == 0`` or ``is_reliable`` is ``False``.
        calibrated_ci_upper (float | None): Upper Lang-Reiczigel confidence bound, or ``None`` when
            ``total_decided == 0`` or ``is_reliable`` is ``False``.
        confidence_level (float): Confidence level used for both observed and calibrated intervals.
        youden_index (float): Empirical Youden's J of ``judge_profile``.
        youden_ci_lower (float): Lower MOVER-Wilson confidence bound for Youden's J at ``confidence_level``.
        is_reliable (bool): Whether calibration passed both Youden's J reliability checks and had decided data.
        was_clipped (bool): Whether the unclipped Rogan-Gladen point estimate fell outside ``[0, 1]``.
        warning_codes (tuple[CalibrationWarningCode, ...]): Machine-readable diagnostic warning codes.
        warnings (tuple[str, ...]): Human-readable diagnostic messages explaining reliability or scope caveats.
        judge_profile (JudgeCalibrationProfile): Calibration profile and provenance supplied by the caller.
    """

    total_decided: int
    total_results: int
    decided_share: float | None
    observed_success_rate: float | None
    observed_ci_lower: float | None
    observed_ci_upper: float | None
    calibrated_success_rate: float | None
    calibrated_ci_lower: float | None
    calibrated_ci_upper: float | None
    confidence_level: float
    youden_index: float
    youden_ci_lower: float
    is_reliable: bool
    was_clipped: bool
    warning_codes: tuple[CalibrationWarningCode, ...]
    warnings: tuple[str, ...]
    judge_profile: JudgeCalibrationProfile


def calibrate_outcome_statistics(
    statistics: OutcomeStatistics,
    *,
    judge_profile: JudgeCalibrationProfile,
    confidence_level: float = 0.95,
    min_youden_index: float = 0.20,
    expected_eval_hash: str | None = None,
) -> CalibratedOutcomeStatistics:
    """
    Compute an opt-in Rogan-Gladen point estimate and Lang-Reiczigel confidence interval.

    ``compute_outcome_statistics`` remains unchanged; callers opt into this helper explicitly
    with a ``JudgeCalibrationProfile`` matching the single-response objective scorer that
    produced ``statistics``. When Youden's J is at or below ``min_youden_index`` or its lower
    confidence bound is at or below zero, calibrated estimates are suppressed (``None``) and
    ``is_reliable`` is ``False``.

    Args:
        statistics (OutcomeStatistics): Validated outcome statistics for a single-scorer cohort.
        judge_profile (JudgeCalibrationProfile): Scorer confusion-matrix profile and provenance.
        confidence_level (float): Confidence level strictly between 0 and 1. Defaults to 0.95.
        min_youden_index (float): Minimum empirical Youden's J required to report a calibrated
            estimate, in ``[0, 1)``. Defaults to 0.20.
        expected_eval_hash (str | None): When supplied, requires ``judge_profile.eval_hash`` to
            match so calibration from a different scorer configuration is rejected.

    Returns:
        CalibratedOutcomeStatistics: Observed and calibrated rates, bounds, reliability flags,
            diagnostic warnings, and scorer provenance.

    Raises:
        ValueError: If ``statistics`` fails consistency checks, ``confidence_level`` or
            ``min_youden_index`` is out of range, or ``expected_eval_hash`` disagrees with
            ``judge_profile.eval_hash``.
    """
    _validate_calibration_inputs(
        statistics=statistics,
        judge_profile=judge_profile,
        confidence_level=confidence_level,
        min_youden_index=min_youden_index,
        expected_eval_hash=expected_eval_hash,
    )
    z = _normal_quantile_decimal(confidence_level)
    youden_j = judge_profile.youden_index
    youden_lower = _mover_youden_lower_bound(profile=judge_profile, z=z)
    codes, messages = _collect_scope_warnings(statistics=statistics, judge_profile=judge_profile)

    if statistics.total_decided == 0:
        codes.append(CalibrationWarningCode.EMPTY_DECIDED_COHORT)
        messages.append("Cohort has zero decided outcomes; calibrated success rate is undefined.")
        return _build_uncalibrated_result(
            statistics=statistics,
            judge_profile=judge_profile,
            confidence_level=confidence_level,
            observed_ci=(None, None),
            youden_index=youden_j,
            youden_ci_lower=youden_lower,
            codes=codes,
            messages=messages,
        )

    observed_ci = _observed_wilson_ci(
        statistics=statistics,
        confidence_level=confidence_level,
        z=z,
    )
    _append_youden_reliability_warnings(
        youden_index=youden_j,
        youden_ci_lower=youden_lower,
        min_youden_index=min_youden_index,
        codes=codes,
        messages=messages,
    )
    if CalibrationWarningCode.LOW_YOUDEN_INDEX in codes or CalibrationWarningCode.YOUDEN_CI_INCLUDES_ZERO in codes:
        return _build_uncalibrated_result(
            statistics=statistics,
            judge_profile=judge_profile,
            confidence_level=confidence_level,
            observed_ci=observed_ci,
            youden_index=youden_j,
            youden_ci_lower=youden_lower,
            codes=codes,
            messages=messages,
        )

    point, ci_lower, ci_upper, was_clipped = _lang_reiczigel_interval(
        successes=statistics.successes,
        total=statistics.total_decided,
        profile=judge_profile,
        z=z,
    )
    if was_clipped:
        codes.append(CalibrationWarningCode.CLIPPED_PREVALENCE_ESTIMATE)
        messages.append(
            "Unclipped Rogan-Gladen prevalence fell outside [0, 1] and was clipped to [0, 1]; "
            "the Lang-Reiczigel interval remains centered on the Wilson/Laplace-shrunk estimate."
        )
    return CalibratedOutcomeStatistics(
        total_decided=statistics.total_decided,
        total_results=statistics.total_results,
        decided_share=statistics.decided_share,
        observed_success_rate=statistics.success_rate,
        observed_ci_lower=observed_ci[0],
        observed_ci_upper=observed_ci[1],
        calibrated_success_rate=point,
        calibrated_ci_lower=ci_lower,
        calibrated_ci_upper=ci_upper,
        confidence_level=confidence_level,
        youden_index=youden_j,
        youden_ci_lower=youden_lower,
        is_reliable=True,
        was_clipped=was_clipped,
        warning_codes=tuple(codes),
        warnings=tuple(messages),
        judge_profile=judge_profile,
    )


def _validate_calibration_inputs(
    *,
    statistics: OutcomeStatistics,
    judge_profile: JudgeCalibrationProfile,
    confidence_level: float,
    min_youden_index: float,
    expected_eval_hash: str | None,
) -> None:
    """
    Validate input types, ranges, outcome consistency, and optional eval-hash binding.

    Raises:
        ValueError: If ``statistics`` is inconsistent, parameters are out of range, or
            ``expected_eval_hash`` mismatches.
    """
    statistics.validate_consistency()
    if (
        isinstance(confidence_level, bool)
        or not isinstance(confidence_level, (int, float))
        or not isfinite(confidence_level)
        or not 0.0 < confidence_level < 1.0
    ):
        raise ValueError("confidence_level must be a finite float strictly between 0 and 1.")
    if (
        isinstance(min_youden_index, bool)
        or not isinstance(min_youden_index, (int, float))
        or not isfinite(min_youden_index)
        or not 0.0 <= min_youden_index < 1.0
    ):
        raise ValueError("min_youden_index must be a finite float in [0, 1).")
    if expected_eval_hash is not None and judge_profile.eval_hash != expected_eval_hash:
        raise ValueError(
            f"judge_profile.eval_hash ({judge_profile.eval_hash!r}) does not match "
            f"expected_eval_hash ({expected_eval_hash!r})."
        )


def _normal_quantile_decimal(confidence_level: float) -> Decimal:
    """
    Compute the two-sided standard normal quantile for ``confidence_level``.

    Returns:
        Decimal: Standard normal quantile ``z``.
    """
    if confidence_level == 0.95:
        return _Z_95
    z_float = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    return Decimal(str(z_float))


def _wilson_bounds_decimal(*, successes: int, total: int, z: Decimal) -> tuple[Decimal, Decimal]:
    """
    Compute exact Decimal Wilson score bounds inside an active ``_CALIBRATION_DECIMAL_CONTEXT``.

    Returns:
        tuple[Decimal, Decimal]: Lower and upper Wilson score bounds in ``[0, 1]``.
    """
    n = Decimal(total)
    p = Decimal(successes) / n
    q = Decimal(total - successes) / n
    z2_inv_n = (z * z) / n
    denom = Decimal(1) + z2_inv_n
    center = (p + Decimal("0.5") * z2_inv_n) / denom
    half_width = (z * ((p * q + Decimal("0.25") * z2_inv_n) / n).sqrt()) / denom
    lower = Decimal(0) if successes == 0 else max(Decimal(0), center - half_width)
    upper = Decimal(1) if successes == total else min(Decimal(1), center + half_width)
    return lower, upper


def _decimal_upper_to_float(upper_decimal: Decimal) -> float:
    """
    Convert an upper probability bound to ``float``, rounding subnormal positives outward.

    Returns:
        float: Upper bound rounded outward to at least ``nextafter(0.0, 1.0)`` when positive.
    """
    upper = float(upper_decimal)
    if upper == 0.0 and upper_decimal > 0:
        return nextafter(0.0, 1.0)
    return upper


def _observed_wilson_ci(
    *,
    statistics: OutcomeStatistics,
    confidence_level: float,
    z: Decimal,
) -> tuple[float | None, float | None]:
    """
    Resolve observed Wilson bounds, reusing ``compute_wilson_ci`` at 95% confidence.

    Returns:
        tuple[float | None, float | None]: Observed lower and upper Wilson confidence bounds.
    """
    if confidence_level == 0.95:
        if statistics.success_rate_ci_lower is not None and statistics.success_rate_ci_upper is not None:
            return statistics.success_rate_ci_lower, statistics.success_rate_ci_upper
        return compute_wilson_ci(successes=statistics.successes, total=statistics.total_decided)
    with localcontext(_CALIBRATION_DECIMAL_CONTEXT):
        lower_dec, upper_dec = _wilson_bounds_decimal(
            successes=statistics.successes,
            total=statistics.total_decided,
            z=z,
        )
        return float(lower_dec), _decimal_upper_to_float(upper_dec)


def _mover_youden_lower_bound(*, profile: JudgeCalibrationProfile, z: Decimal) -> float:
    """
    Compute the MOVER-Wilson lower confidence bound for ``sensitivity + specificity - 1``.

    Returns:
        float: Two-sided lower confidence bound on Youden's J.
    """
    with localcontext(_CALIBRATION_DECIMAL_CONTEXT):
        m1 = Decimal(profile.num_gold_positive)
        m0 = Decimal(profile.num_gold_negative)
        se = Decimal(profile.true_positive) / m1
        sp = Decimal(profile.true_negative) / m0
        se_lower, _ = _wilson_bounds_decimal(successes=profile.true_positive, total=profile.num_gold_positive, z=z)
        sp_lower, _ = _wilson_bounds_decimal(successes=profile.true_negative, total=profile.num_gold_negative, z=z)
        radius = ((se - se_lower) ** 2 + (sp - sp_lower) ** 2).sqrt()
        return float(se + sp - Decimal(1) - radius)


def _collect_scope_warnings(
    *,
    statistics: OutcomeStatistics,
    judge_profile: JudgeCalibrationProfile,
) -> tuple[list[CalibrationWarningCode], list[str]]:
    """
    Collect non-blocking warnings about missing verdicts and multi-trial scorer metrics.

    Returns:
        tuple[list[CalibrationWarningCode], list[str]]: Warning codes and messages.
    """
    codes: list[CalibrationWarningCode] = []
    messages: list[str] = []
    if (
        statistics.total_decided > 0
        and statistics.decided_share is not None
        and statistics.decided_share < _DECIDED_SHARE_WARNING_THRESHOLD
    ):
        codes.append(CalibrationWarningCode.LOW_DECIDED_SHARE)
        messages.append(
            f"Only {statistics.decided_share:.1%} of outcomes reached a decided verdict; "
            "undetermined or errored attempts may bias success_rate_decided."
        )
    if judge_profile.num_scorer_trials > 1:
        codes.append(CalibrationWarningCode.MULTI_TRIAL_CALIBRATION)
        messages.append(
            f"Calibration profile was evaluated using majority vote across {judge_profile.num_scorer_trials} "
            "scorer trials; ensure attack scoring uses matching trial aggregation."
        )
    return codes, messages


def _append_youden_reliability_warnings(
    *,
    youden_index: float,
    youden_ci_lower: float,
    min_youden_index: float,
    codes: list[CalibrationWarningCode],
    messages: list[str],
) -> None:
    """Append blocking reliability warnings when Youden's J is too low or statistically indistinct from zero."""
    if youden_index <= min_youden_index:
        codes.append(CalibrationWarningCode.LOW_YOUDEN_INDEX)
        messages.append(
            f"Judge Youden's index ({youden_index:.3f}) is at or below min_youden_index "
            f"({min_youden_index:.3f}); suppressing unstable Rogan-Gladen calibration."
        )
    if youden_ci_lower <= 0.0:
        codes.append(CalibrationWarningCode.YOUDEN_CI_INCLUDES_ZERO)
        messages.append(
            f"Lower confidence bound on Youden's index ({youden_ci_lower:.3f}) is at or below 0.0; "
            "calibration sample cannot distinguish the scorer from chance."
        )


def _build_uncalibrated_result(
    *,
    statistics: OutcomeStatistics,
    judge_profile: JudgeCalibrationProfile,
    confidence_level: float,
    observed_ci: tuple[float | None, float | None],
    youden_index: float,
    youden_ci_lower: float,
    codes: list[CalibrationWarningCode],
    messages: list[str],
) -> CalibratedOutcomeStatistics:
    """
    Construct an uncalibrated fallback ``CalibratedOutcomeStatistics`` preserving observed bounds.

    Returns:
        CalibratedOutcomeStatistics: Unreliable/empty result with calibrated fields set to ``None``.
    """
    return CalibratedOutcomeStatistics(
        total_decided=statistics.total_decided,
        total_results=statistics.total_results,
        decided_share=statistics.decided_share,
        observed_success_rate=statistics.success_rate,
        observed_ci_lower=observed_ci[0],
        observed_ci_upper=observed_ci[1],
        calibrated_success_rate=None,
        calibrated_ci_lower=None,
        calibrated_ci_upper=None,
        confidence_level=confidence_level,
        youden_index=youden_index,
        youden_ci_lower=youden_ci_lower,
        is_reliable=False,
        was_clipped=False,
        warning_codes=tuple(codes),
        warnings=tuple(messages),
        judge_profile=judge_profile,
    )


def _lang_reiczigel_interval(
    *,
    successes: int,
    total: int,
    profile: JudgeCalibrationProfile,
    z: Decimal,
) -> tuple[float, float, float, bool]:
    """
    Compute the clipped Rogan-Gladen estimate and closed-form Lang-Reiczigel (2014) interval.

    Implements Equations 2, 4, 6, 7, 16, and 17 of Lang and Reiczigel (2014),
    propagating uncertainty from both the attack cohort ``total`` and the finite
    human-labeled evaluation strata ``num_gold_positive`` and ``num_gold_negative``.

    Returns:
        tuple[float, float, float, bool]: ``(point, ci_lower, ci_upper, was_clipped)``.
    """
    with localcontext(_CALIBRATION_DECIMAL_CONTEXT):
        m1 = Decimal(profile.num_gold_positive)
        m0 = Decimal(profile.num_gold_negative)
        se = Decimal(profile.true_positive) / m1
        sp = Decimal(profile.true_negative) / m0
        j = se + sp - Decimal(1)
        p_hat = Decimal(successes) / Decimal(total)

        raw_pi = (p_hat + sp - Decimal(1)) / j
        was_clipped = raw_pi < Decimal(0) or raw_pi > Decimal(1)
        clipped_pi = min(Decimal(1), max(Decimal(0), raw_pi))

        z2 = z * z
        m1_p = m1 + Decimal(2)
        m0_p = m0 + Decimal(2)
        se_p = (Decimal(profile.true_positive) + Decimal(1)) / m1_p
        sp_p = (Decimal(profile.true_negative) + Decimal(1)) / m0_p
        j_p = se_p + sp_p - Decimal(1)
        if j_p <= Decimal(0):
            return float(clipped_pi), 0.0, 1.0, was_clipped

        n_p = Decimal(total) + z2
        p_p = (Decimal(successes) + Decimal("0.5") * z2) / n_p
        pi_p = (p_p + sp_p - Decimal(1)) / j_p
        var_se_p = se_p * (Decimal(1) - se_p) / m1_p
        var_sp_p = sp_p * (Decimal(1) - sp_p) / m0_p
        d_pi = Decimal(2) * z2 * (pi_p * var_se_p - (Decimal(1) - pi_p) * var_sp_p)
        var_pi_p = (
            p_p * (Decimal(1) - p_p) / n_p + (pi_p * pi_p) * var_se_p + ((Decimal(1) - pi_p) ** 2) * var_sp_p
        ) / (j_p * j_p)
        half_width = z * max(Decimal(0), var_pi_p).sqrt()
        center = pi_p + d_pi
        lower_dec = min(Decimal(1), max(Decimal(0), center - half_width))
        upper_dec = min(Decimal(1), max(Decimal(0), center + half_width))
        return float(clipped_pi), float(lower_dec), _decimal_upper_to_float(upper_dec), was_clipped


def _recover_confusion_counts(metrics: ObjectiveScorerMetrics) -> tuple[int, int, int, int] | None:
    """
    Recover exact ``(TP, FP, TN, FN)`` integers from legacy summary metrics when full-rank.

    Returns:
        tuple[int, int, int, int] | None: Exact confusion counts, or ``None`` if rank-deficient or inconsistent.
    """
    n = metrics.num_responses
    acc = metrics.accuracy
    prec = metrics.precision
    rec = metrics.recall
    if (
        type(n) is not int
        or n <= 0
        or not all(not isinstance(v, bool) and isinstance(v, (int, float)) and isfinite(v) for v in (acc, prec, rec))
        or prec <= 0.0
        or rec <= 0.0
        or acc >= 1.0
    ):
        return None

    with localcontext(_CALIBRATION_DECIMAL_CONTEXT):
        acc_d = Decimal(str(acc))
        prec_d = Decimal(str(prec))
        rec_d = Decimal(str(rec))
        denom = (Decimal(1) - rec_d) / rec_d + (Decimal(1) - prec_d) / prec_d
        if denom <= Decimal(0):
            return None
        tp_dec = (Decimal(n) * (Decimal(1) - acc_d)) / denom
        tp = int(tp_dec.to_integral_value(rounding=ROUND_HALF_EVEN))
        fp = int((Decimal(tp) * (Decimal(1) - prec_d) / prec_d).to_integral_value(rounding=ROUND_HALF_EVEN))
        fn = int((Decimal(tp) * (Decimal(1) - rec_d) / rec_d).to_integral_value(rounding=ROUND_HALF_EVEN))
        tn = n - tp - fp - fn

    if tp <= 0 or fp < 0 or tn < 0 or fn < 0 or (tp + fn) == 0 or (tn + fp) == 0:
        return None
    if not (
        isclose((tp + tn) / n, acc, rel_tol=1e-12, abs_tol=1e-12)
        and isclose(tp / (tp + fp), prec, rel_tol=1e-12, abs_tol=1e-12)
        and isclose(tp / (tp + fn), rec, rel_tol=1e-12, abs_tol=1e-12)
    ):
        return None
    if metrics.specificity is not None and not isclose(
        tn / (tn + fp), metrics.specificity, rel_tol=1e-12, abs_tol=1e-12
    ):
        return None
    return tp, fp, tn, fn
