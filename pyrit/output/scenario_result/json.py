# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import TYPE_CHECKING, Any

from pyrit.models import AttackResult, ScenarioResult
from pyrit.output._derivation import attack_score_display, group_success_rate, resolve_target_info, select_attacks
from pyrit.output.scenario_result.base import ScenarioResultPrinterBase, ScenarioView
from pyrit.output.sink import Sink

if TYPE_CHECKING:
    from pyrit.output.scorer.json import JsonScorerPrinter

# A per-attack conversation the scenario layer stitches into the conversations/full
# document: the atomic-attack name, its result, and its already-structured messages.
ConversationEntry = tuple[str, AttackResult, list[dict[str, Any]]]


class JsonScenarioResultPrinter(ScenarioResultPrinterBase):
    """
    JSON printer for scenario results.

    Builds a view-shaped dict from the ``ScenarioResult`` value object and
    serializes it once (``render_async``), so the JSON reports the same facts the
    pretty printer shows for each view. This format class does no data I/O; scorer
    metrics come from an injected ``ScorerPrinterBase`` (the ``*MemoryPrinter`` leaf
    supplies a memory-backed one).
    """

    def __init__(
        self,
        *,
        sink: Sink | None = None,
        indent: int = 2,
        scorer_printer: "JsonScorerPrinter | None" = None,
    ) -> None:
        """
        Initialize the JSON scenario printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
            scorer_printer (JsonScorerPrinter | None): Scorer printer whose ``build`` supplies the
                overview's scorer block. Required to render an overview when the result has an
                objective scorer; leaf classes provide a default. Defaults to None.
        """
        super().__init__(sink=sink)
        self._indent = indent
        self._scorer_printer = scorer_printer

    async def render_async(
        self,
        result: ScenarioResult,
        *,
        view: ScenarioView = "overview",
        attack_result_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        """
        Render a scenario result's ``overview`` or ``attacks`` view as JSON.

        The ``conversations`` / ``full`` document needs per-attack messages the
        caller fetches, so it is assembled by ``build_scenario_conversations_document``
        rather than here.

        Args:
            result (ScenarioResult): The scenario result to render.
            view (ScenarioView): ``"overview"`` or ``"attacks"``. Defaults to ``"overview"``.
            attack_result_ids (list[str] | None): For ``"attacks"``, restrict to these ids.
            limit (int | None): For ``"attacks"``, the maximum number of attacks.

        Returns:
            str: The rendered JSON document.
        """
        payload = self.build(result, view=view, attack_result_ids=attack_result_ids, limit=limit)
        return self._dumps(payload)

    def build(
        self,
        result: ScenarioResult,
        *,
        view: ScenarioView = "overview",
        attack_result_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Build the structured (dict) payload for a scenario ``view``.

        Public so a larger document (e.g. the ``full`` report) can embed the overview.

        Args:
            result (ScenarioResult): The scenario result.
            view (ScenarioView): ``"overview"`` or ``"attacks"``. Defaults to ``"overview"``.
            attack_result_ids (list[str] | None): For ``"attacks"``, restrict to these ids.
            limit (int | None): For ``"attacks"``, the maximum number of attacks.

        Returns:
            dict[str, Any]: The view-shaped payload.
        """
        if view == "attacks":
            return self._build_attacks(result, attack_result_ids=attack_result_ids, limit=limit)
        return self._build_overview(result)

    def _build_overview(self, result: ScenarioResult) -> dict[str, Any]:
        """
        Build the ``overview`` payload from the scenario result and scorer metrics.

        Args:
            result (ScenarioResult): The scenario result.

        Returns:
            dict[str, Any]: The overview envelope.

        Raises:
            ValueError: If the result has an objective scorer but no scorer printer is configured.
        """
        target = resolve_target_info(result.objective_target_identifier)

        display_groups = result.get_display_groups()
        latest_groups = result.get_display_groups(latest_attempts_only=True)
        groups = [
            {
                "name": group_name,
                "num_results": len(group_results),
                "success_rate": group_success_rate(latest_groups.get(group_name, [])),
            }
            for group_name, group_results in display_groups.items()
        ]

        scorer_identifier = result.objective_scorer_identifier
        if scorer_identifier is not None:
            if self._scorer_printer is None:
                raise ValueError("scorer_printer is required when result has objective_scorer_identifier")
            scorer = self._scorer_printer.build(scorer_identifier=scorer_identifier)
        else:
            scorer = None

        return {
            "view": "overview",
            "scenario": {
                "name": result.scenario_name,
                "id": str(result.id),
                "version": result.scenario_version,
                "pyrit_version": result.pyrit_version,
                "description": result.scenario_description,
            },
            "target": {
                "type": target.type,
                "model": target.model,
                "endpoint": target.endpoint,
            },
            "scorer": scorer,
            "stats": {
                "total_techniques": len(result.get_techniques_used()),
                "total_results": sum(len(results) for results in result.attack_results.values()),
                "overall_success_rate": result.objective_achieved_rate(),
                "unique_objectives": len(result.get_objectives()),
            },
            "groups": groups,
        }

    def _build_attacks(
        self,
        result: ScenarioResult,
        *,
        attack_result_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """
        Build the ``attacks`` payload, honoring the id filter and limit.

        Args:
            result (ScenarioResult): The scenario result.
            attack_result_ids (list[str] | None): Restrict to these attack ids.
            limit (int | None): Maximum number of attacks to include.

        Returns:
            dict[str, Any]: The attacks envelope with ``shown`` / ``total`` counts.
        """
        selected = select_attacks(result, attack_result_ids=attack_result_ids)
        total = len(selected)
        if limit is not None:
            selected = selected[:limit]
        return {
            "view": "attacks",
            "attacks": [_attack_entry(atomic_attack_name=name, attack=attack) for name, attack in selected],
            "shown": len(selected),
            "total": total,
        }

    def _dumps(self, payload: Any) -> str:
        """
        Serialize a payload to indented JSON (``default=str`` for UUID/datetime).

        Args:
            payload (Any): The JSON-ready structure.

        Returns:
            str: The serialized JSON.
        """
        return _dumps_json(payload, indent=self._indent)


class JsonScenarioResultMemoryPrinter(JsonScenarioResultPrinter):
    """JSON scenario printer whose scorer block is backed by the evaluation registry."""

    def __init__(self, *, sink: Sink | None = None, indent: int = 2) -> None:
        """
        Initialize with a registry-backed scorer printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
        """
        from pyrit.output.scorer.json import JsonScorerMemoryPrinter

        super().__init__(sink=sink, indent=indent, scorer_printer=JsonScorerMemoryPrinter(indent=indent))


def _attack_entry(*, atomic_attack_name: str, attack: AttackResult) -> dict[str, Any]:
    """
    Build the per-attack summary shared by the attacks and conversations views.

    Args:
        atomic_attack_name (str): The atomic attack (technique) name.
        attack (AttackResult): The attack result to summarize.

    Returns:
        dict[str, Any]: The attack's id, technique, objective, outcome, turns, and score.
    """
    return {
        "id": attack.attack_result_id,
        "technique": atomic_attack_name,
        "objective": attack.objective,
        "outcome": attack.outcome.value,
        "executed_turns": attack.executed_turns,
        "score": attack_score_display(attack),
    }


def build_scenario_conversations_document(
    *,
    result: ScenarioResult,
    entries: list[ConversationEntry],
    indent: int = 2,
) -> str:
    """
    Assemble the ``conversations`` JSON document from fetched messages.

    This composite spans two domains — per-attack summaries (scenario) and message
    transcripts (conversation) — so it is orchestration, not a single printer's job:
    the conversation printer renders each transcript, and this function stitches those
    around the scenario-level attack summaries and envelope. Each entry carries the
    per-attack summary inline (id, technique, objective, outcome, turns, score) plus
    its transcript, so the summary and detail live in one structure.

    Args:
        result (ScenarioResult): The scenario result (for its id).
        entries (list[ConversationEntry]): Per-attack ``(name, result, messages)`` triples,
            whose messages are already rendered by ``JsonConversationPrinter``.
        indent (int): JSON indentation width. Defaults to 2.

    Returns:
        str: The rendered JSON document.
    """
    payload = {
        "view": "conversations",
        "scenario_result_id": str(result.id),
        "conversations": _build_conversations_list(entries),
    }
    return _dumps_json(payload, indent=indent)


def build_scenario_full_document(
    *,
    result: ScenarioResult,
    overview: dict[str, Any],
    entries: list[ConversationEntry],
    indent: int = 2,
) -> str:
    """
    Assemble the ``full`` JSON document: the scenario overview plus every conversation.

    ``full`` is the complete report — the aggregate scorecard (``overview``) that the
    transcripts lack, combined with the per-attack summaries and transcripts. It is the
    natural structure a shareable HTML report renders.

    Args:
        result (ScenarioResult): The scenario result (for its id).
        overview (dict[str, Any]): The overview payload from ``JsonScenarioResultPrinter.build``.
        entries (list[ConversationEntry]): Per-attack ``(name, result, messages)`` triples,
            whose messages are already rendered by ``JsonConversationPrinter``.
        indent (int): JSON indentation width. Defaults to 2.

    Returns:
        str: The rendered JSON document.
    """
    payload = build_scenario_full_payload(result=result, overview=overview, entries=entries)
    return _dumps_json(payload, indent=indent)


def build_scenario_full_payload(
    *,
    result: ScenarioResult,
    overview: dict[str, Any],
    entries: list[ConversationEntry],
) -> dict[str, Any]:
    """
    Build the format-agnostic ``full`` report structure (overview + conversations).

    Shared by the JSON document (serialized) and the HTML report (templated), so both
    render the same facts.

    Args:
        result (ScenarioResult): The scenario result (for its id).
        overview (dict[str, Any]): The overview payload from ``JsonScenarioResultPrinter.build``.
        entries (list[ConversationEntry]): Per-attack ``(name, result, messages)`` triples.

    Returns:
        dict[str, Any]: The ``{view, scenario_result_id, overview, conversations}`` structure.
    """
    return {
        "view": "full",
        "scenario_result_id": str(result.id),
        "overview": overview,
        "conversations": _build_conversations_list(entries),
    }


def _build_conversations_list(entries: list[ConversationEntry]) -> list[dict[str, Any]]:
    """
    Stitch per-attack summaries and transcripts into the conversations list.

    Args:
        entries (list[ConversationEntry]): Per-attack ``(name, result, messages)`` triples.

    Returns:
        list[dict[str, Any]]: One entry per attack: its summary, conversation id, and messages.
    """
    conversations: list[dict[str, Any]] = []
    for atomic_attack_name, attack, messages in entries:
        entry = _attack_entry(atomic_attack_name=atomic_attack_name, attack=attack)
        entry["conversation_id"] = attack.conversation_id
        entry["messages"] = messages
        conversations.append(entry)
    return conversations


def _dumps_json(payload: Any, *, indent: int = 2) -> str:
    """
    Serialize a payload to indented JSON (``default=str`` for UUID/datetime).

    Args:
        payload (Any): The JSON-ready structure.
        indent (int): JSON indentation width. Defaults to 2.

    Returns:
        str: The serialized JSON.
    """
    return json.dumps(payload, indent=indent, default=str, ensure_ascii=False)
