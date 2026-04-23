# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import (
    ConversationSession,
    MultiTurnAttackContext,
)
from pyrit.memory import CentralMemory
from pyrit.models import ConversationType, MessagePiece


def _make_context() -> MultiTurnAttackContext:
    return MultiTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        session=ConversationSession(),
    )


def _make_strategy(*, supports_multi_turn: bool):
    """Create a minimal MultiTurnAttackStrategy for testing."""
    from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import MultiTurnAttackStrategy

    target = MagicMock()
    target.capabilities.supports_multi_turn = supports_multi_turn
    target.configuration.includes.return_value = supports_multi_turn
    target.get_identifier.return_value = MagicMock()

    with patch.multiple(
        MultiTurnAttackStrategy,
        __abstractmethods__=frozenset(),
        _perform_async=AsyncMock(),
        _setup_async=AsyncMock(),
    ):
        strategy = MultiTurnAttackStrategy(
            objective_target=target,
            context_type=MultiTurnAttackContext,
        )
    return strategy  # noqa: RET504


def _seed_conversation(*, conversation_id: str, system_prompt: str, user_text: str = "Hello") -> None:
    """Add a system message and a user message to memory under the given conversation_id."""
    memory = CentralMemory.get_memory_instance()

    sys_piece = MessagePiece(
        original_value=system_prompt,
        role="system",
        conversation_id=conversation_id,
        sequence=0,
    )
    user_piece = MessagePiece(
        original_value=user_text,
        role="user",
        conversation_id=conversation_id,
        sequence=1,
    )
    memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece])


@pytest.mark.usefixtures("patch_central_database")
class TestRotateConversationForSingleTurnTarget:
    """Test the _rotate_conversation_for_single_turn_target helper."""

    def test_noop_for_multi_turn_target(self):
        strategy = _make_strategy(supports_multi_turn=True)
        context = _make_context()
        context.executed_turns = 1
        original_id = context.session.conversation_id

        strategy._rotate_conversation_for_single_turn_target(context=context)

        assert context.session.conversation_id == original_id
        assert len(context.related_conversations) == 0

    def test_noop_on_first_turn(self):
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        context.executed_turns = 0
        original_id = context.session.conversation_id

        strategy._rotate_conversation_for_single_turn_target(context=context)

        assert context.session.conversation_id == original_id
        assert len(context.related_conversations) == 0

    def test_rotates_on_second_turn_for_single_turn_target(self):
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        context.executed_turns = 1
        original_id = context.session.conversation_id

        strategy._rotate_conversation_for_single_turn_target(context=context)

        assert context.session.conversation_id != original_id
        assert len(context.related_conversations) == 1
        ref = next(iter(context.related_conversations))
        assert ref.conversation_id == original_id
        assert ref.conversation_type == ConversationType.PRUNED

    def test_rotates_multiple_turns(self):
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        seen_ids = {context.session.conversation_id}

        for turn in range(1, 4):
            context.executed_turns = turn
            strategy._rotate_conversation_for_single_turn_target(context=context)
            assert context.session.conversation_id not in seen_ids
            seen_ids.add(context.session.conversation_id)

        assert len(context.related_conversations) == 3


