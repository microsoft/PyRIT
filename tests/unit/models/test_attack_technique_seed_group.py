# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for is_general_technique property and AttackTechniqueSeedGroup class."""

from typing import assert_type
from unittest.mock import patch

import pytest

from pyrit.models import Message
from pyrit.models.seeds import (
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.models.technique_edit import SimulationChanges


@pytest.mark.parametrize(
    "trailing",
    [
        SeedPrompt(value="Assistant turn", role="assistant", data_type="text", sequence=4, is_general_technique=True),
        SeedPrompt(value="System turn", role="system", data_type="text", sequence=4, is_general_technique=True),
        SeedPrompt(value="image.png", role="user", data_type="image_path", sequence=4, is_general_technique=True),
    ],
)
def test_simulation_resize_preserves_unrelated_trailing_seed(trailing: SeedPrompt) -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                num_turns=2, adversarial_chat_system_prompt=SeedPrompt(value="Simulate", data_type="text")
            ),
            trailing,
        ]
    )
    before = group.model_dump()
    assert group.fixed_final_user_prompt is None
    reduced = group.with_simulation_changes(SimulationChanges(num_turns=1))
    assert reduced.prompts[0].model_dump() == trailing.model_dump()
    with pytest.raises(ValueError, match="overlaps"):
        group.with_simulation_changes(SimulationChanges(num_turns=3))
    with pytest.raises(ValueError, match="no editable fixed final user message"):
        group.with_simulation_changes(SimulationChanges(final_user_message="Replacement"))
    assert group.model_dump() == before


@pytest.mark.parametrize("generated", [False, True])
def test_simulation_resize_moves_only_unambiguous_fixed_final_text(generated: bool) -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                sequence=2,
                num_turns=2,
                adversarial_chat_system_prompt=SeedPrompt(value="Simulate", data_type="text"),
                next_message_system_prompt=SeedPrompt(value="Generate next", data_type="text") if generated else None,
            ),
            SeedPrompt(
                value="Continue {{ objective }}",
                role="user",
                data_type="text",
                sequence=7 if generated else 6,
                parameters=["objective"],
                metadata={"preserve": "final prompt"},
                is_general_technique=True,
            ),
            SeedPrompt(value="Later", role="assistant", data_type="text", sequence=12, is_general_technique=True),
        ]
    )
    before = group.model_dump()
    assert group.fixed_final_user_prompt is (None if generated else group.prompts[0])
    resized = group.with_simulation_changes(SimulationChanges(num_turns=1))
    assert resized.prompts[0].sequence == (7 if generated else 4)
    assert resized.prompts[0].value == group.prompts[0].value
    assert resized.prompts[0].metadata == group.prompts[0].metadata
    assert resized.prompts[0].parameters == ["objective"]
    assert resized.prompts[1].model_dump() == group.prompts[1].model_dump()
    assert group.model_dump() == before


def test_simulation_resize_rejects_overlap_with_multipart_final_turn() -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                num_turns=2, adversarial_chat_system_prompt=SeedPrompt(value="Simulate", data_type="text")
            ),
            SeedPrompt(value="First", role="user", data_type="text", sequence=4, is_general_technique=True),
            SeedPrompt(value="Second", role="user", data_type="text", sequence=4, is_general_technique=True),
        ]
    )
    resized = group.with_simulation_changes(SimulationChanges(num_turns=1))
    assert [prompt.sequence for prompt in resized.prompts] == [4, 4]
    with pytest.raises(ValueError, match="overlaps"):
        group.with_simulation_changes(SimulationChanges(num_turns=3))
    with pytest.raises(ValueError, match="no editable fixed final user message"):
        group.with_simulation_changes(SimulationChanges(final_user_message="Replacement"))


@pytest.mark.parametrize("fixed_final", [False, True])
def test_invalid_final_message_is_rejected_before_simulation_rebuild(fixed_final: bool) -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                num_turns=2, adversarial_chat_system_prompt=SeedPrompt(value="Simulate", data_type="text")
            ),
            *(
                [SeedPrompt(value="continue", role="user", data_type="text", sequence=4, is_general_technique=True)]
                if fixed_final
                else []
            ),
        ]
    )
    before = group.model_dump()
    message = "cannot be cleared" if fixed_final else "no editable fixed final user message"
    with (
        patch.object(SeedSimulatedConversation, "with_settings", side_effect=AssertionError("Rebuilt invalid edit")),
        pytest.raises(ValueError, match=message),
    ):
        group.with_simulation_changes(SimulationChanges(num_turns=3, final_user_message=None))
    assert group.model_dump() == before


