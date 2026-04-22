# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_parameters import (
    AttackParameters,
)
from pyrit.models import (
    Message,
    MessagePiece,
    SeedAttackGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


class TestFromSeedGroupAsync:
    """Tests for AttackParameters.from_seed_group_async."""

    @pytest.fixture
    def seed_objective(self) -> SeedObjective:
        """Create a basic seed objective."""
        return SeedObjective(value="Test objective")

    @pytest.fixture
    def seed_group_with_objective(self, seed_objective: SeedObjective) -> SeedAttackGroup:
        """Create a SeedAttackGroup with just an objective."""
        return SeedAttackGroup(seeds=[seed_objective])

    async def test_extracts_objective_from_seed_group(self, seed_group_with_objective: SeedAttackGroup) -> None:
        """Test that objective is correctly extracted from seed group."""
        params = await AttackParameters.from_seed_group_async(seed_group=seed_group_with_objective)

        assert params.objective == "Test objective"

    async def test_raises_when_no_objective(self) -> None:
        """Test that ValueError is raised when SeedAttackGroup has no objective."""
        # SeedAttackGroup now validates exactly one objective at construction
        prompt = SeedPrompt(value="Some prompt", data_type="text", role="user")

        with pytest.raises(ValueError, match="SeedAttackGroup must have exactly one objective"):
            SeedAttackGroup(seeds=[prompt])

    async def test_extracts_next_message(self) -> None:
        """Test that next_message is extracted from seed group prompts."""
        objective = SeedObjective(value="Test objective")
        prompt = SeedPrompt(value="Test prompt", data_type="text", role="user")
        seed_group = SeedAttackGroup(seeds=[objective, prompt])

        params = await AttackParameters.from_seed_group_async(seed_group=seed_group)

        assert params.next_message is not None
        assert params.next_message.get_value() == "Test prompt"

    async def test_extracts_prepended_conversation(self) -> None:
        """Test that prepended_conversation is extracted from multi-prompt seed groups."""
        objective = SeedObjective(value="Test objective")
        prompt1 = SeedPrompt(value="First message", data_type="text", role="user", sequence=1)
        prompt2 = SeedPrompt(value="Response", data_type="text", role="assistant", sequence=2)
        prompt3 = SeedPrompt(value="Second message", data_type="text", role="user", sequence=3)
        seed_group = SeedAttackGroup(seeds=[objective, prompt1, prompt2, prompt3])

        params = await AttackParameters.from_seed_group_async(seed_group=seed_group)

        assert params.prepended_conversation is not None
        assert len(params.prepended_conversation) == 2
        assert params.next_message is not None
        assert params.next_message.get_value() == "Second message"

    async def test_applies_overrides(self, seed_group_with_objective: SeedAttackGroup) -> None:
        """Test that overrides are applied to the parameters."""
        custom_message = _make_message("user", "Override message")

        params = await AttackParameters.from_seed_group_async(
            seed_group=seed_group_with_objective,
            next_message=custom_message,
        )

        assert params.next_message == custom_message

    async def test_rejects_invalid_overrides(self, seed_group_with_objective: SeedAttackGroup) -> None:
        """Test that invalid override fields raise ValueError."""
        with pytest.raises(ValueError, match="does not accept parameters"):
            await AttackParameters.from_seed_group_async(
                seed_group=seed_group_with_objective,
                invalid_field="value",
            )


class TestFromSeedGroupAsyncWithSimulatedConversation:
    """Tests for from_seed_group_async with simulated conversation config."""

    @pytest.fixture
    def seed_objective(self) -> SeedObjective:
        """Create a basic seed objective."""
        return SeedObjective(value="Test objective")

    @pytest.fixture
    def simulated_conversation_config(self) -> SeedSimulatedConversation:
        """Create a SeedSimulatedConversation config."""
        return SeedSimulatedConversation(
            num_turns=3,
            adversarial_chat_system_prompt_path="/path/to/adversarial.yaml",
            simulated_target_system_prompt_path="/path/to/target.yaml",
        )

    @pytest.fixture
    def seed_group_with_simulated_conv(
        self, seed_objective: SeedObjective, simulated_conversation_config: SeedSimulatedConversation
    ) -> SeedAttackGroup:
        """Create a SeedAttackGroup with simulated conversation config."""
        return SeedAttackGroup(seeds=[seed_objective, simulated_conversation_config])

    @pytest.fixture
    def mock_adversarial_chat(self) -> MagicMock:
        """Create a mock adversarial chat target."""
        return MagicMock()

    @pytest.fixture
    def mock_objective_scorer(self) -> MagicMock:
        """Create a mock objective scorer."""
        return MagicMock()

    @pytest.fixture
    def mock_simulated_result(self) -> list:
        """Create a mock simulated conversation result (List[SeedPrompt])."""
        return [
            SeedPrompt(value="Simulated user message", data_type="text", role="user", sequence=0),
            SeedPrompt(value="Simulated assistant response", data_type="text", role="assistant", sequence=1),
            SeedPrompt(value="Final simulated message", data_type="text", role="user", sequence=2),
        ]

    async def test_raises_when_adversarial_chat_missing(
        self,
        seed_group_with_simulated_conv: SeedAttackGroup,
        mock_objective_scorer: MagicMock,
    ) -> None:
        """Test that ValueError is raised when adversarial_chat is None."""
        with pytest.raises(ValueError, match="adversarial_chat is required"):
            await AttackParameters.from_seed_group_async(
                seed_group=seed_group_with_simulated_conv,
                adversarial_chat=None,
                objective_scorer=mock_objective_scorer,
            )

    async def test_raises_when_objective_scorer_missing(
        self,
        seed_group_with_simulated_conv: SeedAttackGroup,
        mock_adversarial_chat: MagicMock,
    ) -> None:
        """Test that ValueError is raised when objective_scorer is None."""
        with pytest.raises(ValueError, match="objective_scorer is required"):
            await AttackParameters.from_seed_group_async(
                seed_group=seed_group_with_simulated_conv,
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=None,
            )

    async def test_raises_when_prompt_overlaps_with_simulated_conv(
        self, seed_objective: SeedObjective, simulated_conversation_config: SeedSimulatedConversation
    ) -> None:
        """Test that ValueError is raised when prompts overlap with simulated conv sequences."""
        # SeedSimulatedConversation with default sequence=0 and num_turns=3 occupies sequences 0-5
        # A prompt with sequence=2 would overlap with that range
        prompt = SeedPrompt(value="Static prompt", data_type="text", role="user", sequence=2)

        # Validation now happens at construction time with sequence overlap checking
        with pytest.raises(ValueError, match="overlaps with SeedSimulatedConversation"):
            SeedAttackGroup(seeds=[seed_objective, prompt, simulated_conversation_config])

    async def test_raises_when_multi_sequence_prompts_overlap_with_simulated_conv(
        self, seed_objective: SeedObjective, simulated_conversation_config: SeedSimulatedConversation
    ) -> None:
        """Test that ValueError is raised when any prompts overlap with simulated conv sequences."""
        # Any prompts overlapping with simulated conversation sequences should fail at construction
        prompt = SeedPrompt(value="Static prompt", data_type="text", role="user", sequence=1)

        with pytest.raises(ValueError, match="overlaps with SeedSimulatedConversation"):
            SeedAttackGroup(seeds=[seed_objective, prompt, simulated_conversation_config])

    @patch("pyrit.executor.attack.multi_turn.simulated_conversation.generate_simulated_conversation_async")
    async def test_generates_simulated_conversation(
        self,
        mock_generate: AsyncMock,
        seed_group_with_simulated_conv: SeedAttackGroup,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_simulated_result: MagicMock,
    ) -> None:
        """Test that simulated conversation is generated when config is present."""
        mock_generate.return_value = mock_simulated_result

        await AttackParameters.from_seed_group_async(
            seed_group=seed_group_with_simulated_conv,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args.kwargs
        assert call_kwargs["objective"] == "Test objective"
        assert call_kwargs["adversarial_chat"] == mock_adversarial_chat
        assert call_kwargs["objective_scorer"] == mock_objective_scorer
        assert call_kwargs["num_turns"] == 3

    @patch("pyrit.executor.attack.multi_turn.simulated_conversation.generate_simulated_conversation_async")
    async def test_uses_generated_prepended_messages(
        self,
        mock_generate: AsyncMock,
        seed_group_with_simulated_conv: SeedAttackGroup,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_simulated_result: list,
    ) -> None:
        """Test that prepended_conversation comes from the generated result."""
        mock_generate.return_value = mock_simulated_result

        params = await AttackParameters.from_seed_group_async(
            seed_group=seed_group_with_simulated_conv,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        # prepended_conversation should contain the first two prompts (before the last user message)
        assert params.prepended_conversation is not None
        assert len(params.prepended_conversation) == 2
        assert params.prepended_conversation[0].get_value() == "Simulated user message"
        assert params.prepended_conversation[1].get_value() == "Simulated assistant response"

    @patch("pyrit.executor.attack.multi_turn.simulated_conversation.generate_simulated_conversation_async")
    async def test_uses_generated_next_message(
        self,
        mock_generate: AsyncMock,
        seed_group_with_simulated_conv: SeedAttackGroup,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_simulated_result: list,
    ) -> None:
        """Test that next_message comes from the generated result."""
        mock_generate.return_value = mock_simulated_result

        params = await AttackParameters.from_seed_group_async(
            seed_group=seed_group_with_simulated_conv,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        # next_message should be the last user message
        assert params.next_message is not None
        assert params.next_message.get_value() == "Final simulated message"


class TestExcluding:
    """Tests for AttackParameters.excluding()."""

    def test_excluding_creates_class_without_specified_fields(self) -> None:
        """Test that excluding() creates a class without the specified fields."""
        ExcludedParams = AttackParameters.excluding("next_message", "prepended_conversation")  # noqa: N806

        field_names = {f.name for f in dataclasses.fields(ExcludedParams)}

        assert "objective" in field_names
        assert "memory_labels" in field_names
        assert "next_message" not in field_names
        assert "prepended_conversation" not in field_names

    def test_excluding_raises_for_invalid_fields(self) -> None:
        """Test that excluding() raises ValueError for non-existent fields."""
        with pytest.raises(ValueError, match="Cannot exclude non-existent fields"):
            AttackParameters.excluding("nonexistent_field")

    async def test_excluded_class_has_from_seed_group_async(self) -> None:
        """Test that the excluded class has from_seed_group_async method."""
        ExcludedParams = AttackParameters.excluding("next_message", "prepended_conversation")  # noqa: N806

        assert hasattr(ExcludedParams, "from_seed_group_async")

    async def test_excluded_class_from_seed_group_async_works(self) -> None:
        """Test that from_seed_group_async works on excluded class."""
        ExcludedParams = AttackParameters.excluding("next_message", "prepended_conversation")  # noqa: N806
        objective = SeedObjective(value="Test objective")
        seed_group = SeedAttackGroup(seeds=[objective])

        params = await ExcludedParams.from_seed_group_async(seed_group=seed_group)

        assert params.objective == "Test objective"

    async def test_excluded_class_rejects_excluded_field_overrides(self) -> None:
        """Test that from_seed_group_async rejects overrides for excluded fields."""
        ExcludedParams = AttackParameters.excluding("next_message")  # noqa: N806
        objective = SeedObjective(value="Test objective")
        seed_group = SeedAttackGroup(seeds=[objective])

        with pytest.raises(ValueError, match="does not accept parameters"):
            await ExcludedParams.from_seed_group_async(
                seed_group=seed_group,
                next_message=_make_message("user", "Should fail"),
            )


@pytest.mark.asyncio
async def test_from_seed_group_async_raises_when_objective_is_none():
    """Test that from_seed_group_async raises ValueError when seed_group.objective is None."""
    seed_group = MagicMock(spec=SeedAttackGroup)
    seed_group.validate = MagicMock()
    seed_group.objective = None
    seed_group.simulated_conversation = None

    with pytest.raises(ValueError, match="seed_group.objective is not initialized"):
        await AttackParameters.from_seed_group_async(seed_group=seed_group)