@pytest.mark.usefixtures("patch_central_database")
class TestSystemPromptCarryoverOnRotation:
    """Test that system prompts are duplicated into the new conversation on rotation."""

    def test_system_prompt_duplicated_into_new_conversation(self):
        """When rotating, system messages must be copied to the new conversation_id."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        old_id = context.session.conversation_id

        _seed_conversation(
            conversation_id=old_id,
            system_prompt="You are a helpful assistant.",
            user_text="Turn 1 user message",
        )

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        new_id = context.session.conversation_id
        assert new_id != old_id

        memory = CentralMemory.get_memory_instance()
        new_messages = memory.get_conversation(conversation_id=new_id)

        # Only the system message should be in the new conversation (not the user message)
        assert len(new_messages) == 1
        assert new_messages[0].api_role == "system"
        assert new_messages[0].get_value() == "You are a helpful assistant."

    def test_system_prompt_preserved_across_multiple_rotations(self):
        """System prompt must carry over through successive rotations."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        # Seed initial conversation with system prompt + user message
        _seed_conversation(
            conversation_id=context.session.conversation_id,
            system_prompt="You are an expert.",
        )

        for turn in range(1, 4):
            context.executed_turns = turn
            strategy._rotate_conversation_for_single_turn_target(context=context)

            messages = memory.get_conversation(conversation_id=context.session.conversation_id)
            system_msgs = [m for m in messages if m.api_role == "system"]
            assert len(system_msgs) == 1, f"Turn {turn}: expected 1 system message, got {len(system_msgs)}"
            assert system_msgs[0].get_value() == "You are an expert."

            # Simulate a user message for the next turn's conversation
            user_piece = MessagePiece(
                original_value=f"User message turn {turn}",
                role="user",
                conversation_id=context.session.conversation_id,
                sequence=1,
            )
            memory.add_message_pieces_to_memory(message_pieces=[user_piece])

    def test_no_system_prompt_yields_fresh_conversation_id(self):
        """When there is no system prompt, rotation still generates a new conversation_id."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        # Seed conversation with only a user message (no system prompt)
        user_piece = MessagePiece(
            original_value="Just a user message",
            role="user",
            conversation_id=context.session.conversation_id,
            sequence=0,
        )
        memory.add_message_pieces_to_memory(message_pieces=[user_piece])

        old_id = context.session.conversation_id
        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        assert context.session.conversation_id != old_id
        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        assert len(new_messages) == 0

    def test_user_messages_not_carried_over(self):
        """Only system messages should be carried to the new conversation, not user/assistant."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        # Seed with system + user + assistant
        sys_piece = MessagePiece(
            original_value="System prompt",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="User message",
            role="user",
            conversation_id=context.session.conversation_id,
            sequence=1,
        )
        asst_piece = MessagePiece(
            original_value="Assistant response",
            role="assistant",
            conversation_id=context.session.conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece, asst_piece])

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        roles = [m.api_role for m in new_messages]
        assert roles == ["system"], f"Expected only system, got {roles}"

    def test_multiple_system_messages_all_carried_over(self):
        """When multiple system messages exist (different sequences), all are duplicated."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        # Two system messages at different sequences (e.g., system prompt + safety instructions
        # injected at different points in a prepended conversation)
        sys1 = MessagePiece(
            original_value="System prompt 1",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Hello",
            role="user",
            conversation_id=context.session.conversation_id,
            sequence=1,
        )
        sys2 = MessagePiece(
            original_value="Safety instructions",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys1, user_piece, sys2])

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        system_values = sorted(m.get_value() for m in new_messages if m.api_role == "system")
        assert system_values == ["Safety instructions", "System prompt 1"]
        assert all(m.api_role == "system" for m in new_messages)

    def test_empty_conversation_yields_fresh_id(self):
        """When the conversation has zero messages, rotation still produces a new ID."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        old_id = context.session.conversation_id

        # Don't seed any messages — conversation is completely empty
        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        assert context.session.conversation_id != old_id
        memory = CentralMemory.get_memory_instance()
        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        assert len(new_messages) == 0

    def test_only_system_messages_all_carried_over(self):
        """When the conversation contains only system messages (no user/assistant), all are carried."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        sys_piece = MessagePiece(
            original_value="Only a system message",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=0,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece])

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        assert len(new_messages) == 1
        assert new_messages[0].api_role == "system"
        assert new_messages[0].get_value() == "Only a system message"

    def test_multipiece_system_message_fully_duplicated(self):
        """A system Message with multiple pieces (same sequence) is fully duplicated."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()

        # Two system pieces at the same sequence form one multi-piece Message
        sys_text = MessagePiece(
            original_value="System text instruction",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=0,
        )
        sys_image = MessagePiece(
            original_value="image_placeholder",
            role="system",
            conversation_id=context.session.conversation_id,
            sequence=0,
            original_value_data_type="image_path",
        )
        user_piece = MessagePiece(
            original_value="Hello",
            role="user",
            conversation_id=context.session.conversation_id,
            sequence=1,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_text, sys_image, user_piece])

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        new_messages = memory.get_conversation(conversation_id=context.session.conversation_id)
        assert len(new_messages) == 1
        assert new_messages[0].api_role == "system"
        # Both pieces should be present in the duplicated message
        assert len(new_messages[0].message_pieces) == 2
        values = {p.converted_value for p in new_messages[0].message_pieces}
        assert values == {"System text instruction", "image_placeholder"}

    def test_old_conversation_untouched_after_rotation(self):
        """Rotation must not alter messages in the old conversation."""
        strategy = _make_strategy(supports_multi_turn=False)
        context = _make_context()
        memory = CentralMemory.get_memory_instance()
        old_id = context.session.conversation_id

        _seed_conversation(
            conversation_id=old_id,
            system_prompt="Original system prompt",
            user_text="Original user message",
        )

        context.executed_turns = 1
        strategy._rotate_conversation_for_single_turn_target(context=context)

        # Old conversation should still have both messages intact
        old_messages = memory.get_conversation(conversation_id=old_id)
        old_roles = [m.api_role for m in old_messages]
        assert old_roles == ["system", "user"]


