# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Outcome-count calculations shared by attack and scenario analytics."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from pyrit.models import AttackOutcome, OutcomeStatistics

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pyrit.models import AttackStats


def compute_outcome_statistics(counts: Mapping[str, int] | Mapping[AttackOutcome, int]) -> OutcomeStatistics:
    """
    Calculate both success rates for an already selected population.

    This function neither queries memory nor chooses result IDs, retries, roles,
    or scenario units. ``success_rate_decided`` (also ``success_rate``) uses success + failure; ``success_rate_all``
    uses every outcome. Both are proportions, not rounded percentages.

    Args:
        counts (Mapping[str, int] | Mapping[AttackOutcome, int]): Nonnegative saved-outcome
            counts. Omitted outcomes mean zero observations.

    Returns:
        OutcomeStatistics: Counts, both denominator policies, and whole-population shares.
            Empty denominators produce None; empty outcome shares are zero.

    Raises:
        ValueError: If an outcome is unsupported or a count is not a nonnegative integer.
    """
    values = _validated_counts(counts)
    successes = values.get(AttackOutcome.SUCCESS, 0)
    failures = values.get(AttackOutcome.FAILURE, 0)
    undetermined = values.get(AttackOutcome.UNDETERMINED, 0)
    errors = values.get(AttackOutcome.ERROR, 0)
    decided = successes + failures
    total = decided + undetermined + errors
    return OutcomeStatistics(
        successes=successes,
        failures=failures,
        undetermined=undetermined,
        errors=errors,
        total_decided=decided,
        total_results=total,
        success_rate=_rate(successes=successes, total=decided),
        success_rate_all=_rate(successes=successes, total=total),
        decided_share=_rate(successes=decided, total=total),
        outcome_shares={
            outcome: _rate(successes=values.get(outcome, 0), total=total) or 0.0 for outcome in AttackOutcome
        },
    )


def combine_outcome_statistics(statistics: Iterable[AttackStats]) -> OutcomeStatistics:
    """
    Sum disjoint populations' outcome counts and recalculate both rates.

    Never average subgroup percentages. The caller must ensure its populations
    are disjoint; overlapping harm/converter groups cannot reconstruct a cohort.
    Existing ``AttackStats`` objects are accepted without changing their legacy shape.

    Args:
        statistics (Iterable[AttackStats]): Count-bearing statistics for disjoint populations.

    Returns:
        OutcomeStatistics: Statistics from the combined counts, including an empty population.

    Raises:
        ValueError: If any input count is invalid, even if another input would offset it.
    """
    counts: Counter[AttackOutcome] = Counter()
    for item in statistics:
        if isinstance(item, OutcomeStatistics):
            item.validate_consistency()
        counts.update(
            _validated_counts(
                {
                    AttackOutcome.SUCCESS: item.successes,
                    AttackOutcome.FAILURE: item.failures,
                    AttackOutcome.UNDETERMINED: item.undetermined,
                    AttackOutcome.ERROR: item.errors,
                }
            )
        )
    return compute_outcome_statistics(counts)


def success_percentage(*, succeeded: int, completed: int) -> int | None:
    """
    Format a chosen denominator's success rate as a legacy integer percentage.

    Args:
        succeeded (int): Successful observations.
        completed (int): Observations in the chosen denominator.

    Returns:
        int | None: The shared rate truncated to an integer percentage, or None if empty.
    """
    rate = _rate(successes=succeeded, total=completed)
    return int(rate * 100) if rate is not None else None


def _validated_counts(counts: Mapping[str, int] | Mapping[AttackOutcome, int]) -> dict[AttackOutcome, int]:
    values: dict[AttackOutcome, int] = {}
    for name, count in counts.items():
        try:
            outcome = AttackOutcome(name)
        except ValueError as error:
            raise ValueError("Stored results contain an unsupported attack outcome.") from error
        if type(count) is not int or count < 0:
            raise ValueError("Stored results contain invalid outcome counts.")
        values[outcome] = count
    return values


def _rate(*, successes: int, total: int) -> float | None:
    return successes / total if total else None
