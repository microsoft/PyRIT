# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Generic, Literal, TypedDict, TypeVar, overload

from pyrit.analytics.outcome_statistics import compute_outcome_statistics
from pyrit.common.deprecation import print_deprecation_message
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackStats,
    IdentifierFilter,
    IdentifierType,
    ObjectiveTargetEvaluationIdentifier,
    OutcomeStatistics,
)

if TYPE_CHECKING:
    from pyrit.memory.memory_interface import MemoryInterface

_SYNC_API_REMOVAL_VERSION = "1.4.0"
_StatisticsT = TypeVar("_StatisticsT", bound=AttackStats)


class _AnalysisResult(TypedDict, Generic[_StatisticsT]):
    """The existing result dictionary, with precise types for its two keys."""

    Overall: _StatisticsT
    By_attack_identifier: dict[str, _StatisticsT]


def _compute_stats(successes: int, failures: int, undetermined: int, errors: int) -> AttackStats:
    statistics = compute_outcome_statistics(
        {
            AttackOutcome.SUCCESS: successes,
            AttackOutcome.FAILURE: failures,
            AttackOutcome.UNDETERMINED: undetermined,
            AttackOutcome.ERROR: errors,
        }
    )
    return _as_attack_stats(statistics)


def _as_attack_stats(statistics: OutcomeStatistics) -> AttackStats:
    return AttackStats(
        success_rate=statistics.success_rate,
        total_decided=statistics.total_decided,
        successes=statistics.successes,
        failures=statistics.failures,
        undetermined=statistics.undetermined,
        errors=statistics.errors,
    )


@overload
def analyze_results(
    attack_results: list[AttackResult], *, include_outcome_statistics: Literal[True]
) -> _AnalysisResult[OutcomeStatistics]: ...


@overload
def analyze_results(
    attack_results: list[AttackResult], *, include_outcome_statistics: Literal[False] = False
) -> _AnalysisResult[AttackStats]: ...


@overload
def analyze_results(
    attack_results: list[AttackResult], *, include_outcome_statistics: bool
) -> _AnalysisResult[AttackStats] | _AnalysisResult[OutcomeStatistics]: ...


def analyze_results(
    attack_results: list[AttackResult], *, include_outcome_statistics: bool = False
) -> _AnalysisResult[AttackStats] | _AnalysisResult[OutcomeStatistics]:
    """
    Analyze a list of AttackResult objects and return overall and grouped statistics.

    This API remains supported. Both output shapes use the shared outcome calculator
    and count the supplied results without changing their grouping or selecting retries.

    Args:
        attack_results (list[AttackResult]): Results to count.
        include_outcome_statistics (bool): Return ``OutcomeStatistics`` with both success
            rates, totals, and shares. False preserves the six-field ``AttackStats`` shape.

    Returns:
        dict: Overall statistics under "Overall" and per-attack-type statistics under
            "By_attack_identifier". The opt-in changes values, not dictionary keys.

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

    overall_counts: defaultdict[AttackOutcome, int] = defaultdict(int)
    by_type_counts: defaultdict[str, defaultdict[AttackOutcome, int]] = defaultdict(lambda: defaultdict(int))

    for attack in attack_results:
        if not isinstance(attack, AttackResult):
            raise TypeError(f"Expected AttackResult, got {type(attack).__name__}: {attack!r}")

        outcome = attack.outcome
        _strategy_id = attack.get_attack_strategy_identifier()
        attack_type = _strategy_id.class_name if _strategy_id is not None else "unknown"

        overall_counts[outcome] += 1
        by_type_counts[attack_type][outcome] += 1

    overall_stats = compute_outcome_statistics(overall_counts)
    by_type_stats = {attack_type: compute_outcome_statistics(counts) for attack_type, counts in by_type_counts.items()}
    if include_outcome_statistics:
        return {"Overall": overall_stats, "By_attack_identifier": by_type_stats}
    return {
        "Overall": _as_attack_stats(overall_stats),
        "By_attack_identifier": {name: _as_attack_stats(statistics) for name, statistics in by_type_stats.items()},
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
    print_deprecation_message(
        old_item="get_cached_results_for_technique",
        new_item="get_cached_results_for_technique_async",
        removed_in=_SYNC_API_REMOVAL_VERSION,
    )
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


async def get_cached_results_for_technique_async(
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

    candidates = await memory_interface.get_attack_results_async(identifier_filters=filters)

    matches = [result for result in candidates if _objective_target_eval_hash_for(result) == objective_target_eval_hash]

    matches.sort(key=lambda r: r.timestamp, reverse=True)
    return matches


def _objective_target_eval_hash_for(attack_result: AttackResult) -> str | None:
    """
    Return the ObjectiveTargetEvaluationIdentifier eval hash for a result.

    Walks the current
    ``atomic_attack_identifier.attack_technique.attack.objective_target``
    shape and wraps the resulting identifier in
    ``ObjectiveTargetEvaluationIdentifier``. The legacy direct
    ``attack_technique.objective_target`` shape is also accepted.

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

    attack = technique.get_child("attack")
    target = attack.get_child("objective_target") if attack else technique.get_child("objective_target")
    if target is None:
        return None

    return ObjectiveTargetEvaluationIdentifier(target).eval_hash
