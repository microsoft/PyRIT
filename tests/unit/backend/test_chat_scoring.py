# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real-memory regression tests for optional scoring of manual chat responses."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import PydanticUserError

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    CreateAttackRequest,
    CreateConversationRequest,
    MessagePieceRequest,
    MessageRequest,
    SaveConversationRequest,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
)
from pyrit.backend.models.message_sends import MessageSendRequest
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.chat_scoring_service import ChatScoringService
from pyrit.backend.services.manual_send_scheduler import ManualSendScheduler
from pyrit.backend.services.message_send_service import MessageSendService
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    MessageScorable,
    Score,
    ScorerIdentifier,
    ScoringExpectation,
    TargetIdentifier,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.results.attack_result import AttackScoringInputs
from pyrit.registry import ScorerRegistry
from pyrit.score import ManualScorer, MessageToolCallScorer, SubStringScorer
from unit.mocks import MockPromptTarget


@pytest.fixture
async def scoring_chat(
    sqlite_instance: SQLiteMemory, patch_central_database: MagicMock
) -> AsyncIterator[tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer]]:
    target = MockPromptTarget()
    scorer = SubStringScorer(substring="default")
    reference = RegisteredComponentReference(registry_name="chat-judge", identifier_hash=scorer.get_identifier().hash)
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="Return the expected response",
        metadata=AttackScoringInputs(scorer=reference, revision="initial").to_metadata(),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack", class_module="pyrit.backend", objective_target=target.get_identifier()
            )
        ),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    ScorerRegistry.get_registry_singleton().instances.register(scorer, name="chat-judge")
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=sqlite_instance),
        patch("pyrit.backend.services.message_send_service.get_target_service") as targets,
    ):
        targets.return_value.get_target_object.return_value = target
        service = MessageSendService(scheduler=ManualSendScheduler())
        try:
            yield service, attack, target, scorer
        finally:
            await service.shutdown_async()
            get_scorer_service.cache_clear()
            ScorerRegistry.reset_registry_singleton()


def chat_request(attack: AttackResult) -> AddMessageRequest:
    return AddMessageRequest(
        target_registry_name="objective",
        target_conversation_id=attack.conversation_id,
        pieces=[MessagePieceRequest(original_value="Test response")],
    )


@pytest.fixture
async def automated_chat(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer]:
    service, _, target, scorer = scoring_chat
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="Return the expected response",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="PromptSendingAttack",
                class_module="pyrit.executor.attack",
                objective_target=target.get_identifier(),
                objective_scorer=ScorerIdentifier.from_component_identifier(scorer.get_identifier()),
            )
        ),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    return service, attack, target, scorer


