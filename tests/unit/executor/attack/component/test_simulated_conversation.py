# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_mock_prompt_normalizer

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.exceptions import AdversarialChatResponseBlockedException, InvalidJsonException
from pyrit.executor.attack import (
    AttackConverterConfig,
    RTASystemPromptPaths,
    SimulatedConversationResult,
    generate_simulated_conversation_async,
)
from pyrit.executor.attack.core.attack_preparation import (
    AttackPreparationFailure,
    AttackPreparationFailureKind,
)
from pyrit.executor.attack.multi_turn.simulated_conversation import (
    _generate_next_message_async,
    _ToolResultWriter,
    _with_simulated_tools,
)
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ChatMessageRole,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    JsonResponseConfig,
    Message,
    MessagePiece,
    NextMessageSystemPromptPaths,
    PromptDataType,
    Score,
    SeedPrompt,
    SimulatedConversationMode,
    SimulatedTargetSystemPromptPaths,
    ToolDefinition,
    load_next_message_prompt,
    load_simulated_target_prompt,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import LiteLLMChatTarget, OpenAIChatTarget, OpenAIResponseTarget, PromptTarget, Tool
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
    chat = MagicMock(spec=PromptTarget)
    chat.send_prompt_async = AsyncMock()
    chat.set_system_prompt_async = AsyncMock()
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
def adversarial_system_prompt() -> SeedPrompt:
    """Return a valid, already-resolved adversarial chat system prompt for testing."""
    return SeedPrompt.from_yaml_file(RTASystemPromptPaths.TEXT_GENERATION.value)


@pytest.fixture
def adversarial_system_prompt_path() -> Path:
    """Return a valid adversarial chat system prompt path for testing."""
    return RTASystemPromptPaths.TEXT_GENERATION.value


@pytest.fixture
def simulated_target_system_prompt() -> SeedPrompt:
    return load_simulated_target_prompt(SimulatedTargetSystemPromptPaths.COMPLIANT.value)


