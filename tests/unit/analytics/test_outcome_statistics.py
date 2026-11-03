# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import asdict
from itertools import product

import pytest
from scipy.stats import binomtest

from pyrit.analytics import combine_outcome_statistics, compute_outcome_statistics
from pyrit.analytics.outcome_statistics import success_percentage
from pyrit.analytics.result_analysis import _compute_stats
from pyrit.models import AttackAnalyticsStatistics, AttackOutcome, AttackStats, OutcomeStatistics


@pytest.mark.parametrize(("successes", "failures", "undetermined", "errors"), tuple(product(range(3), repeat=4)))
def test_both_denominators_use_the_same_selected_outcome_counts(
    successes: int, failures: int, undetermined: int, errors: int
) -> None:
    counts = {
        AttackOutcome.SUCCESS: successes,
        AttackOutcome.FAILURE: failures,
        AttackOutcome.UNDETERMINED: undetermined,
        AttackOutcome.ERROR: errors,
    }
    statistics = compute_outcome_statistics(counts)
    decided = successes + failures
    total = sum(counts.values())
    assert statistics.total_results == total
    assert statistics.total_decided == decided
    assert statistics.success_rate == (successes / decided if decided else None)
    assert statistics.success_rate_all == (successes / total if total else None)
    assert statistics.decided_share == (decided / total if total else None)
    assert statistics.outcome_shares == {outcome: count / total if total else 0.0 for outcome, count in counts.items()}
    if decided == 0:
        assert statistics.success_rate_ci_lower is None
        assert statistics.success_rate_ci_upper is None
    else:
        expected_ci = binomtest(successes, decided).proportion_ci(confidence_level=0.95, method="wilson")
        assert statistics.success_rate_ci_lower == pytest.approx(expected_ci.low, rel=1e-12, abs=1e-12)
        assert statistics.success_rate_ci_upper == pytest.approx(expected_ci.high, rel=1e-12, abs=1e-12)
        assert (
            0.0
            <= statistics.success_rate_ci_lower
            <= statistics.success_rate
            <= statistics.success_rate_ci_upper
            <= 1.0
        )
    assert isinstance(statistics, OutcomeStatistics)
    assert isinstance(statistics, AttackStats)


def test_string_and_enum_counts_share_one_model_and_legacy_adapter() -> None:
    counts = {"success": 3, "failure": 1, "error": 2}
    statistics = compute_outcome_statistics(counts)
    assert statistics == compute_outcome_statistics({AttackOutcome(name): value for name, value in counts.items()})
    assert OutcomeStatistics is AttackAnalyticsStatistics
    assert statistics.success_rate == 0.75
    assert statistics.success_rate_all == 0.5
    assert statistics.undetermined == 0
    assert asdict(_compute_stats(successes=3, failures=1, errors=2, undetermined=0)) == {
        "success_rate": 0.75,
        "total_decided": 4,
        "successes": 3,
        "failures": 1,
        "errors": 2,
        "undetermined": 0,
    }


def test_empty_population_rates_are_unavailable_not_zero() -> None:
    statistics = compute_outcome_statistics({})
    assert statistics.success_rate is None
    assert statistics.success_rate_all is None
    assert statistics.success_rate_ci_lower is None
    assert statistics.success_rate_ci_upper is None
    assert statistics.decided_share is None
    assert statistics.total_results == 0
    assert set(statistics.outcome_shares.values()) == {0.0}
    assert combine_outcome_statistics([]) == statistics


def test_large_integer_counts_validate_without_float_overflow() -> None:
    count = 10**400
    statistics = compute_outcome_statistics({"success": count, "failure": count, "error": 2 * count})
    assert statistics.total_results == 4 * count
    assert statistics.success_rate_decided == 0.5
    assert statistics.success_rate_all == 0.25
    assert statistics.success_rate_ci_lower == 0.5
    assert statistics.success_rate_ci_upper == 0.5


