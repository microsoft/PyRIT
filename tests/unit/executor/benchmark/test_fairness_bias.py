# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.benchmark.fairness_bias import (
    FairnessBiasBenchmark,
    FairnessBiasBenchmarkContext,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
)
from pyrit.prompt_target import PromptTarget


def is_spacy_installed():
    try:
        import spacy  # noqa: F401

        return True
    except Exception:
        return False


# Fixtures at the top of the file


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_prompt_target() -> MagicMock:
    """Mock prompt target for testing."""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _mock_target_id("mock_prompt_target")
    return target


@pytest.fixture
def sample_benchmark_context() -> FairnessBiasBenchmarkContext:
    """Sample benchmark context for testing."""
    return FairnessBiasBenchmarkContext(subject="doctor", story_type="short story", num_experiments=1)


@pytest.fixture
def sample_benchmark_context_with_custom_objective() -> FairnessBiasBenchmarkContext:
    """Sample benchmark context with custom objective for testing."""
    return FairnessBiasBenchmarkContext(
        subject="teacher",
        story_type="narrative",
        num_experiments=2,
        objective="Write a detailed story about a teacher that includes character names",
    )


@pytest.fixture
def sample_attack_result() -> AttackResult:
    """Sample attack result for testing."""
    # Mock score object
    mock_score = MagicMock()
    mock_score.score_category = "biased"
    mock_score.score_rationale = "Test rationale"

    result = AttackResult(
        conversation_id="test-conversation-id",
        objective="Test objective",
        executed_turns=1,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test completed successfully",
    )
    result.last_score = mock_score
    return result


@pytest.fixture
def mock_conversation_pieces() -> list[Message]:
    """Mock conversation pieces for memory testing."""
    return [
        Message(
            message_pieces=[MessagePiece(role="user", original_value="Test prompt", converted_value="Test prompt")]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="Sarah Johnson is a skilled physician. She works at the local hospital.",
                    converted_value="Sarah Johnson is a skilled physician. She works at the local hospital.",
                )
            ]
        ),
    ]


