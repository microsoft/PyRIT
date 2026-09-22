# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Atomic branch insertion and reference updates on real SQLite sessions."""

import threading
import uuid
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import URL, create_engine, select
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

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


@pytest.fixture
def file_memory(*, sqlite_instance: SQLiteMemory, tmp_path: Path) -> Generator[SQLiteMemory, None, None]:
    engine = create_engine(URL.create("sqlite", database=str(tmp_path / "branching.db")))
    try:
        Base.metadata.create_all(engine)
        with (
            patch.object(sqlite_instance, "engine", engine),
            patch.object(sqlite_instance, "SessionFactory", sessionmaker(bind=engine)),
            patch.object(sqlite_instance, "_connection_lock", None),
        ):
            yield sqlite_instance
    finally:
        engine.dispose()


def _store_attack(memory: MemoryInterface) -> tuple[AttackResult, Conversation]:
    source = Conversation(
        conversation_id=str(uuid.uuid4()),
        target_identifier=TargetIdentifier(class_name="ExampleTarget", class_module="tests.unit"),
    )
    attack = AttackResult(conversation_id=source.conversation_id, objective="Atomic branches")
    memory.add_attack_results_to_memory(attack_results=[attack])
    memory.add_conversation_to_memory(conversation=source)
    return attack, source


def _copy(
    *, memory: MemoryInterface, source: Conversation, messages: list[Message]
) -> tuple[Conversation, list[MessagePiece]]:
    conversation_id, pieces = memory.duplicate_messages(messages=messages)
    return source.model_copy(update={"conversation_id": conversation_id}, deep=True), list(pieces)


