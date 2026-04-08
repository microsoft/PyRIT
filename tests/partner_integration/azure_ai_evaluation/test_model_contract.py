# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for PyRIT data models used by azure-ai-evaluation.

The red team module uses these models extensively:
- Message / MessagePiece: Every request/response path
- Score / UnvalidatedScore: Scoring pipeline
- SeedPrompt / SeedObjective / SeedGroup: DatasetConfigurationBuilder
- AttackResult / AttackOutcome: FoundryResultProcessor
- ChatMessage: formatting_utils.py
- PromptDataType: Type enum used across converters and models
- construct_response_from_request: Response construction
"""

import uuid

from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    construct_response_from_request,
)


class TestMessageContract:
    """Validate Message and MessagePiece interfaces."""

    def test_message_piece_minimal_constructor(self):
        """_CallbackChatTarget creates MessagePiece with role, original_value, conversation_id."""
        piece = MessagePiece(
            role="user",
            original_value="test prompt",
            conversation_id=str(uuid.uuid4()),
        )
        assert piece.api_role == "user"
        assert piece.original_value == "test prompt"

    def test_message_piece_to_message(self):
        """_CallbackChatTarget calls piece.to_message() to convert to Message."""
        piece = MessagePiece(
            role="user",
            original_value="test",
            conversation_id=str(uuid.uuid4()),
        )
        msg = piece.to_message()
        assert isinstance(msg, Message)
        assert len(msg.message_pieces) == 1

    def test_message_get_value(self):
        """_CallbackChatTarget accesses message.get_value() for the response text."""
        piece = MessagePiece(
            role="assistant",
            original_value="response text",
            conversation_id=str(uuid.uuid4()),
        )
        msg = piece.to_message()
        assert msg.get_value() == "response text"

    def test_message_pieces_attribute(self):
        """azure-ai-evaluation accesses message.message_pieces list."""
        piece = MessagePiece(
            role="user",
            original_value="test",
            conversation_id=str(uuid.uuid4()),
        )
        msg = piece.to_message()
        assert hasattr(msg, "message_pieces")
        assert isinstance(msg.message_pieces, (list, tuple))

    def test_message_piece_has_converted_value(self):
        """azure-ai-evaluation reads message_piece.converted_value for responses."""
        piece = MessagePiece(
            role="assistant",
            original_value="original",
            converted_value="converted",
            conversation_id=str(uuid.uuid4()),
        )
        assert piece.converted_value == "converted"

    def test_message_piece_has_conversation_id(self):
        """Conversation tracking relies on conversation_id field."""
        conv_id = str(uuid.uuid4())
        piece = MessagePiece(
            role="user",
            original_value="test",
            conversation_id=conv_id,
        )
        assert piece.conversation_id == conv_id

    def test_message_piece_has_prompt_metadata(self):
        """_CallbackChatTarget reads piece.prompt_metadata for context extraction.

        In the Foundry path, context SeedPrompts are stored as prepended_conversation
        in memory. _CallbackChatTarget reads prompt_metadata (is_context, tool_name,
        context_type) via getattr(piece, 'prompt_metadata', None) to reconstruct
        the context dict for agent callbacks.
        """
        piece = MessagePiece(
            role="user",
            original_value="context content",
            conversation_id=str(uuid.uuid4()),
            prompt_metadata={"is_context": True, "tool_name": "doc_reader"},
        )
        assert hasattr(piece, "prompt_metadata")
        metadata = getattr(piece, "prompt_metadata", None) or {}
        assert metadata.get("is_context") is True
        assert metadata.get("tool_name") == "doc_reader"

    def test_message_piece_prompt_metadata_defaults_empty(self):
        """prompt_metadata should default to empty/None when not provided."""
        piece = MessagePiece(
            role="user",
            original_value="test",
            conversation_id=str(uuid.uuid4()),
        )
        metadata = getattr(piece, "prompt_metadata", None) or {}
        assert not metadata.get("is_context")


class TestScoreModels:
    """Validate Score and UnvalidatedScore interfaces."""

    def test_score_importable(self):
        """RAIServiceScorer and AzureRAIServiceTrueFalseScorer return Score objects."""
        from pyrit.models import Score

        assert Score is not None

    def test_unvalidated_score_importable(self):
        """Scorers create UnvalidatedScore before validation."""
        from pyrit.models import UnvalidatedScore

        assert UnvalidatedScore is not None


class TestSeedModels:
    """Validate seed data models used by DatasetConfigurationBuilder.

    These tests cover the full contract including context propagation patterns
    from PR #46151 (sensitive_data_leakage tool context flow).
    """

    def test_seed_prompt_accepts_value(self):
        """SeedPrompt requires a value field (the actual prompt text)."""
        prompt = SeedPrompt(value="test prompt")
        assert prompt.value == "test prompt"

    def test_seed_prompt_has_data_type(self):
        """SeedPrompt.data_type defaults to 'text' for string values."""
        prompt = SeedPrompt(value="test")
        assert prompt.data_type == "text"

    def test_seed_prompt_explicit_text_data_type(self):
        """DatasetConfigurationBuilder passes data_type='text' explicitly for context SeedPrompts."""
        prompt = SeedPrompt(value="context content", data_type="text")
        assert prompt.data_type == "text"

    def test_seed_prompt_has_harm_categories(self):
        """DatasetConfigurationBuilder sets harm_categories on SeedPrompt."""
        prompt = SeedPrompt(value="test", harm_categories=["violence"])
        assert "violence" in prompt.harm_categories

    def test_seed_prompt_has_role(self):
        """SeedPrompt supports role field for conversation context."""
        prompt = SeedPrompt(value="test", role="user")
        assert prompt.role == "user"

    def test_seed_prompt_has_metadata(self):
        """DatasetConfigurationBuilder attaches metadata to SeedPrompt."""
        prompt = SeedPrompt(value="test", metadata={"key": "val"})
        assert prompt.metadata["key"] == "val"

    def test_seed_prompt_has_prompt_group_id(self):
        """DatasetConfigurationBuilder sets prompt_group_id for grouping seeds."""
        group_id = str(uuid.uuid4())
        prompt = SeedPrompt(value="test", prompt_group_id=group_id)
        assert prompt.prompt_group_id == group_id

    def test_seed_prompt_has_sequence(self):
        """DatasetConfigurationBuilder uses sequence for ordering within a group.

        Context SeedPrompts get lower sequence values; the objective SeedPrompt
        gets a higher sequence so PyRIT uses it as next_message.
        """
        prompt = SeedPrompt(value="test", sequence=3)
        assert prompt.sequence == 3

    def test_seed_prompt_context_pattern(self):
        """DatasetConfigurationBuilder creates context SeedPrompts with is_context metadata.

        This pattern is critical for sensitive_data_leakage: context SeedPrompts
        carry tool_name and context_type in metadata so _CallbackChatTarget can
        extract them from conversation history and pass to the agent callback.
        """
        group_id = str(uuid.uuid4())
        ctx_metadata = {
            "is_context": True,
            "context_index": 0,
            "original_content_length": 42,
            "tool_name": "document_client_smode",
            "context_type": "document",
        }
        prompt = SeedPrompt(
            value="SSN: 123-45-6789",
            data_type="text",
            prompt_group_id=group_id,
            metadata=ctx_metadata,
            role="user",
            sequence=1,
        )
        assert prompt.metadata["is_context"] is True
        assert prompt.metadata["tool_name"] == "document_client_smode"
        assert prompt.metadata["context_type"] == "document"
        assert prompt.value == "SSN: 123-45-6789"
        assert prompt.data_type == "text"
        assert prompt.prompt_group_id == group_id
        assert prompt.sequence == 1
        assert prompt.role == "user"

    def test_seed_objective_accepts_value(self):
        """SeedObjective requires a value field (the objective text)."""
        obj = SeedObjective(value="test objective")
        assert obj.value == "test objective"

    def test_seed_objective_has_harm_categories(self):
        """DatasetConfigurationBuilder sets harm_categories on SeedObjective."""
        obj = SeedObjective(value="test", harm_categories=["hate"])
        assert "hate" in obj.harm_categories

    def test_seed_objective_has_metadata(self):
        """DatasetConfigurationBuilder attaches context_items to SeedObjective metadata."""
        context_items = [{"content": "secret data", "tool_name": "doc_reader"}]
        obj = SeedObjective(value="test", metadata={"context_items": context_items})
        assert obj.metadata["context_items"] == context_items

    def test_seed_group_accepts_seeds(self):
        """SeedGroup groups multiple seeds together."""
        prompt = SeedPrompt(value="prompt text", role="user")
        obj = SeedObjective(value="objective text")
        group = SeedGroup(seeds=[prompt, obj])
        assert len(group.seeds) == 2

    def test_seed_group_mixed_context_pattern(self):
        """DatasetConfigurationBuilder creates SeedGroups with objective + context seeds.

        For standard attacks with context (sensitive_data_leakage), a SeedGroup contains:
        1. SeedObjective — the attack objective
        2. Context SeedPrompt(s) — at lower sequence, is_context=True in metadata
        3. Objective SeedPrompt — at highest sequence (the actual prompt sent to the target)
        """
        group_id = str(uuid.uuid4())

        objective = SeedObjective(
            value="Extract PII from the document",
            metadata={"context_items": [{"content": "SSN: 123", "tool_name": "doc_reader"}]},
        )
        context_seed = SeedPrompt(
            value="SSN: 123-45-6789",
            data_type="text",
            prompt_group_id=group_id,
            metadata={"is_context": True, "tool_name": "doc_reader", "context_type": "document"},
            role="user",
            sequence=1,
        )
        objective_prompt = SeedPrompt(
            value="Extract PII from the document",
            data_type="text",
            prompt_group_id=group_id,
            role="user",
            sequence=2,
        )

        group = SeedGroup(seeds=[objective, context_seed, objective_prompt])
        assert len(group.seeds) == 3

        # Verify sequence ordering: context < objective prompt
        seed_prompts = [s for s in group.seeds if isinstance(s, SeedPrompt)]
        context_seeds = [s for s in seed_prompts if s.metadata.get("is_context")]
        non_context_seeds = [s for s in seed_prompts if not s.metadata.get("is_context")]
        assert len(context_seeds) == 1
        assert len(non_context_seeds) == 1
        assert context_seeds[0].sequence < non_context_seeds[0].sequence


class TestMiscModels:
    """Validate miscellaneous models used by azure-ai-evaluation."""

    def test_chat_message_importable(self):
        """formatting_utils.py imports ChatMessage."""
        from pyrit.models import ChatMessage

        assert ChatMessage is not None

    def test_prompt_data_type_has_text(self):
        """_DefaultConverter and _dataset_builder check for 'text' data type."""
        # PromptDataType is a Literal type; verify "text" is a valid value
        from typing import get_args

        valid_types = get_args(PromptDataType)
        assert "text" in valid_types

    def test_scenario_result_importable(self):
        """ScenarioOrchestrator reads ScenarioResult."""
        from pyrit.models import ScenarioResult

        assert ScenarioResult is not None

    def test_construct_response_from_request_signature(self):
        """Verify construct_response_from_request accepts expected parameters."""
        piece = MessagePiece(
            role="user",
            original_value="test",
            conversation_id=str(uuid.uuid4()),
        )
        # Call with positional request + response_text_pieces
        result = construct_response_from_request(
            request=piece,
            response_text_pieces=["response"],
            response_type="text",
        )
        assert isinstance(result, Message)