@pytest.mark.usefixtures("patch_central_database")
class TestTAPNodeDuplicateSystemMessages:
    """Test that TAP's duplicate correctly handles system messages."""

    def _make_tap_node(self, *, supports_multi_turn: bool):
        """Create a minimal _TreeOfAttacksNode for testing."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import _TreeOfAttacksNode

        target = MagicMock()
        target.capabilities.supports_multi_turn = supports_multi_turn
        target.configuration.includes.return_value = supports_multi_turn
        target.get_identifier.return_value = MagicMock()

        adversarial_chat = MagicMock()
        adversarial_chat.get_identifier.return_value = MagicMock()

        scorer = MagicMock()
        scorer.get_identifier.return_value = MagicMock()

        seed = MagicMock()
        seed.render_template_value.return_value = "template"

        return _TreeOfAttacksNode(
            objective_target=target,
            adversarial_chat=adversarial_chat,
            adversarial_chat_seed_prompt=seed,
            adversarial_chat_prompt_template=seed,
            adversarial_chat_system_seed_prompt=seed,
            desired_response_prefix="Sure,",
            objective_scorer=scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=None,
            attack_id=MagicMock(),
            attack_strategy_name="TAP",
        )

    def test_single_turn_target_duplicates_only_system_messages(self):
        """For single-turn targets, only system messages are copied to the duplicate node."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        # Seed the node's conversation with system + user + assistant messages
        sys_piece = MessagePiece(
            original_value="TAP system prompt",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Attack prompt turn 1",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        asst_piece = MessagePiece(
            original_value="Target response turn 1",
            role="assistant",
            conversation_id=node.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece, asst_piece])

        duplicate = node.duplicate()

        # The duplicate should have a different conversation_id
        assert duplicate.objective_target_conversation_id != node.objective_target_conversation_id

        # The duplicate's conversation should contain only the system message
        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert len(dup_messages) == 1
        assert dup_messages[0].api_role == "system"
        assert dup_messages[0].get_value() == "TAP system prompt"

    def test_multi_turn_target_duplicates_full_conversation(self):
        """For multi-turn targets, the full conversation is duplicated."""
        node = self._make_tap_node(supports_multi_turn=True)
        memory = CentralMemory.get_memory_instance()

        # Seed the node's conversation with system + user + assistant
        sys_piece = MessagePiece(
            original_value="TAP system prompt",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Attack prompt",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        asst_piece = MessagePiece(
            original_value="Target response",
            role="assistant",
            conversation_id=node.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece, asst_piece])

        duplicate = node.duplicate()

        assert duplicate.objective_target_conversation_id != node.objective_target_conversation_id

        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        roles = [m.api_role for m in dup_messages]
        assert roles == ["system", "user", "assistant"]

    def test_single_turn_no_system_messages_yields_fresh_id(self):
        """For single-turn targets with no system messages, a fresh empty conversation is created."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        # Seed with only user/assistant (no system prompt)
        user_piece = MessagePiece(
            original_value="Attack prompt",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        memory.add_message_pieces_to_memory(message_pieces=[user_piece])

        duplicate = node.duplicate()

        assert duplicate.objective_target_conversation_id != node.objective_target_conversation_id
        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert len(dup_messages) == 0

    def test_adversarial_chat_always_fully_duplicated(self):
        """The adversarial chat conversation should always be fully duplicated regardless of target type."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        # Seed adversarial chat conversation
        sys_piece = MessagePiece(
            original_value="Adversarial system prompt",
            role="system",
            conversation_id=node.adversarial_chat_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Adversarial user message",
            role="user",
            conversation_id=node.adversarial_chat_conversation_id,
            sequence=1,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece])

        # Also seed objective target conversation so it doesn't error
        target_piece = MessagePiece(
            original_value="Target user",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        memory.add_message_pieces_to_memory(message_pieces=[target_piece])

        duplicate = node.duplicate()

        dup_adv_messages = memory.get_conversation(conversation_id=duplicate.adversarial_chat_conversation_id)
        roles = [m.api_role for m in dup_adv_messages]
        assert roles == ["system", "user"]

    def test_single_turn_multiple_system_messages_all_duplicated(self):
        """For single-turn targets with multiple system messages, all are duplicated."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        sys1 = MessagePiece(
            original_value="System prompt A",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Attack prompt",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        sys2 = MessagePiece(
            original_value="System prompt B",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys1, user_piece, sys2])

        duplicate = node.duplicate()

        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert all(m.api_role == "system" for m in dup_messages)
        dup_values = sorted(m.get_value() for m in dup_messages)
        assert dup_values == ["System prompt A", "System prompt B"]

    def test_single_turn_empty_conversation_yields_fresh_id(self):
        """For single-turn targets with empty conversation, a fresh ID is produced."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        # Don't seed any messages
        duplicate = node.duplicate()

        assert duplicate.objective_target_conversation_id != node.objective_target_conversation_id
        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert len(dup_messages) == 0

    def test_duplicate_node_has_correct_parent_id(self):
        """The duplicate node's parent_id should be the original node's node_id."""
        node = self._make_tap_node(supports_multi_turn=False)

        duplicate = node.duplicate()

        assert duplicate.parent_id == node.node_id
        assert duplicate.node_id != node.node_id

    def test_duplicate_node_copies_conversation_context(self):
        """The duplicate node should inherit the _conversation_context from the original."""
        node = self._make_tap_node(supports_multi_turn=False)
        node._conversation_context = "Some prior conversation context"

        duplicate = node.duplicate()

        assert duplicate._conversation_context == "Some prior conversation context"

    def test_system_message_content_preserved_exactly(self):
        """The duplicated system message text must match the original exactly."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        long_prompt = "You are a helpful assistant.\n\nRules:\n1. Be concise\n2. Be accurate\n\n  Special chars: àéîöü"
        sys_piece = MessagePiece(
            original_value=long_prompt,
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Hello",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece])

        duplicate = node.duplicate()

        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert len(dup_messages) == 1
        assert dup_messages[0].get_value() == long_prompt

    def test_original_conversation_untouched_after_duplicate(self):
        """Duplicating must not alter the original node's conversation."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        sys_piece = MessagePiece(
            original_value="System prompt",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="User attack",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece])

        node.duplicate()

        # Original conversation should still have both messages
        orig_messages = memory.get_conversation(conversation_id=node.objective_target_conversation_id)
        orig_roles = [m.api_role for m in orig_messages]
        assert orig_roles == ["system", "user"]

    def test_single_turn_multipiece_system_message_duplicated(self):
        """A multi-piece system Message (same sequence) is fully duplicated in TAP."""
        node = self._make_tap_node(supports_multi_turn=False)
        memory = CentralMemory.get_memory_instance()

        sys_text = MessagePiece(
            original_value="System text",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        sys_image = MessagePiece(
            original_value="image_data",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
            original_value_data_type="image_path",
        )
        user_piece = MessagePiece(
            original_value="Attack",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_text, sys_image, user_piece])

        duplicate = node.duplicate()

        dup_messages = memory.get_conversation(conversation_id=duplicate.objective_target_conversation_id)
        assert len(dup_messages) == 1
        assert dup_messages[0].api_role == "system"
        assert len(dup_messages[0].message_pieces) == 2
        dup_values = {p.converted_value for p in dup_messages[0].message_pieces}
        assert dup_values == {"System text", "image_data"}


