# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for CentralMemory and SQLiteMemory used by azure-ai-evaluation.

The azure-ai-evaluation RedTeam class initializes PyRIT memory during __init__:
    CentralMemory.set_memory_instance(SQLiteMemory())

Multiple modules also access memory via CentralMemory.get_memory_instance().
These tests validate the memory lifecycle contract.
"""

from pyrit.memory import CentralMemory, SQLiteMemory


class TestMemoryContract:
    """Validate CentralMemory/SQLiteMemory interface stability."""

    def test_sqlite_memory_default_constructor(self):
        """RedTeam.__init__ calls SQLiteMemory() with no args."""
        memory = SQLiteMemory()
        assert memory is not None
        memory.dispose_engine()

    def test_sqlite_memory_in_memory_constructor(self):
        """Partner tests use SQLiteMemory(db_path=':memory:')."""
        memory = SQLiteMemory(db_path=":memory:")
        assert memory is not None
        memory.dispose_engine()

    def test_central_memory_set_and_get_instance(self):
        """RedTeam.__init__ sets memory; formatting_utils.py and _rai_scorer.py retrieve it."""
        memory = SQLiteMemory(db_path=":memory:")
        CentralMemory.set_memory_instance(memory)
        retrieved = CentralMemory.get_memory_instance()
        assert retrieved is memory
        memory.dispose_engine()

    def test_sqlite_memory_has_disable_embedding(self):
        """Test fixtures call disable_embedding() on SQLiteMemory."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "disable_embedding")
        assert callable(memory.disable_embedding)
        memory.disable_embedding()
        memory.dispose_engine()

    def test_sqlite_memory_has_reset_database(self):
        """Test fixtures call reset_database() on SQLiteMemory."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "reset_database")
        assert callable(memory.reset_database)
        memory.dispose_engine()

    def test_sqlite_memory_has_dispose_engine(self):
        """Cleanup requires dispose_engine()."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "dispose_engine")
        assert callable(memory.dispose_engine)
        memory.dispose_engine()
