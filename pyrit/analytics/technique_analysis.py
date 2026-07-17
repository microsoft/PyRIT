# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-level analytics: technique success rates and related helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.analytics.result_analysis import AttackStats, _compute_stats
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.memory.memory_interface import MemoryInterface


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
    counts: dict[str, tuple[int, int, int, int]] = {}
    for result in results:
        identifier = result.atomic_attack_identifier
        eval_hash = identifier.eval_hash if identifier is not None else None
        if eval_hash is None or eval_hash not in requested:
            continue

        s, f, u, e = counts.get(eval_hash, (0, 0, 0, 0))
        if result.outcome == AttackOutcome.SUCCESS:
            counts[eval_hash] = (s + 1, f, u, e)
        elif result.outcome == AttackOutcome.FAILURE:
            counts[eval_hash] = (s, f + 1, u, e)
        elif result.outcome == AttackOutcome.ERROR:
            counts[eval_hash] = (s, f, u, e + 1)
        else:
            counts[eval_hash] = (s, f, u + 1, e)

    return {
        eval_hash: _compute_stats(successes=s, failures=f, undetermined=u, errors=e)
        for eval_hash, (s, f, u, e) in counts.items()
    }
