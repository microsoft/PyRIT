# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the SeedGroup and SeedAttackGroup classes."""

import uuid

import pytest

from pyrit.models.seeds import (
    SeedAttackGroup,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)

# =============================================================================
# SeedGroup Tests
# =============================================================================


class TestSeedGroupInit:
    """Tests for SeedGroup initialization."""

    def test_init_with_single_prompt(self):
        """Test initialization with a single SeedPrompt."""
        prompt = SeedPrompt(value="Test prompt", data_type="text")
        group = SeedGroup(seeds=[prompt])

        assert len(group.seeds) == 1
        assert group.prompts[0].value == "Test prompt"

    def test_init_with_multiple_prompts(self):
        """Test initialization with multiple SeedPrompts."""
        prompts = [
            SeedPrompt(value="Prompt 1", data_type="text", sequence=0, role="user"),
            SeedPrompt(value="Prompt 2", data_type="text", sequence=1, role="assistant"),
        ]
        group = SeedGroup(seeds=prompts)

        assert len(group.seeds) == 2
        assert len(group.prompts) == 2

    def test_init_with_objective_and_prompts(self):
        """Test initialization with objective and prompts."""
        objective = SeedObjective(value="Test objective")
        prompt = SeedPrompt(value="Test prompt", data_type="text")
        group = SeedGroup(seeds=[objective, prompt])

        assert len(group.seeds) == 2
        # Objective should be first
        assert isinstance(group.seeds[0], SeedObjective)

    def test_init_with_dict_seeds(self):
        """Test initialization with dictionary seeds."""
        group = SeedGroup(
            seeds=[
                {"value": "Test objective", "seed_type": "objective"},
                {"value": "Test prompt", "data_type": "text"},
            ]
        )

        assert len(group.seeds) == 2
        assert isinstance(group.seeds[0], SeedObjective)
        assert isinstance(group.seeds[1], SeedPrompt)

    def test_init_empty_raises_error(self):
        """Test that empty seeds raises ValueError."""
        with pytest.raises(ValueError, match="SeedGroup cannot be empty"):
            SeedGroup(seeds=[])

    def test_init_multiple_objectives_raises_error(self):
        """Test that multiple objectives raises ValueError."""
        with pytest.raises(ValueError, match="SeedGroup can only have one objective"):
            SeedGroup(
                seeds=[
                    SeedObjective(value="Objective 1"),
                    SeedObjective(value="Objective 2"),
                ]
            )

    def test_init_assigns_consistent_group_id(self):
        """Test that all seeds get the same prompt_group_id."""
        prompts = [
            SeedPrompt(value="Prompt 1", data_type="text"),
            SeedPrompt(value="Prompt 2", data_type="text"),
        ]
        group = SeedGroup(seeds=prompts)

        group_ids = {seed.prompt_group_id for seed in group.seeds}
        assert len(group_ids) == 1
        assert None not in group_ids

    def test_init_preserves_existing_group_id(self):
        """Test that existing group_id is preserved."""
        existing_id = uuid.uuid4()
        prompts = [
            SeedPrompt(value="Prompt 1", data_type="text", prompt_group_id=existing_id),
            SeedPrompt(value="Prompt 2", data_type="text"),
        ]
        group = SeedGroup(seeds=prompts)

        for seed in group.seeds:
            assert seed.prompt_group_id == existing_id

    def test_init_sorts_prompts_by_sequence(self):
        """Test that prompts are sorted by sequence."""
        prompts = [
            SeedPrompt(value="Prompt 2", data_type="text", sequence=2, role="user"),
            SeedPrompt(value="Prompt 0", data_type="text", sequence=0, role="user"),
            SeedPrompt(value="Prompt 1", data_type="text", sequence=1, role="assistant"),
        ]
        group = SeedGroup(seeds=prompts)

        # Check prompts are in order
        assert group.prompts[0].value == "Prompt 0"
        assert group.prompts[1].value == "Prompt 1"
        assert group.prompts[2].value == "Prompt 2"

    def test_init_objective_first_then_sorted_prompts(self):
        """Test that objective comes first, then sorted prompts."""
        seeds = [
            SeedPrompt(value="Prompt 2", data_type="text", sequence=2, role="user"),
            SeedObjective(value="Objective"),
            SeedPrompt(value="Prompt 0", data_type="text", sequence=0, role="assistant"),
        ]
        group = SeedGroup(seeds=seeds)

        assert isinstance(group.seeds[0], SeedObjective)
        assert group.seeds[1].value == "Prompt 0"
        assert group.seeds[2].value == "Prompt 2"


