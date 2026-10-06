# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from typing import Any

import pytest
from unit.mocks import MockPromptTarget

from pyrit.common.attack_result_scope import get_current_attack_result_id
from pyrit.converter import SuffixAppendConverter
from pyrit.converter.converter import ConverterResult
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    PromptSendingAttack,
    RedTeamingAttack,
    SingleTurnAttackContext,
)
from pyrit.executor.attack.compound import SequenceCompletionPolicy, SequentialAttack, SequentialChildAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    Message,
    MessagePiece,
    PromptDataType,
    SeedObjective,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.score import SelfAskRefusalScorer, SubStringScorer

pytestmark = pytest.mark.usefixtures("patch_central_database")


class _RecordingTarget(MockPromptTarget):
    """Record, before replying, the current result ID and the result ID stored on the conversation."""

    def __init__(self, *, reply: str = "default", fail: bool = False) -> None:
        super().__init__()
        self.received_ids: list[str | None] = []
        self.linked_ids: list[str | None] = []
        self.conversation_ids: list[str] = []
        self._reply = reply
        self._fail = fail

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1].get_piece()
        received_id = get_current_attack_result_id()
        conversation = await self._memory.get_conversation_metadata_async(conversation_id=request.conversation_id)
        # Append together after the await so concurrent sends keep the three lists aligned.
        self.received_ids.append(received_id)
        self.linked_ids.append(conversation.attack_result_id if conversation else None)
        self.conversation_ids.append(request.conversation_id)
        if self._fail:
            raise RuntimeError("target unavailable")
        return [
            MessagePiece(
                role="assistant", original_value=self._reply, conversation_id=request.conversation_id
            ).to_message()
        ]


class _RecordingConverter(SuffixAppendConverter):
    """Record the current result ID when converting a request."""

    def __init__(self) -> None:
        super().__init__(suffix="!")
        self.received_ids: list[str | None] = []

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        self.received_ids.append(get_current_attack_result_id())
        return await super().convert_async(prompt=prompt, input_type=input_type)


class _FixedConversationAttack(PromptSendingAttack):
    """Send every execution into the same conversation, as a misbehaving custom attack might."""

    def __init__(self, *, conversation_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fixed_conversation_id = conversation_id

    async def _setup_async(self, *, context: SingleTurnAttackContext[Any]) -> None:
        await super()._setup_async(context=context)
        context.conversation_id = self._fixed_conversation_id


class _BranchingAttack(PromptSendingAttack):
    """Copy the objective conversation after sending, as backtracking and branching attacks do."""

    def __init__(self, *, memory: SQLiteMemory, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._branch_memory = memory
        self.branch_ids: list[str] = []

    async def _perform_async(self, *, context: SingleTurnAttackContext[Any]) -> AttackResult:
        result = await super()._perform_async(context=context)
        self.branch_ids.append(
            await self._branch_memory.duplicate_conversation_async(conversation_id=context.conversation_id)
        )
        return result


async def _owned_ids_async(memory: SQLiteMemory, attack_result_id: str) -> set[str]:
    conversations = await memory.get_attack_result_conversations_async(attack_result_id=attack_result_id)
    return {conversation.conversation_id for conversation in conversations}


async def test_each_execution_allocates_one_result_id_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget()
    attack = PromptSendingAttack(objective_target=target)
    context = SingleTurnAttackContext(params=AttackParameters(objective="first"))
    assert context.attack_result_id is None

    first = await attack.execute_with_context_async(context=context)
    first_id = context.attack_result_id
    second = await attack.execute_with_context_async(context=context)

    assert first_id == first.attack_result_id
    assert context.attack_result_id == second.attack_result_id
    assert first.attack_result_id != second.attack_result_id
    assert target.received_ids == [first.attack_result_id, second.attack_result_id]
    assert target.linked_ids == [first.attack_result_id, second.attack_result_id]
    assert get_current_attack_result_id() is None
    stored = await sqlite_instance.get_attack_results_async(
        attack_result_ids=[first.attack_result_id, second.attack_result_id]
    )
    assert {result.attack_result_id for result in stored} == {first.attack_result_id, second.attack_result_id}
    assert await _owned_ids_async(sqlite_instance, first.attack_result_id) == {first.conversation_id}
    assert await _owned_ids_async(sqlite_instance, second.attack_result_id) == {second.conversation_id}


async def test_concurrent_executions_keep_their_own_result_id_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget()
    attack = PromptSendingAttack(objective_target=target)

    results = await asyncio.gather(*(attack.execute_async(objective=f"objective {i}") for i in range(3)))

    assert len({result.attack_result_id for result in results}) == 3
    for result in results:
        assert await _owned_ids_async(sqlite_instance, result.attack_result_id) == {result.conversation_id}
        sent_from = [
            (received, linked)
            for received, linked, conversation_id in zip(
                target.received_ids, target.linked_ids, target.conversation_ids, strict=True
            )
            if conversation_id == result.conversation_id
        ]
        assert sent_from == [(result.attack_result_id, result.attack_result_id)]


async def test_objective_and_adversarial_conversations_are_linked_async(sqlite_instance: SQLiteMemory) -> None:
    objective_target = _RecordingTarget()
    adversarial_chat = _RecordingTarget(
        reply=json.dumps({"next_message": "next", "rationale": "r", "last_response_summary": "s"})
    )
    attack = RedTeamingAttack(
        objective_target=objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
        attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="never present")),
        max_turns=2,
    )

    result = await attack.execute_async(objective="objective")

    assert result.outcome == AttackOutcome.FAILURE
    conversation_ids = result.get_all_conversation_ids()
    assert len(conversation_ids) == 2
    assert await _owned_ids_async(sqlite_instance, result.attack_result_id) == conversation_ids
    assert set(objective_target.received_ids) == {result.attack_result_id}
    assert set(adversarial_chat.received_ids) == {result.attack_result_id}
    assert set(objective_target.linked_ids) == {result.attack_result_id}
    assert set(adversarial_chat.linked_ids) == {result.attack_result_id}

    linked = await sqlite_instance.get_message_pieces_async(attack_result_id=result.attack_result_id)
    expected = [
        piece
        for conversation_id in conversation_ids
        for piece in await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
    ]
    assert {piece.id for piece in linked} == {piece.id for piece in expected}
    assert {piece.role for piece in linked} >= {"system", "user", "assistant"}
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
    assert stored.get_all_conversation_ids() == conversation_ids


