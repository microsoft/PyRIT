# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from collections.abc import Generator, MutableSequence, Sequence
from datetime import timezone
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import inspect, text

from pyrit.memory import AzureSQLMemory, EmbeddingDataEntry, PromptMemoryEntry
from pyrit.models import MessagePiece
from pyrit.prompt_converter.base64_converter import Base64Converter
from pyrit.prompt_target.text_target import TextTarget
from unit.mocks import get_azure_sql_memory, get_sample_conversation_entries

if TYPE_CHECKING:
    from pyrit.memory.memory_models import Base


@pytest.fixture
def memory_interface() -> Generator[AzureSQLMemory, None, None]:
    yield from get_azure_sql_memory()


@pytest.fixture
def sample_conversation_entries() -> Sequence[PromptMemoryEntry]:
    return get_sample_conversation_entries()


async def test_insert_entry(memory_interface):
    message_piece = MessagePiece(
        id=uuid.uuid4(),
        conversation_id="123",
        role="user",
        original_value_data_type="text",
        original_value="Hello",
        converted_value="Hello",
    )
    await message_piece.set_sha256_values_async()
    entry = PromptMemoryEntry(entry=message_piece)

    # Insert the entry
    memory_interface._insert_entry(entry)

    # Verify the entry was inserted
    with memory_interface.get_session() as session:
        inserted_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert inserted_entry is not None
        assert inserted_entry.role == "user"
        assert inserted_entry.original_value == "Hello"


def test_insert_entries(memory_interface: AzureSQLMemory):
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
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        # Use the insert_entries method to insert multiple entries into the database
        memory_interface._insert_entries(entries=entries)
        inserted_entries = session.query(PromptMemoryEntry).order_by(PromptMemoryEntry.conversation_id).all()
        assert len(inserted_entries) == 5
        for i, entry in enumerate(inserted_entries):
            assert entry.conversation_id == str(i)
            assert entry.role == "user"
            assert entry.original_value == f"Message {i}"
            assert entry.converted_value == f"CMessage {i}"


def test_insert_embedding_entry(memory_interface: AzureSQLMemory):
    # Create a ConversationData entry
    conversation_entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="abc")
    )

    # Insert the ConversationData entry using the _insert_entry method
    memory_interface._insert_entry(conversation_entry)

    # Re-query the ConversationData entry within a new session to ensure it's attached
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        # Assuming uuid is the primary key and is set upon insertion
        reattached_conversation_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").one()
        uuid = reattached_conversation_entry.id

    # Now that we have the uuid, we can create and insert the EmbeddingData entry
    embedding_entry = EmbeddingDataEntry(id=uuid, embedding=[1, 2, 3], embedding_type_name="test_type")
    memory_interface._insert_entry(embedding_entry)

    # Verify the EmbeddingData entry was inserted correctly
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        persisted_embedding_entry = session.query(EmbeddingDataEntry).filter_by(id=uuid).first()
        assert persisted_embedding_entry is not None
        assert persisted_embedding_entry.embedding == [1, 2, 3]
        assert persisted_embedding_entry.embedding_type_name == "test_type"


def test_disable_embedding(memory_interface: AzureSQLMemory):
    memory_interface.disable_embedding()

    assert memory_interface.memory_embedding is None, (
        "disable_memory flag was passed, so memory embedding should be disabled."
    )


def test_default_enable_embedding(memory_interface: AzureSQLMemory):
    os.environ["OPENAI_EMBEDDING_KEY"] = "mock_key"
    os.environ["OPENAI_EMBEDDING_ENDPOINT"] = "embedding"
    os.environ["OPENAI_EMBEDDING_MODEL"] = "deployment"

    memory_interface.enable_embedding()

    assert memory_interface.memory_embedding is not None, (
        "Memory embedding should be enabled when set with environment variables."
    )


def test_default_embedding_raises(memory_interface: AzureSQLMemory):
    os.environ["OPENAI_EMBEDDING_KEY"] = ""
    os.environ["OPENAI_EMBEDDING_ENDPOINT"] = ""
    os.environ["OPENAI_EMBEDDING_MODEL"] = ""

    with pytest.raises(ValueError):
        memory_interface.enable_embedding()


def test_reset_database_recreates_versioned_schema(memory_interface: AzureSQLMemory):
    memory_interface.reset_database()

    inspector = inspect(memory_interface.engine)
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

    with memory_interface.engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()

    assert version


