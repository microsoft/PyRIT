# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging
import os
import tempfile
import uuid
from collections.abc import Sequence
from datetime import timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import ARRAY, DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.sqlite import CHAR, JSON
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.sqltypes import NullType

from pyrit.memory.memory_models import Base, EmbeddingDataEntry, PromptMemoryEntry
from pyrit.memory.migration import run_schema_migrations
from pyrit.models import MessagePiece
from pyrit.prompt_converter.base64_converter import Base64Converter
from pyrit.prompt_target.text_target import TextTarget
from unit.mocks import get_sample_conversation_entries


@pytest.fixture
def sample_conversation_entries() -> Sequence[PromptMemoryEntry]:
    return get_sample_conversation_entries()


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    session.commit = MagicMock()
    session.query = MagicMock()
    session.merge = MagicMock()
    session.rollback = MagicMock()
    return session


def test_conversation_data_schema(sqlite_instance):
    inspector = inspect(sqlite_instance.engine)
    columns = inspector.get_columns("PromptMemoryEntries")
    column_names = [col["name"] for col in columns]

    # Expected columns in ConversationData
    expected_columns = [
        "id",
        "role",
        "conversation_id",
        "sequence",
        "timestamp",
        "labels",
        "prompt_metadata",
        "converter_identifiers",
        "prompt_target_identifier",
        "original_value_data_type",
        "original_value",
        "original_value_sha256",
        "converted_value_data_type",
        "converted_value",
        "converted_value_sha256",
    ]

    for column in expected_columns:
        assert column in column_names, f"{column} not found in PromptMemoryEntries schema."


def test_embedding_data_schema(sqlite_instance):
    inspector = inspect(sqlite_instance.engine)
    columns = inspector.get_columns("EmbeddingData")
    column_names = [col["name"] for col in columns]

    # Expected columns in EmbeddingData
    expected_columns = ["id", "embedding", "embedding_type_name"]
    for column in expected_columns:
        assert column in column_names, f"{column} not found in EmbeddingData schema."


def test_conversation_data_column_types(sqlite_instance):
    inspector = inspect(sqlite_instance.engine)
    columns = inspector.get_columns("PromptMemoryEntries")
    column_types = {col["name"]: type(col["type"]) for col in columns}

    # Expected column types in ConversationData
    expected_column_types = {
        "id": (UUID, CHAR),
        "role": String,
        "conversation_id": String,
        "sequence": Integer,
        "timestamp": DateTime,
        "labels": (String, JSON),
        "prompt_metadata": (String, JSON),
        "converter_identifiers": (String, JSON),
        "prompt_target_identifier": (String, JSON),
        "attack_identifier": (String, JSON),
        "response_error": String,
        "original_value_data_type": String,
        "original_value": String,
        "original_value_sha256": String,
        "converted_value_data_type": String,
        "converted_value": String,
        "converted_value_sha256": String,
        "original_prompt_id": (UUID, CHAR),
    }

    for column, expected_type in expected_column_types.items():
        if column != "labels":
            assert column in column_types, f"{column} not found in PromptMemoryEntries schema."

            # Handle columns that can have multiple types depending on database
            if isinstance(expected_type, tuple):
                assert any(issubclass(column_types[column], t) for t in expected_type), (
                    f"Expected {column} to be a subclass of any of {expected_type}, got {column_types[column]} instead."
                )
            else:
                assert issubclass(column_types[column], expected_type), (
                    f"Expected {column} to be a subclass of {expected_type}, got {column_types[column]} instead."
                )


