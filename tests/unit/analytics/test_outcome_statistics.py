# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import asdict
from itertools import product

import pytest

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


@pytest.mark.parametrize(("succeeded", "completed", "expected"), [(0, 0, None), (0, 2, 0), (2, 3, 66), (1, 1, 100)])
def test_legacy_percentages_format_the_shared_rate(succeeded: int, completed: int, expected: int | None) -> None:
    assert success_percentage(succeeded=succeeded, completed=completed) == expected