def test_query_entries(
    memory_interface: AzureSQLMemory, sample_conversation_entries: MutableSequence[PromptMemoryEntry]
):
    for i in range(3):
        sample_conversation_entries[i].conversation_id = str(i)
        sample_conversation_entries[i].original_value = f"Message {i}"
        sample_conversation_entries[i].converted_value = f"Message {i}"

    memory_interface._insert_entries(entries=sample_conversation_entries)

    # Query entries without conditions
    queried_entries: MutableSequence[Base] = memory_interface._query_entries(PromptMemoryEntry)
    assert len(queried_entries) == 3

    # Query entries with a condition
    filtered_entries: MutableSequence[PromptMemoryEntry] = memory_interface._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "1"
    )
    assert len(filtered_entries) == 1
    assert filtered_entries[0].conversation_id == "1"


def test_get_all_memory(
    memory_interface: AzureSQLMemory, sample_conversation_entries: MutableSequence[PromptMemoryEntry]
):
    memory_interface._insert_entries(entries=sample_conversation_entries)

    # Fetch all entries
    all_entries = memory_interface.get_message_pieces()
    assert len(all_entries) == 3


def test_get_memories_with_json_properties(memory_interface: AzureSQLMemory):
    # Define a specific conversation_id
    specific_conversation_id = "test_conversation_id"

    converter_identifiers = [Base64Converter().get_identifier()]
    target = TextTarget()

    # Start a session
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        # Create a ConversationData entry with all attributes filled
        entry = PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=specific_conversation_id,
                role="user",
                sequence=1,
                original_value="Test content",
                converted_value="Test content",
                labels={"normalizer_id": "id1"},
                converter_identifiers=converter_identifiers,
                prompt_target_identifier=target.get_identifier(),
            )
        )

        # Insert the ConversationData entry
        session.add(entry)
        session.commit()

        # Use the get_memories_with_conversation_id method to retrieve entries with the specific conversation_id
        retrieved_entries = memory_interface.get_conversation(conversation_id=specific_conversation_id)

        # Verify that the retrieved entry matches the inserted entry
        assert len(retrieved_entries) == 1
        retrieved_entry = retrieved_entries[0].message_pieces[0]
        assert retrieved_entry.conversation_id == specific_conversation_id
        assert retrieved_entry.api_role == "user"
        assert retrieved_entry.original_value == "Test content"
        # For timestamp, you might want to check if it's close to the current time instead of an exact match
        assert (
            abs((retrieved_entry.timestamp - entry.timestamp.replace(tzinfo=timezone.utc)).total_seconds()) < 10
        )  # Assuming the test runs quickly

        converter_identifiers = retrieved_entry.converter_identifiers
        assert len(converter_identifiers) == 1
        assert converter_identifiers[0].class_name == "Base64Converter"

        prompt_target = retrieved_entry.prompt_target_identifier
        assert prompt_target.class_name == "TextTarget"

        labels = retrieved_entry.labels
        assert labels["normalizer_id"] == "id1"


def test_get_memories_with_attack_id(memory_interface: AzureSQLMemory):
    # This test would require Azure SQL-specific JSON functions (ISJSON, JSON_VALUE)
    # which are not available in SQLite. Testing is covered in integration tests.
    # See test_azure_sql_memory_integration.py for actual Azure SQL testing.
    pytest.skip("Test requires Azure SQL-specific JSON functions; covered by integration tests")


def test_get_attack_result_label_condition_single_label(memory_interface: AzureSQLMemory):
    """Test that _get_attack_result_label_condition builds a valid condition for a single label."""
    condition = memory_interface._get_attack_result_label_condition(labels={"operation": "test_op"})
    compiled = str(condition.compile(compile_kwargs={"literal_binds": False}))
    assert "JSON_VALUE" in compiled
    assert "ISJSON" in compiled


def test_get_attack_result_label_condition_multiple_labels(memory_interface: AzureSQLMemory):
    """Test that _get_attack_result_label_condition builds a valid condition for multiple labels."""
    condition = memory_interface._get_attack_result_label_condition(
        labels={"operation": "test_op", "operator": "roakey"}
    )
    compiled = str(condition.compile(compile_kwargs={"literal_binds": False}))
    # Both AR-direct and PME-conversation branches should appear
    assert "AttackResultEntries" in compiled
    assert "PromptMemoryEntries" in compiled