class TestSeedGroupHarmCategories:
    """Tests for SeedGroup.harm_categories property."""

    def test_harm_categories_empty(self):
        """Test harm_categories with no categories."""
        prompt = SeedPrompt(value="Test", data_type="text")
        group = SeedGroup(seeds=[prompt])

        assert group.harm_categories == []

    def test_harm_categories_from_single_seed(self):
        """Test harm_categories from a single seed."""
        prompt = SeedPrompt(
            value="Test",
            data_type="text",
            harm_categories=["violence", "hate"],
        )
        group = SeedGroup(seeds=[prompt])

        assert set(group.harm_categories) == {"violence", "hate"}

    def test_harm_categories_deduplicated(self):
        """Test that harm_categories are deduplicated."""
        prompts = [
            SeedPrompt(value="Test 1", data_type="text", harm_categories=["violence"]),
            SeedPrompt(value="Test 2", data_type="text", harm_categories=["violence", "hate"]),
        ]
        group = SeedGroup(seeds=prompts)

        assert set(group.harm_categories) == {"violence", "hate"}


# =============================================================================
# SeedAttackGroup Tests
# =============================================================================


class TestSeedAttackGroupInit:
    """Tests for SeedAttackGroup initialization."""

    def test_init_with_objective_and_prompt(self):
        """Test basic initialization with objective and prompt."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Test objective"),
                SeedPrompt(value="Test prompt", data_type="text"),
            ]
        )

        assert group.objective is not None
        assert group.objective.value == "Test objective"
        assert len(group.prompts) == 1

    def test_init_with_simulated_conversation(self, tmp_path):
        """Test initialization with simulated conversation config."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Test objective"),
                SeedSimulatedConversation(
                    num_turns=3,
                    adversarial_chat_system_prompt_path=adv_path,
                ),
            ]
        )

        assert group.has_simulated_conversation
        assert group.simulated_conversation_config is not None
        assert group.simulated_conversation_config.num_turns == 3

    def test_init_with_dict_simulated_conversation(self, tmp_path):
        """Test initialization with dict-based simulated conversation."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        group = SeedAttackGroup(
            seeds=[
                {"value": "Test objective", "seed_type": "objective"},
                {
                    "seed_type": "simulated_conversation",
                    "num_turns": 5,
                    "adversarial_chat_system_prompt_path": str(adv_path),
                },
            ]
        )

        assert group.has_simulated_conversation
        assert group.simulated_conversation_config.num_turns == 5

    def test_init_simulated_conversation_with_overlapping_prompts_raises_error(self, tmp_path):
        """Test that simulated_conversation with overlapping prompt sequences raises error."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        # SeedSimulatedConversation with sequence=0 and num_turns=3 occupies sequences 0-5
        # SeedPrompt with sequence=2 overlaps with that range
        with pytest.raises(ValueError, match="overlaps with SeedSimulatedConversation"):
            SeedAttackGroup(
                seeds=[
                    SeedObjective(value="Objective"),
                    SeedSimulatedConversation(
                        num_turns=3,
                        adversarial_chat_system_prompt_path=adv_path,
                        sequence=0,
                    ),
                    SeedPrompt(value="Prompt 1", data_type="text", sequence=2, role="user"),
                ]
            )

    def test_init_ordering_objective_simulated(self, tmp_path):
        """Test that seeds are ordered: objective, simulated_conversation."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: adv\ndata_type: text")

        group = SeedAttackGroup(
            seeds=[
                {
                    "seed_type": "simulated_conversation",
                    "num_turns": 2,
                    "adversarial_chat_system_prompt_path": str(adv_path),
                },
                {"value": "Objective", "seed_type": "objective"},
            ]
        )

        assert isinstance(group.seeds[0], SeedObjective)
        assert isinstance(group.seeds[1], SeedSimulatedConversation)


class TestSeedAttackGroupObjective:
    """Tests for SeedAttackGroup objective handling."""

    def test_objective_property_returns_objective(self):
        """Test that objective property returns the SeedObjective."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="My objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        assert group.objective.value == "My objective"

    def test_no_objective_raises_error(self):
        """Test that SeedAttackGroup without objective raises error."""
        with pytest.raises(ValueError, match="must have exactly one objective"):
            SeedAttackGroup(seeds=[SeedPrompt(value="Test", data_type="text")])

    def test_objective_value_can_be_updated(self):
        """Test that objective value can be updated directly."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Old objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        group.objective.value = "New objective"

        assert group.objective.value == "New objective"


class TestSeedAttackGroupSimulatedConversation:
    """Tests for SeedAttackGroup simulated conversation handling."""

    def test_has_simulated_conversation_false_when_none(self):
        """Test has_simulated_conversation is False when no config."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        assert not group.has_simulated_conversation

    def test_has_simulated_conversation_true_when_present(self, tmp_path):
        """Test has_simulated_conversation is True when config present."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedSimulatedConversation(
                    num_turns=3,
                    adversarial_chat_system_prompt_path=adv_path,
                ),
            ]
        )

        assert group.has_simulated_conversation

    def test_simulated_conversation_allows_non_overlapping_prompts(self, tmp_path):
        """Test that prompts can coexist with simulated conversation if sequences don't overlap."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        # SeedSimulatedConversation with sequence=0 and num_turns=2 occupies sequences 0-3 (2*2=4)
        # A prompt with sequence=10 does NOT overlap
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedSimulatedConversation(
                    num_turns=2,
                    adversarial_chat_system_prompt_path=adv_path,
                    sequence=0,
                ),
                SeedPrompt(value="Static follow-up", data_type="text", sequence=10, role="user"),
            ]
        )

        assert group.has_simulated_conversation
        assert len(group.prompts) == 1
        assert group.prompts[0].value == "Static follow-up"

    def test_simulated_conversation_with_custom_sequence(self, tmp_path):
        """Test simulated conversation with non-zero sequence allows prompts before it."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        # SeedSimulatedConversation with sequence=5 and num_turns=2 occupies sequences 5-8
        # A prompt with sequence=0 does NOT overlap (it's before the simulated range)
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Static intro", data_type="text", sequence=0, role="user"),
                SeedSimulatedConversation(
                    num_turns=2,
                    adversarial_chat_system_prompt_path=adv_path,
                    sequence=5,
                ),
            ]
        )

        assert group.has_simulated_conversation
        assert len(group.prompts) == 1
        assert group.prompts[0].value == "Static intro"


class TestSeedAttackGroupMessageExtraction:
    """Tests for SeedAttackGroup message extraction methods."""

    def test_is_single_turn_false_for_attack_group_with_objective(self):
        """Test is_single_turn is False for SeedAttackGroup (always has objective)."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        # SeedAttackGroup always has objective, so is_single_turn is always False
        assert not group.is_single_turn()

    def test_is_single_turn_false_with_objective(self):
        """Test is_single_turn is False when objective present."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        assert not group.is_single_turn()

    def test_is_single_request_true_for_single_sequence(self):
        """Test is_single_request is True for single sequence."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test 1", data_type="text", sequence=0, role="user"),
                SeedPrompt(value="Test 2", data_type="text", sequence=0, role="user"),
            ]
        )

        assert group.is_single_request()

    def test_is_single_request_false_for_multi_sequence(self):
        """Test is_single_request is False for multi-sequence."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test 1", data_type="text", sequence=0, role="user"),
                SeedPrompt(value="Test 2", data_type="text", sequence=1, role="assistant"),
            ]
        )

        assert not group.is_single_request()

    def test_next_message_returns_last_user_message(self):
        """Test next_message returns the last user message."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test prompt", data_type="text", role="user"),
            ]
        )

        next_msg = group.next_message
        assert next_msg is not None
        assert next_msg.get_value() == "Test prompt"

    def test_next_message_none_for_assistant_last(self):
        """Test next_message is None when last message is assistant."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="User msg", data_type="text", sequence=0, role="user"),
                SeedPrompt(value="Assistant msg", data_type="text", sequence=1, role="assistant"),
            ]
        )

        assert group.next_message is None

    def test_prepended_conversation_returns_all_except_last_user(self):
        """Test prepended_conversation returns all except last user message."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="User 1", data_type="text", sequence=0, role="user"),
                SeedPrompt(value="Assistant 1", data_type="text", sequence=1, role="assistant"),
                SeedPrompt(value="User 2", data_type="text", sequence=2, role="user"),
            ]
        )

        prepended = group.prepended_conversation
        assert prepended is not None
        assert len(prepended) == 2

    def test_user_messages_returns_all_prompts_as_messages(self):
        """Test user_messages returns all prompts as Messages."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Prompt 1", data_type="text", sequence=0, role="user"),
                SeedPrompt(value="Prompt 2", data_type="text", sequence=1, role="assistant"),
            ]
        )

        messages = group.user_messages
        assert len(messages) == 2


class TestSeedAttackGroupRepr:
    """Tests for SeedAttackGroup.__repr__ method."""

    def test_repr_basic(self):
        """Test basic __repr__ output."""
        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedPrompt(value="Test", data_type="text"),
            ]
        )

        repr_str = repr(group)
        assert "SeedGroup" in repr_str
        assert "seeds=" in repr_str

    def test_repr_with_simulated_conversation(self, tmp_path):
        """Test __repr__ includes simulated indicator."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        group = SeedAttackGroup(
            seeds=[
                SeedObjective(value="Objective"),
                SeedSimulatedConversation(
                    num_turns=3,
                    adversarial_chat_system_prompt_path=adv_path,
                ),
            ]
        )

        repr_str = repr(group)
        assert "simulated" in repr_str