@pytest.fixture
def next_message_system_prompt() -> SeedPrompt:
    return load_next_message_prompt(NextMessageSystemPromptPaths.DIRECT.value)


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

    async def test_raises_error_for_zero_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
    ):
        """Test that zero num_turns raises ValueError."""
        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                num_turns=0,
            )

    async def test_raises_error_for_negative_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
    ):
        """Test that negative num_turns raises ValueError."""
        with pytest.raises(ValueError, match="num_turns must be a positive integer"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                num_turns=-1,
            )

    async def test_deprecated_system_prompt_path_still_resolves_and_warns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_conversation: list[Message],
    ):
        """Test that the deprecated adversarial_chat_system_prompt_path still works and warns."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                with pytest.warns(DeprecationWarning, match="removed in 1.4.0"):
                    await generate_simulated_conversation_async(
                        objective="Test objective",
                        adversarial_chat=mock_adversarial_chat,
                        objective_scorer=mock_objective_scorer,
                        adversarial_chat_system_prompt_path=RTASystemPromptPaths.TEXT_GENERATION.value,
                        num_turns=3,
                    )

                call_kwargs = mock_attack_class.call_args.kwargs
                resolved_prompt = call_kwargs["attack_adversarial_config"].system_prompt
                expected_prompt = SeedPrompt.from_yaml_file(RTASystemPromptPaths.TEXT_GENERATION.value)
                assert resolved_prompt.value == expected_prompt.value

    async def test_uses_adversarial_chat_as_simulated_target(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                )

                # Verify RedTeamingAttack was created with adversarial_chat as objective_target
                mock_attack_class.assert_called_once()
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["objective_target"] == mock_adversarial_chat

    async def test_passes_system_prompt_to_adversarial_config(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ):
        """Test that adversarial_chat_system_prompt is forwarded as-is onto the adversarial config."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                )

                call_kwargs = mock_attack_class.call_args.kwargs
                adversarial_config = call_kwargs["attack_adversarial_config"]
                # This function no longer composes a prefix itself - callers are expected to pass
                # an already-resolved adversarial_chat_system_prompt (e.g. via
                # SeedSimulatedConversation.with_layered_prefix) before calling in.
                assert adversarial_config.system_prompt is adversarial_system_prompt
                assert adversarial_config.system_prompt_prefix is None

    async def test_creates_attack_with_score_last_turn_only_true(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                )

                # Verify score_last_turn_only was set to True
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["score_last_turn_only"] is True

    async def test_creates_attack_with_correct_max_turns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=5,
                )

                # Verify max_turns was set correctly
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["max_turns"] == 5

    async def test_returns_simulated_conversation_result(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ):
        """Test that the function returns a list of SeedPrompts."""
        conversation_id = str(uuid.uuid4())
        mock_score = MagicMock(spec=Score, scored_expectation=None, observation_ids=[])

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                    automated_score=mock_score,
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                )

                # Verify get_conversation_messages was called with the correct conversation_id
                mock_memory.get_conversation_messages_async.assert_called_once_with(conversation_id=conversation_id)

                assert isinstance(result, SimulatedConversationResult)
                assert len(result.seed_prompts) == len(sample_conversation)
                for seed_prompt in result.seed_prompts:
                    assert isinstance(seed_prompt, SeedPrompt)

    async def test_passes_system_prompt_via_prepended_conversation(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        simulated_target_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    simulated_target_system_prompt=simulated_target_system_prompt,
                    num_turns=3,
                )

                # Verify execute_async was called with a system message in prepended_conversation
                mock_attack.execute_async.assert_called_once()
                call_kwargs = mock_attack.execute_async.call_args.kwargs
                assert "prepended_conversation" in call_kwargs
                prepended = call_kwargs["prepended_conversation"]
                assert len(prepended) == 1
                assert prepended[0].api_role == "system"

    async def test_passes_memory_labels_to_execute(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    memory_labels=memory_labels,
                )

                # Verify memory_labels were passed to execute_async
                execute_kwargs = mock_attack.execute_async.call_args.kwargs
                assert execute_kwargs["memory_labels"] == memory_labels
                assert execute_kwargs["persist_attack_result"] is False

    async def test_returns_preparation_and_adversarial_references(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ) -> None:
        """The transient helper retains both sides of its conversation lineage."""
        preparation_id = str(uuid.uuid4())
        adversarial_id = str(uuid.uuid4())
        adversarial_reference = ConversationReference(
            conversation_id=adversarial_id,
            conversation_type=ConversationType.ADVERSARIAL,
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    conversation_id=preparation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    related_conversations={adversarial_reference},
                )
            )
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                )

        assert {
            (reference.conversation_id, reference.conversation_type) for reference in result.related_conversations
        } == {
            (preparation_id, ConversationType.PREPARATION),
            (adversarial_id, ConversationType.ADVERSARIAL),
        }

    async def test_adversarial_block_is_returned_as_preparation_failure(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
    ) -> None:
        preparation_id = str(uuid.uuid4())
        adversarial_id = str(uuid.uuid4())
        adversarial_reference = ConversationReference(
            conversation_id=adversarial_id,
            conversation_type=ConversationType.ADVERSARIAL,
        )
        failure_reason = "Adversarial chat blocked the attack before it could generate the next prompt."

        with (
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class,
            patch(
                "pyrit.executor.attack.multi_turn.simulated_conversation._generate_next_message_async",
                new_callable=AsyncMock,
            ) as mock_generate_next,
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
        ):
            mock_attack = MagicMock()
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    conversation_id=preparation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.UNDETERMINED,
                    outcome_reason=failure_reason,
                    related_conversations={adversarial_reference},
                    metadata=AttackPreparationFailure(
                        kind=AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED,
                        reason=failure_reason,
                    ).to_metadata(),
                )
            )
            mock_attack_class.return_value = mock_attack
            mock_memory = MagicMock(spec=MemoryInterface)
            mock_memory.get_conversation_messages_async.return_value = []
            mock_memory_class.get_memory_instance.return_value = mock_memory

            result = await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                next_message_system_prompt=next_message_system_prompt,
            )

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.reason == failure_reason
        assert {
            (reference.conversation_id, reference.conversation_type) for reference in result.related_conversations
        } == {
            (preparation_id, ConversationType.PREPARATION),
            (adversarial_id, ConversationType.ADVERSARIAL),
        }
        mock_generate_next.assert_not_awaited()

    async def test_adversarial_block_without_outcome_reason_still_reports_a_reason(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
    ) -> None:
        with (
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class,
            patch(
                "pyrit.executor.attack.multi_turn.simulated_conversation._generate_next_message_async",
                new_callable=AsyncMock,
            ),
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
        ):
            mock_attack = MagicMock()
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.UNDETERMINED,
                    outcome_reason=None,
                    metadata={
                        AttackPreparationFailure.METADATA_KEY: (
                            AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED.value
                        )
                    },
                )
            )
            mock_attack_class.return_value = mock_attack
            mock_memory = MagicMock(spec=MemoryInterface)
            mock_memory.get_conversation_messages_async.return_value = []
            mock_memory_class.get_memory_instance.return_value = mock_memory

            result = await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                next_message_system_prompt=next_message_system_prompt,
            )

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.reason

    async def test_final_prompt_block_is_returned_as_preparation_failure(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ) -> None:
        preparation_id = str(uuid.uuid4())

        with (
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class,
            patch(
                "pyrit.executor.attack.multi_turn.simulated_conversation._generate_next_message_async",
                new_callable=AsyncMock,
                side_effect=AdversarialChatResponseBlockedException(
                    status_code=200,
                    message="I cannot assist with that request.",
                ),
            ),
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
        ):
            mock_attack = MagicMock()
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    conversation_id=preparation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.FAILURE,
                )
            )
            mock_attack_class.return_value = mock_attack
            mock_memory = MagicMock(spec=MemoryInterface)
            mock_memory.get_conversation_messages_async.return_value = sample_conversation
            mock_memory_class.get_memory_instance.return_value = mock_memory

            result = await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                next_message_system_prompt=next_message_system_prompt,
            )

        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED
        assert "I cannot assist with that request." in result.preparation_failure.reason
        assert any(
            reference.description == "simulated next-message generation" for reference in result.related_conversations
        )

    async def test_passes_converter_config_to_attack(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    attack_converter_config=converter_config,
                )

                # Verify converter_config was passed to RedTeamingAttack
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["attack_converter_config"] == converter_config

    async def test_prepends_system_message_to_conversation(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        simulated_target_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    simulated_target_system_prompt=simulated_target_system_prompt,
                    num_turns=3,
                )

                # Verify prepended_conversation was passed to execute_async
                execute_kwargs = mock_attack.execute_async.call_args.kwargs
                assert "prepended_conversation" in execute_kwargs
                prepended = execute_kwargs["prepended_conversation"]
                assert len(prepended) == 1
                assert prepended[0].message_pieces[0].api_role == "system"

    async def test_uses_default_num_turns_of_3(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # Call without specifying num_turns
                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                )

                # Verify default max_turns is 3
                call_kwargs = mock_attack_class.call_args.kwargs
                assert call_kwargs["max_turns"] == 3

    async def test_next_message_system_prompt_generates_final_user_message(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ):
        """Test that next_message_system_prompt generates a final user message via LLM call."""

        conversation_id = str(uuid.uuid4())

        # The DIRECT template declares the canonical adversarial_chat schema, so the adversarial
        # chat returns JSON and the manager parses ``next_message`` out of it.
        next_message_reply = Message.from_prompt(
            role="assistant",
            prompt=json.dumps(
                {
                    "next_message": "Generated next user message",
                    "rationale": "advance the objective",
                    "last_response_summary": "prior turn",
                }
            ),
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with (
                patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
                patch(
                    "pyrit.executor.attack.multi_turn.simulated_conversation.PromptNormalizer"
                ) as mock_normalizer_class,
            ):
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                # The manager sends the adversarial turn through this normalizer.
                mock_normalizer = get_mock_prompt_normalizer()
                mock_normalizer.send_prompt_async = AsyncMock(return_value=next_message_reply)
                mock_normalizer_class.return_value = mock_normalizer

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    next_message_system_prompt=next_message_system_prompt,
                )

                # The manager sent exactly one adversarial turn to generate the next message.
                mock_normalizer.send_prompt_async.assert_called_once()

                # Verify the result includes the generated next message
                # sample_conversation has 2 messages, plus 1 generated next message = 3
                assert len(result.seed_prompts) == 3

                # Verify the last message is the parsed next_message with role="user"
                assert result.seed_prompts[-1].value == "Generated next user message"
                assert result.seed_prompts[-1].role == "user"

                send_conversation_id = mock_normalizer.send_prompt_async.call_args.kwargs["conversation_id"]
                assert any(
                    reference.conversation_id == send_conversation_id
                    and reference.conversation_type == ConversationType.ADVERSARIAL
                    for reference in result.related_conversations
                )

    async def test_next_message_system_prompt_sets_system_prompt(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ):
        """Test that next_message_system_prompt sets a system prompt on adversarial_chat."""

        conversation_id = str(uuid.uuid4())

        next_message_reply = Message.from_prompt(
            role="assistant",
            prompt=json.dumps(
                {
                    "next_message": "Generated message",
                    "rationale": "advance",
                    "last_response_summary": "prior turn",
                }
            ),
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=conversation_id,
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with (
                patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
                patch(
                    "pyrit.executor.attack.multi_turn.simulated_conversation.PromptNormalizer"
                ) as mock_normalizer_class,
            ):
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                mock_normalizer = get_mock_prompt_normalizer()
                mock_normalizer.send_prompt_async = AsyncMock(return_value=next_message_reply)
                mock_normalizer_class.return_value = mock_normalizer

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    next_message_system_prompt=next_message_system_prompt,
                )

                # The manager renders and sets the adversarial system prompt before sending.
                mock_adversarial_chat.set_system_prompt_async.assert_called()

    async def test_next_message_scopes_system_prompt_to_generated_message_conversation(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
        next_message_system_prompt: SeedPrompt,
        sample_conversation: list[Message],
    ):
        """Regression: the next-message system prompt must be scoped to a concrete conversation id.

        ``Message.from_prompt`` leaves ``conversation_id`` unset (None). Passing that to
        ``set_system_prompt`` makes ``get_conversation_messages`` skip its conversation filter and
        return every piece in memory, which raises once memory holds more than one conversation.
        The ``_AdversarialConversationManager`` now owns this scoping, so the system prompt and the
        adversarial send it drives must share the same non-empty conversation id.
        """
        next_message_reply = Message.from_prompt(
            role="assistant",
            prompt=json.dumps(
                {
                    "next_message": "Generated message",
                    "rationale": "advance",
                    "last_response_summary": "prior turn",
                }
            ),
        )

        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = MagicMock()
            mock_attack.get_identifier.return_value = ComponentIdentifier(
                class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
            )
            mock_attack.execute_async = AsyncMock(
                return_value=AttackResult(
                    atomic_attack_identifier=ComponentIdentifier(
                        class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                    ),
                    conversation_id=str(uuid.uuid4()),
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=3,
                )
            )
            mock_attack_class.return_value = mock_attack

            with (
                patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class,
                patch(
                    "pyrit.executor.attack.multi_turn.simulated_conversation.PromptNormalizer"
                ) as mock_normalizer_class,
            ):
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                mock_normalizer = get_mock_prompt_normalizer()
                mock_normalizer.send_prompt_async = AsyncMock(return_value=next_message_reply)
                mock_normalizer_class.return_value = mock_normalizer

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    next_message_system_prompt=next_message_system_prompt,
                )

                system_prompt_conversation_id = mock_adversarial_chat.set_system_prompt_async.call_args.kwargs[
                    "conversation_id"
                ]
                assert system_prompt_conversation_id

                # The manager sends the next-message turn on the same concrete conversation id it
                # scoped the system prompt to (via the normalizer, not the target directly).
                send_conversation_id = mock_normalizer.send_prompt_async.call_args.kwargs["conversation_id"]
                assert send_conversation_id == system_prompt_conversation_id

    async def test_starting_sequence_sets_first_sequence_number(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
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
                    atomic_attack_identifier=ComponentIdentifier(
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
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                result = await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=adversarial_system_prompt,
                    num_turns=3,
                    starting_sequence=5,
                )

                # Verify the first prompt starts at sequence 5
                assert result.seed_prompts[0].sequence == 5
                assert result.seed_prompts[1].sequence == 6


class TestGenerateNextMessageAsync:
    """Tests for _generate_next_message_async, which folds schema resolution, the send, and JSON
    parse/retry onto the shared _AdversarialConversationManager (there is no raw-text path)."""

    @staticmethod
    def _mock_normalizer(reply: Message | None) -> MagicMock:
        normalizer = get_mock_prompt_normalizer()
        normalizer.send_prompt_async = AsyncMock(return_value=reply)
        return normalizer

    async def test_parses_next_message_from_json_reply(self, mock_adversarial_chat: MagicMock):
        """The DIRECT template declares the canonical schema, so ``next_message`` is parsed out of the
        JSON reply and returned as a fresh user message."""
        reply = Message.from_prompt(
            role="assistant",
            prompt=json.dumps(
                {
                    "next_message": "parsed user message",
                    "rationale": "advance",
                    "last_response_summary": "prior",
                }
            ),
        )
        normalizer = self._mock_normalizer(reply)

        with patch(
            "pyrit.executor.attack.multi_turn.simulated_conversation.ConversationContextNormalizer"
        ) as mock_normalizer_cls:
            mock_normalizer_cls.return_value.normalize_string_async = AsyncMock(return_value="ctx")

            result = await _generate_next_message_async(
                objective="obj",
                conversation_messages=[],
                adversarial_chat=mock_adversarial_chat,
                conversation_id="next-message-conversation",
                next_message_system_prompt=load_next_message_prompt(NextMessageSystemPromptPaths.DIRECT.value),
                prompt_normalizer=normalizer,
            )

        assert result.get_value() == "parsed user message"
        assert result.message_pieces[0].role == "user"
        assert normalizer.send_prompt_async.call_args.kwargs["conversation_id"] == "next-message-conversation"
        # The manager renders and sets the adversarial system prompt before sending.
        mock_adversarial_chat.set_system_prompt_async.assert_called_once()
        # The canonical schema is always resolved and forwarded so schema-aware targets constrain output.
        sent_message = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent_message.message_pieces[0].prompt_metadata.get("response_format") == "json"

    async def test_invalid_json_reply_raises_after_retry(self, mock_adversarial_chat: MagicMock):
        """A malformed reply is retried by the manager's ``pyrit_json_retry`` and then surfaces
        InvalidJsonException. This is a regression guard: the removed raw-text path used to swallow
        bad JSON by flipping it straight to the user, so a schema mismatch now fails loudly."""
        normalizer = self._mock_normalizer(Message.from_prompt(role="assistant", prompt="not valid json"))

        with patch(
            "pyrit.executor.attack.multi_turn.simulated_conversation.ConversationContextNormalizer"
        ) as mock_normalizer_cls:
            mock_normalizer_cls.return_value.normalize_string_async = AsyncMock(return_value="ctx")

            with pytest.raises(InvalidJsonException):
                await _generate_next_message_async(
                    objective="obj",
                    conversation_messages=[],
                    adversarial_chat=mock_adversarial_chat,
                    conversation_id="next-message-conversation",
                    next_message_system_prompt=load_next_message_prompt(NextMessageSystemPromptPaths.DIRECT.value),
                    prompt_normalizer=normalizer,
                )

        # The manager re-sent the turn rather than giving up on the first bad reply.
        assert normalizer.send_prompt_async.await_count > 1

    async def test_raises_when_no_response(self, mock_adversarial_chat: MagicMock):
        """A missing adversarial response surfaces a clear ValueError from the manager."""
        normalizer = self._mock_normalizer(None)

        with patch(
            "pyrit.executor.attack.multi_turn.simulated_conversation.ConversationContextNormalizer"
        ) as mock_normalizer_cls:
            mock_normalizer_cls.return_value.normalize_string_async = AsyncMock(return_value="ctx")

            with pytest.raises(ValueError, match="No response received"):
                await _generate_next_message_async(
                    objective="obj",
                    conversation_messages=[],
                    adversarial_chat=mock_adversarial_chat,
                    conversation_id="next-message-conversation",
                    next_message_system_prompt=load_next_message_prompt(NextMessageSystemPromptPaths.DIRECT.value),
                    prompt_normalizer=normalizer,
                )


@pytest.mark.usefixtures("patch_central_database")
class TestSimulatedConversationPromptSources:
    """Tests for the canonical prompt inputs and the deprecated path inputs."""

    @staticmethod
    def _mock_attack(conversation_id: str) -> MagicMock:
        attack = MagicMock()
        attack.get_identifier.return_value = ComponentIdentifier(
            class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
        )
        attack.execute_async = AsyncMock(
            return_value=AttackResult(
                atomic_attack_identifier=ComponentIdentifier(
                    class_name="RedTeamingAttack", class_module="pyrit.executor.attack"
                ),
                conversation_id=conversation_id,
                objective="Test objective",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=2,
            )
        )
        return attack

    async def test_canonical_prompts_never_read_from_disk(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_conversation: list[Message],
    ):
        """Canonical prompts are carried in, so the async path performs no YAML loading."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack_class.return_value = self._mock_attack(str(uuid.uuid4()))

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                with patch("pyrit.models.seeds.yaml_seed_loader.load_seed_from_yaml") as mock_load:
                    await generate_simulated_conversation_async(
                        objective="Test objective",
                        adversarial_chat=mock_adversarial_chat,
                        objective_scorer=mock_objective_scorer,
                        adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
                        simulated_target_system_prompt=SeedPrompt(
                            value="Objective: {{ objective }} Turns: {{ num_turns }}",
                            parameters=["objective", "num_turns"],
                        ),
                        num_turns=2,
                    )

                mock_load.assert_not_called()

    async def test_simulated_target_prompt_is_rendered_into_system_message(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_conversation: list[Message],
    ):
        """The simulated target template is rendered with the objective and turn count."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack = self._mock_attack(str(uuid.uuid4()))
            mock_attack_class.return_value = mock_attack

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                await generate_simulated_conversation_async(
                    objective="Test objective",
                    adversarial_chat=mock_adversarial_chat,
                    objective_scorer=mock_objective_scorer,
                    adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
                    simulated_target_system_prompt=SeedPrompt(
                        value="Objective: {{ objective }} Turns: {{ num_turns }}",
                        parameters=["objective", "num_turns"],
                    ),
                    num_turns=2,
                )

        prepended = mock_attack.execute_async.call_args.kwargs["prepended_conversation"]
        assert prepended[0].get_value() == "Objective: Test objective Turns: 2"

    async def test_deprecated_path_input_warns(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
        sample_conversation: list[Message],
    ):
        """A deprecated path input still works and warns."""
        with patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as mock_attack_class:
            mock_attack_class.return_value = self._mock_attack(str(uuid.uuid4()))

            with patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as mock_memory_class:
                mock_memory = MagicMock(spec=MemoryInterface)
                mock_memory.get_conversation_messages_async = AsyncMock(return_value=iter(sample_conversation))
                mock_memory_class.get_memory_instance.return_value = mock_memory

                with pytest.warns(DeprecationWarning, match="adversarial_chat_system_prompt_path"):
                    await generate_simulated_conversation_async(
                        objective="Test objective",
                        adversarial_chat=mock_adversarial_chat,
                        objective_scorer=mock_objective_scorer,
                        adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                        num_turns=2,
                    )

    async def test_prompt_and_path_together_raises(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt_path: Path,
    ):
        """Supplying both a canonical prompt and its deprecated path is ambiguous."""
        with pytest.raises(ValueError, match="Set only one of adversarial_chat_system_prompt"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=SeedPrompt(value="adversarial"),
                adversarial_chat_system_prompt_path=adversarial_system_prompt_path,
                num_turns=2,
            )

    async def test_missing_adversarial_prompt_raises(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
    ):
        """The adversarial system prompt is required."""
        with pytest.raises(ValueError, match="adversarial_chat_system_prompt is required"):
            await generate_simulated_conversation_async(
                objective="Test objective",
                adversarial_chat=mock_adversarial_chat,
                objective_scorer=mock_objective_scorer,
                num_turns=2,
            )


_LOOKUP = ToolDefinition(
    name="lookup_ticket",
    description="Look up a ticket.",
    parameters={"type": "object", "properties": {"ticket_id": {"type": "string"}}},
)


class _SourceToolProvider:
    identifier = {"provider": "source"}

    async def get_tools_async(self) -> list[Tool]:
        raise AssertionError("A stand-in must not use the source's tool providers.")


@pytest.mark.usefixtures("patch_central_database")
class TestSimulatedConversationWithTools:
    async def test_stand_in_gets_tools_whose_results_the_adversarial_chat_writes_async(self) -> None:
        adversarial_chat = MockPromptTarget()
        writer = _ToolResultWriter(objective="Reset the admin password.", adversarial_chat=adversarial_chat)
        source = OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://mock.azure.com",
            api_key="key",
            custom_functions={"send_email": AsyncMock()},
            extra_body_parameters={
                "tools": [{"type": "function", "name": "send_email", "parameters": {}}, {"type": "web_search"}],
                "tool_choice": "auto",
            },
        )

        stand_in = _with_simulated_tools(target=source, tools=[_LOOKUP], writer=writer)
        [simulated_tool] = stand_in._tools
        result = await simulated_tool.execute_async(arguments={"ticket_id": "SEC-1"})

        assert stand_in is not source
        assert await stand_in.get_tool_definitions_async() == [_LOOKUP]
        assert not stand_in._custom_functions
        assert not stand_in._extra_body_parameters
        assert [tool.name for tool in await source.get_tool_definitions_async()] == ["send_email"]
        assert result == "default"
        assert writer.started
        request = json.loads(adversarial_chat.prompt_sent[-1])
        assert (request["tool"]["name"], request["arguments"]) == ("lookup_ticket", {"ticket_id": "SEC-1"})
        assert "Reset the admin password." in adversarial_chat.system_prompt

    @pytest.mark.parametrize(
        ("target_type", "tool_providers"),
        # Chat targets reject tools in a nested extra_body when a tool provider is configured.
        [(OpenAIChatTarget, None), (LiteLLMChatTarget, None), (OpenAIResponseTarget, [_SourceToolProvider()])],
    )
    async def test_stand_in_drops_the_source_tool_settings_async(
        self, target_type: type[PromptTarget], tool_providers: list[_SourceToolProvider] | None
    ) -> None:
        settings = {
            "tools": [{"type": "function", "name": "send_email"}],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "functions": [{"name": "send_email"}],
            "function_call": {"name": "send_email"},
        }
        source = target_type(
            model_name="gpt-4",
            endpoint="https://mock.azure.com",
            api_key="key",
            tool_providers=tool_providers,
            execute_tools=False,
            extra_body_parameters={**settings, "metadata": {"keep": "yes"}, "extra_body": {**settings, "user": "rt"}},
        )
        writer = _ToolResultWriter(objective="objective", adversarial_chat=MockPromptTarget())

        stand_in = _with_simulated_tools(target=source, tools=[_LOOKUP], writer=writer)

        assert await stand_in.get_tool_definitions_async() == [_LOOKUP]
        assert stand_in._extra_body_parameters == {"metadata": {"keep": "yes"}, "extra_body": {"user": "rt"}}
        assert stand_in._execute_tools

    async def test_tool_results_share_one_writer_conversation_async(self) -> None:
        adversarial_chat = MockPromptTarget()
        adversarial_chat.set_system_prompt_async = AsyncMock()  # type: ignore[method-assign]
        writer = _ToolResultWriter(objective="objective", adversarial_chat=adversarial_chat)
        reply = Message.from_prompt(prompt="SEC-1 is approved.", role="assistant")

        with patch.object(PromptNormalizer, "send_prompt_async", new_callable=AsyncMock, return_value=reply) as send:
            await writer.write_async(tool=_LOOKUP, arguments={"ticket_id": "SEC-1"})
            await writer.write_async(tool=_LOOKUP, arguments={"ticket_id": "SEC-2"})

        assert {call.kwargs["conversation_id"] for call in send.call_args_list} == {writer.conversation_id}
        adversarial_chat.set_system_prompt_async.assert_awaited_once()

    @pytest.mark.parametrize(
        ("reply", "kind"),
        [
            (
                MessagePiece(role="assistant", original_value="filtered", response_error="blocked").to_message(),
                AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED,
            ),
            (
                Message.from_prompt(prompt=" ", role="assistant"),
                AttackPreparationFailureKind.ADVERSARIAL_CHAT_INVALID_OUTPUT,
            ),
        ],
    )
    async def test_unusable_tool_result_ends_the_conversation_as_a_preparation_failure_async(
        self,
        reply: Message,
        kind: AttackPreparationFailureKind,
        mock_objective_scorer: MagicMock,
        adversarial_system_prompt: SeedPrompt,
    ) -> None:
        adversarial_chat = OpenAIResponseTarget(model_name="gpt-4", endpoint="https://mock.azure.com", api_key="key")

        async def run_stand_in_async(**_: object) -> AttackResult:
            stand_in = attack_class.call_args.kwargs["objective_target"]
            await stand_in._tools[0].execute_async(arguments={"ticket_id": "SEC-1"})
            raise AssertionError("the tool result should have failed")

        with (
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as attack_class,
            patch.object(PromptNormalizer, "send_prompt_async", new_callable=AsyncMock, return_value=reply),
        ):
            attack_class.return_value.execute_async = AsyncMock(side_effect=run_stand_in_async)
            result = await generate_simulated_conversation_async(
                objective="objective",
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                tools=[_LOOKUP],
            )

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is kind
        assert [reference.description for reference in result.related_conversations] == ["simulated tool results"]

    async def test_tools_need_a_stand_in_that_runs_them_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        with pytest.raises(ValueError, match="needs a simulated target that runs tool calls"):
            await generate_simulated_conversation_async(
                objective="objective",
                adversarial_chat=MockPromptTarget(),
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                tools=[_LOOKUP],
            )

    async def test_one_shot_needs_tools_async(self, adversarial_system_prompt: SeedPrompt) -> None:
        with pytest.raises(ValueError, match="need the tools"):
            await generate_simulated_conversation_async(
                objective="objective",
                adversarial_chat=MockPromptTarget(),
                objective_scorer=None,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                mode=SimulatedConversationMode.ONE_SHOT,
            )

    async def test_custom_stand_in_runs_its_own_tools_into_the_history_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        stand_in = OpenAIResponseTarget(model_name="gpt-4", endpoint="https://mock.azure.com", api_key="key")
        call = json.dumps({"type": "function_call", "call_id": "c1", "name": "lookup_ticket", "arguments": "{}"})
        output = json.dumps({"type": "function_call_output", "call_id": "c1", "output": "SEC-1 is approved."})
        conversation = [
            _piece_message("user", "Check SEC-1.", "text"),
            _piece_message("assistant", call, "function_call"),
            _piece_message("tool", output, "function_call_output"),
            _piece_message("assistant", "SEC-1 is approved.", "text"),
        ]

        result, attack_class = await _run_turn_by_turn_async(
            conversation=conversation,
            mock_objective_scorer=mock_objective_scorer,
            adversarial_system_prompt=adversarial_system_prompt,
            simulated_target=stand_in,
        )

        assert attack_class.call_args.kwargs["objective_target"] is stand_in
        assert result.preparation_failure is None
        assert [(prompt.role, prompt.data_type) for prompt in result.seed_prompts] == [
            ("user", "text"),
            ("assistant", "function_call"),
            ("tool", "function_call_output"),
            ("assistant", "text"),
        ]

    async def test_tools_and_a_custom_stand_in_cannot_be_combined_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        stand_in = MockPromptTarget()
        stand_in.get_tool_definitions_async = AsyncMock(return_value=[_LOOKUP])  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="not both"):
            await generate_simulated_conversation_async(
                objective="objective",
                adversarial_chat=MockPromptTarget(),
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                simulated_target=stand_in,
                tools=[_LOOKUP],
            )

    async def test_empty_stand_in_turns_are_dropped_with_the_user_turns_they_answer_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        def empty() -> Message:
            return MessagePiece(role="assistant", original_value="", response_error="empty").to_message()

        result, _ = await _run_turn_by_turn_async(
            conversation=[
                _piece_message("user", "Check SEC-1.", "text"),
                empty(),
                _piece_message("user", "And SEC-2?", "text"),
                _piece_message("assistant", "SEC-2 is approved.", "text"),
                _piece_message("user", "Who approved it?", "text"),
                empty(),
            ],
            mock_objective_scorer=mock_objective_scorer,
            adversarial_system_prompt=adversarial_system_prompt,
        )

        assert result.preparation_failure is None
        assert [prompt.value for prompt in result.seed_prompts] == ["And SEC-2?", "SEC-2 is approved."]

    async def test_tool_result_conversation_is_linked_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        adversarial_chat = OpenAIResponseTarget(model_name="gpt-4", endpoint="https://mock.azure.com", api_key="key")
        reply = Message.from_prompt(prompt="SEC-1 is approved.", role="assistant")
        identifier = ComponentIdentifier(class_name="RedTeamingAttack", class_module="pyrit.executor.attack")

        async def run_stand_in_async(**_: object) -> AttackResult:
            stand_in = attack_class.call_args.kwargs["objective_target"]
            await stand_in._tools[0].execute_async(arguments={"ticket_id": "SEC-1"})
            return AttackResult(
                atomic_attack_identifier=identifier,
                conversation_id="simulated",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
                automated_score=MagicMock(spec=Score, scored_expectation=None, observation_ids=[]),
            )

        with (
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as attack_class,
            patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as memory_class,
            patch.object(PromptNormalizer, "send_prompt_async", new_callable=AsyncMock, return_value=reply),
        ):
            attack_class.return_value.execute_async = AsyncMock(side_effect=run_stand_in_async)
            memory_class.get_memory_instance.return_value.get_conversation_messages_async = AsyncMock(
                return_value=[_piece_message("user", "Check SEC-1.", "text")]
            )
            result = await generate_simulated_conversation_async(
                objective="objective",
                adversarial_chat=adversarial_chat,
                objective_scorer=mock_objective_scorer,
                adversarial_chat_system_prompt=adversarial_system_prompt,
                tools=[_LOOKUP],
            )

        assert "simulated tool results" in {reference.description for reference in result.related_conversations}

    async def test_all_empty_stand_in_replies_are_a_preparation_failure_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        empty = MessagePiece(role="assistant", original_value="", response_error="empty").to_message()

        result, _ = await _run_turn_by_turn_async(
            conversation=[_piece_message("user", "Check SEC-1.", "text"), empty],
            mock_objective_scorer=mock_objective_scorer,
            adversarial_system_prompt=adversarial_system_prompt,
        )

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is AttackPreparationFailureKind.SIMULATED_TARGET_ERROR
        assert "every simulated target reply was empty" in result.preparation_failure.reason

    async def test_stand_in_error_is_a_preparation_failure_async(
        self, mock_objective_scorer: MagicMock, adversarial_system_prompt: SeedPrompt
    ) -> None:
        error = MessagePiece(
            role="assistant",
            original_value="Tool loop stopped after 10 rounds.",
            original_value_data_type="error",
            response_error="unknown",
        ).to_message()

        result, _ = await _run_turn_by_turn_async(
            conversation=[_piece_message("user", "Check SEC-1.", "text"), error],
            mock_objective_scorer=mock_objective_scorer,
            adversarial_system_prompt=adversarial_system_prompt,
        )

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is AttackPreparationFailureKind.SIMULATED_TARGET_ERROR
        assert "Tool loop stopped after 10 rounds." in result.preparation_failure.reason