@pytest.fixture
async def scan_chat(
    automated_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer]:
    service, source, target, scorer = automated_chat
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=source.objective,
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                objective_target=target.get_identifier(),
            )
        ),
    )
    ChatScoringService().continue_manually(
        source=source,
        destination=attack,
        source_conversation_id=source.conversation_id,
        retained_pieces=[],
        explicit_selection=False,
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    return service, attack, target, scorer


@pytest.mark.parametrize("mutation", ["send", "store", "objective", "scorer", "branch", "promote", "save"])
async def test_automated_execution_requires_explicit_manual_continuation_async(
    automated_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    mutation: str,
) -> None:
    sender, source, target, _ = automated_chat
    service = AttackService()
    before = (await sqlite_instance.get_attack_results_async(attack_result_ids=[source.attack_result_id]))[
        0
    ].model_dump()
    summary = await service.get_attack_async(attack_result_id=source.attack_result_id)
    assert summary is not None and summary.execution_read_only
    with pytest.raises(PermissionError, match="Continue manually"):
        if mutation in ("send", "store"):
            await sender.add_message_async(
                attack_result_id=source.attack_result_id,
                request=chat_request(source).model_copy(update={"send": mutation == "send"}),
            )
        elif mutation in ("objective", "scorer"):
            await service.update_attack_async(
                attack_result_id=source.attack_result_id,
                request=UpdateAttackRequest(objective="Changed")
                if mutation == "objective"
                else UpdateAttackRequest(chat_scorer=None),
            )
        elif mutation == "branch":
            await service.create_related_conversation_async(
                attack_result_id=source.attack_result_id,
                request=CreateConversationRequest(),
            )
        elif mutation == "promote":
            await service.update_main_conversation_async(
                attack_result_id=source.attack_result_id,
                request=UpdateMainConversationRequest(conversation_id=source.conversation_id),
            )
        else:
            await service.save_conversation_async(
                request=SaveConversationRequest(
                    save_id=uuid.uuid4(),
                    destination="same_attack",
                    attack_result_id=uuid.UUID(source.attack_result_id),
                )
            )
    [unchanged] = await sqlite_instance.get_attack_results_async(attack_result_ids=[source.attack_result_id])
    assert unchanged.model_dump() == before
    assert target.prompt_sent == []


async def test_manual_handoff_copies_history_and_inputs_without_copying_score_links_async(
    automated_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> None:
    _, source, target, scorer = automated_chat
    response = MessagePiece(
        role="assistant",
        conversation_id=source.conversation_id,
        original_value="Original default response",
    ).to_message()
    await sqlite_instance.add_message_to_memory_async(request=response)
    [score] = await scorer.score_async(
        scorable=MessageScorable.from_message(response),
        expectation=ScoringExpectation(objective=source.objective),
    )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=source.attack_result_id,
        update_fields={
            "automated_score_id": score.id,
            "last_response_id": response.message_pieces[0].id,
            "outcome": AttackOutcome.SUCCESS.value,
        },
    )
    [before] = await sqlite_instance.get_attack_results_async(attack_result_ids=[source.attack_result_id])
    service = AttackService()
    request = SaveConversationRequest(
        save_id=uuid.uuid4(),
        destination="new_attack",
        source_attack_result_id=uuid.UUID(source.attack_result_id),
        source_conversation_id=uuid.UUID(source.conversation_id),
        objective=source.objective,
        target_registry_name="objective",
        messages=[
            MessageRequest(
                role="simulated_assistant",
                pieces=[
                    MessagePieceRequest(
                        original_value="Original default response",
                        source_piece_id=str(response.message_pieces[0].id),
                    )
                ],
            )
        ],
    )
    with (
        patch.object(
            service,
            "_get_save_target_async",
            new_callable=AsyncMock,
            return_value=TargetIdentifier.from_component_identifier(target.get_identifier()),
        ),
        patch.object(service, "_validate_editor_target_async", new_callable=AsyncMock, return_value=None),
    ):
        saved = await service.save_conversation_async(request=request)
        retried = await service.save_conversation_async(request=request)
    assert saved.attack.attack_result_id != source.attack_result_id
    assert saved.messages.conversation_id != source.conversation_id
    assert saved.messages.messages[0].role == "simulated_assistant"
    assert saved.attack.manual_continuation is not None
    assert saved.attack.manual_continuation.source_response_id == str(response.message_pieces[0].id)
    assert saved.attack.manual_continuation.source_attack_result_id == source.attack_result_id
    assert saved.attack.manual_continuation.source_conversation_id == source.conversation_id
    assert not saved.attack.execution_read_only
    assert saved.attack.automated_score is None and saved.attack.human_score is None
    assert (
        saved.attack.chat_scorer is not None
        and saved.attack.chat_scorer.identifier_hash == scorer.get_identifier().hash
    )
    assert saved.attack.scoring_inputs.expectation == score.scored_expectation and saved.attack.scoring_inputs.inherited
    assert retried.attack.attack_result_id == saved.attack.attack_result_id
    [unchanged] = await sqlite_instance.get_attack_results_async(attack_result_ids=[source.attack_result_id])
    assert unchanged.model_dump() == before.model_dump()
    assert target.prompt_sent == []


@pytest.mark.parametrize("override", ["off", "replacement"])
async def test_explicit_scan_override_takes_precedence_async(
    scan_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    override: str,
) -> None:
    service, attack, target, scorer = scan_chat
    alternative = SubStringScorer(substring="not present")
    ScorerRegistry.get_registry_singleton().instances.register(alternative, name="alternative")
    reference = RegisteredComponentReference(
        registry_name="alternative", identifier_hash=alternative.get_identifier().hash
    )
    await AttackService().update_attack_async(
        attack_result_id=attack.attack_result_id,
        request=UpdateAttackRequest(chat_scorer=reference if override == "replacement" else None),
    )
    with patch.object(scorer, "score_async", wraps=scorer.score_async) as original_score:
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    assert original_score.call_count == 0
    assert len(target.prompt_sent) == 1
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.atomic_attack_identifier == attack.atomic_attack_identifier
    summary = await AttackService().get_attack_async(attack_result_id=attack.attack_result_id)
    assert summary.chat_scorer_inherited is False
    if override == "off":
        assert stored.automated_score is None and summary.chat_scorer is None
    else:
        assert stored.automated_score.get_value() is False
        assert summary.chat_scorer == reference


@pytest.mark.parametrize("replacement_registered", [False, True])
async def test_unregistered_scan_scorer_is_not_added_or_replaced_async(
    automated_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    replacement_registered: bool,
) -> None:
    service, source, target, _ = automated_chat
    registry = ScorerRegistry.get_registry_singleton()
    registry.instances.unregister("chat-judge")
    if replacement_registered:
        registry.instances.register(SubStringScorer(substring="different"), name="chat-judge")
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=source.objective,
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                objective_target=target.get_identifier(),
            )
        ),
    )
    ChatScoringService().continue_manually(
        source=source,
        destination=attack,
        source_conversation_id=source.conversation_id,
        retained_pieces=[],
        explicit_selection=False,
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    before = [(entry.name, entry.instance) for entry in registry.instances.get_all_instances()]
    summary = await AttackService().get_attack_async(attack_result_id=attack.attack_result_id)
    assert summary is not None and summary.chat_scorer is None and not summary.chat_scorer_inherited
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.get_scoring_failure(stored.conversation_id) is None
    assert stored.scoring_inputs.scorer is None
    assert stored.automated_score is None and len(target.prompt_sent) == 1
    assert [(entry.name, entry.instance) for entry in registry.instances.get_all_instances()] == before


async def test_scan_continuation_preserves_recorded_conditions_async(
    scan_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> None:
    service, attack, target, _ = scan_chat
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    tool_scorer = MessageToolCallScorer()
    ScorerRegistry.get_registry_singleton().instances.register(tool_scorer, name="tool-judge")
    expectation = ScoringExpectation(
        objective=attack.objective,
        conditions=(ToolsCalled(tools=(ToolCallRequirement(name="lookup"),)),),
    )
    response = (await sqlite_instance.get_conversation_messages_async(conversation_id=attack.conversation_id))[-1]
    [original] = await tool_scorer.score_async(scorable=MessageScorable.from_message(response), expectation=expectation)
    settings = AttackScoringInputs(
        scorer=RegisteredComponentReference(
            registry_name="tool-judge", identifier_hash=tool_scorer.get_identifier().hash
        ),
        inherited=True,
        expectation=expectation,
        revision="tool-criteria",
    )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id,
        update_fields={"attack_metadata": settings.to_metadata(), "automated_score_id": original.id},
    )
    with patch.object(
        tool_scorer, "score_async", new_callable=AsyncMock, side_effect=RuntimeError("Transient judge error")
    ):
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [failed] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert failed.automated_score is None
    assert failed.scoring_inputs.expectation == expectation
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.automated_score.scored_expectation == expectation
    assert stored.get_scoring_failure(stored.conversation_id) is None


@pytest.mark.parametrize("explicit_off", [False, True])
async def test_first_manual_objective_uses_suggested_default_unless_explicitly_off_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    explicit_off: bool,
) -> None:
    _, _, _, scorer = scoring_chat
    request = CreateAttackRequest(chat_scorer=None) if explicit_off else CreateAttackRequest()
    service = AttackService()
    created = await service.create_attack_async(request=request)
    [before] = await sqlite_instance.get_attack_results_async(attack_result_ids=[created.attack_result_id])
    assert (before.scoring_inputs is not None) is explicit_off
    reference = RegisteredComponentReference(registry_name="chat-judge", identifier_hash=scorer.get_identifier().hash)
    summary = await service.update_attack_async(
        attack_result_id=created.attack_result_id,
        request=UpdateAttackRequest(objective="New goal", expected_objective="", default_chat_scorer=reference),
    )
    assert summary.chat_scorer == (None if explicit_off else reference)
    assert summary.chat_scorer_inherited is False


