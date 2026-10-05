# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared *derivation* helpers for the output printers.

These compute values from models (target fields, success rates, score display,
attack and objective-score selection) so the pretty / markdown / json printers
derive them **once** instead of each keeping its own copy. Presentation (color,
fallback strings) stays in the printers; these return raw values with an optional
``none_value`` fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from pyrit.models import AttackOutcome

if TYPE_CHECKING:
    from pyrit.models import AttackResult, ComponentIdentifier, MessagePiece, ScenarioResult, Score


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


def group_success_rate(attacks: list[AttackResult]) -> int:
    """
    Return the percentage of *attacks* whose outcome is SUCCESS (0 when empty).

    Args:
        attacks (list[AttackResult]): The attacks to score.

    Returns:
        int: The success rate as an integer percent.
    """
    total = len(attacks)
    if not total:
        return 0
    successful = sum(1 for attack in attacks if attack.outcome == AttackOutcome.SUCCESS)
    return int((successful / total) * 100)


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


def select_objective_scores(
    *,
    pieces: list[MessagePiece],
    scores: list[Score],
    objective_scorer_identifier: ComponentIdentifier,
) -> dict[str, Score]:
    """
    Pick the objective scorer's score for each message piece.

    A score only counts for the piece it is stored on: a duplicated piece's scores are stored on
    its original, and a message-level score is stored on one piece of the message. The identity
    hash is matched before the class name.

    Args:
        pieces (list[MessagePiece]): The pieces to pick scores for.
        scores (list[Score]): The scores read for those pieces.
        objective_scorer_identifier (ComponentIdentifier): The objective scorer to match.

    Returns:
        dict[str, Score]: The objective score of each piece that has one, keyed by piece id.
    """
    hash_matches: dict[str, Score] = {}
    class_name_matches: dict[str, Score] = {}
    for score in scores:
        identifier = score.scorer_class_identifier
        if identifier is None or score.message_piece_id is None:
            continue
        owner_id = str(score.message_piece_id)
        if identifier.hash == objective_scorer_identifier.hash:
            hash_matches.setdefault(owner_id, score)
        elif identifier.class_name == objective_scorer_identifier.class_name:
            class_name_matches.setdefault(owner_id, score)

    selected: dict[str, Score] = {}
    for piece in pieces:
        owner_id = str(piece.original_prompt_id or piece.id)
        score = hash_matches.get(owner_id, class_name_matches.get(owner_id))
        if score is not None:
            selected[str(piece.id)] = score
    return selected
