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

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from pyrit.cli._cli_args import ScenarioResultView

if TYPE_CHECKING:
    from pyrit.cli.api_client import PyRITApiClient
    from pyrit.models import AttackResult, ScenarioResult, Score

#: Default cap on how many attacks the expensive views (``conversations`` /
#: ``full``) render when the user gives neither ``--attack-result-ids`` nor
#: ``--limit``. Unlike ``attacks`` (a single embedded read), these views make a
#: per-attack message fetch, so an unbounded run could pull many transcripts.
_DEFAULT_HEAVY_VIEW_LIMIT = 5


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


class TranscriptScore(BaseModel):
    """
    The objective (top-level) score attached to a transcript message.

    A response is often scored by several scorers (refusal, objective,
    composite, ...), but only the scenario's objective scorer determines
    success, so the transcript surfaces just that one. ``scorer`` names it so a
    bare ``true``/``false`` isn't ambiguous about which dimension it measures.
    """

    scorer: str | None = None
    value: str | None = None
    rationale: str | None = None


class TranscriptMessage(BaseModel):
    """One message in a conversation transcript (role, turn, text, objective score)."""

    role: str
    turn: int
    text: str
    score: TranscriptScore | None = None


class AttackConversation(BaseModel):
    """The main-conversation transcript for a single attack result."""

    attack_result_id: str
    atomic_attack_name: str
    objective: str
    outcome: str
    conversation_id: str
    messages: list[TranscriptMessage] = Field(default_factory=list)


class ConversationsPayload(BaseModel):
    """
    The ``conversations`` view: the main-conversation transcript per attack.

    ``total`` is the number of attacks that matched the selection before
    ``--limit`` was applied; ``len(conversations)`` is how many are actually
    included. Exposing both lets any renderer show a "showing N of M" note.
    """

    scenario_result_id: str
    conversations: list[AttackConversation] = Field(default_factory=list)
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


def _row_score_value(*, score: Score | None) -> str | None:
    """
    Render a score for the attacks table.

    Args:
        score (Score | None): The attack's last score, if it had one.

    Returns:
        str | None: The score value, its status when undetermined, or None.
    """
    if score is None:
        return None
    # An undetermined score has no value, so name its status instead.
    return score.score_value if score.score_value is not None else score.status.value


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
    selected = _select_attacks(result=result, attack_result_ids=attack_result_ids)
    total = len(selected)
    if limit is not None:
        selected = selected[:limit]

    rows = [
        AttackRow(
            attack_result_id=attack_result.attack_result_id,
            atomic_attack_name=atomic_attack_name,
            objective=attack_result.objective,
            outcome=attack_result.outcome.value,
            executed_turns=attack_result.executed_turns,
            score_value=_row_score_value(score=attack_result.last_score),
        )
        for atomic_attack_name, attack_result in selected
    ]
    return AttacksTablePayload(scenario_result_id=scenario_result_id, rows=rows, total=total)


async def build_conversations_payload_async(
    *,
    result: ScenarioResult,
    client: PyRITApiClient,
    scenario_result_id: str,
    attack_result_ids: list[str] | None = None,
    limit: int | None = None,
) -> ConversationsPayload:
    """
    Build the ``conversations`` payload, fetching each attack's main transcript.

    Unlike the ``attacks`` builder (all data embedded, pure and sync), this view
    needs one message fetch per attack, so the builder owns the I/O loop. Attack
    selection and ``--limit`` are applied *before* fetching, so the effective
    limit gates the number of network calls — not just the rendered rows — and
    both front-ends share that guard.

    Args:
        result (ScenarioResult): The full scenario result to read attacks from.
        client (PyRITApiClient): Client used to fetch each conversation's messages.
        scenario_result_id (str): The run id, echoed back on the payload.
        attack_result_ids (list[str] | None): When provided, keep only attacks
            whose id is in this set. Defaults to None (all attacks).
        limit (int | None): Maximum number of attacks to fetch. Defaults to None.

    Returns:
        ConversationsPayload: The per-attack transcripts plus the pre-limit total.
    """
    selected = _select_attacks(result=result, attack_result_ids=attack_result_ids)
    total = len(selected)
    if limit is not None:
        selected = selected[:limit]

    objective_hash, objective_class = _objective_scorer_key(result=result)
    conversations: list[AttackConversation] = []
    for atomic_attack_name, attack_result in selected:
        response = await client.get_conversation_messages_async(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=attack_result.conversation_id,
        )
        messages = [
            _message_to_transcript(
                message=message,
                objective_hash=objective_hash,
                objective_class=objective_class,
            )
            for message in response.get("messages", [])
        ]
        conversations.append(
            AttackConversation(
                attack_result_id=attack_result.attack_result_id,
                atomic_attack_name=atomic_attack_name,
                objective=attack_result.objective,
                outcome=attack_result.outcome.value,
                conversation_id=attack_result.conversation_id,
                messages=messages,
            )
        )

    return ConversationsPayload(
        scenario_result_id=scenario_result_id,
        conversations=conversations,
        total=total,
    )


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