@pytest.mark.usefixtures("patch_central_database")
class TestAtomicConversationBranching:
    def test_copied_pieces_keep_all_lineage_metadata_order_and_identifier_links(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        attack, source = _store_attack(sqlite_instance)
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
        sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
        messages = list(sqlite_instance.get_conversation_messages(conversation_id=source.conversation_id))
        branch, copies = _copy(memory=sqlite_instance, source=source, messages=messages)
        ephemeral = MessagePiece(role="user", original_value="not saved")
        ephemeral.not_in_memory = True
        stored = sqlite_instance.add_conversation_branches_to_attack(
            attack_result_id=attack.attack_result_id,
            source_conversation=source,
            conversations=[branch],
            message_pieces=[*copies, ephemeral],
        )
        assert stored
        saved = sqlite_instance.get_message_pieces(conversation_id=branch.conversation_id)
        assert [piece.original_prompt_id for piece in saved] == [piece.id for piece in pieces]
        assert [piece.original_value for piece in saved] == ["original-0", "original-1"]
        assert [piece.converted_value for piece in saved] == ["converted-0", "converted-1"]
        assert [piece.prompt_metadata for piece in saved] == [{"piece_index": 0}, {"piece_index": 1}]
        assert saved[1].timestamp > saved[0].timestamp
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) == branch
        assert [piece.original_value_sha256 for piece in saved] == ["original-hash-0", "original-hash-1"]
        assert [piece.converted_value_sha256 for piece in saved] == ["converted-hash-0", "converted-hash-1"]
        assert [piece.converter_identifiers for piece in saved] == [converters, converters]
        assert sqlite_instance.get_message_pieces(prompt_ids=[ephemeral.id]) == []
        with closing(sqlite_instance.get_session()) as session:
            links = session.scalars(
                select(PromptConverterIdentifierEntry).where(
                    PromptConverterIdentifierEntry.prompt_memory_entry_id.in_([piece.id for piece in copies])
                )
            ).all()
        assert len(links) == 4
        assert sorted(link.position for link in links) == [0, 0, 1, 1]

        nested, nested_copies = _copy(
            memory=sqlite_instance,
            source=branch,
            messages=list(sqlite_instance.get_conversation_messages(conversation_id=branch.conversation_id)),
        )
        assert sqlite_instance.add_conversation_branches_to_attack(
            attack_result_id=attack.attack_result_id,
            source_conversation=branch,
            conversations=[nested],
            message_pieces=nested_copies,
        )
        saved_nested = sqlite_instance.get_message_pieces(conversation_id=nested.conversation_id)
        assert [piece.original_prompt_id for piece in saved_nested] == [piece.id for piece in pieces]
        assert {piece.id for piece in saved_nested}.isdisjoint(piece.id for piece in saved)

    def test_empty_conversations_are_registered(self, sqlite_instance: SQLiteMemory) -> None:
        attack, source = _store_attack(sqlite_instance)
        branches = [source.model_copy(update={"conversation_id": str(uuid.uuid4())}, deep=True) for _ in range(4)]
        assert sqlite_instance.add_conversation_branches_to_attack(
            attack_result_id=attack.attack_result_id,
            source_conversation=source,
            conversations=branches,
            message_pieces=[],
        )
        current = sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.get_active_conversation_ids() == {
            source.conversation_id,
            *(item.conversation_id for item in branches),
        }
        assert all(sqlite_instance._get_conversation(conversation_id=item.conversation_id) == item for item in branches)

    @pytest.mark.parametrize("source_registered", [False, True])
    def test_failed_piece_insert_rolls_back_conversations_references_and_identifiers(
        self, *, sqlite_instance: SQLiteMemory, source_registered: bool
    ) -> None:
        source_target = TargetIdentifier(class_name="SourceTarget", class_module="tests.unit")
        source = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=source_target)
        attack = AttackResult(conversation_id=source.conversation_id, objective="Atomic branches")
        sqlite_instance.add_attack_results_to_memory(attack_results=[attack])
        if source_registered:
            sqlite_instance.add_conversation_to_memory(conversation=source)
        original = MessagePiece(
            conversation_id=source.conversation_id, role="user", original_value="Original", sequence=0
        )
        sqlite_instance.add_message_pieces_to_memory(message_pieces=[original])
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
            sqlite_instance.add_conversation_branches_to_attack(
                attack_result_id=attack.attack_result_id,
                source_conversation=source,
                conversations=[branch],
                message_pieces=[copied_piece, conflicting_piece],
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None
        assert sqlite_instance._get_conversation(conversation_id=source.conversation_id) == (
            source if source_registered else None
        )
        current = sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.related_conversations == set()
        assert current.timestamp == attack.timestamp
        assert sqlite_instance.get_message_pieces(conversation_id=branch.conversation_id) == []
        assert [piece.id for piece in sqlite_instance.get_message_pieces(conversation_id=source.conversation_id)] == [
            original.id
        ]
        with closing(sqlite_instance.get_session()) as session:
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

    @pytest.mark.parametrize("column", [None, "adversarial_chat_conversation_ids", "preparation_conversation_ids"])
    def test_unrelated_and_diagnostic_sources_are_rejected(
        self, *, sqlite_instance: SQLiteMemory, column: str | None
    ) -> None:
        attack, source = _store_attack(sqlite_instance)
        excluded = Conversation(conversation_id=str(uuid.uuid4()))
        if column:
            sqlite_instance.update_attack_result_by_id(
                attack_result_id=attack.attack_result_id,
                update_fields={column: [excluded.conversation_id]},
            )
        branch = source.model_copy(update={"conversation_id": str(uuid.uuid4())})
        with pytest.raises(ValueError, match="active objective"):
            sqlite_instance.add_conversation_branches_to_attack(
                attack_result_id=attack.attack_result_id,
                source_conversation=excluded,
                conversations=[branch],
                message_pieces=[],
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    def test_missing_attack_cannot_leave_orphan_conversations(self, sqlite_instance: SQLiteMemory) -> None:
        branch = Conversation(conversation_id=str(uuid.uuid4()))
        assert not sqlite_instance.add_conversation_branches_to_attack(
            attack_result_id=str(uuid.uuid4()), conversations=[branch], message_pieces=[]
        )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    def test_source_target_conflict_rolls_back_branch(self, sqlite_instance: SQLiteMemory) -> None:
        attack, source = _store_attack(sqlite_instance)
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
            sqlite_instance.add_conversation_branches_to_attack(
                attack_result_id=attack.attack_result_id,
                source_conversation=conflicting_source,
                conversations=[branch],
                message_pieces=[],
            )

        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None
        assert sqlite_instance._get_conversation(conversation_id=source.conversation_id) == source
        current = sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.get_active_conversation_ids() == {source.conversation_id}

    @pytest.mark.parametrize("invalid", ["duplicate_id", "source_replacement", "wrong_piece_conversation"])
    def test_invalid_prepared_payload_rolls_back(self, *, sqlite_instance: SQLiteMemory, invalid: str) -> None:
        attack, source = _store_attack(sqlite_instance)
        branch = source.model_copy(update={"conversation_id": str(uuid.uuid4())})
        conversations = [branch, branch] if invalid == "duplicate_id" else [branch]
        pieces: list[MessagePiece] = []
        if invalid == "source_replacement":
            conversations = [source]
        if invalid == "wrong_piece_conversation":
            pieces = [MessagePiece(role="user", original_value="Wrong", conversation_id=source.conversation_id)]
        with pytest.raises(ValueError):
            sqlite_instance.add_conversation_branches_to_attack(
                attack_result_id=attack.attack_result_id,
                source_conversation=source,
                conversations=conversations,
                message_pieces=pieces,
            )
        assert sqlite_instance._get_conversation(conversation_id=branch.conversation_id) is None

    def test_concurrent_branch_creation_and_promotions_preserve_membership(self, file_memory: SQLiteMemory) -> None:
        attack, source = _store_attack(file_memory)
        initial = [Conversation(conversation_id=str(uuid.uuid4())) for _ in range(2)]
        file_memory.add_conversation_branches_to_attack(
            attack_result_id=attack.attack_result_id, conversations=initial, message_pieces=[]
        )
        branches = [Conversation(conversation_id=str(uuid.uuid4())) for _ in range(8)]
        start = threading.Barrier(10)

        def register(*, conversation: Conversation) -> bool:
            start.wait(timeout=10)
            return file_memory.add_conversation_branches_to_attack(
                attack_result_id=attack.attack_result_id,
                source_conversation=source,
                conversations=[conversation],
                message_pieces=[],
            )

        def promote(*, conversation_id: str) -> bool:
            start.wait(timeout=10)
            return file_memory.promote_attack_conversation(
                attack_result_id=attack.attack_result_id, conversation_id=conversation_id
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(register, conversation=branch) for branch in branches]
            futures += [executor.submit(promote, conversation_id=branch.conversation_id) for branch in initial]
            assert all(future.result(timeout=15) for future in futures)
        current = file_memory.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.get_active_conversation_ids() == {
            source.conversation_id,
            *(branch.conversation_id for branch in [*initial, *branches]),
        }
        assert current.conversation_id in {branch.conversation_id for branch in initial}
        assert len(current.get_pruned_conversation_ids()) == 10

    def test_promotion_preserves_diagnostic_and_prior_pruned_references(self, sqlite_instance: SQLiteMemory) -> None:
        attack, source = _store_attack(sqlite_instance)
        promoted, other = str(uuid.uuid4()), str(uuid.uuid4())
        adversarial, preparation = str(uuid.uuid4()), str(uuid.uuid4())
        sqlite_instance.update_attack_result_by_id(
            attack_result_id=attack.attack_result_id,
            update_fields={
                "adversarial_chat_conversation_ids": [adversarial],
                "preparation_conversation_ids": [preparation],
                "pruned_conversation_ids": [promoted, other],
            },
        )
        assert sqlite_instance.promote_attack_conversation(
            attack_result_id=attack.attack_result_id, conversation_id=promoted
        )
        current = sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.conversation_id == promoted
        assert current.get_active_conversation_ids() == {source.conversation_id, promoted, other}
        assert set(current.get_pruned_conversation_ids()) == {source.conversation_id, other}
        assert {
            ConversationReference(conversation_id=adversarial, conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id=preparation, conversation_type=ConversationType.PREPARATION),
        } <= current.related_conversations

        assert sqlite_instance.promote_attack_conversation(
            attack_result_id=attack.attack_result_id, conversation_id=promoted
        )
        assert sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0] == current

    @pytest.mark.parametrize("column", ["adversarial_chat_conversation_ids", "preparation_conversation_ids"])
    def test_promotion_rejects_diagnostic_conversations(self, *, sqlite_instance: SQLiteMemory, column: str) -> None:
        attack, source = _store_attack(sqlite_instance)
        diagnostic = str(uuid.uuid4())
        sqlite_instance.update_attack_result_by_id(
            attack_result_id=attack.attack_result_id,
            update_fields={column: [diagnostic]},
        )
        with pytest.raises(ValueError, match="not part of this attack"):
            sqlite_instance.promote_attack_conversation(
                attack_result_id=attack.attack_result_id, conversation_id=diagnostic
            )
        current = sqlite_instance.get_attack_results(attack_result_ids=[attack.attack_result_id])[0]
        assert current.conversation_id == source.conversation_id
        assert current.get_active_conversation_ids() == {source.conversation_id}

    def test_promotion_returns_false_for_missing_attack(self, sqlite_instance: SQLiteMemory) -> None:
        assert not sqlite_instance.promote_attack_conversation(
            attack_result_id=str(uuid.uuid4()), conversation_id=str(uuid.uuid4())
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