def test_embedding_data_column_types(sqlite_instance):
    inspector = inspect(sqlite_instance.engine)
    columns = inspector.get_columns("EmbeddingData")
    column_types = {col["name"]: col["type"].__class__ for col in columns}

    # Expected column types in EmbeddingData
    expected_column_types = {
        "id": (UUID, CHAR),  # SQLite uses CHAR for UUID, PostgreSQL uses UUID
        "embedding": ARRAY,
        "embedding_type_name": String,
    }

    for column, expected_type in expected_column_types.items():
        if column != "embedding":
            assert column in column_types, f"{column} not found in EmbeddingStore schema."
            # Handle columns that can have multiple types depending on database
            if isinstance(expected_type, tuple):
                assert any(issubclass(column_types[column], t) for t in expected_type), (
                    f"Expected {column} to be a subclass of any of {expected_type}, got {column_types[column]} instead."
                )
            else:
                # Allow for flexibility in type representation (String vs. VARCHAR)
                assert issubclass(column_types[column], expected_type), (
                    f"Expected {column} to be a subclass of {expected_type}, got {column_types[column]} instead."
                )
    # Handle 'embedding' column separately
    assert "embedding" in column_types, "'embedding' column not found in EmbeddingData schema."
    # Check if 'embedding' column type is either NullType (due to reflection issue), ARRAY, or JSON (SQLite)
    assert column_types["embedding"] in [
        NullType,
        ARRAY,
        JSON,
    ], f"Unexpected type for 'embedding' column: {column_types['embedding']}"


def test_run_schema_migrations_stamps_matching_unversioned_legacy_database():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "legacy-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine, checkfirst=True)

            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "pyrit_memory_alembic_version" in table_names

            with engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()

            assert version
        finally:
            engine.dispose()