def _piece_message(role: ChatMessageRole, value: str, data_type: PromptDataType) -> Message:
    return MessagePiece(role=role, original_value=value, original_value_data_type=data_type).to_message()


async def _run_turn_by_turn_async(
    *,
    conversation: list[Message],
    mock_objective_scorer: MagicMock,
    adversarial_system_prompt: SeedPrompt,
    **kwargs: object,
) -> tuple[SimulatedConversationResult, MagicMock | AsyncMock]:
    identifier = ComponentIdentifier(class_name="RedTeamingAttack", class_module="pyrit.executor.attack")
    with (
        patch("pyrit.executor.attack.multi_turn.simulated_conversation.RedTeamingAttack") as attack_class,
        patch("pyrit.executor.attack.multi_turn.simulated_conversation.CentralMemory") as memory_class,
    ):
        attack_class.return_value.execute_async = AsyncMock(
            return_value=AttackResult(
                atomic_attack_identifier=identifier,
                conversation_id="simulated",
                objective="objective",
                outcome=AttackOutcome.SUCCESS,
                executed_turns=1,
                automated_score=MagicMock(spec=Score, scored_expectation=None, observation_ids=[]),
            )
        )
        memory_class.get_memory_instance.return_value.get_conversation_messages_async = AsyncMock(
            return_value=conversation
        )
        result = await generate_simulated_conversation_async(
            objective="objective",
            adversarial_chat=MockPromptTarget(),
            objective_scorer=mock_objective_scorer,
            adversarial_chat_system_prompt=adversarial_system_prompt,
            num_turns=1,
            **kwargs,
        )
    return result, attack_class