@pytest.mark.parametrize("selection", ["unconfigured", "off", "selected"])
async def test_editor_first_objective_uses_default_without_replacing_explicit_choices_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    selection: str,
) -> None:
    _, _, _, scorer = scoring_chat
    alternative = SubStringScorer(substring="alternative")
    ScorerRegistry.get_registry_singleton().instances.register(alternative, name="alternative")
    explicit = RegisteredComponentReference(
        registry_name="alternative", identifier_hash=alternative.get_identifier().hash
    )
    default = RegisteredComponentReference(registry_name="chat-judge", identifier_hash=scorer.get_identifier().hash)
    create_request = (
        CreateAttackRequest()
        if selection == "unconfigured"
        else CreateAttackRequest(chat_scorer=explicit if selection == "selected" else None)
    )
    service = AttackService()
    created = await service.create_attack_async(request=create_request)
    request = SaveConversationRequest(
        save_id=uuid.uuid4(),
        destination="same_attack",
        attack_result_id=uuid.UUID(created.attack_result_id),
        objective="Editor goal",
        expected_objective="",
        default_chat_scorer=default,
    )
    saved = await service.save_conversation_async(request=request)
    retried = await service.save_conversation_async(request=request)
    expected = default if selection == "unconfigured" else explicit if selection == "selected" else None
    assert saved.attack.chat_scorer == expected
    assert retried.messages.conversation_id == saved.messages.conversation_id
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[created.attack_result_id])
    assert stored.objective == "Editor goal"
    assert stored.scoring_inputs.scorer == expected


