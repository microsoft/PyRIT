# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import Any

import pytest

from pyrit.backend.services.attack_service import AttackService
from pyrit.cli._output import print_conversations_async, print_full_async
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    MessageScorable,
    Score,
)
from pyrit.output import FileSink, output_scenario_conversations_async, output_scenario_full_async
from unit.mocks import make_scenario_result

OBJECTIVE_SCORER = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests")
REFUSAL_SCORER = ComponentIdentifier(class_name="RefusalScorer", class_module="tests")


class _BackendMessagesClient:
    """Serves ``/messages`` payloads from the real backend service over the same memory."""

    async def get_conversation_messages_async(self, *, attack_result_id: str, conversation_id: str) -> dict[str, Any]:
        response = await AttackService().get_conversation_messages_async(
            attack_result_id=attack_result_id, conversation_id=conversation_id
        )
        return response.model_dump(mode="json")


def _score(*, piece_id: uuid.UUID, scorer: ComponentIdentifier, value: str, **kwargs: Any) -> Score:
    return Score(
        score_type="true_false",
        score_value=value,
        score_rationale=f"{scorer.class_name} rationale",
        message_piece_id=piece_id,
        scorer_class_identifier=scorer,
        **kwargs,
    )


def _seed_attack(memory: MemoryInterface, *, response_parts: list[str]) -> AttackResult:
    conversation_id = str(uuid.uuid4())
    user_piece = MessagePiece(role="user", original_value="objective", conversation_id=conversation_id)
    response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value=part, conversation_id=conversation_id)
            for part in response_parts
        ]
    )
    memory.add_message_to_memory(request=Message(message_pieces=[user_piece]))
    memory.add_message_to_memory(request=response)
    scored_piece_id = response.message_pieces[0].id
    memory.add_scores_to_memory(
        scores=[
            _score(piece_id=scored_piece_id, scorer=REFUSAL_SCORER, value="true"),
            _score(
                piece_id=scored_piece_id,
                scorer=OBJECTIVE_SCORER,
                value="false",
                scorable=MessageScorable.from_message(response),
            ),
        ]
    )
    attack = AttackResult(conversation_id=conversation_id, objective="objective", outcome=AttackOutcome.FAILURE)
    memory.add_attack_results_to_memory(attack_results=[attack])
    return attack


def _duplicate_attack(memory: MemoryInterface, *, attack: AttackResult) -> AttackResult:
    duplicate = AttackResult(
        conversation_id=memory.duplicate_conversation(conversation_id=attack.conversation_id),
        objective=attack.objective,
        outcome=attack.outcome,
    )
    memory.add_attack_results_to_memory(attack_results=[duplicate])
    return duplicate


@pytest.mark.parametrize(
    ("cli_printer", "helper", "fmt"),
    [
        (print_conversations_async, output_scenario_conversations_async, "json"),
        (print_full_async, output_scenario_full_async, "json"),
        (print_full_async, output_scenario_full_async, "html"),
    ],
)
async def test_cli_and_notebook_reports_match(
    cli_printer, helper, fmt, sqlite_instance, patch_central_database, tmp_path
):
    two_piece_attack = _seed_attack(sqlite_instance, response_parts=["first part", "second part"])
    attacks = [
        _seed_attack(sqlite_instance, response_parts=["single reply"]),
        two_piece_attack,
        _duplicate_attack(sqlite_instance, attack=two_piece_attack),
    ]
    result = make_scenario_result(attack_results={"tech_a": attacks}, objective_scorer_identifier=OBJECTIVE_SCORER)
    cli_path, notebook_path = tmp_path / "cli_report", tmp_path / "notebook_report"

    await cli_printer(
        result=result,
        client=_BackendMessagesClient(),
        scenario_result_id=str(result.id),
        format=fmt,
        sink=FileSink(path=cli_path),
    )
    await helper(result, format=fmt, sink=FileSink(path=notebook_path))

    # Identical for stored values; the CLI's REST source fills an empty original_value from converted_value.
    assert notebook_path.read_text(encoding="utf-8") == cli_path.read_text(encoding="utf-8")
