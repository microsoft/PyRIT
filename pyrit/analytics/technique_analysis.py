# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-level analytics: technique success rates and related helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from pyrit.analytics.result_analysis import AttackStats, _compute_stats
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pyrit.memory.memory_interface import MemoryInterface


def _compute_grouped_outcome_stats(grouped_outcomes: Iterable[tuple[str, AttackOutcome]]) -> dict[str, AttackStats]:
    """
    Aggregate keyed outcomes into attack statistics.

    Returns:
        dict[str, AttackStats]: Statistics keyed by the caller's grouping value.
    """
    counts: dict[str, Counter[AttackOutcome]] = defaultdict(Counter)
    for key, outcome in grouped_outcomes:
        counts[key][outcome] += 1

    return {
        key: _compute_stats(
            successes=outcomes[AttackOutcome.SUCCESS],
            failures=outcomes[AttackOutcome.FAILURE],
            undetermined=outcomes[AttackOutcome.UNDETERMINED],
            errors=outcomes[AttackOutcome.ERROR],
        )
        for key, outcomes in counts.items()
    }


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
    if not technique_eval_hashes:
        return {}

    if memory is None:
        memory = CentralMemory.get_memory_instance()
    results = memory.get_attack_results(
        atomic_attack_eval_hashes=list(technique_eval_hashes),
        scenario_result_id=scenario_result_id,
        targeted_harm_categories=targeted_harm_categories,
    )

    requested = set(technique_eval_hashes)
    grouped_outcomes: list[tuple[str, AttackOutcome]] = []
    for result in results:
        identifier = result.atomic_attack_identifier
        eval_hash = identifier.eval_hash if identifier is not None else None
        if eval_hash is None or eval_hash not in requested:
            continue
        grouped_outcomes.append((eval_hash, result.outcome))

    return _compute_grouped_outcome_stats(grouped_outcomes)


def compute_labeled_technique_stats(
    *,
    technique_identifiers: Sequence[str],
    label_name: str,
    technique_eval_hashes_by_identifier: Mapping[str, str] | None = None,
    scenario_result_id: str | None = None,
    targeted_harm_categories: Sequence[str] | None = None,
    memory: MemoryInterface | None = None,
) -> dict[str, AttackStats]:
    """
    Compute per-technique statistics from identity labels and eval-hash history.

    Args:
        technique_identifiers (Sequence[str]): Stable technique identifiers to
            aggregate. Returned dict is keyed by these identifiers.
        label_name (str): Result-label key containing the technique identifier.
        technique_eval_hashes_by_identifier (Mapping[str, str] | None):
            Optional mapping from requested selector identifiers to the full
            ``AttackTechnique`` eval hashes persisted by normal scenarios.
            Matching labeled and eval-hash rows are merged by result ID so a
            row visible through both paths is counted once.
        scenario_result_id (str | None): Restrict to a single scenario run.
            Defaults to ``None`` (aggregate across all runs).
        targeted_harm_categories (Sequence[str] | None): Restrict to results
            whose attack targeted these harm categories. Defaults to ``None``.
        memory (MemoryInterface | None): Memory backend to query. Defaults to
            ``CentralMemory.get_memory_instance()``.

    Returns:
        dict[str, AttackStats]: Stats per requested technique identifier.
            Identifiers with no historical results are omitted.
    """
    if not technique_identifiers:
        return {}

    if memory is None:
        memory = CentralMemory.get_memory_instance()
    labeled_results = memory.get_attack_results(
        labels={label_name: list(technique_identifiers)},
        scenario_result_id=scenario_result_id,
        targeted_harm_categories=targeted_harm_categories,
    )
    eval_results = (
        memory.get_attack_results(
            atomic_attack_eval_hashes=sorted(set(technique_eval_hashes_by_identifier.values())),
            scenario_result_id=scenario_result_id,
            targeted_harm_categories=targeted_harm_categories,
        )
        if technique_eval_hashes_by_identifier
        else []
    )

    requested = set(technique_identifiers)
    identifiers_by_eval_hash: dict[str, list[str]] = {}
    for technique_identifier, eval_hash in (technique_eval_hashes_by_identifier or {}).items():
        if technique_identifier in requested:
            identifiers_by_eval_hash.setdefault(eval_hash, []).append(technique_identifier)

    unique_results = {result.attack_result_id: result for result in [*labeled_results, *eval_results]}
    grouped_outcomes: list[tuple[str, AttackOutcome]] = []
    for result in unique_results.values():
        labeled_identifier = result.labels.get(label_name)
        if labeled_identifier in requested:
            matching_identifiers = [labeled_identifier]
        else:
            result_identifier = result.atomic_attack_identifier
            result_eval_hash = result_identifier.eval_hash if result_identifier is not None else None
            matching_identifiers = identifiers_by_eval_hash.get(result_eval_hash or "", [])
        if not matching_identifiers:
            continue

        grouped_outcomes.extend((technique_identifier, result.outcome) for technique_identifier in matching_identifiers)

    return _compute_grouped_outcome_stats(grouped_outcomes)
