# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Atomic branch insertion and reference updates on real SQLite sessions."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.memory.memory_models import (
    AttackResultEntry,
    Base,
    ConverterIdentifierEntry,
    PromptConverterIdentifierEntry,
    TargetIdentifierEntry,
)
from pyrit.models import (
    AttackResult,
    Conversation,
    ConversationReference,
    ConversationType,
    ConverterIdentifier,
    Message,
    MessagePiece,
    TargetIdentifier,
)
from unit.mocks import run_memory_session_async


@pytest.fixture
async def file_memory(*, tmp_path: Path) -> AsyncGenerator[SQLiteMemory, None]:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    memory.__init__(db_path=tmp_path / "branching.db", skip_schema_migration=True)
    try:
        Base.metadata.create_all(memory.engine)
        yield memory
    finally:
        await memory.dispose_engine_async()


async def _store_attack_async(memory: MemoryInterface) -> tuple[AttackResult, Conversation]:
    source = Conversation(
        conversation_id=str(uuid.uuid4()),
        target_identifier=TargetIdentifier(class_name="ExampleTarget", class_module="tests.unit"),
    )
    attack = AttackResult(conversation_id=source.conversation_id, objective="Atomic branches")
    (await memory.add_attack_results_to_memory_async(attack_results=[attack]))
    (await memory.add_conversation_to_memory_async(conversation=source))
    return attack, source