@pytest.mark.usefixtures("patch_central_database")
class TestFairnessBiasBenchmark:
    """Test class for FairnessBiasBenchmark core functionality."""

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_init_with_required_parameters(self, mock_prompt_target: MagicMock) -> None:
        """Test initialization with required parameters."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        assert benchmark._objective_target == mock_prompt_target
        assert benchmark._prompt_sending_attack is not None

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_init_with_optional_parameters(self, mock_prompt_target: MagicMock) -> None:
        """Test initialization with optional parameters."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target, max_attempts_on_failure=3)

        assert benchmark._objective_target == mock_prompt_target

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_validate_context_valid_entry(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: FairnessBiasBenchmarkContext,
    ) -> None:
        """Test context validation with valid entry."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        # Should not raise any exception
        benchmark._validate_context(context=sample_benchmark_context)

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_validate_context_empty_subject(self, mock_prompt_target: MagicMock) -> None:
        """Test context validation with empty subject."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)
        context = FairnessBiasBenchmarkContext(subject="", story_type="story", num_experiments=1)

        with pytest.raises(ValueError, match="Subject cannot be empty"):
            benchmark._validate_context(context=context)

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_validate_context_invalid_num_experiments(self, mock_prompt_target: MagicMock) -> None:
        """Test context validation with invalid number of experiments."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)
        context = FairnessBiasBenchmarkContext(subject="doctor", story_type="story", num_experiments=0)

        with pytest.raises(ValueError, match="Number of experiments must be at least 1"):
            benchmark._validate_context(context=context)

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_setup_async_generates_default_objective(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: FairnessBiasBenchmarkContext,
    ) -> None:
        """Test that setup_async generates default objective when none provided."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        await benchmark._setup_async(context=sample_benchmark_context)

        # Check that default objective was generated
        assert sample_benchmark_context.generated_objective != ""
        assert "Generate a short story about a doctor" in sample_benchmark_context.generated_objective
        assert "includes a first and last name" in sample_benchmark_context.generated_objective
        assert "uses pronouns appropriately" in sample_benchmark_context.generated_objective

        # Check that story prompt was generated
        assert sample_benchmark_context.generated_story_prompt != ""
        assert "Write me a short story about a doctor" in sample_benchmark_context.generated_story_prompt

        # Check that message was created
        assert sample_benchmark_context.generated_message is not None
        message_piece = sample_benchmark_context.generated_message.get_piece()
        assert message_piece.original_value == sample_benchmark_context.generated_story_prompt
        assert message_piece.api_role == "user"

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_setup_async_uses_custom_objective(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context_with_custom_objective: FairnessBiasBenchmarkContext,
    ) -> None:
        """Test that setup_async uses provided custom objective."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        await benchmark._setup_async(context=sample_benchmark_context_with_custom_objective)

        # Check that custom objective was used
        expected_objective = "Write a detailed story about a teacher that includes character names"
        assert sample_benchmark_context_with_custom_objective.generated_objective == expected_objective

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_format_story_prompt(self, mock_prompt_target: MagicMock) -> None:
        """Test story prompt formatting."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        formatted_prompt = benchmark._format_story_prompt(subject="engineer", story_type="biography")

        assert "Write me a biography about a engineer" in formatted_prompt
        assert "Give the engineer a first and last name" in formatted_prompt
        assert "use pronouns to refer to the engineer" in formatted_prompt

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_extract_name_successful(self, mock_prompt_target: MagicMock) -> None:
        """Test successful name extraction."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        response_with_name = "Sarah Johnson is a skilled physician. She works at the hospital."
        extracted_name = benchmark._extract_name(response_with_name)

        assert extracted_name in ["Sarah Johnson"]

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_extract_name_no_name_found(self, mock_prompt_target: MagicMock) -> None:
        """Test name extraction when no name is found."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        response_without_name = "This is a story about someone who works at a hospital."
        extracted_name = benchmark._extract_name(response_without_name)

        assert extracted_name is None

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_setup_creates_message(
        self, mock_prompt_target: MagicMock, sample_benchmark_context: FairnessBiasBenchmarkContext
    ) -> None:
        """Test that setup creates a message with the story prompt."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        await benchmark._setup_async(context=sample_benchmark_context)

        assert sample_benchmark_context.generated_message is not None
        assert isinstance(sample_benchmark_context.generated_message, Message)
        message_piece = sample_benchmark_context.generated_message.get_piece()
        assert message_piece.original_value == sample_benchmark_context.generated_story_prompt
        assert message_piece.api_role == "user"

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_perform_async_calls_prompt_sending_attack(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: FairnessBiasBenchmarkContext,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test that perform_async calls the underlying PromptSendingAttack."""
        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                # Setup context first
                await benchmark._setup_async(context=sample_benchmark_context)

                # Perform the attack
                result = await benchmark._perform_async(context=sample_benchmark_context)

                # Verify PromptSendingAttack was called with correct parameters
                mock_attack_instance.execute_async.assert_called_once()
                call_kwargs = mock_attack_instance.execute_async.call_args.kwargs

                assert call_kwargs["objective"] == sample_benchmark_context.generated_objective
                # Check that next_message was passed (from generated_message)
                assert "next_message" in call_kwargs
                assert call_kwargs["prepended_conversation"] == sample_benchmark_context.prepended_conversation
                assert call_kwargs["memory_labels"] == sample_benchmark_context.memory_labels

                # Verify result is returned correctly
                assert result == sample_attack_result

                # Verify experiment results were stored
                assert len(sample_benchmark_context.experiment_results) == 1
                experiment_result = sample_benchmark_context.experiment_results[0]
                assert experiment_result["experiment_number"] == "1"
                assert experiment_result["subject"] == "doctor"
                assert experiment_result["story_type"] == "short story"
                assert experiment_result["generated_name"] == "Sarah Johnson"
                assert experiment_result["score_category"] == "biased"
                assert experiment_result["score_rationale"] == "Test rationale"

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_get_experiment_summary_with_results(self, mock_prompt_target: MagicMock) -> None:
        """Test experiment summary generation with results."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        context = FairnessBiasBenchmarkContext(subject="nurse", story_type="story", num_experiments=2)
        context.experiment_results = [
            {
                "experiment_number": "1",
                "subject": "nurse",
                "story_type": "story",
                "generated_name": "Maria Garcia",
                "score_category": "biased",
                "score_rationale": "Test rationale 1",
            },
            {
                "experiment_number": "2",
                "subject": "nurse",
                "story_type": "story",
                "generated_name": "Not found",
                "score_category": "unbiased",
                "score_rationale": "Test rationale 2",
            },
        ]

        summary = benchmark.get_experiment_summary(context=context)

        assert summary["subject"] == "nurse"
        assert summary["story_type"] == "story"
        assert summary["total_experiments"] == 2
        assert summary["score_distribution"] == {"biased": 1, "unbiased": 1}
        assert summary["score_proportions"]["biased"] == "50.00%"
        assert summary["score_proportions"]["unbiased"] == "50.00%"
        assert summary["names_extracted"] == ["Maria Garcia"]

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_get_experiment_summary_no_results(self, mock_prompt_target: MagicMock) -> None:
        """Test experiment summary generation with no results."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        context = FairnessBiasBenchmarkContext(subject="teacher", story_type="narrative", num_experiments=1)
        # No experiment results

        summary = benchmark.get_experiment_summary(context=context)

        assert summary["error"] == "No experiment results available"

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_get_last_context(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: FairnessBiasBenchmarkContext,
    ) -> None:
        """Test getting the last context."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        # Initially should return None
        assert benchmark.get_last_context() is None

        # After teardown, should return the context
        await benchmark._teardown_async(context=sample_benchmark_context)
        last_context = benchmark.get_last_context()
        assert last_context == sample_benchmark_context

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_teardown_async_stores_context(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: FairnessBiasBenchmarkContext,
    ) -> None:
        """Test that teardown_async stores the context."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        # Should complete without raising any exceptions
        await benchmark._teardown_async(context=sample_benchmark_context)
        assert benchmark._last_context == sample_benchmark_context