@pytest.mark.parametrize("has_scorer", [False, True])
async def test_personal_suggestion_never_changes_scan_scoring_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    has_scorer: bool,
) -> None:
    _, _, target, scorer = scoring_chat
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="PromptSendingAttack",
                class_module="pyrit.executor.attack",
                objective_target=target.get_identifier(),
                objective_scorer=ScorerIdentifier.from_component_identifier(scorer.get_identifier())
                if has_scorer
                else None,
            )
        ),
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    alternative = SubStringScorer(substring="alternative")
    ScorerRegistry.get_registry_singleton().instances.register(alternative, name="personal-default")
    with pytest.raises(PermissionError, match="Continue manually"):
        await AttackService().update_attack_async(
            attack_result_id=attack.attack_result_id,
            request=UpdateAttackRequest(
                objective="Goal",
                expected_objective="",
                default_chat_scorer=RegisteredComponentReference(
                    registry_name="personal-default", identifier_hash=alternative.get_identifier().hash
                ),
            ),
        )
    summary = await AttackService().get_attack_async(attack_result_id=attack.attack_result_id)
    if has_scorer:
        assert summary.chat_scorer is not None
        assert summary.chat_scorer.identifier_hash == scorer.get_identifier().hash
    else:
        assert summary.chat_scorer is None
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.scoring_inputs is None


