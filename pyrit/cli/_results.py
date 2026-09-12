# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
View resolution, ``--limit`` policy, and attack selection for the
``scenario-results`` command.

Rendering is delegated to ``pyrit.output`` (the scenario, attacks, and conversation
printers); this module holds only the CLI-side flag policy and the shared
attack-selection helpers. ``ScenarioResultView`` lives in ``pyrit.cli._cli_args``
so the argument parsers can reference it cheaply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.cli._cli_args import ScenarioResultView

if TYPE_CHECKING:
    from pyrit.models import AttackResult, ScenarioResult

#: Default cap on how many attacks the expensive views (``conversations`` /
#: ``full``) render when the user gives neither ``--attack-result-ids`` nor
#: ``--limit``. Unlike ``attacks`` (a single embedded read), these views make a
#: per-attack message fetch, so an unbounded run could pull many transcripts.
_DEFAULT_HEAVY_VIEW_LIMIT = 5


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


def apply_view_limit_policy(
    *,
    view: ScenarioResultView,
    limit: int | None,
    attack_result_ids: list[str] | None = None,
) -> int | None:
    """
    Apply the ``--limit`` policy for the chosen *view*.

    Each view treats ``--limit`` differently, so the policy is centralized here
    (rather than in a renderer) so every output format honors the same effective
    limit:

    - ``overview`` has no per-attack list, so a ``--limit`` is a no-op: warn and
      drop it.
    - ``attacks`` is a single embedded read, so it honors ``--limit`` verbatim
      and has no default cap (silent truncation would hide data).
    - ``conversations`` / ``full`` make a per-attack message fetch, so when the
      user scopes neither the attacks (``--attack-result-ids``) nor the count
      (``--limit``), fall back to ``_DEFAULT_HEAVY_VIEW_LIMIT`` and say so, to
      avoid accidentally pulling every transcript in a large run.

    Args:
        view (ScenarioResultView): The resolved view.
        limit (int | None): The requested row cap, if any.
        attack_result_ids (list[str] | None): The attacks the user scoped to, if
            any. Only consulted for the heavy views' default-limit fallback.
            Defaults to None.

    Returns:
        int | None: The effective limit (``None`` means "no cap").
    """
    if view is ScenarioResultView.OVERVIEW:
        if limit is not None:
            print("Note: --limit has no effect with --view overview; ignoring it.")
        return None
    if view in (ScenarioResultView.CONVERSATIONS, ScenarioResultView.FULL):
        if limit is None and not attack_result_ids:
            print(
                f"Note: no --attack-result-ids or --limit given; showing at most "
                f"{_DEFAULT_HEAVY_VIEW_LIMIT} conversations. Pass --limit or "
                "--attack-result-ids to see more."
            )
            return _DEFAULT_HEAVY_VIEW_LIMIT
        return limit
    return limit


def _select_attacks(*, result: ScenarioResult, attack_result_ids: list[str] | None) -> list[tuple[str, AttackResult]]:
    """
    Return ``(atomic_attack_name, attack_result)`` pairs, optionally id-filtered.

    Shared by the ``attacks`` and ``conversations`` builders so both select and
    order attacks identically.

    Args:
        result (ScenarioResult): The scenario result whose attacks to walk.
        attack_result_ids (list[str] | None): When provided, keep only attacks
            whose id is in this set.

    Returns:
        list[tuple[str, AttackResult]]: The selected pairs in scenario order.
    """
    id_filter = set(attack_result_ids) if attack_result_ids else None
    selected: list[tuple[str, AttackResult]] = []
    for atomic_attack_name, attack_results in result.attack_results.items():
        for attack_result in attack_results:
            if id_filter is not None and attack_result.attack_result_id not in id_filter:
                continue
            selected.append((atomic_attack_name, attack_result))
    return selected


def _objective_scorer_key(*, result: ScenarioResult) -> tuple[str | None, str | None]:
    """
    Extract the scenario objective scorer's ``(hash, class_name)`` match key.

    The objective scorer is the one whose verdict determines attack success, so
    its identity is how the transcript picks the single meaningful score out of
    the several attached to each response.

    Args:
        result (ScenarioResult): The scenario result whose objective scorer to read.

    Returns:
        tuple[str | None, str | None]: The identity hash and class name, or
            ``(None, None)`` when the scenario declares no objective scorer.
    """
    identifier = result.objective_scorer_identifier
    if identifier is None:
        return None, None
    return identifier.hash, identifier.class_name