def test_get_message_pieces_memory_label_conditions_single_label(memory_interface: AzureSQLMemory):
    """Test that _get_message_pieces_memory_label_conditions builds a valid OR condition."""
    conditions = memory_interface._get_message_pieces_memory_label_conditions(memory_labels={"operation": "test_op"})
    assert len(conditions) == 1
    compiled = str(conditions[0].compile(compile_kwargs={"literal_binds": False}))
    assert "ISJSON" in compiled
    assert "JSON_VALUE" in compiled


def test_get_message_pieces_memory_label_conditions_includes_ar_fallback(memory_interface: AzureSQLMemory):
    """Test that the condition references both PME and AR tables for the OR fallback."""
    conditions = memory_interface._get_message_pieces_memory_label_conditions(
        memory_labels={"operation": "test_op", "operator": "roakey"}
    )
    compiled = str(conditions[0].compile(compile_kwargs={"literal_binds": False}))
    assert "AttackResultEntries" in compiled
    assert "PromptMemoryEntries" in compiled


def test_get_message_pieces_memory_label_conditions_bind_params(memory_interface: AzureSQLMemory):
    """Test that bind parameters are created for both PME and AR branches."""
    conditions = memory_interface._get_message_pieces_memory_label_conditions(memory_labels={"operation": "test_op"})
    params = conditions[0].compile().params
    # PME branch param
    assert params.get("pme_ml_operation") == "test_op"
    # AR branch param
    assert params.get("are_ml_operation") == "test_op"


def test_update_entries(memory_interface: AzureSQLMemory):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    memory_interface._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update: MutableSequence[Base] = memory_interface._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    memory_interface._update_entries(entries=entries_to_update, update_fields={"original_value": "Updated Hello"})

    # Verify the entry was updated
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        updated_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert updated_entry.original_value == "Updated Hello"


def test_update_entries_empty_update_fields(memory_interface: AzureSQLMemory):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    memory_interface._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update: MutableSequence[Base] = memory_interface._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    with pytest.raises(ValueError):
        memory_interface._update_entries(entries=entries_to_update, update_fields={})


def test_update_entries_nonexistent_fields(memory_interface):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(conversation_id="123", role="user", original_value="Hello", converted_value="Hello")
    )

    memory_interface._insert_entry(entry)

    # Fetch the entry to update and update its content
    entries_to_update = memory_interface._query_entries(
        PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == "123"
    )
    with pytest.raises(ValueError):
        memory_interface._update_entries(
            entries=entries_to_update, update_fields={"original_value": "Updated", "nonexistent_field": "Updated Hello"}
        )


def test_update_prompt_entries_by_conversation_id(memory_interface: AzureSQLMemory, sample_conversation_entries):
    specific_conversation_id = "update_test_id"

    for entry in sample_conversation_entries:
        entry.conversation_id = specific_conversation_id

    memory_interface._insert_entries(entries=sample_conversation_entries)

    # Update the entry using the update_prompt_entries_by_conversation_id method
    update_result = memory_interface.update_prompt_entries_by_conversation_id(
        conversation_id=specific_conversation_id, update_fields={"original_value": "Updated Hello", "role": "assistant"}
    )

    assert update_result is True

    # Verify the entry was updated
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        updated_entries = session.query(PromptMemoryEntry).filter_by(conversation_id=specific_conversation_id)
        for entry in updated_entries:
            assert entry.original_value == "Updated Hello"
            assert entry.role == "assistant"


def test_update_labels_by_conversation_id(memory_interface: AzureSQLMemory):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(
            conversation_id="123",
            role="user",
            original_value="Hello",
            converted_value="Hello",
            labels={"test": "label"},
        )
    )

    memory_interface._insert_entry(entry)

    # Update the labels using the update_labels_by_conversation_id method
    memory_interface.update_labels_by_conversation_id(conversation_id="123", labels={"test1": "change"})

    # Verify the labels were updated
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        updated_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert updated_entry.labels["test1"] == "change"


@pytest.mark.parametrize(
    "partial_match, expected_value",
    [
        (False, "testvalue"),
        (True, "%testvalue%"),
    ],
    ids=["exact_match", "partial_match"],
)
def test_get_condition_json_property_match_bind_params(
    memory_interface: AzureSQLMemory, partial_match: bool, expected_value: str
):
    condition = memory_interface._get_condition_json_property_match(
        json_column=PromptMemoryEntry.labels,
        property_path="$.key",
        value="TestValue",
        partial_match=partial_match,
    )
    # Extract the compiled bind parameters (param names include a random uid suffix)
    params = condition.compile().params
    pp_params = {k: v for k, v in params.items() if k.startswith("pp_")}
    mv_params = {k: v for k, v in params.items() if k.startswith("mv_")}
    assert len(pp_params) == 1
    assert list(pp_params.values())[0] == "$.key"
    assert len(mv_params) == 1
    assert list(mv_params.values())[0] == expected_value