def _message_to_transcript(
    *,
    message: dict[str, Any],
    objective_hash: str | None,
    objective_class: str | None,
) -> TranscriptMessage:
    """
    Map one raw ``MessageView`` payload into a ``TranscriptMessage``.

    Args:
        message (dict[str, Any]): A single message from the
            ``ConversationMessagesResponse`` payload.
        objective_hash (str | None): The objective scorer's identity hash, used
            to select which of the message's scores to surface.
        objective_class (str | None): The objective scorer's class name, used as
            a fallback match when the hash is unavailable.

    Returns:
        TranscriptMessage: The role, turn, joined text, and objective score.
    """
    pieces: list[dict[str, Any]] = message.get("message_pieces") or []
    return TranscriptMessage(
        role=str(message.get("role", "")),
        turn=int(message.get("turn_number", 0) or 0),
        text=_join_piece_text(pieces=pieces),
        score=_select_objective_score(
            pieces=pieces,
            objective_hash=objective_hash,
            objective_class=objective_class,
        ),
    )


def _join_piece_text(*, pieces: list[dict[str, Any]]) -> str:
    """
    Join the text of a message's pieces, preferring the converted value.

    Args:
        pieces (list[dict[str, Any]]): The message's piece payloads.

    Returns:
        str: The non-empty piece values joined by newlines.
    """
    parts = [str(value) for piece in pieces if (value := piece.get("converted_value") or piece.get("original_value"))]
    return "\n".join(parts)


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


def _select_objective_score(
    *,
    pieces: list[dict[str, Any]],
    objective_hash: str | None,
    objective_class: str | None,
) -> TranscriptScore | None:
    """
    Pick the objective (top-level) score from a message's pieces.

    A response carries several scores (refusal, objective, composite, ...); only
    the objective scorer's verdict reflects attack success. Match it by identity
    hash (exact), falling back to class name, rather than by list order — which
    is undocumented and would arbitrarily surface an auxiliary sub-score.

    Args:
        pieces (list[dict[str, Any]]): The message's piece payloads.
        objective_hash (str | None): The objective scorer's identity hash.
        objective_class (str | None): The objective scorer's class name (fallback).

    Returns:
        TranscriptScore | None: The objective score, or ``None`` if not found.
    """
    fallback: TranscriptScore | None = None
    for piece in pieces:
        for score in piece.get("scores") or []:
            identifier = score.get("scorer_class_identifier") or {}
            if objective_hash and identifier.get("hash") == objective_hash:
                return _to_transcript_score(score=score)
            if objective_class and fallback is None and score.get("scorer_type") == objective_class:
                fallback = _to_transcript_score(score=score)
    return fallback


def _to_transcript_score(*, score: dict[str, Any]) -> TranscriptScore:
    """
    Map one raw ``ScoreView`` payload into a ``TranscriptScore``.

    Args:
        score (dict[str, Any]): A single score payload from a message piece.

    Returns:
        TranscriptScore: The scorer name, value, and rationale.
    """
    value = score.get("score_value")
    if value is None:
        value = score.get("status")
    return TranscriptScore(
        scorer=score.get("scorer_type") or None,
        value=str(value) if value is not None else None,
        rationale=score.get("score_rationale") or None,
    )
