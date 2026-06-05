# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import (
    AttackOutcome,
    AttackResult,
    IdentifierFilter,
    IdentifierType,
    ObjectiveTargetEvaluationIdentifier,
)

if TYPE_CHECKING:
    from pyrit.memory.memory_interface import MemoryInterface


@dataclass
class AttackStats:
    """Statistics for attack analysis results."""

    success_rate: float | None
    total_decided: int
    successes: int
    failures: int
    undetermined: int
    errors: int


def _compute_stats(successes: int, failures: int, undetermined: int, errors: int) -> AttackStats:
    total_decided = successes + failures
    success_rate = successes / total_decided if total_decided > 0 else None
    return AttackStats(
        success_rate=success_rate,
        total_decided=total_decided,
        successes=successes,
        failures=failures,
        undetermined=undetermined,
        errors=errors,
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
        _strategy_id = attack.get_attack_strategy_identifier()
        attack_type = _strategy_id.class_name if _strategy_id is not None else "unknown"

        if outcome == AttackOutcome.SUCCESS:
            overall_counts["successes"] += 1
            by_type_counts[attack_type]["successes"] += 1
        elif outcome == AttackOutcome.FAILURE:
            overall_counts["failures"] += 1
            by_type_counts[attack_type]["failures"] += 1
        elif outcome == AttackOutcome.ERROR:
            overall_counts["errors"] += 1
            by_type_counts[attack_type]["errors"] += 1
        else:
            overall_counts["undetermined"] += 1
            by_type_counts[attack_type]["undetermined"] += 1

    overall_stats = _compute_stats(
        successes=overall_counts["successes"],
        failures=overall_counts["failures"],
        undetermined=overall_counts["undetermined"],
        errors=overall_counts["errors"],
    )

    by_type_stats = {
        attack_type: _compute_stats(
            successes=counts["successes"],
            failures=counts["failures"],
            undetermined=counts["undetermined"],
            errors=counts["errors"],
        )
        for attack_type, counts in by_type_counts.items()
    }

    return {
        "Overall": overall_stats,
        "By_attack_identifier": by_type_stats,
    }


def get_cached_results_for_technique(
    memory_interface: "MemoryInterface",
    *,
    technique_eval_hash: str,
    objective_target_eval_hash: str,
    additional_filters: Sequence[IdentifierFilter] | None = None,
) -> list[AttackResult]:
    """
    Return cached AttackResults matching a (technique × objective target) pair.

    Memory is queried for AttackResults whose stamped
    ``atomic_attack_identifier.eval_hash`` equals ``technique_eval_hash``,
    then results are filtered in Python to those whose nested objective
    target produces the requested ``objective_target_eval_hash`` (computed
    via ``ObjectiveTargetEvaluationIdentifier``). Returned results are sorted
    newest-first by ``timestamp`` so the most recent is at index 0.

    No scenario scoping is applied; this is a behavioral cache spanning every
    run that produced the same (technique × target) combination. Callers that
    need scenario-level scoping should pass additional ``IdentifierFilter``s
    or filter the returned list themselves.

    Args:
        memory_interface (MemoryInterface): The memory interface to query.
            Analytics is stateless, so callers (e.g. scenarios) must pass
            their own ``CentralMemory.get_memory_instance()``.
        technique_eval_hash (str): Behavioral eval hash of the atomic-attack
            technique, as produced by ``AtomicAttackEvaluationIdentifier.eval_hash``
            (also exposed as ``AtomicAttack.technique_eval_hash``).
        objective_target_eval_hash (str): Behavioral eval hash of the objective
            target, as produced by ``ObjectiveTargetEvaluationIdentifier.eval_hash``.
        additional_filters (Sequence[IdentifierFilter] | None): Extra
            ``IdentifierFilter`` predicates appended to the SQL pre-filter.
            Defaults to None.

    Returns:
        list[AttackResult]: Matching attack results sorted newest-first.
            Empty list if no cache hit.
    """
    filters: list[IdentifierFilter] = [
        IdentifierFilter(
            identifier_type=IdentifierType.ATTACK,
            property_path="$.eval_hash",
            value=technique_eval_hash,
        ),
    ]
    if additional_filters:
        filters.extend(additional_filters)

    candidates = memory_interface.get_attack_results(identifier_filters=filters)

    matches = [result for result in candidates if _objective_target_eval_hash_for(result) == objective_target_eval_hash]

    matches.sort(key=lambda r: r.timestamp, reverse=True)
    return matches


def _objective_target_eval_hash_for(attack_result: AttackResult) -> str | None:
    """
    Return the ObjectiveTargetEvaluationIdentifier eval hash for a result.

    Walks ``atomic_attack_identifier.attack_technique.objective_target`` and
    wraps the resulting identifier in ``ObjectiveTargetEvaluationIdentifier``.

    Args:
        attack_result (AttackResult): The attack result whose persisted
            ``atomic_attack_identifier`` tree should be inspected.

    Returns:
        str | None: The ``ObjectiveTargetEvaluationIdentifier.eval_hash``
            computed from the persisted objective-target identifier, or
            ``None`` when the identifier tree is missing expected nodes
            (e.g. legacy rows or atomic attacks without a distinct objective
            target).
    """
    if attack_result.atomic_attack_identifier is None:
        return None

    technique = attack_result.atomic_attack_identifier.get_child("attack_technique")
    if technique is None:
        return None

    target = technique.get_child("objective_target")
    if target is None:
        return None

    return ObjectiveTargetEvaluationIdentifier(target).eval_hash
