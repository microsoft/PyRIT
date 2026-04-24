# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import AttackConverterConfig, RTASystemPromptPaths
from pyrit.executor.attack.multi_turn.simulated_conversation import (
    generate_simulated_conversation_async,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    NextMessageSystemPromptPaths,
    Score,
    SeedPrompt,
    SimulatedTargetSystemPromptPaths,
)
from pyrit.prompt_target import PromptChatTarget
from pyrit.score import TrueFalseScorer


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_adversarial_chat() -> MagicMock:
    """Create a mock adversarial chat target for testing."""
    chat = MagicMock(spec=PromptChatTarget)
    chat.send_prompt_async = AsyncMock()
    chat.set_system_prompt = MagicMock()
    chat.get_identifier.return_value = _mock_target_id("MockAdversarialChat")
    return chat


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    """Create a mock objective scorer for testing."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


@pytest.fixture
def adversarial_system_prompt_path() -> Path:
    """Return a valid adversarial chat system prompt path for testing."""
    return RTASystemPromptPaths.TEXT_GENERATION.value


@pytest.fixture
def sample_conversation() -> list[Message]:
    """Create a sample conversation for testing."""
    return [
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="Hello",
                    original_value_data_type="text",
                    conversation_id=str(uuid.uuid4()),
                )
            ]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="Hi there!",
                    original_value_data_type="text",
                    conversation_id=str(uuid.uuid4()),
                )
            ]
        ),
    ]


@pytest.mark.usefixtures("patch_central_database")
class TestSimulatedTargetSystemPromptPaths:
    """Tests for SimulatedTargetSystemPromptPaths enum."""

    def test_compliant_path_exists(self):
        """Test that the COMPLIANT system prompt path points to an existing file."""
        path = SimulatedTargetSystemPromptPaths.COMPLIANT.value
        assert isinstance(path, Path)
        assert path.exists(), f"Expected compliant.yaml at {path}"

    def test_compliant_path_is_yaml(self):
        """Test that the COMPLIANT path is a YAML file."""
        path = SimulatedTargetSystemPromptPaths.COMPLIANT.value
        assert path.suffix == ".yaml"


@pytest.mark.usefixtures("patch_central_database")
class TestGenerateSimulatedConversationAsync:
    """Tests for generate_simulated_conversation_async function."""

    @pytest.mark.asyncio
    async def test_raises_error_for_zero_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
    ):
        """Test that zero num_turns raises ValueError."""
        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                num_turns=0,
            )

    @pytest.mark.asyncio
    async def test_raises_error_for_negative_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
    ):
        """Test that negative num_turns raises ValueError."""
        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                num_turns=-1,
            )

    @pytest.mark.asyncio
    async def test_uses_adversarial_chat_as_simulated_target(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that the same adversarial_chat is used as simulated target."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            # Configure the mock attack
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                )

                # Verify RedTeamingAttack was created with adversarial_chat as objective_target
                mock_attack_class.assert_called_once()
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["objective_target"] == mock_adversarial_chat

    @pytest.mark.asyncio
    async def test_creates_attack_with_score_last_turn_only_true(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that the attack is created with score_last_turn_only=True."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                )

                # Verify score_last_turn_only was set to True
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["score_last_turn_only"] is True

    @pytest.mark.asyncio
    async def test_creates_attack_with_correct_max_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that the attack is created with the specified num_turns as max_turns."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=5,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=5,
                )

                # Verify max_turns was set correctly
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["max_turns"] == 5

    @pytest.mark.asyncio
    async def test_returns_simulated_conversation_result(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that the function returns a list of SeedPrompts."""
        conversation_id = str(uuid.uuid4())
        mock_score = MagicMock(spec=Score)

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                    last_score=mock_score,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                )

                # Verify get_conversation was called with the correct conversation_id
                mock_memory.get_conversation.assert_called_once_with(conversation_id=conversation_id)

                # Verify the result is a list of SeedPrompts
                assert isinstance(result, list)
                assert len(result) == len(sample_conversation)
                for seed_prompt in result:
                    assert isinstance(seed_prompt, SeedPrompt)

    @pytest.mark.asyncio
    async def test_passes_system_prompt_via_prepended_conversation(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that the simulated target system prompt is passed via prepended_conversation."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # Pass a simulated_target_system_prompt_path to test prepending behavior
                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    simulated_target_system_prompt_path=SimulatedTargetSystemPromptPaths.COMPLIANT.value,
                    num_turns=3,
                )

                # Verify execute_async was called with a system message in prepended_conversation
                mock_attack.execute_async.assert_called_once()
                call_kwargs = mock_attack.execute_async.call_args.kwargs
                assert "prepended_conversation" in call_kwargs
                prepended = call_kwargs["prepended_conversation"]
                assert len(prepended) == 1
                assert prepended[0].api_role == "system"

    @pytest.mark.asyncio
    async def test_passes_memory_labels_to_execute(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that memory_labels are passed to attack.execute_async."""
        memory_labels = {"source": "test", "type": "simulated"}

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                    memory_labels=memory_labels,
                )

                # Verify memory_labels were passed to execute_async
                execute_kwargs = mock_attack.execute_async.call_args.kwargs
                assert execute_kwargs["memory_labels"] == memory_labels

    @pytest.mark.asyncio
    async def test_passes_converter_config_to_attack(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that attack_converter_config is passed to RedTeamingAttack."""
        converter_config = AttackConverterConfig()

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                    attack_converter_config=converter_config,
                )

                # Verify converter_config was passed to RedTeamingAttack
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["attack_converter_config"] == converter_config

    @pytest.mark.asyncio
    async def test_prepends_system_message_to_conversation(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that a system message is prepended when executing the attack."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # Pass a simulated_target_system_prompt_path to test prepending behavior
                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    simulated_target_system_prompt_path=SimulatedTargetSystemPromptPaths.COMPLIANT.value,
                    num_turns=3,
                )

                # Verify prepended_conversation was passed to execute_async
                execute_kwargs = mock_attack.execute_async.call_args.kwargs
                assert "prepended_conversation" in execute_kwargs
                prepended = execute_kwargs["prepended_conversation"]
                assert len(prepended) == 1
                assert prepended[0].message_pieces[0].api_role == "system"

    @pytest.mark.asyncio
    async def test_uses_default_num_turns_of_3(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that default num_turns is 3."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # Call without specifying num_turns
                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                )

                # Verify default max_turns is 3
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["max_turns"] == 3

    @pytest.mark.asyncio
    async def test_next_message_system_prompt_path_generates_final_user_message(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that next_message_system_prompt_path generates a final user message via LLM call."""

        conversation_id = str(uuid.uuid4())

        # Create the expected response from the adversarial chat for generating next message
        next_message_response = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",  # LLM responds as assistant, we convert to user
                    original_value="Generated next user message",
                    original_value_data_type="text",
                    conversation_id=str(uuid.uuid4()),
                )
            ]
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # Configure adversarial_chat to return next message response
                mock_adversarial_chat.send_prompt_async = AsyncMock(return_value=[next_message_response])

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                    next_message_system_prompt_path=NextMessageSystemPromptPaths.DIRECT.value,
                )

                # Verify adversarial_chat was called to generate the next message
                mock_adversarial_chat.send_prompt_async.assert_called_once()

                # Verify the result includes the generated next message
                # sample_conversation has 2 messages, plus 1 generated next message = 3
                assert len(result) == 3

                # Verify the last message is the generated one with role="user"
                assert result[-1].value == "Generated next user message"
                assert result[-1].role == "user"

    @pytest.mark.asyncio
    async def test_next_message_system_prompt_path_sets_system_prompt(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that next_message_system_prompt_path sets a system prompt on adversarial_chat."""
        from pyrit.models.seeds import NextMessageSystemPromptPaths

        conversation_id = str(uuid.uuid4())

        next_message_response = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="Generated message",
                    original_value_data_type="text",
                    conversation_id=str(uuid.uuid4()),
                )
            ]
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                mock_adversarial_chat.send_prompt_async = AsyncMock(return_value=[next_message_response])

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                    next_message_system_prompt_path=NextMessageSystemPromptPaths.DIRECT.value,
                )

                # Verify set_system_prompt was called on adversarial_chat
                mock_adversarial_chat.set_system_prompt.assert_called()

    @pytest.mark.asyncio
    async def test_starting_sequence_sets_first_sequence_number(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """Test that starting_sequence sets the sequence number of the first prompt."""
        conversation_id = str(uuid.uuid4())

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock()
                mock_memory.get_conversation.return_value = iter(sample_conversation)
                mock_memory_class.get_memory_instance.return_value = mock_memory

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                    num_turns=3,
                    starting_sequence=5,
                )

                # Verify the first prompt starts at sequence 5
                assert result[0].sequence == 5
                assert result[1].sequence == 6