@pytest.mark.parametrize("inherited", [False, True])
async def test_scores_and_restores_actual_response_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    scan_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    inherited: bool,
) -> None:
    service, attack, target, scorer = scan_chat if inherited else scoring_chat
    with patch.object(scorer, "score_async", wraps=scorer.score_async) as score:
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    assert score.call_count == 1
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.outcome == AttackOutcome.SUCCESS
    assert stored.automated_score is not None
    assert stored.automated_score.get_value() is True
    assert stored.automated_score.objective == attack.objective
    assert stored.automated_score.message_piece_id == stored.last_response.id
    assert stored.atomic_attack_identifier == attack.atomic_attack_identifier
    assert stored.scoring_inputs.inherited is inherited
    assert stored.get_scoring_failure(stored.conversation_id) is None
    assert "chat_scoring_result" not in stored.metadata
    assert target.prompt_sent == ["Test response"]
    summary = await AttackService().get_attack_async(attack_result_id=attack.attack_result_id)
    assert summary is not None and summary.chat_scorer == stored.scoring_inputs.scorer
    assert summary.chat_scorer_inherited is inherited
    assert "scoring_failure" not in summary.model_dump()


@pytest.mark.parametrize("wrapped_configuration_error", [False, True])
async def test_scoring_failure_keeps_response_and_allows_next_send_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    wrapped_configuration_error: bool,
) -> None:
    service, attack, target, scorer = scoring_chat
    failure = RuntimeError("Judge unavailable")
    if wrapped_configuration_error:
        failure.__cause__ = PydanticUserError("WorkshopGrade needs StrictBool", code="class-not-fully-defined")
    with patch.object(scorer, "score_async", new_callable=AsyncMock, side_effect=failure):
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    result = stored.get_scoring_failure(stored.conversation_id)
    assert result is not None and result.error
    assert stored.last_response is not None and stored.last_response.converted_value == "default"
    assert str(stored.last_response.id) in result.error
    assert (
        "PydanticUserError (class-not-fully-defined)" if wrapped_configuration_error else "RuntimeError"
    ) in result.error
    assert "Judge unavailable" in result.error and "Traceback:" in result.error
    if wrapped_configuration_error:
        assert "WorkshopGrade needs StrictBool" in result.error
    transcript = await AttackService().get_conversation_messages_async(
        attack_result_id=attack.attack_result_id, conversation_id=attack.conversation_id
    )
    assert transcript.scoring_failure.error == result.error
    assert stored.automated_score is None and stored.outcome == AttackOutcome.UNDETERMINED
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    assert target.prompt_sent == ["Test response", "Test response"]
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.get_scoring_failure(stored.conversation_id) is None
    assert stored.automated_score is not None


@pytest.mark.parametrize("provider", ["openai", "httpx", "unread_httpx"])
def test_scoring_failure_includes_provider_diagnostics(provider: str) -> None:
    import httpx
    from openai import APIStatusError

    request = httpx.Request("POST", "https://judge.invalid/evaluate")
    response = (
        httpx.Response(
            429, stream=httpx.ByteStream(b"unread"), request=request, headers={"x-request-id": "request-123"}
        )
        if provider == "unread_httpx"
        else httpx.Response(
            429, json={"error": "quota_exceeded"}, request=request, headers={"x-request-id": "request-123"}
        )
    )
    failure = (
        APIStatusError("Provider quota exceeded", response=response, body={"error": "quota_exceeded"})
        if provider == "openai"
        else httpx.HTTPStatusError("Provider quota exceeded", request=request, response=response)
    )
    wrapper = RuntimeError("Scoring wrapper")
    wrapper.__cause__ = failure
    detail = MessageSendService._scoring_failure_detail(exception=wrapper, response_id="response-123")
    assert "Provider HTTP status: 429" in detail
    assert "Provider request ID: request-123" in detail
    assert "Provider quota exceeded" in detail and "Scoring wrapper" in detail
    assert ("response body was not read" if provider == "unread_httpx" else "quota_exceeded") in detail
    assert "Traceback:" in detail and "response-123" in detail