_CALL = {"type": "function_call", "call_id": "call_1", "name": "lookup_ticket", "arguments": {"ticket_id": "SEC-1"}}
_OUTPUT = {"type": "function_call_output", "call_id": "call_1", "output": "SEC-1 is APPROVED {{ literal }}."}
_SECOND_CALL = {**_CALL, "call_id": "call_2"}
_SECOND_OUTPUT = {**_OUTPUT, "call_id": "call_2"}


def _history_response(items: list[dict[str, object]], *, fenced: bool = False) -> Message:
    text = json.dumps({"items": items})
    return Message.from_prompt(prompt=f"```json\n{text}\n```" if fenced else text, role="assistant")


async def _generate_one_shot_async(
    *, responses: list[Message], tools: list[ToolDefinition] | None = None, **kwargs: object
) -> tuple[SimulatedConversationResult, AsyncMock]:
    with patch.object(PromptNormalizer, "send_prompt_async", new_callable=AsyncMock, side_effect=responses) as send:
        result = await generate_simulated_conversation_async(
            objective="Reset the admin password.",
            adversarial_chat=MockPromptTarget(),
            objective_scorer=None,
            adversarial_chat_system_prompt=SeedPrompt.from_yaml_file(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "tool_priming_one_shot.yaml"
            ),
            mode=SimulatedConversationMode.ONE_SHOT,
            tools=tools or [_LOOKUP],
            **kwargs,
        )
    return result, send


