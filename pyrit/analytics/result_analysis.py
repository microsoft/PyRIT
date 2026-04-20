# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from pyrit.models import AttackOutcome, AttackResult


@dataclass
class AttackStats:
    """Statistics for attack analysis results."""

    success_rate: Optional[float]
    total_decided: int
    successes: int
    failures: int
    undetermined: int


def _compute_stats(successes: int, failures: int, undetermined: int) -> AttackStats:
    total_decided = successes + failures
    success_rate = successes / total_decided if total_decided > 0 else None
    return AttackStats(
        success_rate=success_rate,
        total_decided=total_decided,
        successes=successes,
        failures=failures,
        undetermined=undetermined,
    )


def analyze_results(attack_results: list[AttackResult]) -> dict[str, AttackStats | dict[str, AttackStats]]:
    """
    Analyze a list of AttackResult objects and return overall and grouped statistics.

    Returns:
        A dictionary of AttackStats objects. The overall stats are accessible with the key
        "Overall", and the stats of any attack can be retrieved using "By_attack_identifier"
        followed by the identifier of the attack.

    Raises:
        ValueError: if attack_results is empty.
        TypeError: if any element is not an AttackResult.

    Example:
        >>> analyze_results(attack_results)
        {
            "Overall": AttackStats,
            "By_attack_identifier": dict[str, AttackStats]
        }
    """
    if not attack_results:
        raise ValueError("attack_results cannot be empty")

    overall_counts: defaultdict[str, int] = defaultdict(int)
    by_type_counts: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))

    for attack in attack_results:
        if not isinstance(attack, AttackResult):
            raise TypeError(f"Expected AttackResult, got {type(attack).__name__}: {attack!r}")

        outcome = attack.outcome
        attack_type = (
            attack.get_attack_strategy_identifier().class_name if attack.get_attack_strategy_identifier() else "unknown"
        )

        if outcome == AttackOutcome.SUCCESS:
            overall_counts["successes"] += 1
            by_type_counts[attack_type]["successes"] += 1
        elif outcome == AttackOutcome.FAILURE:
            overall_counts["failures"] += 1
            by_type_counts[attack_type]["failures"] += 1
        else:
            overall_counts["undetermined"] += 1
            by_type_counts[attack_type]["undetermined"] += 1

    overall_stats = _compute_stats(
        successes=overall_counts["successes"],
        failures=overall_counts["failures"],
        undetermined=overall_counts["undetermined"],
    )

    by_type_stats = {
        attack_type: _compute_stats(
            successes=counts["successes"],
            failures=counts["failures"],
            undetermined=counts["undetermined"],
        )
        for attack_type, counts in by_type_counts.items()
    }

    return {
        "Overall": overall_stats,
        "By_attack_identifier": by_type_stats,
    }