async def _copy_async(
    *, memory: MemoryInterface, source: Conversation, messages: list[Message]
) -> tuple[Conversation, list[MessagePiece]]:
    conversation_id, pieces = await memory.duplicate_messages_async(messages=messages)
    return source.model_copy(update={"conversation_id": conversation_id}, deep=True), list(pieces)


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicConversationBranching:
    async def test_copied_pieces_keep_all_lineage_metadata_order_and_identifier_links(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        converters = [
            ConverterIdentifier(class_name="FirstConverter", class_module="tests.unit"),
            ConverterIdentifier(class_name="SecondConverter", class_module="tests.unit"),
        ]
        pieces = [
            MessagePiece(
                id=uuid.UUID(int=2 - index),
                conversation_id=source.conversation_id,
                role="user",
                sequence=3,
                original_value=f"original-{index}",
                converted_value=f"converted-{index}",
                original_value_sha256=f"original-hash-{index}",
                converted_value_sha256=f"converted-hash-{index}",
                prompt_metadata={"piece_index": index},
                converter_identifiers=converters,
            )
            for index in range(2)
        ]
        pieces[1].timestamp = pieces[0].timestamp
        (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
        messages = list(await sqlite_instance.get_conversation_messages_async(conversation_id=source.conversation_id))
        branch, copies = await _copy_async(memory=sqlite_instance, source=source, messages=messages)
        ephemeral = MessagePiece(role="user", original_value="not saved")
        ephemeral.not_in_memory = True
        stored = await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=attack.attack_result_id,
            source_conversation=source,
            conversations=[branch],
            message_pieces=[*copies, ephemeral],
        )
        assert stored
        saved = await sqlite_instance.get_message_pieces_async(conversation_id=branch.conversation_id)
        assert [piece.original_prompt_id for piece in saved] == [piece.id for piece in pieces]
        assert [piece.original_value for piece in saved] == ["original-0", "original-1"]
        assert [piece.converted_value for piece in saved] == ["converted-0", "converted-1"]
        assert [piece.prompt_metadata for piece in saved] == [{"piece_index": 0}, {"piece_index": 1}]
        assert saved[1].timestamp > saved[0].timestamp
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) == branch
        assert [piece.original_value_sha256 for piece in saved] == ["original-hash-0", "original-hash-1"]
        assert [piece.converted_value_sha256 for piece in saved] == ["converted-hash-0", "converted-hash-1"]
        assert [piece.converter_identifiers for piece in saved] == [converters, converters]
        assert (await sqlite_instance.get_message_pieces_async(prompt_ids=[ephemeral.id])) == []

        def load_links(session):
            return session.scalars(
                select(PromptConverterIdentifierEntry).where(
                    PromptConverterIdentifierEntry.prompt_memory_entry_id.in_([piece.id for piece in copies])
                )
            ).all()

        links = await run_memory_session_async(memory=sqlite_instance, operation=load_links)
        assert len(links) == 4
        assert sorted(link.position for link in links) == [0, 0, 1, 1]

        nested, nested_copies = await _copy_async(
            memory=sqlite_instance,
            source=branch,
            messages=list(
                await sqlite_instance.get_conversation_messages_async(conversation_id=branch.conversation_id)
            ),
        )
        assert await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=attack.attack_result_id,
            source_conversation=branch,
            conversations=[nested],
            message_pieces=nested_copies,
        )
        saved_nested = await sqlite_instance.get_message_pieces_async(conversation_id=nested.conversation_id)
        assert [piece.original_prompt_id for piece in saved_nested] == [piece.id for piece in pieces]
        assert {piece.id for piece in saved_nested}.isdisjoint(piece.id for piece in saved)

    async def test_empty_conversations_are_registered(self, sqlite_instance: SQLiteMemory) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        branches = [source.model_copy(update={"conversation_id": str(uuid.uuid4())}, deep=True) for _ in range(4)]
        assert await sqlite_instance.add_conversation_branches_to_attack_async(
            attack_result_id=attack.attack_result_id,
            source_conversation=source,
            conversations=branches,
            message_pieces=[],
        )
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.get_active_conversation_ids() == {
            source.conversation_id,
            *(item.conversation_id for item in branches),
        }
        assert all(sqlite_instance._get_conversation(conversation_id=item.conversation_id) == item for item in branches)

    @pytest.mark.parametrize("source_registered", [False, True])
    async def test_failed_piece_insert_rolls_back_conversations_references_and_identifiers(
        self, *, sqlite_instance: SQLiteMemory, source_registered: bool
    ) -> None:
        source_target = TargetIdentifier(class_name="SourceTarget", class_module="tests.unit")
        source = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=source_target)
        attack = AttackResult(conversation_id=source.conversation_id, objective="Atomic branches")
        (await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack]))
        if source_registered:
            (await sqlite_instance.add_conversation_to_memory_async(conversation=source))
        original = MessagePiece(
            conversation_id=source.conversation_id, role="user", original_value="Original", sequence=0
        )
        (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[original]))
        new_target = TargetIdentifier(class_name="NewTarget", class_module="tests.unit")
        new_converter = ConverterIdentifier(class_name="NewConverter", class_module="tests.unit")
        branch = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=new_target)
        copied_piece = original.model_copy(
            deep=True,
            update={
                "id": uuid.uuid4(),
                "conversation_id": branch.conversation_id,
                "converter_identifiers": [new_converter],
            },
        )
        conflicting_piece = copied_piece.model_copy(deep=True, update={"id": original.id})
        with pytest.raises(IntegrityError):
            (
                await sqlite_instance.add_conversation_branches_to_attack_async(
                    attack_result_id=attack.attack_result_id,
                    source_conversation=source,
                    conversations=[branch],
                    message_pieces=[copied_piece, conflicting_piece],
                )
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None
        assert sqlite_instance._get_conversation(conversation_id=source.conversation_id) == (
            source if source_registered else None
        )
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.related_conversations == set()
        assert current.timestamp == attack.timestamp
        assert (await sqlite_instance.get_message_pieces_async(conversation_id=branch.conversation_id)) == []
        assert [
            piece.id
            for piece in (await sqlite_instance.get_message_pieces_async(conversation_id=source.conversation_id))
        ] == [original.id]

        def check_links(session):
            assert session.get(TargetIdentifierEntry, new_target.hash) is None
            assert session.get(ConverterIdentifierEntry, new_converter.hash) is None
            assert (session.get(TargetIdentifierEntry, source_target.hash) is not None) == source_registered
            assert (
                session.scalars(
                    select(PromptConverterIdentifierEntry).where(
                        PromptConverterIdentifierEntry.prompt_memory_entry_id == copied_piece.id
                    )
                ).all()
                == []
            )

        await run_memory_session_async(memory=sqlite_instance, operation=check_links)

    @pytest.mark.parametrize("column", [None, "adversarial_chat_conversation_ids", "preparation_conversation_ids"])
    async def test_unrelated_and_diagnostic_sources_are_rejected(
        self, *, sqlite_instance: SQLiteMemory, column: str | None
    ) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        excluded = Conversation(conversation_id=str(uuid.uuid4()))
        if column:
            (
                await sqlite_instance.update_attack_result_by_id_async(
                    attack_result_id=attack.attack_result_id,
                    update_fields={column: [excluded.conversation_id]},
                )
            )
        branch = source.model_copy(update={"conversation_id": str(uuid.uuid4())})
        with pytest.raises(ValueError, match="active objective"):
            (
                await sqlite_instance.add_conversation_branches_to_attack_async(
                    attack_result_id=attack.attack_result_id,
                    source_conversation=excluded,
                    conversations=[branch],
                    message_pieces=[],
                )
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    async def test_missing_attack_cannot_leave_orphan_conversations(self, sqlite_instance: SQLiteMemory) -> None:
        branch = Conversation(conversation_id=str(uuid.uuid4()))
        assert not (
            await sqlite_instance.add_conversation_branches_to_attack_async(
                attack_result_id=str(uuid.uuid4()), conversations=[branch], message_pieces=[]
            )
        )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    async def test_source_target_conflict_rolls_back_branch(self, sqlite_instance: SQLiteMemory) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        conflicting_source = source.model_copy(
            update={
                "target_identifier": TargetIdentifier(
                    class_name="ExampleTarget",
                    class_module="tests.unit",
                    params={"endpoint": "different"},
                )
            }
        )
        branch = source.model_copy(update={"conversation_id": str(uuid.uuid4())})

        with pytest.raises(ValueError, match="already registered with a different target"):
            (
                await sqlite_instance.add_conversation_branches_to_attack_async(
                    attack_result_id=attack.attack_result_id,
                    source_conversation=conflicting_source,
                    conversations=[branch],
                    message_pieces=[],
                )
            )

        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None
        assert sqlite_instance._get_conversation(conversation_id=source.conversation_id) == source
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.get_active_conversation_ids() == {source.conversation_id}

    @pytest.mark.parametrize("invalid", ["duplicate_id", "source_replacement", "wrong_piece_conversation"])
    async def test_invalid_prepared_payload_rolls_back(self, *, sqlite_instance: SQLiteMemory, invalid: str) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        branch = source.model_copy(update={"conversation_id": str(uuid.uuid4())})
        conversations = [branch, branch] if invalid == "duplicate_id" else [branch]
        pieces: list[MessagePiece] = []
        if invalid == "source_replacement":
            conversations = [source]
        if invalid == "wrong_piece_conversation":
            pieces = [MessagePiece(role="user", original_value="Wrong", conversation_id=source.conversation_id)]
        with pytest.raises(ValueError):
            (
                await sqlite_instance.add_conversation_branches_to_attack_async(
                    attack_result_id=attack.attack_result_id,
                    source_conversation=source,
                    conversations=conversations,
                    message_pieces=pieces,
                )
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    async def test_concurrent_branch_creation_and_promotions_preserve_membership(
        self, file_memory: SQLiteMemory
    ) -> None:
        attack, source = await _store_attack_async(file_memory)
        initial = [Conversation(conversation_id=str(uuid.uuid4())) for _ in range(2)]
        (
            await file_memory.add_conversation_branches_to_attack_async(
                attack_result_id=attack.attack_result_id, conversations=initial, message_pieces=[]
            )
        )
        branches = [Conversation(conversation_id=str(uuid.uuid4())) for _ in range(8)]
        start = asyncio.Barrier(10)

        async def register_async(*, conversation: Conversation) -> bool:
            await asyncio.wait_for(start.wait(), timeout=10)
            return await file_memory.add_conversation_branches_to_attack_async(
                attack_result_id=attack.attack_result_id,
                source_conversation=source,
                conversations=[conversation],
                message_pieces=[],
            )

        async def promote_async(*, conversation_id: str) -> bool:
            await asyncio.wait_for(start.wait(), timeout=10)
            return await file_memory.promote_attack_conversation_async(
                attack_result_id=attack.attack_result_id, conversation_id=conversation_id
            )

        results = await asyncio.wait_for(
            asyncio.gather(
                *(register_async(conversation=branch) for branch in branches),
                *(promote_async(conversation_id=branch.conversation_id) for branch in initial),
            ),
            timeout=15,
        )
        assert all(results)
        current = (await file_memory.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.get_active_conversation_ids() == {
            source.conversation_id,
            *(branch.conversation_id for branch in [*initial, *branches]),
        }
        assert current.conversation_id in {branch.conversation_id for branch in initial}
        assert len(current.get_pruned_conversation_ids()) == 10

    async def test_promotion_preserves_diagnostic_and_prior_pruned_references(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        promoted, other = str(uuid.uuid4()), str(uuid.uuid4())
        adversarial, preparation = str(uuid.uuid4()), str(uuid.uuid4())
        (
            await sqlite_instance.update_attack_result_by_id_async(
                attack_result_id=attack.attack_result_id,
                update_fields={
                    "adversarial_chat_conversation_ids": [adversarial],
                    "preparation_conversation_ids": [preparation],
                    "pruned_conversation_ids": [promoted, other],
                },
            )
        )
        assert await sqlite_instance.promote_attack_conversation_async(
            attack_result_id=attack.attack_result_id, conversation_id=promoted
        )
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.conversation_id == promoted
        assert current.get_active_conversation_ids() == {source.conversation_id, promoted, other}
        assert set(current.get_pruned_conversation_ids()) == {source.conversation_id, other}
        assert {
            ConversationReference(conversation_id=adversarial, conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id=preparation, conversation_type=ConversationType.PREPARATION),
        } <= current.related_conversations

        assert await sqlite_instance.promote_attack_conversation_async(
            attack_result_id=attack.attack_result_id, conversation_id=promoted
        )
        assert (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[
            0
        ] == current

    @pytest.mark.parametrize("column", ["adversarial_chat_conversation_ids", "preparation_conversation_ids"])
    async def test_promotion_rejects_diagnostic_conversations(
        self, *, sqlite_instance: SQLiteMemory, column: str
    ) -> None:
        attack, source = await _store_attack_async(sqlite_instance)
        diagnostic = str(uuid.uuid4())
        (
            await sqlite_instance.update_attack_result_by_id_async(
                attack_result_id=attack.attack_result_id,
                update_fields={column: [diagnostic]},
            )
        )
        with pytest.raises(ValueError, match="not part of this attack"):
            (
                await sqlite_instance.promote_attack_conversation_async(
                    attack_result_id=attack.attack_result_id, conversation_id=diagnostic
                )
            )
        current = (await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id]))[0]
        assert current.conversation_id == source.conversation_id
        assert current.get_active_conversation_ids() == {source.conversation_id}

    async def test_promotion_returns_false_for_missing_attack(self, sqlite_instance: SQLiteMemory) -> None:
        assert not (
            await sqlite_instance.promote_attack_conversation_async(
                attack_result_id=str(uuid.uuid4()), conversation_id=str(uuid.uuid4())
            )
        )


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_reference_guard_uses_a_portable_update_before_reading(dialect: object) -> None:
    session = MagicMock(spec=Session)
    result_id = uuid.uuid4()
    MemoryInterface._get_locked_attack_result(session=session, attack_result_id=str(result_id))
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=dialect)
    sql = str(compiled)
    assert sql.startswith("UPDATE ")
    assert "timestamp" in sql
    assert "FOR UPDATE" not in sql
    assert result_id in compiled.params.values()
    assert [call[0] for call in session.method_calls] == ["execute", "get"]
    session.get.assert_called_once_with(AttackResultEntry, result_id, populate_existing=True)