@pytest.mark.parametrize("edit", ["receipt", "human", "objective", "scorer"])
async def test_scoring_publication_reloads_concurrent_edits_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    edit: str,
) -> None:
    sender, attack, target, _ = scoring_chat
    original_update = sqlite_instance.update_attack_result_conditionally_async
    injected = False

    async def concurrent_update_async(**kwargs: Any) -> None:
        nonlocal injected
        if not injected and "automated_score_id" in kwargs["update_fields"]:
            injected = True
            if edit == "receipt":
                await original_update(
                    attack_result_id=attack.attack_result_id,
                    expected_fields={},
                    update_fields={"attack_metadata": {"conversation_save:external": "accepted"}},
                )
            elif edit == "human":
                manual = ManualScorer(value=False, rationale="Human decision", user_identifier="tester")
                [human] = await manual.score_async(
                    scorable=MessageScorable(message_piece_ids=(kwargs["update_fields"]["last_response_id"],)),
                    expectation=ScoringExpectation(objective=attack.objective),
                )
                await sqlite_instance.update_attack_result_by_id_async(
                    attack_result_id=attack.attack_result_id,
                    update_fields={"human_score_id": human.id, "outcome": AttackOutcome.FAILURE.value},
                )
            else:
                await AttackService().update_attack_async(
                    attack_result_id=attack.attack_result_id,
                    request=UpdateAttackRequest(objective="Changed goal")
                    if edit == "objective"
                    else UpdateAttackRequest(chat_scorer=None),
                )
        await original_update(**kwargs)

    with patch.object(sqlite_instance, "update_attack_result_conditionally_async", side_effect=concurrent_update_async):
        await sender.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert injected and target.prompt_sent == ["Test response"]
    assert stored.last_response is not None
    if edit in ("receipt", "human"):
        assert stored.automated_score is not None and stored.automated_score.get_value() is True
        assert stored.get_scoring_failure(stored.conversation_id) is None
        if edit == "human":
            assert stored.human_score is not None and stored.outcome is AttackOutcome.FAILURE
            removed = await AttackService().remove_human_score_async(attack_result_id=attack.attack_result_id)
            assert removed is not None and removed.outcome is AttackOutcome.SUCCESS
        else:
            assert stored.metadata["conversation_save:external"] == "accepted"
    else:
        assert stored.automated_score is None
        assert stored.get_scoring_failure(stored.conversation_id) is None
        assert stored.scoring_inputs is not None


@pytest.mark.parametrize("change", ["objective", "scorer"])
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("judge_fails", [False, True])
async def test_late_score_does_not_overwrite_changed_settings_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    scan_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    change: str,
    inherited: bool,
    judge_fails: bool,
) -> None:
    service, attack, _, scorer = scan_chat if inherited else scoring_chat
    entered, release = asyncio.Event(), asyncio.Event()
    original = scorer.score_async

    async def delayed_async(**kwargs: Any) -> list[Score]:
        entered.set()
        await release.wait()
        if judge_fails:
            raise RuntimeError("Late scoring failure")
        return await original(**kwargs)

    with patch.object(scorer, "score_async", side_effect=delayed_async):
        task = asyncio.create_task(
            service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
        )
        try:
            await asyncio.wait_for(entered.wait(), 10)
            update = (
                UpdateAttackRequest(objective="New objective")
                if change == "objective"
                else UpdateAttackRequest(chat_scorer=None)
            )
            await AttackService().update_attack_async(attack_result_id=attack.attack_result_id, request=update)
        finally:
            release.set()
            await task
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.automated_score is None
    assert stored.get_scoring_failure(stored.conversation_id) is None
    assert stored.outcome == AttackOutcome.UNDETERMINED


async def test_store_only_and_missing_objective_do_not_score_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
) -> None:
    service, attack, _, scorer = scoring_chat
    with patch.object(scorer, "score_async", wraps=scorer.score_async) as score:
        await service.add_message_async(
            attack_result_id=attack.attack_result_id,
            request=chat_request(attack).model_copy(update={"send": False}),
        )
        await AttackService().update_attack_async(
            attack_result_id=attack.attack_result_id, request=UpdateAttackRequest(objective="")
        )
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    assert score.call_count == 0


