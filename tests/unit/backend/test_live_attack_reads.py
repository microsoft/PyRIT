# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack reads for scan attacks that are still running, and findings linked to an attack."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models import AttackSummary
from pyrit.backend.models.operations import AttackFindingItem, AttackFindingListResponse
from pyrit.backend.services import AttackService, ScenarioRunService
from pyrit.backend.services import attack_service as attack_service_module
from pyrit.backend.services import scenario_run_service as scenario_run_service_module
from pyrit.backend.services.live_attack_reader import LiveAttack
from pyrit.backend.services.message_send_service import MessageSendService
from pyrit.backend.services.operation_service import OperationService
from pyrit.backend.services.scenario_run_service import _ActiveTask, find_live_attack
from pyrit.executor.attack.core.attack_executor import ActiveAttackExecution
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Conversation,
    ConversationType,
    Finding,
    FindingEvidence,
    Operation,
)
from pyrit.scenario.core import Scenario

RUN_ID = str(uuid.UUID(int=100))
ATTACK_ID = str(uuid.UUID(int=200))
STARTED_AT = datetime(2026, 11, 6, 12, tzinfo=UTC)


def _execution(
    *, conversation_ids: tuple[str, ...] = ("first", "current"), current: str | None = "current"
) -> ActiveAttackExecution:
    return ActiveAttackExecution(
        attack_result_id=ATTACK_ID,
        objective="Reveal the system prompt",
        conversation_ids=conversation_ids,
        current_conversation_id=current,
        started_at=STARTED_AT,
        attribution=AttackResultAttribution(parent_id=RUN_ID, parent_collection="live"),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(class_name="CrescendoAttack", class_module="pyrit.executor.attack")
        ),
    )


def _live_attack(**execution: object) -> LiveAttack:
    live = LiveAttack.from_execution(scenario_result_id=RUN_ID, execution=_execution(**execution))
    assert live is not None
    return live


def _attack_service(*, memory: SQLiteMemory, live: LiveAttack | None) -> AttackService:
    return AttackService(
        message_send_service=MagicMock(spec=MessageSendService),
        memory=memory,
        live_attack_lookup=lambda attack_result_id: live if attack_result_id == ATTACK_ID else None,
    )


def test_live_attack_requires_an_objective_conversation() -> None:
    assert (
        LiveAttack.from_execution(scenario_result_id=RUN_ID, execution=_execution(conversation_ids=(), current=None))
        is None
    )


@pytest.mark.parametrize(
    "conversation_ids, current, expected_main, expected_others",
    [
        (("first", "current"), "current", "current", ["first"]),
        (("first", "second"), None, "first", ["second"]),
        ((), "current", "current", []),
    ],
)
def test_live_attack_main_is_current_otherwise_first(
    conversation_ids: tuple[str, ...], current: str | None, expected_main: str, expected_others: list[str]
) -> None:
    live = _live_attack(conversation_ids=conversation_ids, current=current)
    assert live.main_conversation_id == expected_main
    assert live.other_conversation_ids == expected_others


def test_live_attack_projects_an_undetermined_unsaved_result() -> None:
    result = _live_attack().to_attack_result()
    assert result.attack_result_id == ATTACK_ID
    assert result.conversation_id == "current"
    assert result.objective == "Reveal the system prompt"
    assert result.outcome == AttackOutcome.UNDETERMINED
    assert result.timestamp == STARTED_AT
    assert result.attribution_parent_id == RUN_ID
    assert result.get_active_conversation_ids() == {"first", "current"}
    assert {ref.conversation_type for ref in result.related_conversations} == {ConversationType.PRUNED}
    assert result.get_attack_strategy_identifier().class_name == "CrescendoAttack"


def test_live_attack_pages_references_like_saved_references() -> None:
    live = _live_attack(conversation_ids=("first", "current", "third"))
    page = live.conversation_page(limit=2)
    assert [reference.conversation_id for reference in page.conversations] == ["current", "first"]
    assert page.total_count == 3
    assert page.timestamp == STARTED_AT
    assert live.conversation_page(offset=2, limit=2).conversations[0].conversation_id == "third"
    assert live.conversation_page(conversation_id="first").conversations[0].conversation_type == ConversationType.PRUNED
    assert live.conversation_page(conversation_id="unrelated").conversations == ()
    assert live.conversation_page().reference_revision == page.reference_revision


