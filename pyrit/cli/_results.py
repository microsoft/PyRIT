# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Typed payloads and builders for the ``scenario-results`` command.

A *view* selects the data (one of these payloads); a *format* serializes it.
Keeping the payload a Pydantic model makes it the single source of truth: the
console renderer reads it today, and ``--output json`` will serialize the same
object in a later phase, so every format stays consistent.

This module imports ``pydantic`` and is therefore loaded only from deferred
(post-parse) call sites, never on the CLI ``--help`` path. The lightweight
``ScenarioResultView`` enum lives in ``pyrit.cli._cli_args`` for that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from pyrit.cli._cli_args import ScenarioResultView

if TYPE_CHECKING:
    from pyrit.models import ScenarioResult


class AttackRow(BaseModel):
    """A single attack result rendered as one row of the attacks table."""

    attack_result_id: str
    atomic_attack_name: str
    objective: str
    outcome: str
    executed_turns: int
    score_value: str | None = None


class AttacksTablePayload(BaseModel):
    """
    The ``attacks`` view: one row per attack result in a scenario run.

    ``total`` is the number of attacks that matched the selection before
    ``--limit`` was applied; ``len(rows)`` is how many are actually included.
    Exposing both lets any renderer show a "showing N of M" note.
    """

    scenario_result_id: str
    rows: list[AttackRow] = Field(default_factory=list)
    total: int = 0


def resolve_view(*, view: ScenarioResultView | None) -> ScenarioResultView:
    """
    Resolve an optional ``--view`` value to a concrete view.

    Args:
        view (ScenarioResultView | None): The parsed view, or ``None`` when the
            flag was omitted.

    Returns:
        ScenarioResultView: The explicit view, defaulting to ``OVERVIEW``.
    """
    return view if view is not None else ScenarioResultView.OVERVIEW


def apply_view_limit_policy(*, view: ScenarioResultView, limit: int | None) -> int | None:
    """
    Apply the ``--limit`` policy for the chosen *view*.

    ``--limit`` caps a per-attack row list, which the aggregate ``overview`` view
    does not have. Rather than silently accept a no-op flag, warn the user and
    drop it so the behavior is explicit.

    Args:
        view (ScenarioResultView): The resolved view.
        limit (int | None): The requested row cap, if any.

    Returns:
        int | None: The effective limit (``None`` for ``overview``).
    """
    if view is ScenarioResultView.OVERVIEW and limit is not None:
        print("Note: --limit has no effect with --view overview; ignoring it.")
        return None
    return limit


def build_attacks_table_payload(
    *,
    result: ScenarioResult,
    scenario_result_id: str,
    attack_result_ids: list[str] | None = None,
    limit: int | None = None,
) -> AttacksTablePayload:
    """
    Build the ``attacks`` payload from an already-fetched scenario result.

    Every ``AttackResult`` is already embedded in *result* (grouped by atomic
    attack name), so no extra server calls are needed. ``--limit`` is applied
    here, on the payload, rather than in a renderer, so that all output formats
    honor it identically.

    Args:
        result (ScenarioResult): The full scenario result to read attacks from.
        scenario_result_id (str): The run id, echoed back on the payload.
        attack_result_ids (list[str] | None): When provided, keep only attacks
            whose id is in this set. Defaults to None (all attacks).
        limit (int | None): Maximum number of rows to include. Defaults to None.

    Returns:
        AttacksTablePayload: The rows plus the pre-limit total.
    """
    id_filter = set(attack_result_ids) if attack_result_ids else None

    rows: list[AttackRow] = []
    for atomic_attack_name, attack_results in result.attack_results.items():
        for attack_result in attack_results:
            if id_filter is not None and attack_result.attack_result_id not in id_filter:
                continue
            score = attack_result.last_score
            rows.append(
                AttackRow(
                    attack_result_id=attack_result.attack_result_id,
                    atomic_attack_name=atomic_attack_name,
                    objective=attack_result.objective,
                    outcome=attack_result.outcome.value,
                    executed_turns=attack_result.executed_turns,
                    score_value=str(score.score_value) if score is not None else None,
                )
            )

    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    return AttacksTablePayload(scenario_result_id=scenario_result_id, rows=rows, total=total)
