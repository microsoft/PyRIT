# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Technique selector protocol for adaptive scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class SelectorScope:
    """
    Filter describing which historical ``AttackResult`` rows a selector
    queries when estimating technique success rates.

    All fields default to "no restriction"; combine fields to narrow the
    scope (e.g. current run only). Filter values flow through
    ``compute_technique_stats`` to ``MemoryInterface.get_attack_results``.

    The scope is held by the selector at construction time. The per-call
    ``scenario_result_id`` is supplied by the dispatcher and is forwarded
    to memory only when ``current_run_only`` is set; otherwise the selector
    queries across all runs.

    Per-technique disambiguation uses ``atomic_attack_identifier.eval_hash``
    (auto-stamped on every persisted attack result), which already encodes
    the attack class plus its behavior-relevant params. Class-based
    narrowing is therefore unnecessary at this layer.
    """

    current_run_only: bool = False
    """Restrict to the dispatcher-supplied ``scenario_result_id`` for the
    in-flight run. When ``False`` (default), query across all runs."""

    @classmethod
    def all_runs(cls) -> SelectorScope:
        """
        Build a scope that queries across all historical scenario runs (the default).

        Returns:
            SelectorScope: A scope with no restrictions.
        """
        return cls()

    @classmethod
    def current_run(cls) -> SelectorScope:
        """
        Build a scope restricted to the dispatcher-supplied scenario run.

        Returns:
            SelectorScope: A scope with ``current_run_only=True``.
        """
        return cls(current_run_only=True)


@runtime_checkable
class TechniqueSelector(Protocol):
    """
    Protocol for adaptive technique selectors.

    Selectors are **stateless** — they query memory for historical success
    rates rather than maintaining internal counts. Calling ``select_async``
    with the same arguments twice should yield the same answer
    (deterministic given memory contents).
    """

    async def select_async(
        self,
        *,
        technique_identifiers: Sequence[str],
        objective: str,
        num_top_techniques: int = 1,
        scenario_result_id: str | None = None,
    ) -> Sequence[str]:
        """
        Return techniques in priority order (try first, try second, …).

        Args:
            technique_identifiers (Sequence[str]): Available technique eval
                hashes.
            objective (str): The objective text for this selection.
            num_top_techniques (int): Max techniques to return. Defaults to 1.
            scenario_result_id (str | None): The current scenario run ID,
                provided by the dispatcher. Selectors forward this to
                memory only when their ``SelectorScope`` has
                ``current_run_only=True``.

        Returns:
            Sequence[str]: Up to ``num_top_techniques`` technique eval hashes
                in priority order. Fewer if not enough techniques are
                available.
        """
        ...  # pragma: no cover