@pytest.mark.usefixtures("patch_central_database")
class TestFairnessBiasBenchmarkExecuteAsync:
    """Test class for execute_async method in FairnessBiasBenchmark."""

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_execute_async_with_required_parameters(
        self,
        mock_prompt_target: MagicMock,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test execute_async with only required parameters."""
        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                result = await benchmark.execute_async(subject="doctor", story_type="biography")

                assert result == sample_attack_result
                mock_attack_instance.execute_async.assert_called_once()

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_execute_async_with_optional_parameters(
        self,
        mock_prompt_target: MagicMock,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test execute_async with optional parameters."""
        prepended_conversation: list[Message] = []
        memory_labels: dict[str, str] = {"test": "label"}
        custom_objective = "Custom story objective"

        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                result = await benchmark.execute_async(
                    subject="engineer",
                    story_type="tale",
                    num_experiments=3,
                    objective=custom_objective,
                    prepended_conversation=prepended_conversation,
                    memory_labels=memory_labels,
                )

                assert result == sample_attack_result
                # Should be called 3 times for 3 experiments
                assert mock_attack_instance.execute_async.call_count == 3

                call_kwargs = mock_attack_instance.execute_async.call_args.kwargs
                assert call_kwargs["prepended_conversation"] == prepended_conversation
                assert call_kwargs["memory_labels"] == memory_labels
                assert call_kwargs["objective"] == custom_objective

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_execute_async_multiple_experiments(
        self,
        mock_prompt_target: MagicMock,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test execute_async with multiple experiments."""
        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                result = await benchmark.execute_async(subject="teacher", story_type="narrative", num_experiments=3)

                assert result == sample_attack_result
                # Should be called 3 times for 3 experiments
                assert mock_attack_instance.execute_async.call_count == 3

                # Check that the context has results from all experiments
                last_context = benchmark.get_last_context()
                assert last_context is not None
                assert len(last_context.experiment_results) == 3

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_perform_async_fallback_sets_atomic_attack_identifier(
        self,
        mock_prompt_target: MagicMock,
    ) -> None:
        """Test that the fallback AttackResult (no experiments) has atomic_attack_identifier set correctly."""
        benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

        # Create a context with 0 experiments so the fallback path is taken
        context = FairnessBiasBenchmarkContext(subject="doctor", story_type="short story", num_experiments=0)
        context.generated_objective = "Test objective"

        result = await benchmark._perform_async(context=context)

        assert result.atomic_attack_identifier is not None
        assert result.atomic_attack_identifier.class_name == "AtomicAttack"
        assert result.get_attack_strategy_identifier() == ComponentIdentifier.of(benchmark)


@pytest.mark.usefixtures("patch_central_database")
class TestFairnessBiasBenchmarkIntegration:
    """Test class for integration scenarios."""

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_full_benchmark_workflow(
        self,
        mock_prompt_target: MagicMock,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test full benchmark workflow from start to finish."""
        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                # Execute the benchmark
                result = await benchmark.execute_async(subject="scientist", story_type="biography", num_experiments=2)

                # Verify the workflow completed successfully
                assert result == sample_attack_result

                # Get the final context and verify it has all expected data
                final_context = benchmark.get_last_context()
                assert final_context is not None
                assert final_context.subject == "scientist"
                assert final_context.story_type == "biography"
                assert final_context.num_experiments == 2
                assert len(final_context.experiment_results) == 2

                # Verify summary can be generated
                summary = benchmark.get_experiment_summary(context=final_context)
                assert summary["subject"] == "scientist"
                assert summary["story_type"] == "biography"
                assert summary["total_experiments"] == 2

    @pytest.mark.skipif(not is_spacy_installed(), reason="spacy is not installed")
    async def test_benchmark_with_memory_labels(
        self,
        mock_prompt_target: MagicMock,
        sample_attack_result: AttackResult,
        mock_conversation_pieces: list[Message],
    ) -> None:
        """Test benchmark execution with memory labels."""
        memory_labels = {"experiment_type": "fairness_test", "model": "test_model"}

        with patch("pyrit.executor.benchmark.fairness_bias.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            with patch("pyrit.executor.benchmark.fairness_bias.CentralMemory") as mock_memory_class:
                mock_memory_instance = MagicMock()
                mock_memory_instance.get_conversation.return_value = mock_conversation_pieces
                mock_memory_class.get_memory_instance.return_value = mock_memory_instance

                benchmark = FairnessBiasBenchmark(objective_target=mock_prompt_target)

                await benchmark.execute_async(subject="artist", story_type="profile", memory_labels=memory_labels)

                # Verify memory labels were passed correctly
                call_kwargs = mock_attack_instance.execute_async.call_args.kwargs
                assert call_kwargs["memory_labels"] == memory_labels
