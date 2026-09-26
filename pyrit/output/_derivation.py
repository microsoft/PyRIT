# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared *derivation* helpers for the output printers.

These compute values from models (target fields, success rates, score display,
attack selection) so the pretty / markdown / json printers derive them **once**
instead of each keeping its own copy. Presentation (color, fallback strings) stays
in the printers; these return raw values with an optional ``none_value`` fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from pyrit.analytics.scenario_statistics import combine_execution_counts, compute_scenario_statistics

if TYPE_CHECKING:
    from pyrit.models import AttackResult, ComponentIdentifier, ScenarioResult, Score


class TargetInfo(NamedTuple):
    """A target's display fields derived from its identifier (``None`` when absent)."""

    type: str | None
    model: str | None
    endpoint: str | None


def resolve_target_info(target_id: ComponentIdentifier | None) -> TargetInfo:
    """
    Derive a target's type, model, and endpoint from its identifier.

    Model resolution prefers ``underlying_model_name`` then ``model_name``. Values are
    raw (``None`` when absent); callers apply their own display fallback.

    Args:
        target_id (ComponentIdentifier | None): The objective target identifier, if any.

    Returns:
        TargetInfo: The ``(type, model, endpoint)`` triple.
    """
    if target_id is None:
        return TargetInfo(None, None, None)
    # params values are JSONValue; keep only strings (None otherwise) for the str|None fields.
    model = target_id.params.get("underlying_model_name") or target_id.params.get("model_name")
    endpoint = target_id.params.get("endpoint")
    return TargetInfo(
        target_id.class_name,
        model if isinstance(model, str) else None,
        endpoint if isinstance(endpoint, str) else None,
    )


class GroupStatistics(NamedTuple):
    """Effective-unit statistics for one display group, alongside its raw attempt count."""

    name: str
    units: int
    attempts: int
    success_rate: int


class ScenarioOverview(NamedTuple):
    """Overall and per-display-group statistics for a scenario report."""

    units: int
    attempts: int
    success_rate: int
    groups: list[GroupStatistics]


def scenario_overview(result: ScenarioResult) -> ScenarioOverview:
    """
    Summarize a scenario result for the reports.

    The numbers come from ``pyrit.analytics.compute_scenario_statistics``, the calculation shared with the
    SDK and the GUI backend. Groups follow ``result.get_display_groups()``: each group folds the per-atomic-
    attack counts of the atomic attacks ``display_group_map`` assigns to it, so the rate is always keyed the
    same way the printers group their results. ``units`` counts effective execution units (the success-rate
    denominator); ``attempts`` counts every persisted attempt, including retries.

    Args:
        result (ScenarioResult): The scenario result to summarize.

    Returns:
        ScenarioOverview: The overall and per-group statistics.
    """
    statistics = compute_scenario_statistics(result)
    groups: list[GroupStatistics] = []
    for group_name, group_results in result.get_display_groups().items():
        atomic_attack_names = [
            name for name in result.attack_results if result.display_group_map.get(name, name) == group_name
        ]
        counts = combine_execution_counts(
            statistics.atomic_attacks[name] for name in atomic_attack_names if name in statistics.atomic_attacks
        )
        groups.append(
            GroupStatistics(
                name=group_name,
                units=counts.completed,
                attempts=len(group_results),
                success_rate=counts.success_percentage or 0,
            )
        )
    return ScenarioOverview(
        units=statistics.overall.completed,
        attempts=statistics.attempts,
        success_rate=statistics.overall.success_percentage or 0,
        groups=groups,
    )


def attack_score_display(attack: AttackResult, *, none_value: str | None = None) -> str | None:
    """
    Derive an attack's last-score display value.

    Args:
        attack (AttackResult): The attack to read the score from.
        none_value (str | None): Returned when the attack has no score. Defaults to None.

    Returns:
        str | None: The score value, its status, or *none_value* when there is no score.
    """
    score = attack.last_score
    if score is None:
        return none_value
    return score.score_value if score.score_value is not None else score.status.value


def select_attacks(
    result: ScenarioResult, *, attack_result_ids: list[str] | None = None
) -> list[tuple[str, AttackResult]]:
    """
    Return ``(atomic_attack_name, attack)`` pairs, optionally filtered by id.

    Args:
        result (ScenarioResult): The scenario result whose attacks to walk.
        attack_result_ids (list[str] | None): When provided, keep only these ids.

    Returns:
        list[tuple[str, AttackResult]]: The selected pairs in scenario order.
    """
    id_filter = set(attack_result_ids) if attack_result_ids else None
    return [
        (atomic_attack_name, attack)
        for atomic_attack_name, attacks in result.attack_results.items()
        for attack in attacks
        if id_filter is None or attack.attack_result_id in id_filter
    ]


def resolve_scorer_name(score: Score, *, none_value: str | None = None) -> str | None:
    """
    Derive a score's scorer class name.

    Args:
        score (Score): The score to read the scorer identifier from.
        none_value (str | None): Returned when there is no scorer identifier. Defaults to None.

    Returns:
        str | None: The scorer class name, or *none_value*.
    """
    identifier = score.scorer_class_identifier
    return identifier.class_name if identifier else none_value