@pytest.mark.usefixtures("patch_central_database")
class TestValueErrorGuards:
    """Test that incompatible attacks raise ValueError for single-turn targets."""

    def _make_single_turn_target(self):
        from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
        from pyrit.prompt_target.common.target_configuration import TargetConfiguration

        target = MagicMock()
        target.capabilities.supports_multi_turn = False
        target.configuration = TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True),
        )
        target.get_identifier.return_value = MagicMock()
        return target

    def _make_adversarial_config(self):
        from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig

        adversarial_chat = MagicMock()
        adversarial_chat.get_identifier.return_value = MagicMock()
        return AttackAdversarialConfig(target=adversarial_chat)

    def _make_scoring_config(self):
        from pyrit.executor.attack.core.attack_config import AttackScoringConfig
        from pyrit.score import TrueFalseScorer

        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.get_identifier.return_value = MagicMock()
        return AttackScoringConfig(objective_scorer=scorer)

    @pytest.mark.asyncio
    async def test_crescendo_raises_for_single_turn_target(self):
        from pyrit.executor.attack.multi_turn.crescendo import CrescendoAttack

        target = self._make_single_turn_target()

        with pytest.raises(ValueError, match="supports_multi_turn"):
            CrescendoAttack(
                objective_target=target,
                attack_adversarial_config=self._make_adversarial_config(),
                attack_scoring_config=self._make_scoring_config(),
            )

    @pytest.mark.asyncio
    async def test_multi_prompt_sending_raises_for_single_turn_target(self):
        from pyrit.executor.attack.multi_turn.multi_prompt_sending import MultiPromptSendingAttack

        target = self._make_single_turn_target()

        with pytest.raises(ValueError, match="supports_multi_turn"):
            MultiPromptSendingAttack(objective_target=target)

    @pytest.mark.asyncio
    async def test_chunked_request_raises_for_single_turn_target(self):
        from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttack

        target = self._make_single_turn_target()

        with pytest.raises(ValueError, match="supports_multi_turn"):
            ChunkedRequestAttack(objective_target=target)


