# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for is_general_technique property and AttackTechniqueSeedGroup class."""

import pytest

from pyrit.models.seeds import (
    AttackTechniqueSeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)

# =============================================================================
# is_general_technique on Seed / SeedPrompt
# =============================================================================


class TestIsGeneralTechnique:
    """Tests for the is_general_technique property across seed types."""

    def test_seed_prompt_defaults_to_false(self):
        """Test that SeedPrompt.is_general_technique defaults to False."""
        prompt = SeedPrompt(value="Test prompt", data_type="text")
        assert prompt.is_general_technique is False

    def test_seed_prompt_can_be_set_true(self):
        """Test that SeedPrompt.is_general_technique can be set to True."""
        prompt = SeedPrompt(value="Test prompt", data_type="text", is_general_technique=True)
        assert prompt.is_general_technique is True

    def test_seed_objective_defaults_to_false(self):
        """Test that SeedObjective.is_general_technique defaults to False."""
        objective = SeedObjective(value="Test objective")
        assert objective.is_general_technique is False

    def test_seed_objective_raises_if_set_true(self):
        """Test that SeedObjective raises ValueError if is_general_technique is True."""
        with pytest.raises(ValueError, match="SeedObjective cannot be a general technique"):
            SeedObjective(value="Test objective", is_general_technique=True)

    def test_seed_simulated_conversation_defaults_to_true(self, tmp_path):
        """Test that SeedSimulatedConversation.is_general_technique defaults to True."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        sim = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=2,
        )
        assert sim.is_general_technique is True

    def test_seed_simulated_conversation_can_be_set_false(self, tmp_path):
        """Test that SeedSimulatedConversation.is_general_technique can be overridden to False."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        sim = SeedSimulatedConversation(
            adversarial_chat_system_prompt_path=adv_path,
            num_turns=2,
            is_general_technique=False,
        )
        assert sim.is_general_technique is False


# =============================================================================
# AttackTechniqueSeedGroup Tests
# =============================================================================


class TestAttackTechniqueSeedGroupInit:
    """Tests for AttackTechniqueSeedGroup initialization."""

    def test_init_with_general_technique_prompts(self):
        """Test initialization with all general technique seeds."""
        prompts = [
            SeedPrompt(value="Technique 1", data_type="text", is_general_technique=True),
            SeedPrompt(value="Technique 2", data_type="text", is_general_technique=True),
        ]
        group = AttackTechniqueSeedGroup(seeds=prompts)

        assert len(group.seeds) == 2

    def test_init_raises_if_non_general_technique_prompt(self):
        """Test that initialization fails if any seed is not a general technique."""
        with pytest.raises(ValueError, match="must have is_general_technique=True"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedPrompt(value="Technique", data_type="text", is_general_technique=True),
                    SeedPrompt(value="Not a technique", data_type="text", is_general_technique=False),
                ]
            )

    def test_init_raises_if_all_non_general_technique(self):
        """Test that initialization fails if all seeds are not general techniques."""
        with pytest.raises(ValueError, match="must have is_general_technique=True"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedPrompt(value="Not a technique", data_type="text"),
                ]
            )

    def test_init_raises_with_objective(self):
        """Test that initialization fails with a SeedObjective (never general technique)."""
        with pytest.raises(ValueError, match="must have is_general_technique=True"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedObjective(value="Objective"),
                    SeedPrompt(value="Technique", data_type="text", is_general_technique=True),
                ]
            )

    def test_init_with_simulated_conversation(self, tmp_path):
        """Test initialization with SeedSimulatedConversation (defaults to general technique)."""
        adv_path = tmp_path / "adversarial.yaml"
        adv_path.write_text("value: Adversarial\ndata_type: text")

        group = AttackTechniqueSeedGroup(
            seeds=[
                SeedSimulatedConversation(
                    num_turns=3,
                    adversarial_chat_system_prompt_path=adv_path,
                ),
                SeedPrompt(
                    value="Technique prompt", data_type="text", sequence=10, role="user", is_general_technique=True
                ),
            ]
        )

        assert group.has_simulated_conversation
        assert len(group.prompts) == 1

    def test_init_empty_raises_error(self):
        """Test that empty seeds raises ValueError."""
        with pytest.raises(ValueError, match="SeedGroup cannot be empty"):
            AttackTechniqueSeedGroup(seeds=[])


class TestAttackTechniqueSeedGroupValidation:
    """Tests for AttackTechniqueSeedGroup validation."""

    def test_validate_all_general_technique_passes(self):
        """Test validate passes when all seeds are general techniques."""
        group = AttackTechniqueSeedGroup(
            seeds=[
                SeedPrompt(value="Technique 1", data_type="text", is_general_technique=True),
            ]
        )
        # Should not raise
        group._check_invariants()

    def test_error_message_includes_non_general_types(self):
        """Test that error message lists the types of non-general seeds."""
        with pytest.raises(ValueError, match="SeedPrompt"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedPrompt(value="Non-technique", data_type="text", is_general_technique=False),
                ]
            )

    def test_mixed_general_and_non_general_raises(self):
        """Test that mix of general and non-general seeds raises error."""
        with pytest.raises(ValueError, match="must have is_general_technique=True"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedPrompt(value="General", data_type="text", is_general_technique=True),
                    SeedPrompt(value="Not general", data_type="text", is_general_technique=False),
                ]
            )


class TestAttackTechniqueSeedGroupNoObjectives:
    """Tests for _enforce_no_objectives validation."""

    def test_rejects_seed_objective(self):
        """Test that _enforce_no_objectives rejects SeedObjective seeds."""
        group = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="ok", data_type="text", is_general_technique=True)],
        )
        # Inject a SeedObjective after construction to bypass the general-technique check.
        group.seeds.append(SeedObjective(value="sneaky objective"))

        with pytest.raises(ValueError, match="must not contain objectives"):
            group._enforce_no_objectives()

    def test_init_rejects_objective_via_general_technique_check(self):
        """Test that constructing with a SeedObjective fails (caught by general-technique check)."""
        with pytest.raises(ValueError, match="is_general_technique"):
            AttackTechniqueSeedGroup(
                seeds=[
                    SeedObjective(value="objective"),
                    SeedPrompt(value="ok", data_type="text", is_general_technique=True),
                ]
            )


class TestAttackTechniqueSeedGroupInsertionIndex:
    """Tests for insertion_index parameter."""

    def test_default_insertion_index_is_none(self):
        """Test that insertion_index defaults to None."""
        group = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="s", data_type="text", is_general_technique=True)],
        )
        assert group.insertion_index is None

    def test_insertion_index_set_to_int(self):
        """Test that insertion_index can be set to an integer."""
        group = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="s", data_type="text", is_general_technique=True)],
            insertion_index=2,
        )
        assert group.insertion_index == 2

    def test_insertion_index_zero(self):
        """Test that insertion_index can be zero (insert at beginning)."""
        group = AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value="s", data_type="text", is_general_technique=True)],
            insertion_index=0,
        )
        assert group.insertion_index == 0


class TestAttackTechniqueSeedGroupRepr:
    """Tests for AttackTechniqueSeedGroup.__repr__ method."""

    def test_repr_basic(self):
        """Test basic __repr__ output."""
        group = AttackTechniqueSeedGroup(
            seeds=[
                SeedPrompt(value="Technique", data_type="text", is_general_technique=True),
            ]
        )

        repr_str = repr(group)
        assert "SeedGroup" in repr_str
        assert "seeds=" in repr_str