def test_skewed_large_integer_counts_do_not_underflow_wilson_interval() -> None:
    total = 10**200
    statistics = compute_outcome_statistics({"success": 1, "failure": total - 1})
    assert statistics.success_rate_ci_lower is not None
    assert statistics.success_rate_ci_upper is not None
    assert 0.0 < statistics.success_rate_ci_lower < statistics.success_rate < statistics.success_rate_ci_upper
    assert statistics.success_rate_ci_lower == pytest.approx(1.7655213934448394e-201, rel=1e-12, abs=0.0)
    assert statistics.success_rate_ci_upper == pytest.approx(5.664049203893855e-200, rel=1e-12, abs=0.0)


@pytest.mark.parametrize(
    ("successes", "failures", "expected_lower", "expected_upper"),
    [
        (0, 50, 0.0, 0.07134759913335872),
        (25, 25, 0.3664451431682858, 0.6335548568317142),
        (50, 0, 0.9286524008666414, 1.0),
    ],
)
def test_wilson_confidence_interval_boundaries_and_interior(
    successes: int, failures: int, expected_lower: float, expected_upper: float
) -> None:
    statistics = compute_outcome_statistics({"success": successes, "failure": failures, "undetermined": 5, "error": 3})
    scipy_ci = binomtest(successes, successes + failures).proportion_ci(confidence_level=0.95, method="wilson")
    assert statistics.success_rate_ci_lower == pytest.approx(expected_lower, rel=1e-12, abs=1e-12)
    assert statistics.success_rate_ci_upper == pytest.approx(expected_upper, rel=1e-12, abs=1e-12)
    assert statistics.success_rate_ci_lower == pytest.approx(scipy_ci.low, rel=1e-12, abs=1e-12)
    assert statistics.success_rate_ci_upper == pytest.approx(scipy_ci.high, rel=1e-12, abs=1e-12)
    if successes == 0:
        assert statistics.success_rate_ci_lower == 0.0
    if failures == 0:
        assert statistics.success_rate_ci_upper == 1.0


@pytest.mark.parametrize("counts", [{"unexpected": 1}, {"success": -1}, {"success": True}, {"error": 1.5}])
def test_invalid_outcome_counts_are_explicit_errors(counts: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="unsupported|invalid"):
        compute_outcome_statistics(counts)


def test_combining_populations_recomputes_rates_instead_of_averaging() -> None:
    first = compute_outcome_statistics({"success": 1, "error": 9})
    second = compute_outcome_statistics({"success": 9, "failure": 91})
    combined = combine_outcome_statistics([first, second])
    assert combined.success_rate == pytest.approx(10 / 101)
    assert combined.success_rate_all == pytest.approx(10 / 110)
    assert combined.success_rate != (first.success_rate + second.success_rate) / 2
    assert combined.success_rate_all != (first.success_rate_all + second.success_rate_all) / 2
    assert combined == compute_outcome_statistics({"success": 10, "failure": 91, "error": 9})


def test_combining_legacy_attack_stats_uses_counts_not_caller_supplied_rates() -> None:
    legacy = AttackStats(0.99, 999, 1, 1, 1, 1)
    statistics = combine_outcome_statistics([legacy])
    assert statistics.success_rate == 0.5
    assert statistics.success_rate_all == 0.25
    assert statistics.total_decided == 2
    assert statistics.total_results == 4


def test_combining_rejects_invalid_counts_before_they_can_cancel_out() -> None:
    with pytest.raises(ValueError, match="invalid"):
        combine_outcome_statistics([AttackStats(0, 0, -1, 0, 0, 0), AttackStats(1, 1, 1, 0, 0, 0)])


def test_combining_rejects_mutated_rich_statistics_instead_of_silently_repairing_them() -> None:
    statistics = compute_outcome_statistics({"success": 1, "failure": 1})
    statistics.successes = 10
    with pytest.raises(ValueError):
        combine_outcome_statistics([statistics])


@pytest.mark.parametrize(
    ("succeeded", "completed", "expected"),
    [(0, 0, None), (0, 2, 0), (2, 3, 66), (1, 1, 100), (29, 100, 29), (29, 50, 58)],
)
def test_legacy_percentages_truncate_the_exact_ratio(succeeded: int, completed: int, expected: int | None) -> None:
    assert success_percentage(succeeded=succeeded, completed=completed) == expected
