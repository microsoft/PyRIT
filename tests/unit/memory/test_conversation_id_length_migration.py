# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Conversation ids up to the shared length limit persist in every table."""

import importlib
import io
import os
import tempfile

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations

from pyrit.memory import AttackResultEntry, PromptMemoryEntry, SQLiteMemory
from pyrit.memory.memory_models import CONVERSATION_ID_MAX_LENGTH, ConversationEntry
from pyrit.memory.migration import _make_config
from pyrit.models import AttackOutcome, AttackResult, Conversation, MessagePiece

_MIGRATION = "pyrit.memory.alembic.versions.0d3592c62ad8_widen_conversation_ids"


@pytest.mark.parametrize("entry_type", [ConversationEntry, AttackResultEntry, PromptMemoryEntry])
def test_every_table_stores_the_longest_conversation_id(entry_type: type) -> None:
    assert entry_type.__table__.c.conversation_id.type.length == CONVERSATION_ID_MAX_LENGTH


def _sql_server_ddl(step: str) -> str:
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="mssql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        getattr(importlib.import_module(_MIGRATION), step)()
    return output.getvalue()


def test_sql_server_migration_rebuilds_the_conversation_key_and_widens_both_tables() -> None:
    ddl = _sql_server_ddl("upgrade")
    assert "DROP CONSTRAINT ' + QUOTENAME(@pk)" in ddl
    assert "ALTER TABLE [Conversations] ALTER COLUMN [conversation_id] VARCHAR(128) NOT NULL" in ddl
    assert "PRIMARY KEY '\n    + @pk_type" in ddl
    assert "ALTER TABLE [AttackResultEntries] ALTER COLUMN [conversation_id] VARCHAR(128) NOT NULL" in ddl


def test_sql_server_downgrade_refuses_long_ids_then_restores_both_columns_and_indexes() -> None:
    ddl = _sql_server_ddl("downgrade")
    guard = ddl.index("DATALENGTH([conversation_id]) > 36")
    narrowed_key = ddl.index("ALTER TABLE [Conversations] ALTER COLUMN [conversation_id] VARCHAR(36) NOT NULL")
    dropped = ddl.index("DROP INDEX [ix_AttackResultEntries_conversation_timestamp_id]")
    narrowed = ddl.index("ALTER TABLE [AttackResultEntries] ALTER COLUMN conversation_id VARCHAR(36) NOT NULL")
    recreated = ddl.index(
        "CREATE INDEX [ix_AttackResultEntries_operation_timestamp_id] ON [AttackResultEntries] "
        "(operation, timestamp, id) INCLUDE (conversation_id)"
    )
    assert guard < narrowed_key < dropped < narrowed < recreated


async def test_memory_accepts_the_longest_conversation_id(sqlite_instance: SQLiteMemory) -> None:
    conversation_id = "c" * CONVERSATION_ID_MAX_LENGTH
    await sqlite_instance.add_conversation_to_memory_async(conversation=Conversation(conversation_id=conversation_id))
    await sqlite_instance.add_message_pieces_to_memory_async(
        message_pieces=[MessagePiece(conversation_id=conversation_id, role="user", original_value="hi")]
    )
    messages = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    assert [message.message_pieces[0].original_value for message in messages] == ["hi"]


async def test_memory_rejects_a_conversation_id_no_table_can_store(sqlite_instance: SQLiteMemory) -> None:
    conversation_id = "c" * (CONVERSATION_ID_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id)
        )
    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[MessagePiece(conversation_id=conversation_id, role="user", original_value="hi")]
        )
    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        AttackResultEntry(
            entry=AttackResult(conversation_id=conversation_id, objective="o", outcome=AttackOutcome.UNDETERMINED)
        )


async def test_memory_rejects_updates_to_a_conversation_id_no_table_can_store(sqlite_instance: SQLiteMemory) -> None:
    too_long = "c" * (CONVERSATION_ID_MAX_LENGTH + 1)
    await sqlite_instance.add_message_pieces_to_memory_async(
        message_pieces=[MessagePiece(conversation_id="kept", role="user", original_value="hi")]
    )
    result = AttackResult(conversation_id="kept", objective="o", outcome=AttackOutcome.UNDETERMINED)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[result])

    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        await sqlite_instance.update_prompt_entries_by_conversation_id_async(
            conversation_id="kept", update_fields={"conversation_id": too_long}
        )
    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        await sqlite_instance.update_attack_result_async(
            conversation_id="kept", update_fields={"conversation_id": too_long}
        )
    with pytest.raises(ValueError, match="conversation_id is 129 characters"):
        await sqlite_instance.update_attack_result_conditionally_async(
            attack_result_id=result.attack_result_id,
            expected_fields={},
            update_fields={"conversation_id": too_long},
        )

    messages = await sqlite_instance.get_conversation_messages_async(conversation_id="kept")
    results = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
    assert [message.message_pieces[0].original_value for message in messages] == ["hi"]
    assert [stored.conversation_id for stored in results] == ["kept"]


def _conversation_id_lengths(connection: sa.Connection) -> dict[str, int]:
    return {
        table: next(
            column["type"].length
            for column in sa.inspect(connection).get_columns(table)
            if column["name"] == "conversation_id"
        )
        for table in ("Conversations", "AttackResultEntries")
    }


def test_sqlite_migration_round_trips_declared_lengths_and_keeps_rows() -> None:
    long_id = "c" * CONVERSATION_ID_MAX_LENGTH
    with tempfile.TemporaryDirectory() as temp_dir:
        engine = sa.create_engine(f"sqlite:///{os.path.join(temp_dir, 'widen.db')}")
        try:
            with engine.begin() as connection:
                config = _make_config(connection=connection)
                command.upgrade(config, "3e55dd099e38")
                connection.execute(
                    sa.text('INSERT INTO "Conversations" (conversation_id) VALUES (:id)'), {"id": "kept"}
                )
                command.upgrade(config, "0d3592c62ad8")
                widened = _conversation_id_lengths(connection)
                connection.execute(
                    sa.text('INSERT INTO "Conversations" (conversation_id) VALUES (:id)'), {"id": long_id}
                )
                command.downgrade(config, "3e55dd099e38")
                restored = _conversation_id_lengths(connection)
                kept = (
                    connection.execute(sa.text('SELECT conversation_id FROM "Conversations" ORDER BY conversation_id'))
                    .scalars()
                    .all()
                )
        finally:
            engine.dispose()
    assert widened == {"Conversations": CONVERSATION_ID_MAX_LENGTH, "AttackResultEntries": CONVERSATION_ID_MAX_LENGTH}
    assert restored == {"Conversations": 36, "AttackResultEntries": 36}
    assert kept == [long_id, "kept"]