def test_run_schema_migrations_stamps_unversioned_legacy_database_with_extra_tables():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "legacy-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine, checkfirst=True)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE "SharedAuditLog" (
                            id INTEGER PRIMARY KEY,
                            event_name VARCHAR NOT NULL
                        )
                        """
                    )
                )

            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "SharedAuditLog" in table_names
            assert "pyrit_memory_alembic_version" in table_names

            with engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()

            assert version
        finally:
            engine.dispose()


def test_run_schema_migrations_fails_synthetic_unversioned_schema_with_drift():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "legacy-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine, checkfirst=True)
            with engine.begin() as connection:
                connection.execute(text('DROP TABLE "ScoreEntries"'))

            with pytest.raises(RuntimeError, match="unversioned legacy memory schema"):
                run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "pyrit_memory_alembic_version" not in table_names
        finally:
            engine.dispose()


def test_run_schema_migrations_fails_pre_alembic_like_schema() -> None:
    """
    Validate strict behavior for unsupported legacy schemas.

    This schema shape intentionally resembles an older pre-Alembic layout where
    newer columns (e.g. pyrit_version, converter_identifiers) are absent.
    Such databases are intentionally unsupported and must fail migration checks.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "legacy-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE "PromptMemoryEntries" (
                            id CHAR(36) NOT NULL,
                            role VARCHAR NOT NULL,
                            conversation_id VARCHAR NOT NULL,
                            sequence INTEGER NOT NULL,
                            timestamp DATETIME NOT NULL,
                            labels JSON NOT NULL,
                            prompt_metadata JSON NOT NULL,
                            prompt_target_identifier JSON NOT NULL,
                            attack_identifier JSON NOT NULL,
                            original_value_data_type VARCHAR NOT NULL,
                            original_value VARCHAR NOT NULL,
                            original_value_sha256 VARCHAR,
                            converted_value_data_type VARCHAR NOT NULL,
                            converted_value VARCHAR,
                            converted_value_sha256 VARCHAR,
                            original_prompt_id CHAR(36) NOT NULL,
                            PRIMARY KEY (id)
                        )
                        """
                    )
                )

            with pytest.raises(RuntimeError, match="unversioned legacy memory schema"):
                run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "pyrit_memory_alembic_version" not in table_names
        finally:
            engine.dispose()


def test_run_schema_migrations_isolates_foreign_alembic_version_table():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "legacy-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine, checkfirst=True)
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE "alembic_version" (version_num VARCHAR(32) NOT NULL)'))

            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "alembic_version" in table_names
            assert "pyrit_memory_alembic_version" in table_names

            with engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()

            assert version
        finally:
            engine.dispose()


def test_reset_database_recreates_schema(sqlite_instance):
    sqlite_instance.reset_database()

    inspector = inspect(sqlite_instance.engine)
    table_names = set(inspector.get_table_names())

    assert {
        "AttackResultEntries",
        "EmbeddingData",
        "PromptMemoryEntries",
        "ScenarioResultEntries",
        "ScoreEntries",
        "SeedPromptEntries",
        "pyrit_memory_alembic_version",
    }.issubset(table_names)

    with sqlite_instance.engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()

    assert version


def test_reset_database_keeps_foreign_alembic_version_table(sqlite_instance):
    with sqlite_instance.engine.begin() as connection:
        connection.execute(text('CREATE TABLE "alembic_version" (version_num VARCHAR(32) NOT NULL)'))

    sqlite_instance.reset_database()

    table_names = set(inspect(sqlite_instance.engine).get_table_names())
    assert "alembic_version" in table_names
    assert "pyrit_memory_alembic_version" in table_names


async def test_insert_entry(sqlite_instance):
    session = sqlite_instance.get_session()
    message_piece_entry = MessagePiece(
        id=uuid.uuid4(),
        conversation_id="123",
        role="user",
        original_value_data_type="text",
        original_value="Hello",
        converted_value="Hello after conversion",
    )
    await message_piece_entry.set_sha256_values_async()

    message_piece_entry.original_value = "Hello"
    message_piece_entry.converted_value = "Hello after conversion"

    entry = PromptMemoryEntry(entry=message_piece_entry)
    # Use the insert_entry method to insert the entry into the database
    sqlite_instance._insert_entry(entry)

    # Now, get a new session to query the database and verify the entry was inserted
    with sqlite_instance.get_session() as session:
        inserted_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert inserted_entry is not None
        assert inserted_entry.role == "user"
        assert inserted_entry.original_value == "Hello"
        sha265 = "185f8db32271fe25f561a6fc938b2e264306ec304eda518007d1764826381969"
        assert inserted_entry.original_value_sha256 == sha265
        assert inserted_entry.converted_value == "Hello after conversion"
        converted_sha256 = "3313a61af7b34d6cde2840bfa000843ac7c6ce5bfaa454ab7e8feef0fb2c5c6c"
        assert inserted_entry.converted_value_sha256 == converted_sha256


def test_insert_entry_violates_constraint(sqlite_instance):
    # Generate a fixed UUID
    fixed_uuid = uuid.uuid4()
    # Create two entries with the same UUID
    entry1 = PromptMemoryEntry(
        entry=MessagePiece(
            id=fixed_uuid,
            conversation_id="123",
            role="user",
            original_value="Hello",
            converted_value="Hello",
        )
    )

    entry2 = PromptMemoryEntry(
        entry=MessagePiece(
            id=fixed_uuid,
            conversation_id="456",
            role="user",
            original_value="Hello again",
            converted_value="Hello again",
        )
    )

    # Insert the first entry
    with sqlite_instance.get_session() as session:
        session.add(entry1)
        session.commit()

    # Attempt to insert the second entry with the same UUID
    with sqlite_instance.get_session() as session:
        session.add(entry2)
        with pytest.raises(SQLAlchemyError):
            session.commit()


def test_insert_entries(sqlite_instance):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(i),
                role="user",
                original_value=f"Message {i}",
                converted_value=f"CMessage {i}",
            )
        )
        for i in range(5)
    ]

    # Now, get a new session to query the database and verify the entries were inserted
    with sqlite_instance.get_session() as session:
        # Use the insert_entries method to insert multiple entries into the database
        sqlite_instance._insert_entries(entries=entries)
        inserted_entries = session.query(PromptMemoryEntry).all()
        assert len(inserted_entries) == 5
        for i, entry in enumerate(inserted_entries):
            assert entry.conversation_id == str(i)
            assert entry.role == "user"
            assert entry.original_value == f"Message {i}"
            assert entry.converted_value == f"CMessage {i}"


def test_insert_embedding_entry(sqlite_instance):
    # Create a ConversationData entry
    conversation_entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="abc")
    )

    # Insert the ConversationData entry using the insert_entry method
    sqlite_instance._insert_entry(conversation_entry)

    # Re-query the ConversationData entry within a new session to ensure it's attached
    with sqlite_instance.get_session() as session:
        # Assuming uuid is the primary key and is set upon insertion
        reattached_conversation_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").one()
        uuid = reattached_conversation_entry.id

    # Now that we have the uuid, we can create and insert the EmbeddingData entry
    embedding_entry = EmbeddingDataEntry(id=uuid, embedding=[1, 2, 3], embedding_type_name="test_type")
    sqlite_instance._insert_entry(embedding_entry)

    # Verify the EmbeddingData entry was inserted correctly
    with sqlite_instance.get_session() as session:
        persisted_embedding_entry = session.query(EmbeddingDataEntry).filter_by(id=uuid).first()
        assert persisted_embedding_entry is not None
        assert persisted_embedding_entry.embedding == [1, 2, 3]
        assert persisted_embedding_entry.embedding_type_name == "test_type"


def test_disable_embedding(sqlite_instance):
    sqlite_instance.disable_embedding()

    assert sqlite_instance.memory_embedding is None, (
        "disable_memory flag was passed, so memory embedding should be disabled."
    )


def test_default_enable_embedding(sqlite_instance):
    os.environ["OPENAI_EMBEDDING_KEY"] = "mock_key"
    os.environ["OPENAI_EMBEDDING_ENDPOINT"] = "embedding"
    os.environ["OPENAI_EMBEDDING_MODEL"] = "deployment"

    sqlite_instance.enable_embedding()

    assert sqlite_instance.memory_embedding is not None, (
        "Memory embedding should be enabled when set with environment variables."
    )


def test_default_embedding_raises(sqlite_instance):
    os.environ["OPENAI_EMBEDDING_KEY"] = ""
    os.environ["OPENAI_EMBEDDING_ENDPOINT"] = ""
    os.environ["OPENAI_EMBEDDING_MODEL"] = ""

    with pytest.raises(ValueError):
        sqlite_instance.enable_embedding()


def test_query_entries(sqlite_instance, sample_conversation_entries):
    for i in range(3):
        sample_conversation_entries[i].conversation_id = str(i)
        sample_conversation_entries[i].original_value = f"Message {i}"
        sample_conversation_entries[i].converted_value = f"Message {i}"

    sqlite_instance._insert_entries(entries=sample_conversation_entries)

    # Query entries without conditions
    queried_entries = sqlite_instance._query_entries(PromptMemoryEntry)
    assert len(queried_entries) == 3

    # Query entries with a condition
    specific_entry = sqlite_instance._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "1"
    )
    assert len(specific_entry) == 1
    assert specific_entry[0].original_value == "Message 1"


def test_get_all_memory(sqlite_instance, sample_conversation_entries):
    sqlite_instance._insert_entries(entries=sample_conversation_entries)

    # Fetch all entries
    all_entries = sqlite_instance.get_message_pieces()
    assert len(all_entries) == 3


def test_get_memories_with_json_properties(sqlite_instance):
    # Define a specific conversation_id
    specific_conversation_id = "test_conversation_id"

    converter_identifiers = [Base64Converter().get_identifier()]
    target = TextTarget()

    # Start a session
    with sqlite_instance.get_session() as session:
        # Create a ConversationData entry with all attributes filled
        piece = MessagePiece(
            conversation_id=specific_conversation_id,
            role="user",
            sequence=1,
            original_value="Test content",
            converted_value="Test content",
            labels={"normalizer_id": "id1"},
            converter_identifiers=converter_identifiers,
            prompt_target_identifier=target.get_identifier(),
        )
        entry = PromptMemoryEntry(entry=piece)

        # Insert the ConversationData entry
        session.add(entry)
        session.commit()

        # Use the get_memories_with_conversation_id method to retrieve entries with the specific conversation_id
        retrieved_entries = sqlite_instance.get_conversation(conversation_id=specific_conversation_id)

        # Verify that the retrieved entry matches the inserted entry
        assert len(retrieved_entries) == 1
        retrieved_entry = retrieved_entries[0].message_pieces[0]
        assert retrieved_entry.conversation_id == specific_conversation_id
        assert retrieved_entry.api_role == "user"
        assert retrieved_entry.original_value == "Test content"
        # For timestamp, you might want to check if it's close to the current time instead of an exact match
        assert abs((retrieved_entry.timestamp - piece.timestamp).total_seconds()) < 0.1
        assert abs((retrieved_entry.timestamp - entry.timestamp.replace(tzinfo=timezone.utc)).total_seconds()) < 0.1

        converter_identifiers = retrieved_entry.converter_identifiers
        assert len(converter_identifiers) == 1
        assert converter_identifiers[0].class_name == "Base64Converter"

        prompt_target = retrieved_entry.prompt_target_identifier
        assert prompt_target.class_name == "TextTarget"

        labels = retrieved_entry.labels
        assert labels["normalizer_id"] == "id1"


def test_update_entries(sqlite_instance):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    sqlite_instance._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update = sqlite_instance._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    sqlite_instance._update_entries(entries=entries_to_update, update_fields={"original_value": "Updated Hello"})

    # Verify the entry was updated
    with sqlite_instance.get_session() as session:
        updated_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert updated_entry.original_value == "Updated Hello"


def test_update_entries_empty_update_fields(sqlite_instance):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    sqlite_instance._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update = sqlite_instance._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    with pytest.raises(ValueError):
        sqlite_instance._update_entries(entries=entries_to_update, update_fields={})


def test_update_entries_nonexistent_fields(sqlite_instance):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    sqlite_instance._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update = sqlite_instance._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    with pytest.raises(ValueError):
        sqlite_instance._update_entries(
            entries=entries_to_update, update_fields={"original_value": "Updated", "nonexistent_field": "Updated Hello"}
        )
    # Verify changes were rolled back and entry was not updated
    assert entries_to_update[0].original_value == "Hello"


def test_update_entries_by_conversation_id(sqlite_instance, sample_conversation_entries):
    # Define a specific conversation_id to update
    specific_conversation_id = "update_test_id"

    sample_conversation_entries[0].conversation_id = specific_conversation_id
    sample_conversation_entries[2].conversation_id = specific_conversation_id

    sample_conversation_entries[1].conversation_id = "other_id"
    original_content = sample_conversation_entries[1].original_value

    # Insert the ConversationData entries using the insert_entries method within a session
    with sqlite_instance.get_session() as session:
        sqlite_instance._insert_entries(entries=sample_conversation_entries)
        session.commit()  # Ensure all entries are committed to the database

        # Define the fields to update for entries with the specific conversation_id
        update_fields = {"original_value": "Updated content", "role": "assistant"}

        # Use the update_prompt_entries_by_conversation_id method to update the entries
        update_result = sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=specific_conversation_id, update_fields=update_fields
        )

        assert update_result is True  # Ensure the update operation was reported as successful

        # Verify that the entries with the specific conversation_id were updated
        updated_entries = sqlite_instance._query_entries(
            PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == specific_conversation_id
        )
        for entry in updated_entries:
            assert entry.original_value == "Updated content"
            assert entry.role == "assistant"

        # Verify that the entry with a different conversation_id was not updated
        other_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="other_id").first()
        assert other_entry.original_value == original_content  # Content should remain unchanged


def test_update_labels_by_conversation_id(sqlite_instance, sample_conversation_entries):
    # Define a specific conversation_id to update
    specific_conversation_id = "update_test_id"

    sample_conversation_entries[0].conversation_id = specific_conversation_id
    sample_conversation_entries[2].conversation_id = specific_conversation_id

    sample_conversation_entries[1].conversation_id = "other_id"
    original_labels = sample_conversation_entries[1].labels

    # Insert the ConversationData entries using the insert_entries method within a session
    with sqlite_instance.get_session() as session:
        sqlite_instance._insert_entries(entries=sample_conversation_entries)
        session.commit()  # Ensure all entries are committed to the database

        # Define the fields to update for entries with the specific conversation_id
        update_fields = {"labels": {"new_label": "new_value"}}

        # Use the update_prompt_entries_by_conversation_id method to update the entries
        update_result = sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=specific_conversation_id, update_fields=update_fields
        )

        assert update_result is True  # Ensure the update operation was reported as successful

        # Verify that the entries with the specific conversation_id were updated
        updated_entries = sqlite_instance._query_entries(
            PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == specific_conversation_id
        )
        for entry in updated_entries:
            assert entry.labels == {"new_label": "new_value"}

        # Verify that the entry with a different conversation_id was not updated
        other_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="other_id").first()
        assert other_entry.labels == original_labels  # Labels should remain unchanged


def test_update_prompt_metadata_by_conversation_id(sqlite_instance, sample_conversation_entries):
    # Define a specific conversation_id to update
    specific_conversation_id = "update_test_id"

    sample_conversation_entries[0].conversation_id = specific_conversation_id
    sample_conversation_entries[2].conversation_id = specific_conversation_id

    sample_conversation_entries[1].conversation_id = "other_id"
    original_metadata = sample_conversation_entries[1].prompt_metadata

    # Insert the ConversationData entries using the insert_entries method within a session
    with sqlite_instance.get_session() as session:
        sqlite_instance._insert_entries(entries=sample_conversation_entries)
        session.commit()  # Ensure all entries are committed to the database

        # Define the fields to update for entries with the specific conversation_id
        update_fields = {"prompt_metadata": "updated_metadata"}
        # Use the update_prompt_entries_by_conversation_id method to update the entries
        update_result = sqlite_instance.update_prompt_entries_by_conversation_id(
            conversation_id=specific_conversation_id, update_fields=update_fields
        )

        assert update_result is True  # Ensure the update operation was reported as successful

        # Verify that the entries with the specific conversation_id were updated
        updated_entries = sqlite_instance._query_entries(
            PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == specific_conversation_id
        )
        for entry in updated_entries:
            assert entry.prompt_metadata == "updated_metadata"

        # Verify that the entry with a different conversation_id was not updated
        other_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="other_id").first()
        assert other_entry.prompt_metadata == original_metadata  # Metadata should remain unchanged


def test_get_conversation_stats_returns_empty_for_no_ids(sqlite_instance):
    """Test that get_conversation_stats returns empty dict for empty input."""
    result = sqlite_instance.get_conversation_stats(conversation_ids=[])
    assert result == {}


def test_get_conversation_stats_returns_empty_for_unknown_ids(sqlite_instance):
    """Test that get_conversation_stats omits unknown conversation IDs."""
    result = sqlite_instance.get_conversation_stats(conversation_ids=["nonexistent"])
    assert result == {}


def test_get_conversation_stats_counts_distinct_sequences(sqlite_instance, sample_conversation_entries):
    """Test that message_count reflects distinct sequence numbers, not raw rows."""
    # Extract conversation IDs and sequences before inserting (entries get detached after commit)
    from pyrit.models import Message
    from unit.mocks import get_sample_conversations

    conversations = get_sample_conversations()
    pieces = Message.flatten_to_message_pieces(conversations)
    expected: dict[str, set[int]] = {}
    for p in pieces:
        expected.setdefault(p.conversation_id, set()).add(p.sequence)

    sqlite_instance._insert_entries(entries=sample_conversation_entries)

    conv_ids = list(expected.keys())
    result = sqlite_instance.get_conversation_stats(conversation_ids=conv_ids)

    for conv_id in conv_ids:
        if conv_id in result:
            assert result[conv_id].message_count == len(expected[conv_id]), (
                f"Conv {conv_id}: expected {len(expected[conv_id])}, got {result[conv_id].message_count}"
            )


def test_get_conversation_stats_returns_labels(sqlite_instance):
    """Test that labels from the first piece with non-empty labels are returned."""
    import uuid

    from pyrit.models import MessagePiece

    conv_id = str(uuid.uuid4())
    piece = MessagePiece(
        role="user",
        original_value="hello",
        original_value_data_type="text",
        converted_value="hello",
        converted_value_data_type="text",
        conversation_id=conv_id,
        sequence=0,
        labels={"env": "prod", "source": "gui"},
    )
    entry = PromptMemoryEntry(entry=piece)
    sqlite_instance._insert_entry(entry)

    result = sqlite_instance.get_conversation_stats(conversation_ids=[conv_id])
    assert conv_id in result
    assert result[conv_id].labels == {"env": "prod", "source": "gui"}


def test_get_conversation_stats_preview_truncates(sqlite_instance):
    """Test that last_message_preview is truncated to 100 chars + ellipsis."""
    import uuid

    from pyrit.models import MessagePiece

    conv_id = str(uuid.uuid4())
    long_text = "x" * 200
    piece = MessagePiece(
        role="assistant",
        original_value=long_text,
        original_value_data_type="text",
        converted_value=long_text,
        converted_value_data_type="text",
        conversation_id=conv_id,
        sequence=0,
    )
    entry = PromptMemoryEntry(entry=piece)
    sqlite_instance._insert_entry(entry)

    result = sqlite_instance.get_conversation_stats(conversation_ids=[conv_id])
    assert conv_id in result
    preview = result[conv_id].last_message_preview
    assert preview is not None
    assert len(preview) == 103  # 100 chars + "..."
    assert preview.endswith("...")


def test_get_conversation_stats_batches_multiple_conversations(sqlite_instance):
    """Test that a single call returns stats for multiple conversations."""
    import uuid

    from pyrit.models import MessagePiece

    conv_ids = [str(uuid.uuid4()) for _ in range(3)]
    entries = []
    for i, cid in enumerate(conv_ids):
        for seq in range(i + 1):  # conv 0: 1 msg, conv 1: 2 msgs, conv 2: 3 msgs
            piece = MessagePiece(
                role="user",
                original_value=f"msg-{seq}",
                original_value_data_type="text",
                converted_value=f"msg-{seq}",
                converted_value_data_type="text",
                conversation_id=cid,
                sequence=seq,
            )
            entries.append(PromptMemoryEntry(entry=piece))

    sqlite_instance._insert_entries(entries=entries)

    result = sqlite_instance.get_conversation_stats(conversation_ids=conv_ids)

    assert len(result) == 3
    assert result[conv_ids[0]].message_count == 1
    assert result[conv_ids[1]].message_count == 2
    assert result[conv_ids[2]].message_count == 3


def test_dispose_engine_tolerates_closed_log_stream(sqlite_instance, capsys):
    """Verify dispose_engine does not raise or emit 'Logging error' when streams are closed (GH-1520)."""
    pyrit_logger = logging.getLogger("pyrit")
    prev_level = pyrit_logger.level
    pyrit_logger.setLevel(logging.INFO)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)

    try:
        stream.close()
        sqlite_instance.dispose_engine()
    finally:
        root.removeHandler(handler)
        pyrit_logger.setLevel(prev_level)

    captured = capsys.readouterr()
    assert "Logging error" not in captured.err


def test_create_engine_uses_static_pool_for_in_memory(sqlite_instance):
    """In-memory databases must use StaticPool so all threads share one database."""
    from sqlalchemy.pool import StaticPool

    assert isinstance(sqlite_instance.engine.pool, StaticPool)


def test_run_schema_migrations_early_return_with_existing_version_table():
    """
    Test that migration early-returns when the version table already exists.
    This tests the line 57 return in _validate_and_stamp_unversioned_memory_schema.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "versioned-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(engine, checkfirst=True)

            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "pyrit_memory_alembic_version" in table_names

            with engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()
            assert version

            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert "pyrit_memory_alembic_version" in table_names

            with engine.connect() as connection:
                version_after = connection.execute(
                    text("SELECT version_num FROM pyrit_memory_alembic_version")
                ).scalar_one()
            assert version_after == version
        finally:
            engine.dispose()


def test_run_schema_migrations_no_memory_tables():
    """
    Test that migration early-returns when no memory tables exist.
    This tests the line 60 return in _validate_and_stamp_unversioned_memory_schema.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "empty-memory.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            run_schema_migrations(engine=engine)

            table_names = set(inspect(engine).get_table_names())
            assert {
                "AttackResultEntries",
                "EmbeddingData",
                "PromptMemoryEntries",
                "ScenarioResultEntries",
                "ScoreEntries",
                "SeedPromptEntries",
                "pyrit_memory_alembic_version",
            }.issubset(table_names)
        finally:
            engine.dispose()
