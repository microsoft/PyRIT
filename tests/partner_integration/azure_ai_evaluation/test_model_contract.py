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
    AttackOutcome,
    AttackResult,
    ChatMessage,
    Message,
    MessagePiece,
    PromptDataType,
    ScenarioResult,
    Score,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    UnvalidatedScore,
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


class TestScoreModels:
    """Validate Score and UnvalidatedScore interfaces."""

    def test_score_class_exists(self):
        """RAIServiceScorer and AzureRAIServiceTrueFalseScorer return Score objects."""
        assert Score is not None

    def test_unvalidated_score_class_exists(self):
        """Scorers create UnvalidatedScore before validation."""
        assert UnvalidatedScore is not None


class TestSeedModels:
    """Validate seed data models used by DatasetConfigurationBuilder."""

    def test_seed_prompt_class_exists(self):
        """DatasetConfigurationBuilder creates SeedPrompt instances."""
        assert SeedPrompt is not None

    def test_seed_objective_class_exists(self):
        """DatasetConfigurationBuilder creates SeedObjective instances."""
        assert SeedObjective is not None

    def test_seed_group_class_exists(self):
        """DatasetConfigurationBuilder creates SeedGroup instances."""
        assert SeedGroup is not None


class TestAttackModels:
    """Validate attack result models used by FoundryResultProcessor."""

    def test_attack_result_class_exists(self):
        """ScenarioOrchestrator processes AttackResult from FoundryScenario."""
        assert AttackResult is not None

    def test_attack_outcome_class_exists(self):
        """FoundryResultProcessor checks AttackOutcome values."""
        assert AttackOutcome is not None


class TestMiscModels:
    """Validate miscellaneous models used by azure-ai-evaluation."""

    def test_chat_message_class_exists(self):
        """formatting_utils.py imports ChatMessage."""
        assert ChatMessage is not None

    def test_prompt_data_type_has_text(self):
        """_DefaultConverter and _dataset_builder check for 'text' data type."""
        # PromptDataType is a Literal type; verify "text" is a valid value
        from typing import get_args

        valid_types = get_args(PromptDataType)
        assert "text" in valid_types

    def test_scenario_result_class_exists(self):
        """ScenarioOrchestrator reads ScenarioResult."""
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