def test_final_message_text_and_sequence_are_copied_without_changing_source() -> None:
    group = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                num_turns=2, adversarial_chat_system_prompt=SeedPrompt(value="Simulate", data_type="text")
            ),
            SeedPrompt(
                value="Original {{ objective }}",
                role="user",
                data_type="text",
                sequence=4,
                parameters=["objective"],
                is_jinja_template=True,
                is_general_technique=True,
                metadata={"preserve": "final prompt"},
            ),
        ]
    )
    before = group.model_dump()
    copied = group.with_simulation_changes(SimulationChanges(num_turns=3, final_user_message="Edited {{ objective }}"))
    assert copied.fixed_final_user_prompt is not None
    assert copied.fixed_final_user_prompt.value == "Edited {{ objective }}"
    assert copied.fixed_final_user_prompt.sequence == 6
    assert copied.fixed_final_user_prompt.parameters == ["objective"]
    assert copied.fixed_final_user_prompt.is_jinja_template
    assert copied.fixed_final_user_prompt.metadata == {"preserve": "final prompt"}
    assert group.model_dump() == before


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

    def test_seed_simulated_conversation_defaults_to_true(self):
        """Test that SeedSimulatedConversation.is_general_technique defaults to True."""
        sim = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Adversarial", parameters=["objective"]),
            num_turns=2,
        )
        assert sim.is_general_technique is True

    def test_seed_simulated_conversation_can_be_set_false(self):
        """Test that SeedSimulatedConversation.is_general_technique can be overridden to False."""
        sim = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Adversarial", parameters=["objective"]),
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

    def test_init_with_simulated_conversation(self):
        """Test initialization with SeedSimulatedConversation (defaults to general technique)."""
        group = AttackTechniqueSeedGroup(
            seeds=[
                SeedSimulatedConversation(
                    num_turns=3,
                    adversarial_chat_system_prompt=SeedPrompt(value="Adversarial", parameters=["objective"]),
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
        assert group.prompt_placement == "preserve"

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


class _CustomAttackTechniqueSeedGroup(AttackTechniqueSeedGroup):
    """A subclass used to check classmethod factory return types."""


class TestAttackTechniqueSeedGroupFromSystemPrompt:
    """Tests for the from_system_prompt classmethod."""

    def test_returns_calling_subclass(self):
        """Test that from_system_prompt preserves the calling subclass."""
        group = _CustomAttackTechniqueSeedGroup.from_system_prompt("Follow these rules.")

        assert_type(group, _CustomAttackTechniqueSeedGroup)
        assert type(group) is _CustomAttackTechniqueSeedGroup
        assert group.seeds[0].value == "Follow these rules."

    def test_builds_single_system_general_technique_seed(self):
        """Test that from_system_prompt yields one system-role general-technique seed."""
        group = AttackTechniqueSeedGroup.from_system_prompt("Follow these rules.")

        assert len(group.seeds) == 1
        seed = group.seeds[0]
        assert isinstance(seed, SeedPrompt)
        assert seed.value == "Follow these rules."
        assert seed.role == "system"
        assert seed.sequence == 0
        assert seed.data_type == "text"
        assert seed.is_general_technique is True
        assert group.insertion_index is None
        assert group.prompt_placement == "prepend"

    def test_preserves_literal_braces_without_rendering(self):
        """Test that literal Jinja braces are preserved (is_jinja_template stays False)."""
        group = AttackTechniqueSeedGroup.from_system_prompt("Decode this: {{ prompt }}")
        assert group.seeds[0].value == "Decode this: {{ prompt }}"

    def test_respects_insertion_index(self):
        """Test that insertion_index is forwarded to the group."""
        group = AttackTechniqueSeedGroup.from_system_prompt("s", insertion_index=0)
        assert group.insertion_index == 0

    def test_prompt_placement_survives_serialization_round_trip(self):
        """Test that prepend intent is preserved when the group is serialized."""
        group = AttackTechniqueSeedGroup.from_system_prompt("Follow these rules.")

        restored = AttackTechniqueSeedGroup.model_validate_json(group.model_dump_json())

        assert restored.prompt_placement == "prepend"


class TestAttackTechniqueSeedGroupFromMessages:
    """Tests for the from_messages classmethod."""

    def test_returns_calling_subclass(self):
        """Test that from_messages preserves the calling subclass."""
        group = _CustomAttackTechniqueSeedGroup.from_messages(
            messages=[Message.from_prompt(prompt="encoded practice", role="user")],
        )

        assert_type(group, _CustomAttackTechniqueSeedGroup)
        assert type(group) is _CustomAttackTechniqueSeedGroup
        assert group.seeds[0].value == "encoded practice"

    def test_builds_general_technique_seeds_from_messages(self):
        """Test that from_messages preserves roles and marks prompts as a technique."""
        messages = [
            Message.from_system_prompt(system_prompt="Use this notation table."),
            Message.from_prompt(prompt="encoded practice", role="user"),
            Message.from_prompt(prompt="encoded answer", role="assistant"),
        ]

        group = AttackTechniqueSeedGroup.from_messages(messages=messages, starting_sequence=4)

        assert [seed.value for seed in group.prompts] == [
            "Use this notation table.",
            "encoded practice",
            "encoded answer",
        ]
        assert [seed.role for seed in group.prompts] == ["system", "user", "assistant"]
        assert [seed.sequence for seed in group.prompts] == [4, 5, 6]
        assert all(seed.is_general_technique for seed in group.prompts)
        assert group.prompt_placement == "prepend"

    def test_respects_insertion_index(self):
        """Test that insertion_index is forwarded to the group."""
        group = AttackTechniqueSeedGroup.from_messages(
            messages=[Message.from_prompt(prompt="encoded practice", role="user")],
            insertion_index=0,
        )

        assert group.insertion_index == 0

    def test_merged_message_technique_extracts_teaching_context_before_objective(self):
        """Test that message-built techniques prepend context while leaving the objective as final turn."""
        technique = AttackTechniqueSeedGroup.from_messages(
            messages=[
                Message.from_system_prompt(system_prompt="Use this notation table."),
                Message.from_prompt(prompt="encoded practice", role="user"),
                Message.from_prompt(prompt="encoded answer", role="assistant"),
            ]
        )
        seed_group = AttackSeedGroup(seeds=[SeedObjective(value="Final objective")]).with_technique(technique=technique)

        assert seed_group.prepended_conversation is not None
        assert [message.get_value() for message in seed_group.prepended_conversation] == [
            "Use this notation table.",
            "encoded practice",
            "encoded answer",
        ]
        assert seed_group.next_message is None


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