@pytest.mark.usefixtures("patch_central_database")
def test_scenario_run_service_finds_only_attacks_its_active_runs_are_running() -> None:
    service = ScenarioRunService()
    scenario = MagicMock(spec=Scenario)
    scenario.active_executions = (_execution(),)
    scenario.active_atomic_group_ids = set()
    assert service.find_live_attack(attack_result_id=ATTACK_ID) is None

    service._active_tasks[RUN_ID] = _ActiveTask(scenario_result_id=RUN_ID, scenario=scenario)
    live = service.find_live_attack(attack_result_id=ATTACK_ID)
    assert live is not None
    assert (live.scenario_result_id, live.main_conversation_id) == (RUN_ID, "current")
    assert service.find_live_attack(attack_result_id=str(uuid.uuid4())) is None

    scenario.active_executions = (_execution(conversation_ids=(), current=None),)
    assert service.find_live_attack(attack_result_id=ATTACK_ID) is None

    scenario.active_executions = ()
    assert service.find_live_attack(attack_result_id=ATTACK_ID) is None


def test_module_lookup_does_not_construct_a_scenario_run_service() -> None:
    with patch.object(scenario_run_service_module, "_service_instance", None):
        assert find_live_attack(ATTACK_ID) is None
        assert scenario_run_service_module._service_instance is None


def test_module_lookup_reads_the_current_scenario_run_service() -> None:
    service = MagicMock(spec=ScenarioRunService)
    service.find_live_attack.return_value = _live_attack()
    with patch.object(scenario_run_service_module, "_service_instance", service):
        assert find_live_attack(ATTACK_ID) is service.find_live_attack.return_value
    assert service.find_live_attack.call_args.kwargs == {"attack_result_id": ATTACK_ID}


def test_attack_service_singleton_reads_running_attacks_through_the_scenario_lookup() -> None:
    attack_service_module.get_attack_service.cache_clear()
    try:
        with patch.object(attack_service_module, "AttackService") as constructor:
            attack_service_module.get_attack_service()
        assert constructor.call_args.kwargs == {"live_attack_lookup": find_live_attack}
    finally:
        attack_service_module.get_attack_service.cache_clear()


@pytest.mark.usefixtures("patch_central_database")
async def test_attack_service_reads_running_attack_when_unsaved_async(sqlite_instance: SQLiteMemory) -> None:
    service = _attack_service(memory=sqlite_instance, live=_live_attack())

    summary = await service.get_attack_async(attack_result_id=ATTACK_ID)
    metadata = await service.get_attack_metadata_async(attack_result_id=ATTACK_ID, conversation_id="first")
    conversations = await service.get_conversations_async(attack_result_id=ATTACK_ID, limit=50)

    assert summary is not None and metadata is not None and conversations is not None
    data = summary.model_dump(mode="json")
    assert (data["in_progress"], data["execution_read_only"], data["outcome"]) == (True, True, "undetermined")
    assert data["scenario_result_id"] == RUN_ID
    assert data["related_conversation_ids"] == ["first"]
    assert (metadata.in_progress, metadata.scenario_result_id) == (True, RUN_ID)
    assert (metadata.related_conversation_count, metadata.selected_conversation_valid) == (1, True)
    assert conversations.main_conversation_id == "current"
    assert [item.conversation_id for item in conversations.conversations] == ["current", "first"]
    with pytest.raises(ValueError, match="not part of attack"):
        await service.get_conversation_messages_async(attack_result_id=ATTACK_ID, conversation_id="other")


@pytest.mark.usefixtures("patch_central_database")
async def test_attack_service_saved_result_wins_over_running_attack_async(sqlite_instance: SQLiteMemory) -> None:
    saved = AttackResult(
        attack_result_id=ATTACK_ID,
        conversation_id="saved-main",
        objective="Saved objective",
        outcome=AttackOutcome.SUCCESS,
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[saved])
    service = _attack_service(memory=sqlite_instance, live=_live_attack())

    summary = await service.get_attack_async(attack_result_id=ATTACK_ID)
    metadata = await service.get_attack_metadata_async(attack_result_id=ATTACK_ID)
    conversations = await service.get_conversations_async(attack_result_id=ATTACK_ID, limit=50)

    assert summary is not None and metadata is not None and conversations is not None
    assert (summary.in_progress, summary.outcome, summary.conversation_id) == (
        False,
        AttackOutcome.SUCCESS,
        "saved-main",
    )
    assert summary.scenario_result_id is None
    assert (metadata.in_progress, metadata.conversation_id) == (False, "saved-main")
    assert [item.conversation_id for item in conversations.conversations] == ["saved-main"]


