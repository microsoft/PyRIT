# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-level analytics: technique success rates and related helpers."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Literal, overload

from pyrit.analytics.outcome_statistics import compute_outcome_statistics
from pyrit.analytics.result_analysis import _as_attack_stats
from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory
from pyrit.models import AttackStats as AttackStats  # noqa: TC001 - preserve the existing module export

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.memory.memory_interface import MemoryInterface
    from pyrit.models import AttackOutcome, AttackResult, OutcomeStatistics


def compute_technique_stats(
    *,
    technique_eval_hashes: Sequence[str],
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
) -> dict[str, AttackStats]:
    """
    Compute per-technique outcome statistics from persisted attack results.

    Queries memory for all ``AttackResult`` rows whose
    ``atomic_attack_identifier.eval_hash`` matches one of
    ``technique_eval_hashes``, then aggregates outcomes into per-technique
    ``AttackStats``. The eval hash is auto-stamped on every persisted result
    by ``AtomicAttackEvaluationIdentifier`` and is the canonical primitive
    for behavioral-equivalence aggregation (seeds excluded, scorer excluded,
    only behavior-relevant target params included).

    Args:
        technique_eval_hashes (Sequence[str]): Eval hashes to aggregate.
            Returned dict is keyed by these.
        scenario_result_id (str | None): Restrict to a single scenario run.
            Defaults to ``None`` (aggregate across all runs).
        targeted_harm_categories (Sequence[str] | None): Restrict to results
            whose attack targeted these harm categories. Defaults to ``None``.
        memory (MemoryInterface | None): Memory backend to query. Defaults to
            ``CentralMemory.get_memory_instance()``.

    Returns:
        dict[str, AttackStats]: Stats per technique eval hash. Hashes with no
            historical results are omitted from the result.
    """
    print_deprecation_message(
        old_item="compute_technique_stats",
        new_item="compute_technique_stats_async",
        removed_in="1.4.0",
    )
    if not technique_eval_hashes:
        return {}

    if memory is None:
        memory = CentralMemory.get_memory_instance()
    results = memory.get_attack_results(
        atomic_attack_eval_hashes=list(technique_eval_hashes),
        scenario_result_id=scenario_result_id,
        targeted_harm_categories=targeted_harm_categories,
    )

    statistics = _aggregate_technique_stats(results=results, technique_eval_hashes=technique_eval_hashes)
    return {key: _as_attack_stats(value) for key, value in statistics.items()}


@overload
async def compute_technique_stats_async(
    *,
    technique_eval_hashes: Sequence[str],
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
    include_outcome_statistics: Literal[True],
) -> dict[str, OutcomeStatistics]: ...


@overload
async def compute_technique_stats_async(
    *,
    technique_eval_hashes: Sequence[str],
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
    include_outcome_statistics: Literal[False] = False,
) -> dict[str, AttackStats]: ...


@overload
async def compute_technique_stats_async(
    *,
    technique_eval_hashes: Sequence[str],
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
    include_outcome_statistics: bool,
) -> dict[str, AttackStats] | dict[str, OutcomeStatistics]: ...


async def compute_technique_stats_async(
    *,
    technique_eval_hashes: Sequence[str],
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
    include_outcome_statistics: bool = False,
) -> dict[str, AttackStats] | dict[str, OutcomeStatistics]:
    """
    Compute per-technique outcome statistics from persisted attack results.

    Queries memory for all ``AttackResult`` rows whose
    ``atomic_attack_identifier.eval_hash`` matches one of
    ``technique_eval_hashes``, then aggregates outcomes into per-technique
    ``AttackStats``. The eval hash is auto-stamped on every persisted result
    by ``AtomicAttackEvaluationIdentifier`` and is the canonical primitive
    for behavioral-equivalence aggregation (seeds excluded, scorer excluded,
    only behavior-relevant target params included).

    This async API remains supported. The output opt-in does not change its memory
    query, cohort selection, or behavior used by adaptive technique selectors.

    Args:
        technique_eval_hashes (Sequence[str]): Eval hashes to aggregate.
            Returned dict is keyed by these.
        scenario_result_id (str | None): Restrict to a single scenario run.
            Defaults to ``None`` (aggregate across all runs).
        targeted_harm_categories (Sequence[str] | None): Restrict to results
            whose attack targeted these harm categories. Defaults to ``None``.
        memory (MemoryInterface | None): Memory backend to query. Defaults to
            ``CentralMemory.get_memory_instance()``.
        include_outcome_statistics (bool): Return shared statistics with both success
            rates instead of the compatible six-field ``AttackStats`` shape. Defaults to False.

    Returns:
        dict[str, AttackStats] | dict[str, OutcomeStatistics]: Stats per technique eval
            hash, including both rates when requested. Hashes with no results are omitted.
    """
    if not technique_eval_hashes:
        return {}

    if memory is None:
        memory = CentralMemory.get_memory_instance()
    results = await memory.get_attack_results_async(
        atomic_attack_eval_hashes=list(technique_eval_hashes),
        scenario_result_id=scenario_result_id,
        targeted_harm_categories=targeted_harm_categories,
    )

    statistics = _aggregate_technique_stats(results=results, technique_eval_hashes=technique_eval_hashes)
    if include_outcome_statistics:
        return statistics
    return {key: _as_attack_stats(value) for key, value in statistics.items()}


def _aggregate_technique_stats(
    *, results: Sequence[AttackResult], technique_eval_hashes: Sequence[str]
) -> dict[str, OutcomeStatistics]:
    requested = set(technique_eval_hashes)
    counts: dict[str, Counter[AttackOutcome]] = {}
    for result in results:
        identifier = result.atomic_attack_identifier
        eval_hash = identifier.eval_hash if identifier is not None else None
        if eval_hash is None or eval_hash not in requested:
            continue

        counts.setdefault(eval_hash, Counter())[result.outcome] += 1

    return {eval_hash: compute_outcome_statistics(outcomes) for eval_hash, outcomes in counts.items()}