def test_get_attack_result_label_condition_with_string_value(memory_interface: AzureSQLMemory):
    """String values produce a single-placeholder IN clause with the stringified value."""
    condition = memory_interface._get_attack_result_label_condition(labels={"operator": "roakey"})
    params = condition.compile().params
    assert params.get("pme_label_operator_0") == "roakey"
    assert params.get("are_label_operator_0") == "roakey"


def test_get_attack_result_label_condition_with_sequence_value(memory_interface: AzureSQLMemory):
    """Sequence values produce one placeholder per element."""
    condition = memory_interface._get_attack_result_label_condition(labels={"operation": ["op_a", "op_b", "op_c"]})
    params = condition.compile().params
    assert params.get("pme_label_operation_0") == "op_a"
    assert params.get("pme_label_operation_1") == "op_b"
    assert params.get("pme_label_operation_2") == "op_c"
    assert params.get("are_label_operation_0") == "op_a"
    assert params.get("are_label_operation_1") == "op_b"
    assert params.get("are_label_operation_2") == "op_c"


def test_get_attack_result_label_condition_skips_empty_sequence(memory_interface: AzureSQLMemory):
    """Empty sequence values are skipped (no filter applied for that key)."""
    condition = memory_interface._get_attack_result_label_condition(labels={"operator": "roakey", "operation": []})
    params = condition.compile().params
    # operator gets bind params; operation (empty) does not.
    assert params.get("pme_label_operator_0") == "roakey"
    assert params.get("are_label_operator_0") == "roakey"
    assert not any("label_operation_" in k for k in params)


def test_get_attack_result_label_condition_empty_labels_dict(memory_interface: AzureSQLMemory):
    """An empty labels dict produces a condition with no label filters bound."""
    condition = memory_interface._get_attack_result_label_condition(labels={})
    params = condition.compile().params
    assert not any("label_" in k for k in params)


@pytest.mark.parametrize(
    "case_sensitive, partial_match, expected_sql_fragment",
    [
        (False, False, "LOWER(JSON_VALUE("),
        (True, False, "JSON_VALUE("),
        (False, True, "LOWER(JSON_VALUE("),
    ],
    ids=["case_insensitive_exact", "case_sensitive_exact", "case_insensitive_partial"],
)
def test_get_condition_json_property_match_sql_text(
    memory_interface: AzureSQLMemory,
    case_sensitive: bool,
    partial_match: bool,
    expected_sql_fragment: str,
):
    condition = memory_interface._get_condition_json_property_match(
        json_column=PromptMemoryEntry.labels,
        property_path="$.key",
        value="TestValue",
        partial_match=partial_match,
        case_sensitive=case_sensitive,
    )
    sql_text = str(condition.compile(compile_kwargs={"literal_binds": False}))
    assert expected_sql_fragment in sql_text
    # When case_sensitive=False, LOWER must wrap the entire JSON_VALUE(...) call
    if not case_sensitive:
        assert "LOWER(JSON_VALUE)" not in sql_text.replace("LOWER(JSON_VALUE(", "")


def test_get_condition_json_array_match_all_mode_joins_with_and(memory_interface: AzureSQLMemory):
    """match_mode='all' (default) produces per-element EXISTS clauses joined by AND."""
    from pyrit.memory.memory_models import AttackResultEntry

    condition = memory_interface._get_condition_json_array_match(
        json_column=AttackResultEntry.atomic_attack_identifier,
        property_path="$.children.attack_technique.children.attack.children.request_converters",
        array_element_path="$.class_name",
        array_to_match=["Base64Converter", "ROT13Converter"],
    )
    sql_text = str(condition.compile(compile_kwargs={"literal_binds": False}))
    # Two EXISTS clauses joined by AND inside the outer parens, wrapped by ISJSON AND (...)
    assert sql_text.count("EXISTS(SELECT 1 FROM OPENJSON") == 2
    assert " AND " in sql_text
    assert " OR " not in sql_text