@pytest.mark.usefixtures("patch_central_database")
class TestTAPBranchingPreservesSystemPrompts:
    """Integration test: TAP branching with real memory verifies system prompt carryover."""

    def _make_tap_node(self, *, supports_multi_turn: bool):
        """Create a _TreeOfAttacksNode with real memory."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import _TreeOfAttacksNode

        target = MagicMock()
        target.capabilities.supports_multi_turn = supports_multi_turn
        target.configuration.includes.return_value = supports_multi_turn
        target.get_identifier.return_value = MagicMock()

        adversarial_chat = MagicMock()
        adversarial_chat.get_identifier.return_value = MagicMock()

        scorer = MagicMock()
        scorer.get_identifier.return_value = MagicMock()

        seed = MagicMock()
        seed.render_template_value.return_value = "template"

        return _TreeOfAttacksNode(
            objective_target=target,
            adversarial_chat=adversarial_chat,
            adversarial_chat_seed_prompt=seed,
            adversarial_chat_prompt_template=seed,
            adversarial_chat_system_seed_prompt=seed,
            desired_response_prefix="Sure,",
            objective_scorer=scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=None,
            attack_id=MagicMock(),
            attack_strategy_name="TAP",
        )

    def test_branching_single_turn_target_preserves_system_across_depths(self):
        """Simulate TAP branching across 2 depths and verify system prompts survive.

        Depth 1: Create a node, seed system + user + assistant messages.
        Depth 2: duplicate() the node (simulating branching). For single-turn targets,
        only the system message should be in the duplicate's conversation.
        Then simulate another turn on the duplicate (add user + assistant).
        Depth 3: duplicate() again. System message should still be there.
        """
        memory = CentralMemory.get_memory_instance()
        node = self._make_tap_node(supports_multi_turn=False)

        # Depth 1: seed the conversation with system prompt + a completed turn
        sys_piece = MessagePiece(
            original_value="You are a red team assistant.",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="Tell me about X",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        asst_piece = MessagePiece(
            original_value="Here is info about X",
            role="assistant",
            conversation_id=node.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece, asst_piece])

        # Depth 2: branch (duplicate) — single-turn means only system msg is copied
        branch1 = node.duplicate()

        branch1_msgs = memory.get_conversation(conversation_id=branch1.objective_target_conversation_id)
        assert len(branch1_msgs) == 1
        assert branch1_msgs[0].api_role == "system"
        assert branch1_msgs[0].get_value() == "You are a red team assistant."

        # Simulate depth-2 turn on branch1: add user + assistant on branch1's conversation
        user2 = MessagePiece(
            original_value="Now tell me about Y",
            role="user",
            conversation_id=branch1.objective_target_conversation_id,
            sequence=1,
        )
        asst2 = MessagePiece(
            original_value="Here is info about Y",
            role="assistant",
            conversation_id=branch1.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[user2, asst2])

        # Verify branch1 now has system + user + assistant
        branch1_full = memory.get_conversation(conversation_id=branch1.objective_target_conversation_id)
        assert [m.api_role for m in branch1_full] == ["system", "user", "assistant"]

        # Depth 3: branch again from branch1
        branch2 = branch1.duplicate()

        branch2_msgs = memory.get_conversation(conversation_id=branch2.objective_target_conversation_id)
        assert len(branch2_msgs) == 1
        assert branch2_msgs[0].api_role == "system"
        assert branch2_msgs[0].get_value() == "You are a red team assistant."

    def test_branching_multi_turn_target_preserves_full_history(self):
        """For multi-turn targets, branching should preserve the full conversation."""
        memory = CentralMemory.get_memory_instance()
        node = self._make_tap_node(supports_multi_turn=True)

        # Seed system + user + assistant
        sys_piece = MessagePiece(
            original_value="System prompt",
            role="system",
            conversation_id=node.objective_target_conversation_id,
            sequence=0,
        )
        user_piece = MessagePiece(
            original_value="User message",
            role="user",
            conversation_id=node.objective_target_conversation_id,
            sequence=1,
        )
        asst_piece = MessagePiece(
            original_value="Assistant response",
            role="assistant",
            conversation_id=node.objective_target_conversation_id,
            sequence=2,
        )
        memory.add_message_pieces_to_memory(message_pieces=[sys_piece, user_piece, asst_piece])

        branch = node.duplicate()

        branch_msgs = memory.get_conversation(conversation_id=branch.objective_target_conversation_id)
        assert [m.api_role for m in branch_msgs] == ["system", "user", "assistant"]

        # Add another turn on the branch
        user2 = MessagePiece(
            original_value="Follow-up",
            role="user",
            conversation_id=branch.objective_target_conversation_id,
            sequence=3,
        )
        memory.add_message_pieces_to_memory(message_pieces=[user2])

        # Branch again — should have all 4 messages
        branch2 = branch.duplicate()
        branch2_msgs = memory.get_conversation(conversation_id=branch2.objective_target_conversation_id)
        assert [m.api_role for m in branch2_msgs] == ["system", "user", "assistant", "user"]