@pytest.mark.usefixtures("patch_central_database")
class TestOneShotToolHistory:
    async def test_valid_history_is_returned_in_order_async(self) -> None:
        result, send = await _generate_one_shot_async(
            responses=[_history_response([_CALL, _OUTPUT, _SECOND_CALL, _SECOND_OUTPUT])],
            num_turns=2,
            starting_sequence=4,
        )

        assert result.preparation_failure is None
        assert [(p.role, p.data_type, p.sequence) for p in result.seed_prompts] == [
            ("assistant", "function_call", 4),
            ("tool", "function_call_output", 5),
            ("assistant", "function_call", 6),
            ("tool", "function_call_output", 7),
        ]
        assert json.loads(json.loads(result.seed_prompts[0].value)["arguments"]) == {"ticket_id": "SEC-1"}
        assert not any(prompt.is_jinja_template for prompt in result.seed_prompts)
        [reference] = result.related_conversations
        assert reference.conversation_type is ConversationType.PREPARATION
        request = send.call_args.kwargs["message"]
        assert json.loads(request.get_value()) == {
            "objective": "Reset the admin password.",
            "tools": [_LOOKUP.model_dump(mode="json")],
            "num_tool_calls": 2,
        }
        assert JsonResponseConfig.from_metadata(metadata=request.message_pieces[0].prompt_metadata).enabled

    async def test_wrong_number_of_tool_calls_is_fed_back_and_repaired_async(self) -> None:
        result, send = await _generate_one_shot_async(
            responses=[
                _history_response([_CALL, _OUTPUT]),
                _history_response([_CALL, _OUTPUT, _SECOND_CALL, _SECOND_OUTPUT], fenced=True),
            ],
            num_turns=2,
        )

        assert len(result.seed_prompts) == 4
        first, second = send.call_args_list
        assert first.kwargs["conversation_id"] == second.kwargs["conversation_id"]
        feedback = second.kwargs["message"].get_value()
        assert feedback.startswith("Your history failed validation.")
        assert "The history has 1 tool calls; write exactly 2, each followed by its result." in feedback

    async def test_invalid_tool_call_is_fed_back_async(self) -> None:
        _, send = await _generate_one_shot_async(
            responses=[
                _history_response([{**_CALL, "name": "delete_ticket"}, _OUTPUT]),
                _history_response([_CALL, _OUTPUT]),
            ],
            num_turns=1,
        )

        feedback = send.call_args_list[1].kwargs["message"].get_value()
        assert "'delete_ticket' is not an available tool. Available tools: ['lookup_ticket']." in feedback

    async def test_unparseable_responses_are_retried_without_spending_an_attempt_async(self) -> None:
        prose = Message.from_prompt(prompt="Sure, here is a history.", role="assistant")
        wrapped = Message.from_prompt(prompt=f"Here it is: {json.dumps({'items': [_CALL, _OUTPUT]})}", role="assistant")

        result, send = await _generate_one_shot_async(responses=[prose, wrapped], num_turns=1, max_attempts=1)

        assert len(result.seed_prompts) == 2
        first, second = send.call_args_list
        assert first.kwargs["message"].get_value() == second.kwargs["message"].get_value()

    async def test_blocked_response_ends_generation_without_retrying_async(self) -> None:
        blocked = MessagePiece(role="assistant", original_value="filtered", response_error="blocked").to_message()

        result, send = await _generate_one_shot_async(responses=[blocked], num_turns=1)

        assert send.await_count == 1
        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED

    async def test_remote_schema_references_are_never_retrieved_async(self) -> None:
        remote_tool = ToolDefinition(
            name="lookup_ticket",
            parameters={"type": "object", "properties": {"ticket_id": {"$ref": "https://example.invalid/id.json"}}},
        )
        with patch("urllib.request.urlopen") as urlopen:
            result, _ = await _generate_one_shot_async(
                responses=[_history_response([_CALL, _OUTPUT])], tools=[remote_tool], num_turns=1
            )

        urlopen.assert_not_called()
        assert result.preparation_failure is None

    @pytest.mark.parametrize(
        ("items", "problem"),
        [
            ([_CALL, {"type": "function_call_output", "call_id": "call_1"}], "not a valid history object"),
            ([_CALL, _OUTPUT, {"type": "message", "role": "assistant", "content": "Done."}], "write no assistant"),
            ([_CALL, _OUTPUT, {"type": "message", "role": "user", "content": "Next."}], "only one user message"),
            ([_CALL, _SECOND_CALL, _OUTPUT], "must be followed immediately by its output"),
            ([_CALL, _OUTPUT, _CALL, _OUTPUT], "call_id 'call_1' is reused"),
            ([{**_CALL, "call_id": " "}, {**_OUTPUT, "call_id": " "}], "function_call.call_id: String should match"),
            ([_CALL, {**_OUTPUT, "call_id": ""}], "function_call_output.call_id: String should have at least 1"),
        ],
    )
    async def test_exhausted_attempts_are_a_preparation_failure_async(
        self, items: list[dict[str, object]], problem: str
    ) -> None:
        result, _ = await _generate_one_shot_async(responses=[_history_response(items)], num_turns=1, max_attempts=1)

        assert result.seed_prompts == []
        assert result.preparation_failure is not None
        assert result.preparation_failure.kind is AttackPreparationFailureKind.ADVERSARIAL_CHAT_INVALID_OUTPUT
        assert "No valid history after 1 attempt(s)" in result.preparation_failure.reason
        assert problem in result.preparation_failure.reason

    async def test_duplicate_tool_names_are_rejected_async(self) -> None:
        with pytest.raises(ValueError, match="Tool names must be unique"):
            await _generate_one_shot_async(responses=[], tools=[_LOOKUP, _LOOKUP])

    @pytest.mark.parametrize(
        "name", ["next_message_system_prompt", "attack_converter_config", "simulated_target", "memory_labels"]
    )
    async def test_arguments_one_shot_does_not_support_are_rejected_async(self, name: str) -> None:
        values = {
            "next_message_system_prompt": SeedPrompt(value="Ask for the objective."),
            "attack_converter_config": AttackConverterConfig(),
            "simulated_target": MockPromptTarget(),
            "memory_labels": {"operation": "test"},
        }

        with pytest.raises(ValueError, match=f"do not support {name}"):
            await _generate_one_shot_async(responses=[], **{name: values[name]})
