# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the SeedSimulatedConversation class."""

import json
import uuid

import pytest

from pyrit.models.seeds import (
    SeedSimulatedConversation,
    SimulatedTargetSystemPromptPaths,
)


class TestSeedSimulatedConversationInit:
    """Tests for SeedSimulatedConversation initialization."""

    def test_init_with_all_parameters(self, tmp_path):
        """Test initialization with all parameters."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
            simulated_target_system_prompt_path=sim_path,
        )

        assert conv.num_turns == 5
        assert conv.adversarial_chat_system_prompt_path == adv_path
        assert conv.simulated_target_system_prompt_path == sim_path
        assert conv.data_type == "text"
        assert isinstance(conv.id, uuid.UUID)

    def test_init_with_minimal_parameters(self, tmp_path):
        """Test initialization with only required parameters."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.num_turns == 3  # default
        assert conv.adversarial_chat_system_prompt_path == adv_path
        # Default simulated_target_system_prompt_path is the compliant prompt
        assert conv.simulated_target_system_prompt_path == SimulatedTargetSystemPromptPaths.COMPLIANT.value

    def test_init_default_num_turns(self, tmp_path):
        """Test that default num_turns is 3."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.num_turns == 3

    def test_init_invalid_num_turns_zero_raises_error(self, tmp_path):
        """Test that num_turns=0 raises ValueError."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path=adv_path,
                num_turns=0,
            )

    def test_init_invalid_num_turns_negative_raises_error(self, tmp_path):
        """Test that negative num_turns raises ValueError."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path=adv_path,
                num_turns=-1,
            )

    def test_init_sets_data_type_to_text(self, tmp_path):
        """Test that data_type is always set to 'text'."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.data_type == "text"

    def test_init_generates_json_value(self, tmp_path):
        """Test that value is set to a JSON serialization of config."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )

        value = json.loads(conv.value)
        assert value["num_turns"] == 5
        assert "adversarial_chat_system_prompt_path" in value
        assert "pyrit_version" in value

    def test_init_value_is_deterministic(self, tmp_path):
        """Test that the same config produces the same value."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )

        assert conv1.value == conv2.value

    def test_init_default_sequence_is_zero(self, tmp_path):
        """Test that default sequence is 0."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.sequence == 0

    def test_init_custom_sequence(self, tmp_path):
        """Test that sequence can be set to a custom value."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            sequence=5,
        )

        assert conv.sequence == 5

    def test_init_default_next_message_system_prompt_path_is_none(self, tmp_path):
        """Test that default next_message_system_prompt_path is None."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )

        assert conv.next_message_system_prompt_path is None

    def test_init_next_message_system_prompt_path_set(self, tmp_path):
        """Test that next_message_system_prompt_path can be set."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")
        next_msg_path = tmp_path / "next_message.yaml"
        next_msg_path.write_text("value: test\ndata_type: text\nparameters:\n  - objective\n  - conversation_context")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            next_message_system_prompt_path=next_msg_path,
        )

        assert conv.next_message_system_prompt_path == next_msg_path


class TestSeedSimulatedConversationFromDict:
    """Tests for SeedSimulatedConversation.from_dict method."""

    def test_from_dict_with_paths(self, tmp_path):
        """Test from_dict with path values."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "num_turns": 5,
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.from_dict(data)

        assert conv.num_turns == 5
        assert conv.adversarial_chat_system_prompt_path == adv_path

    def test_from_dict_without_simulated_target_path(self, tmp_path):
        """Test from_dict without simulated_target_system_prompt_path uses compliant default."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "num_turns": 3,
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.from_dict(data)

        # Default simulated_target_system_prompt_path is the compliant prompt
        assert conv.simulated_target_system_prompt_path == SimulatedTargetSystemPromptPaths.COMPLIANT.value

    def test_from_dict_default_num_turns(self, tmp_path):
        """Test from_dict uses default num_turns when not specified."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        data = {
            "adversarial_chat_system_prompt_path": str(adv_path),
        }
        conv = SeedSimulatedConversation.from_dict(data)

        assert conv.num_turns == 3

    def test_from_dict_missing_adversarial_path_raises_error(self):
        """Test that from_dict raises error when adversarial path is missing."""
        data = {"num_turns": 3}

        with pytest.raises(ValueError, match="adversarial_chat_system_prompt_path is required"):
            SeedSimulatedConversation.from_dict(data)


class TestSeedSimulatedConversationGetIdentifier:
    """Tests for SeedSimulatedConversation.get_identifier method."""

    def test_get_identifier_returns_correct_structure(self, tmp_path):
        """Test that get_identifier returns the expected structure."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        identifier = conv.get_identifier()

        assert identifier["__type__"] == "SeedSimulatedConversation"
        assert identifier["num_turns"] == 3
        assert "adversarial_chat_system_prompt_path" in identifier
        assert "pyrit_version" in identifier


class TestSeedSimulatedConversationComputeHash:
    """Tests for SeedSimulatedConversation.compute_hash method."""

    def test_compute_hash_returns_sha256(self, tmp_path):
        """Test that compute_hash returns a valid SHA256 hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
        )
        hash_value = conv.compute_hash()

        # SHA256 hash is 64 hex characters
        assert len(hash_value) == 64
        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_compute_hash_is_deterministic(self, tmp_path):
        """Test that the same config produces the same hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )

        assert conv1.compute_hash() == conv2.compute_hash()

    def test_compute_hash_differs_for_different_num_turns(self, tmp_path):
        """Test that different num_turns produces different hash."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv1 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=3,
        )
        conv2 = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )

        assert conv1.compute_hash() != conv2.compute_hash()


class TestSeedSimulatedConversationRepr:
    """Tests for SeedSimulatedConversation.__repr__ method."""

    def test_repr_shows_num_turns_and_path(self, tmp_path):
        """Test __repr__ shows key information."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: test\ndata_type: text")

        conv = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=5,
        )
        repr_str = repr(conv)

        assert "SeedSimulatedConversation" in repr_str
        assert "num_turns=5" in repr_str
        assert "adversarial.yaml" in repr_str


class TestSeedSimulatedConversationLoadSimulatedTargetSystemPrompt:
    """Tests for SeedSimulatedConversation.load_simulated_target_system_prompt static method."""

    def test_load_simulated_target_system_prompt_renders_template(self, tmp_path):
        """Test that load_simulated_target_system_prompt renders the template."""
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text(
            "value: 'Objective: {{ objective }} Turns: {{ num_turns }}'\n"
            "data_type: text\n"
            "parameters:\n"
            "  - objective\n"
            "  - num_turns"
        )

        result = SeedSimulatedConversation.load_simulated_target_system_prompt(
            objective="Test objective",
            num_turns=5,
            simulated_target_system_prompt_path=sim_path,
        )

        assert "Test objective" in result
        assert "5" in result

    def test_load_simulated_target_system_prompt_raises_for_missing_params(self, tmp_path):
        """Test that missing template params raise an error."""
        sim_path = tmp_path / "simulated.yaml"
        sim_path.write_text("value: 'No params'\ndata_type: text")

        with pytest.raises(ValueError, match="objective and num_turns"):
            SeedSimulatedConversation.load_simulated_target_system_prompt(
                objective="Test",
                num_turns=3,
                simulated_target_system_prompt_path=sim_path,
            )