@pytest.mark.parametrize("scoring_failure", [False, True])
async def test_repeated_submission_scores_each_response_once_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
    scoring_failure: bool,
) -> None:
    service, attack, target, scorer = scoring_chat
    request = MessageSendRequest(**chat_request(attack).model_dump(), submission_id="repeat", count=2)
    original = scorer.score_async
    calls = 0

    async def evaluate_async(**kwargs: Any) -> list[Score]:
        nonlocal calls
        calls += 1
        if scoring_failure and calls == 1:
            raise RuntimeError("First branch scorer failed")
        return await original(**kwargs)

    with patch.object(scorer, "score_async", side_effect=evaluate_async) as score:
        status = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
        duplicate = await service.submit_async(attack_result_id=attack.attack_result_id, request=request)
        assert duplicate.send_id == status.send_id
        for _ in range(100):
            status = await service.get_status_async(
                attack_result_id=attack.attack_result_id, send_id=status.send_id, wait_ms=1000
            )
            if status.state in ("completed", "failed"):
                break
        assert status.state == "completed" and status.failure_stage is None
        assert score.call_count == 2
    assert len(target.prompt_sent) == 2
    pieces = await sqlite_instance.get_message_pieces_async(attack_result_id=attack.attack_result_id)
    scores = await sqlite_instance.get_prompt_scores_async(prompt_ids=[piece.id for piece in pieces])
    assert len(scores) == (1 if scoring_failure else 2)
    if scoring_failure:
        failed = next(progress for progress in status.conversations if progress.scoring_error)
        transcript = await AttackService().get_conversation_messages_async(
            attack_result_id=attack.attack_result_id, conversation_id=failed.conversation_id
        )
        assert transcript.scoring_failure is not None
        assert "First branch scorer failed" in transcript.scoring_failure.error
        assert transcript.scoring_failure.conversation_id == failed.conversation_id


async def test_human_override_keeps_precedence_over_new_automated_score_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> None:
    service, attack, _, _ = scoring_chat
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [first] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    manual = ManualScorer(value=False, rationale="Human review", user_identifier="tester")
    scores = await manual.score_async(
        scorable=MessageScorable(message_piece_ids=(first.last_response.id,)),
        expectation=ScoringExpectation(objective=attack.objective),
    )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=attack.attack_result_id,
        update_fields={"human_score_id": scores[0].id, "outcome": AttackOutcome.FAILURE.value},
    )
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.outcome == AttackOutcome.FAILURE
    assert stored.human_score.id == scores[0].id
    assert stored.automated_score.get_value() is True


async def test_changed_registry_identity_reports_scoring_error_without_blocking_send_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> None:
    service, attack, target, _ = scoring_chat
    ScorerRegistry.get_registry_singleton().instances.register(
        SubStringScorer(substring="replacement"), name="chat-judge", replace=True
    )
    await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    result = stored.get_scoring_failure(stored.conversation_id)
    assert result is not None and "configuration changed" in result.error
    assert len(target.prompt_sent) == 1


async def test_scores_complete_final_response_not_an_intermediate_piece_async(
    scoring_chat: tuple[MessageSendService, AttackResult, MockPromptTarget, SubStringScorer],
    sqlite_instance: SQLiteMemory,
) -> None:
    service, attack, target, scorer = scoring_chat

    async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
        return [
            MessagePiece(
                role="assistant", original_value="intermediate", conversation_id=attack.conversation_id
            ).to_message(),
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value=text, conversation_id=attack.conversation_id)
                    for text in ("first piece", "default second piece")
                ]
            ),
        ]

    with (
        patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async),
        patch.object(scorer, "score_async", wraps=scorer.score_async) as score,
    ):
        await service.add_message_async(attack_result_id=attack.attack_result_id, request=chat_request(attack))
    assert score.call_count == 1
    assert len(score.call_args.kwargs["scorable"].message_piece_ids) == 2
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
    assert stored.last_response.converted_value == "first piece"
    assert stored.automated_score.get_value() is True