def test_get_condition_json_array_match_any_mode_joins_with_or(memory_interface: AzureSQLMemory):
    """match_mode='any' produces per-element EXISTS clauses joined by OR and wrapped in parens.

    The outer parens are essential: without them, operator precedence would bind the outer
    ``ISJSON(...) = 1 AND`` tighter than the inner ``OR``, corrupting the predicate.
    """
    from pyrit.memory.memory_models import AttackResultEntry

    condition = memory_interface._get_condition_json_array_match(
        json_column=AttackResultEntry.atomic_attack_identifier,
        property_path="$.children.attack_technique.children.attack.children.request_converters",
        array_element_path="$.class_name",
        array_to_match=["Base64Converter", "ROT13Converter"],
        match_mode="any",
    )
    sql_text = str(condition.compile(compile_kwargs={"literal_binds": False}))
    assert sql_text.count("EXISTS(SELECT 1 FROM OPENJSON") == 2
    assert " OR " in sql_text
    # The only "AND" in the statement should be the outer ISJSON(...) = 1 AND (...)
    # wrapper — none of the per-element EXISTS clauses should be AND-joined.
    assert sql_text.count(" AND ") == 1


def test_get_condition_json_array_match_any_mode_preserves_empty_absence_overload(
    memory_interface: AzureSQLMemory,
):
    """match_mode is ignored when array_to_match is empty — always returns the 'no converters' predicate."""
    import re

    from pyrit.memory.memory_models import AttackResultEntry

    def _normalize(condition) -> str:
        # _uid() generates a random per-call suffix on param names; strip it for equality.
        return re.sub(r":pp_[0-9a-f]+", ":pp_X", str(condition.compile(compile_kwargs={"literal_binds": False})))

    condition_any = memory_interface._get_condition_json_array_match(
        json_column=AttackResultEntry.atomic_attack_identifier,
        property_path="$.children.attack_technique.children.attack.children.request_converters",
        array_element_path="$.class_name",
        array_to_match=[],
        match_mode="any",
    )
    condition_all = memory_interface._get_condition_json_array_match(
        json_column=AttackResultEntry.atomic_attack_identifier,
        property_path="$.children.attack_technique.children.attack.children.request_converters",
        array_element_path="$.class_name",
        array_to_match=[],
        match_mode="all",
    )
    # Both modes generate identical "IS NULL OR JSON_QUERY(...) = '[]'" absence predicates.
    assert _normalize(condition_any) == _normalize(condition_all)
    # Neither EXISTS nor OR should appear in the absence predicate.
    assert "EXISTS" not in _normalize(condition_any)


def test_update_prompt_metadata_by_conversation_id(memory_interface: AzureSQLMemory):
    # Insert a test entry
    entry = PromptMemoryEntry(
        entry=MessagePiece(
            conversation_id="123",
            role="user",
            original_value="Hello",
            converted_value="Hello",
            prompt_metadata={"test": "test"},
        )
    )

    memory_interface._insert_entry(entry)

    # Update the metadata using the update_prompt_metadata_by_conversation_id method
    memory_interface.update_prompt_metadata_by_conversation_id(
        conversation_id="123", prompt_metadata={"updated": "updated"}
    )

    # Verify the metadata was updated
    with memory_interface.get_session() as session:  # type: ignore[arg-type]
        updated_entry = session.query(PromptMemoryEntry).filter_by(conversation_id="123").first()
        assert updated_entry.prompt_metadata == {"updated": "updated"}


def test_refresh_token_if_needed_raises_when_expiry_none():
    obj = AzureSQLMemory.__new__(AzureSQLMemory)
    obj._auth_token_expiry = None
    with pytest.raises(RuntimeError, match="Auth token expiry not initialized"):
        obj._refresh_token_if_needed()


def test_provide_token_raises_when_auth_token_none():
    obj = AzureSQLMemory.__new__(AzureSQLMemory)
    obj._auth_token = None
    obj._auth_token_expiry = 9999999999.0
    obj.engine = MagicMock()

    captured_fn = None

    def fake_listens_for(*args, **kwargs):
        def decorator(fn):
            nonlocal captured_fn
            captured_fn = fn
            return fn

        return decorator

    with patch("pyrit.memory.azure_sql_memory.event.listens_for", side_effect=fake_listens_for):
        obj._enable_azure_authorization()

    assert captured_fn is not None
    with pytest.raises(RuntimeError, match="Azure auth token is not initialized"):
        captured_fn(None, None, ["some_connection_string"], {})


def test_reset_database_raises_when_engine_none():
    obj = AzureSQLMemory.__new__(AzureSQLMemory)
    obj.engine = None
    with pytest.raises(RuntimeError, match="Engine is not initialized"):
        obj.reset_database()
