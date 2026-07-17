# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import EmbeddingDataEntry, PromptMemoryEntry
from pyrit.models import MessagePiece


def test_memory(sqlite_instance: MemoryInterface):
    assert sqlite_instance


def test_print_schema_raises_when_engine_none():
    # Test the MemoryInterface.print_schema guard; use AzureSQLMemory which inherits it without override
    from pyrit.memory import AzureSQLMemory

    obj = AzureSQLMemory.__new__(AzureSQLMemory)
    obj.engine = None
    with pytest.raises(RuntimeError, match="Engine is not initialized"):
        obj.print_schema()


def test_get_all_embeddings_delegates_to_query(sqlite_instance: MemoryInterface):
    embedding = MagicMock(spec=EmbeddingDataEntry)

    with patch.object(sqlite_instance, "_query_entries", return_value=[embedding]) as query_entries:
        result = sqlite_instance.get_all_embeddings()

    assert result == [embedding]
    query_entries.assert_called_once_with(EmbeddingDataEntry)


def test_add_embeddings_delegates_to_insert(sqlite_instance: MemoryInterface):
    embeddings = [MagicMock(spec=EmbeddingDataEntry)]

    with patch.object(sqlite_instance, "_insert_entries") as insert_entries:
        sqlite_instance._add_embeddings_to_memory(embedding_data=embeddings)

    insert_entries.assert_called_once_with(entries=embeddings)


def test_query_entries_applies_limit(sqlite_instance: MemoryInterface):
    assert sqlite_instance._query_entries(PromptMemoryEntry, limit=1) == []


def test_query_entries_rolls_back_on_error(sqlite_instance: MemoryInterface):
    session = MagicMock()
    session.query.side_effect = SQLAlchemyError("query failed")

    with patch.object(sqlite_instance, "get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="query failed"):
            sqlite_instance._query_entries(PromptMemoryEntry)


def test_insert_entry_rolls_back_on_error(sqlite_instance: MemoryInterface):
    session = MagicMock()
    session.commit.side_effect = SQLAlchemyError("insert failed")

    with patch.object(sqlite_instance, "get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="insert failed"):
            sqlite_instance._insert_entry(MagicMock())

    session.rollback.assert_called_once()


def test_insert_entries_rolls_back_on_error(sqlite_instance: MemoryInterface):
    session = MagicMock()
    session.commit.side_effect = SQLAlchemyError("bulk insert failed")

    with patch.object(sqlite_instance, "get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="bulk insert failed"):
            sqlite_instance._insert_entries(entries=[MagicMock()])

    session.rollback.assert_called_once()


def test_update_entries_merges_missing_entry(sqlite_instance: MemoryInterface):
    entry = PromptMemoryEntry(entry=MessagePiece(conversation_id="conversation", role="user", original_value="before"))
    session = MagicMock()
    session.get.return_value = None
    session.merge.return_value = entry

    with patch.object(sqlite_instance, "get_session", return_value=session):
        result = sqlite_instance._update_entries(entries=[entry], update_fields={"original_value": "after"})

    assert result is True
    assert entry.original_value == "after"
    session.merge.assert_called_once_with(entry)


def test_update_entries_rolls_back_on_error(sqlite_instance: MemoryInterface):
    entry = PromptMemoryEntry(entry=MessagePiece(conversation_id="conversation", role="user", original_value="before"))
    session = MagicMock()
    session.get.side_effect = SQLAlchemyError("update failed")

    with patch.object(sqlite_instance, "get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="update failed"):
            sqlite_instance._update_entries(entries=[entry], update_fields={"original_value": "after"})

    session.rollback.assert_called_once()