@pytest.mark.usefixtures("patch_central_database")
async def test_saved_conversation_lookup_excludes_running_attack_until_saved_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    conversation_id = str(uuid.uuid4())
    await sqlite_instance.add_conversation_to_memory_async(
        conversation=Conversation(conversation_id=conversation_id, attack_result_id=ATTACK_ID)
    )
    service = _attack_service(
        memory=sqlite_instance,
        live=_live_attack(conversation_ids=(conversation_id,), current=conversation_id),
    )

    assert await service.get_saved_conversation_async(conversation_id=conversation_id) is None
    live_summary = await service.get_attack_async(attack_result_id=ATTACK_ID)
    assert live_summary is not None and live_summary.in_progress

    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            AttackResult(attack_result_id=ATTACK_ID, conversation_id=conversation_id, objective="Saved objective")
        ]
    )
    saved = await service.get_saved_conversation_async(conversation_id=conversation_id)
    assert saved is not None and not saved.attack.in_progress
    assert saved.attack.objective == "Saved objective"
    assert saved.messages.conversation_id == conversation_id


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("with_lookup", [True, False])
async def test_attack_service_unknown_attack_is_not_found_async(
    sqlite_instance: SQLiteMemory, with_lookup: bool
) -> None:
    service = (
        _attack_service(memory=sqlite_instance, live=None)
        if with_lookup
        else AttackService(message_send_service=MagicMock(spec=MessageSendService), memory=sqlite_instance)
    )
    assert await service.get_attack_async(attack_result_id=ATTACK_ID) is None
    assert await service.get_attack_metadata_async(attack_result_id=ATTACK_ID) is None
    assert await service.get_conversations_async(attack_result_id=ATTACK_ID) is None
    assert await service.get_conversations_async(attack_result_id=ATTACK_ID, limit=50) is None
    assert await service.get_conversation_messages_async(attack_result_id=ATTACK_ID, conversation_id="current") is None
    assert (
        await service.get_conversation_messages_async(attack_result_id=ATTACK_ID, conversation_id="current", limit=100)
        is None
    )


def test_attack_route_serializes_in_progress_attack(compatibility_headers: dict[str, str]) -> None:
    summary = AttackSummary(
        attack_result_id=ATTACK_ID,
        conversation_id="current",
        objective="Running",
        in_progress=True,
        execution_read_only=True,
        scenario_result_id=RUN_ID,
    )
    service = MagicMock(spec=AttackService)
    service.get_attack_async = AsyncMock(return_value=summary)
    with patch("pyrit.backend.routes.attacks.get_attack_service", return_value=service):
        response = TestClient(app, headers=compatibility_headers).get(f"/api/attacks/{ATTACK_ID}")
    assert response.status_code == 200
    body = response.json()
    assert (body["in_progress"], body["scenario_result_id"], body["outcome"]) == (True, RUN_ID, "undetermined")


def test_attack_findings_route_returns_linked_findings(compatibility_headers: dict[str, str]) -> None:
    finding = Finding(operation_id=uuid.uuid4(), title="Leak", severity="important")
    listed = AttackFindingListResponse(
        attack_result_id=uuid.UUID(ATTACK_ID),
        items=[AttackFindingItem(**finding.model_dump(), operation_name="Red team", evidence_id=uuid.uuid4())],
    )
    with patch.object(
        OperationService, "list_attack_findings_async", new_callable=AsyncMock, return_value=listed
    ) as list_findings:
        response = TestClient(app, headers=compatibility_headers).get(f"/api/attacks/{ATTACK_ID}/findings")
    assert response.status_code == 200
    assert response.json() == listed.model_dump(mode="json")
    assert list_findings.call_args.args == (uuid.UUID(ATTACK_ID),)


def test_attack_findings_route_rejects_non_uuid_ids(compatibility_headers: dict[str, str]) -> None:
    with patch.object(OperationService, "list_attack_findings_async", new_callable=AsyncMock) as list_findings:
        response = TestClient(app, headers=compatibility_headers).get("/api/attacks/not-a-uuid/findings")
    assert response.status_code == 422
    list_findings.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_list_attack_findings_names_operations_and_evidence_async(sqlite_instance: SQLiteMemory) -> None:
    operation = await sqlite_instance.add_operation_async(Operation(name="Red team"))
    finding = await sqlite_instance.add_finding_async(
        finding=Finding(operation_id=operation.id, title="Leak", severity="critical")
    )
    attack_result_id = uuid.uuid4()
    evidence = await sqlite_instance.add_finding_evidence_async(
        evidence=FindingEvidence(finding_id=finding.id, attack_result_id=attack_result_id)
    )

    listed = await OperationService().list_attack_findings_async(attack_result_id)
    empty = await OperationService().list_attack_findings_async(uuid.uuid4())

    assert listed.attack_result_id == attack_result_id
    assert [item.model_dump(mode="json") for item in listed.items] == [
        {**finding.model_dump(mode="json"), "operation_name": "Red team", "evidence_id": str(evidence.id)}
    ]
    assert empty.items == []