async def test_scorer_conversation_is_linked_async(sqlite_instance: SQLiteMemory) -> None:
    verdict = json.dumps({"score_value": "false", "rationale": "r", "description": "d", "metadata": ""})
    judge = _RecordingTarget(reply=verdict)
    attack = PromptSendingAttack(
        objective_target=_RecordingTarget(),
        attack_scoring_config=AttackScoringConfig(objective_scorer=SelfAskRefusalScorer(chat_target=judge)),
    )

    result = await attack.execute_async(objective="objective")

    assert result.automated_score is not None
    assert judge.received_ids == [result.attack_result_id]
    assert judge.linked_ids == [result.attack_result_id]
    [judge_conversation_id] = judge.conversation_ids
    assert judge_conversation_id != result.conversation_id
    assert await _owned_ids_async(sqlite_instance, result.attack_result_id) == {
        result.conversation_id,
        judge_conversation_id,
    }


async def test_converter_and_target_read_the_result_id_before_sending_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget()
    converter = _RecordingConverter()
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(
            request_converters=ConverterConfiguration.from_converters(converters=[converter])
        ),
    )

    result = await attack.execute_async(objective="objective")

    assert converter.received_ids == [result.attack_result_id]
    assert target.received_ids == [result.attack_result_id]
    assert target.linked_ids == [result.attack_result_id]
    linked = await sqlite_instance.get_message_pieces_async(attack_result_id=result.attack_result_id, role="user")
    assert [piece.converted_value for piece in linked] == ["objective !"]


async def test_error_result_keeps_the_allocated_result_id_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget(fail=True)
    attack = PromptSendingAttack(objective_target=target)
    context = SingleTurnAttackContext(params=AttackParameters(objective="objective"))

    with pytest.raises(RuntimeError):
        await attack.execute_with_context_async(context=context)

    assert context.attack_result_id is not None
    assert target.received_ids == [context.attack_result_id]
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[context.attack_result_id])
    assert stored.outcome == AttackOutcome.ERROR
    assert await _owned_ids_async(sqlite_instance, context.attack_result_id) == {stored.conversation_id}
    linked = await sqlite_instance.get_message_pieces_async(attack_result_id=context.attack_result_id)
    assert [piece.role for piece in linked] == ["user", "assistant"]
    assert linked[1].response_error != "none"


@pytest.mark.parametrize("fail", [False, True])
async def test_standalone_result_records_its_role_without_a_parent_async(
    sqlite_instance: SQLiteMemory, fail: bool
) -> None:
    attack = PromptSendingAttack(objective_target=_RecordingTarget(fail=fail))
    context = SingleTurnAttackContext(params=AttackParameters(objective="objective"))

    if fail:
        with pytest.raises(RuntimeError):
            await attack.execute_with_context_async(context=context)
    else:
        await attack.execute_with_context_async(context=context)

    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[context.attack_result_id])
    assert (stored.outcome == AttackOutcome.ERROR) is fail
    assert stored.attribution_parent_id is None
    assert stored.attribution_data == {"result_role": "target_facing"}


