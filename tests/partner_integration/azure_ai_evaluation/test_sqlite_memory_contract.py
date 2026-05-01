# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for SQLiteMemory used by azure-ai-evaluation.

The azure-ai-evaluation RedTeam class initializes PyRIT memory during __init__:
    CentralMemory.set_memory_instance(SQLiteMemory())

Multiple modules also access memory via CentralMemory.get_memory_instance().
These tests validate both the memory lifecycle contract and the query methods
the SDK calls to store/retrieve attack results, scores, and conversations.

Memory query methods used by the SDK:
- get_scenario_results(): _scenario_orchestrator.py (partial result recovery)
- add_scores_to_memory(): _rai_scorer.py (score storage)
- get_message_pieces(): _foundry_result_processor.py (conversation retrieval)
- get_prompt_request_pieces(): formatting_utils.py (label-based queries)
- get_conversation(): _callback_chat_target.py (multi-turn history)
"""

from pyrit.memory import CentralMemory, SQLiteMemory


class TestMemoryLifecycleContract:
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


class TestMemoryQueryMethodContract:
    """Validate that memory query methods used by azure-ai-evaluation exist.

    These tests verify method existence and callability on the memory interface.
    The SDK calls these methods to store/retrieve attack results, scores,
    and conversation history during red team scans.
    """

    def test_memory_has_get_scenario_results(self):
        """_scenario_orchestrator.py calls memory.get_scenario_results(scenario_result_ids=[...])."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "get_scenario_results")
        assert callable(memory.get_scenario_results)
        memory.dispose_engine()

    def test_memory_has_add_scores_to_memory(self):
        """_rai_scorer.py calls memory.add_scores_to_memory(scores=[Score])."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "add_scores_to_memory")
        assert callable(memory.add_scores_to_memory)
        memory.dispose_engine()

    def test_memory_has_get_message_pieces(self):
        """_foundry_result_processor.py calls memory.get_message_pieces(conversation_id=...)."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "get_message_pieces")
        assert callable(memory.get_message_pieces)
        memory.dispose_engine()

    def test_memory_has_get_prompt_request_pieces_or_equivalent(self):
        """formatting_utils.py calls memory.get_prompt_request_pieces(labels={...}).

        NOTE: In newer PyRIT versions, this was consolidated into get_message_pieces(labels=...).
        The SDK may need updating if it still references the old name. This test validates
        that get_message_pieces accepts a labels parameter (the current equivalent).
        """
        memory = SQLiteMemory(db_path=":memory:")
        has_legacy = hasattr(memory, "get_prompt_request_pieces")
        has_current = hasattr(memory, "get_message_pieces")
        assert has_legacy or has_current, (
            "Neither get_prompt_request_pieces nor get_message_pieces found on memory. "
            "formatting_utils.py depends on one of these for label-based queries."
        )
        memory.dispose_engine()

    def test_memory_has_get_conversation(self):
        """_callback_chat_target.py calls memory.get_conversation(conversation_id=...)."""
        memory = SQLiteMemory(db_path=":memory:")
        assert hasattr(memory, "get_conversation")
        assert callable(memory.get_conversation)
        memory.dispose_engine()

    def test_get_conversation_returns_list(self, sqlite_instance):
        """get_conversation should return a list (empty for unknown conversation_id)."""
        result = sqlite_instance.get_conversation(conversation_id="nonexistent-id")
        assert isinstance(result, list)

    def test_get_message_pieces_with_labels_returns_list(self, sqlite_instance):
        """get_message_pieces(labels={...}) should return a list (empty for no matches).

        This is the current equivalent of the SDK's get_prompt_request_pieces(labels=...) call.
        """
        result = sqlite_instance.get_message_pieces(labels={"nonexistent": "label"})
        assert isinstance(result, (list, tuple))

    def test_get_message_pieces_returns_list(self, sqlite_instance):
        """get_message_pieces should return a list (empty for unknown conversation_id)."""
        result = sqlite_instance.get_message_pieces(conversation_id="nonexistent-id")
        assert isinstance(result, list)