@pytest.mark.parametrize("fail", [False, True])
async def test_standalone_sequential_results_record_their_roles_without_a_parent_async(
    sqlite_instance: SQLiteMemory, fail: bool
) -> None:
    target = _RecordingTarget(fail=fail)
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
    sequential = SequentialAttack(
        objective_target=target,
        child_attacks=[
            SequentialChildAttack(strategy=PromptSendingAttack(objective_target=target), seed_group=seed_group)
        ],
    )

    if fail:
        with pytest.raises(RuntimeError):
            await sequential.execute_async(objective="objective")
    else:
        await sequential.execute_async(objective="objective")

    stored = await sqlite_instance.get_attack_results_async()
    assert len(stored) == 2
    assert all((result.outcome == AttackOutcome.ERROR) is fail for result in stored)
    assert all(result.attribution_parent_id is None for result in stored)
    assert sorted(result.attribution_data["result_role"] for result in stored) == ["orchestration", "target_facing"]
    assert all(result.attribution_data.keys() == {"result_role"} for result in stored)


async def test_history_from_an_earlier_execution_is_copied_into_a_new_conversation_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    target = _RecordingTarget()
    attack = PromptSendingAttack(objective_target=target)
    first = await attack.execute_async(objective="first")
    history = list(await sqlite_instance.get_conversation_messages_async(conversation_id=first.conversation_id))

    second = await attack.execute_async(objective="second", prepended_conversation=history)

    assert second.conversation_id != first.conversation_id
    assert await _owned_ids_async(sqlite_instance, first.attack_result_id) == {first.conversation_id}
    assert await _owned_ids_async(sqlite_instance, second.attack_result_id) == {second.conversation_id}
    pieces = await sqlite_instance.get_message_pieces_async(attack_result_id=second.attack_result_id)
    copied = [piece for piece in pieces if piece.prompt_metadata.get(MessagePiece.PREPENDED_HISTORY_METADATA_KEY)]
    assert len(copied) == 2
    assert {piece.conversation_id for piece in pieces} == {second.conversation_id}
    first_pieces = await sqlite_instance.get_message_pieces_async(attack_result_id=first.attack_result_id)
    assert {piece.conversation_id for piece in first_pieces} == {first.conversation_id}
    assert len(first_pieces) == 2


async def test_reusing_another_executions_conversation_raises_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget()
    attack = _FixedConversationAttack(conversation_id="shared-conversation", objective_target=target)
    first = await attack.execute_async(objective="first")
    assert first.conversation_id == "shared-conversation"

    context = SingleTurnAttackContext(params=AttackParameters(objective="second"))
    with pytest.raises(RuntimeError, match="cannot be assigned to attack result") as raised:
        await attack.execute_with_context_async(context=context)

    assert isinstance(raised.value.__cause__, ValueError)

    assert context.attack_result_id not in (None, first.attack_result_id)
    assert target.received_ids == [first.attack_result_id]
    conversation = await sqlite_instance.get_conversation_metadata_async(conversation_id="shared-conversation")
    assert conversation is not None
    assert conversation.attack_result_id == first.attack_result_id
    assert len(await sqlite_instance.get_message_pieces_async(conversation_id="shared-conversation")) == 2
    assert await sqlite_instance.get_message_pieces_async(attack_result_id=context.attack_result_id) == []


async def test_copies_within_an_execution_keep_its_result_id_async(sqlite_instance: SQLiteMemory) -> None:
    attack = _BranchingAttack(memory=sqlite_instance, objective_target=_RecordingTarget())

    result = await attack.execute_async(objective="objective")

    [branch_id] = attack.branch_ids
    assert branch_id != result.conversation_id
    assert await _owned_ids_async(sqlite_instance, result.attack_result_id) == {result.conversation_id, branch_id}
    assert len(await sqlite_instance.get_message_pieces_async(conversation_id=branch_id)) == 2


async def test_child_attack_conversations_link_to_the_child_async(sqlite_instance: SQLiteMemory) -> None:
    target = _RecordingTarget()
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value="objective")])
    sequential = SequentialAttack(
        objective_target=target,
        child_attacks=[
            SequentialChildAttack(strategy=PromptSendingAttack(objective_target=target), seed_group=seed_group),
            SequentialChildAttack(strategy=PromptSendingAttack(objective_target=target), seed_group=seed_group),
        ],
        completion_policy=SequenceCompletionPolicy.EXHAUSTIVE,
    )

    result = await sequential.execute_async(objective="objective")

    children = result.child_attack_results
    assert len(children) == 2
    child_ids = [child.attack_result_id for child in children]
    assert len({result.attack_result_id, *child_ids}) == 3
    assert target.received_ids == child_ids
    assert target.linked_ids == child_ids
    for child in children:
        assert await _owned_ids_async(sqlite_instance, child.attack_result_id) == {child.conversation_id}
    assert await _owned_ids_async(sqlite_instance, result.attack_result_id) == set()
