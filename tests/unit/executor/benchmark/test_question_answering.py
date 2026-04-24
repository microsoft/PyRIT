# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.benchmark.question_answering import (
    QuestionAnsweringBenchmark,
    QuestionAnsweringBenchmarkContext,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    QuestionAnsweringEntry,
    QuestionChoice,
)
from pyrit.prompt_target import PromptTarget

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
def sample_question_entry() -> QuestionAnsweringEntry:
    """Sample question answering entry for testing."""
    return QuestionAnsweringEntry(
        question="What is the capital of France?",
        answer_type="int",
        correct_answer=1,
        choices=[
            QuestionChoice(index=0, text="London"),
            QuestionChoice(index=1, text="Paris"),
            QuestionChoice(index=2, text="Berlin"),
            QuestionChoice(index=3, text="Madrid"),
        ],
    )


@pytest.fixture
def invalid_question_entry() -> QuestionAnsweringEntry:
    """Invalid question answering entry for error testing."""
    return QuestionAnsweringEntry(
        question="What is the capital of France?",
        answer_type="int",
        correct_answer=5,  # Invalid - not in choices
        choices=[
            QuestionChoice(index=0, text="London"),
            QuestionChoice(index=1, text="Paris"),
        ],
    )


@pytest.fixture
def empty_question_entry() -> QuestionAnsweringEntry:
    """Empty question entry for error testing."""
    return QuestionAnsweringEntry(question="", answer_type="int", correct_answer=0, choices=[])


@pytest.fixture
def sample_benchmark_context(sample_question_entry: QuestionAnsweringEntry) -> QuestionAnsweringBenchmarkContext:
    """Sample benchmark context for testing."""
    return QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)


@pytest.fixture
def mock_prompt_sending_attack() -> AsyncMock:
    """Mock PromptSendingAttack for testing."""
    attack = AsyncMock()
    attack.execute_async = AsyncMock()
    return attack


@pytest.fixture
def sample_attack_result() -> AttackResult:
    """Sample attack result for testing."""
    return AttackResult(
        conversation_id="test-conversation-id",
        objective="Test objective",
        executed_turns=1,
        execution_time_ms=1000,
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test completed successfully",
    )


@pytest.mark.usefixtures("patch_central_database")
class TestQuestionAnsweringBenchmark:
    """Test class for QuestionAnsweringBenchmark core functionality."""

    @pytest.mark.asyncio
    async def test_validate_context_valid_entry(
        self, mock_prompt_target: MagicMock, sample_benchmark_context: QuestionAnsweringBenchmarkContext
    ) -> None:
        """Test context validation with valid entry."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Should not raise any exception
        benchmark._validate_context(context=sample_benchmark_context)

    @pytest.mark.asyncio
    async def test_validate_context_empty_question(
        self, mock_prompt_target: MagicMock, empty_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test context validation with empty question."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)
        context = QuestionAnsweringBenchmarkContext(question_answering_entry=empty_question_entry)

        with pytest.raises(ValueError, match="Question text cannot be empty"):
            benchmark._validate_context(context=context)

    @pytest.mark.asyncio
    async def test_validate_context_invalid_correct_answer(
        self, mock_prompt_target: MagicMock, invalid_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test context validation with invalid correct answer index."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)
        context = QuestionAnsweringBenchmarkContext(question_answering_entry=invalid_question_entry)

        with pytest.raises(ValueError, match="choice index=5"):
            benchmark._validate_context(context=context)

    @pytest.mark.asyncio
    async def test_setup_async_generates_objective_and_prompt(
        self, mock_prompt_target: MagicMock, sample_benchmark_context: QuestionAnsweringBenchmarkContext
    ) -> None:
        """Test that setup_async generates objective and question prompt correctly."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        await benchmark._setup_async(context=sample_benchmark_context)

        # Check that objective was generated
        assert sample_benchmark_context.generated_objective != ""
        assert "What is the capital of France?" in sample_benchmark_context.generated_objective
        assert "Paris" in sample_benchmark_context.generated_objective
        assert "1:" in sample_benchmark_context.generated_objective

        # Check that question prompt was generated
        assert sample_benchmark_context.generated_question_prompt != ""
        assert "What is the capital of France?" in sample_benchmark_context.generated_question_prompt
        assert "Option 0: London" in sample_benchmark_context.generated_question_prompt
        assert "Option 1: Paris" in sample_benchmark_context.generated_question_prompt

        # Check that message was created with metadata
        assert sample_benchmark_context.generated_message is not None
        message_piece = sample_benchmark_context.generated_message.get_piece()
        assert message_piece.original_value == sample_benchmark_context.generated_question_prompt
        assert message_piece.prompt_metadata is not None
        assert message_piece.prompt_metadata["correct_answer_index"] == "1"
        assert message_piece.prompt_metadata["correct_answer"] == "Paris"

    @pytest.mark.asyncio
    async def test_format_question_prompt(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test question prompt formatting."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        formatted_prompt = benchmark._format_question_prompt(sample_question_entry)

        assert "Answer the following question" in formatted_prompt
        assert "What is the capital of France?" in formatted_prompt
        assert "Option 0: London" in formatted_prompt
        assert "Option 1: Paris" in formatted_prompt
        assert "Option 2: Berlin" in formatted_prompt
        assert "Option 3: Madrid" in formatted_prompt

    @pytest.mark.asyncio
    async def test_format_options(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test options formatting."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        options_text = benchmark._format_options(sample_question_entry)

        expected_lines = ["Option 0: London", "Option 1: Paris", "Option 2: Berlin", "Option 3: Madrid"]

        for expected_line in expected_lines:
            assert expected_line in options_text

        # Check that options are separated by newlines
        options_lines = options_text.strip().split("\n")
        assert len(options_lines) == 4

    @pytest.mark.asyncio
    async def test_create_message(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test message creation with metadata."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)
        question_prompt = "Test question prompt"

        message = benchmark._create_message(entry=sample_question_entry, question_prompt=question_prompt)

        assert isinstance(message, Message)
        message_piece = message.get_piece()
        assert message_piece.original_value == question_prompt
        assert message_piece.api_role == "user"
        assert message_piece.prompt_metadata is not None
        assert message_piece.prompt_metadata["correct_answer_index"] == "1"
        assert message_piece.prompt_metadata["correct_answer"] == "Paris"

    @pytest.mark.asyncio
    async def test_perform_async_calls_prompt_sending_attack(
        self,
        mock_prompt_target: MagicMock,
        sample_benchmark_context: QuestionAnsweringBenchmarkContext,
        sample_attack_result: AttackResult,
    ) -> None:
        """Test that perform_async calls the underlying PromptSendingAttack."""
        with patch("pyrit.executor.benchmark.question_answering.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

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

    @pytest.mark.asyncio
    async def test_teardown_async_completes_successfully(
        self, mock_prompt_target: MagicMock, sample_benchmark_context: QuestionAnsweringBenchmarkContext
    ) -> None:
        """Test that teardown_async completes without errors."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Should complete without raising any exceptions
        await benchmark._teardown_async(context=sample_benchmark_context)


@pytest.mark.usefixtures("patch_central_database")
class TestQuestionAnsweringBenchmarkCustomFormatting:
    """Test class for custom formatting options in QuestionAnsweringBenchmark."""

    @pytest.mark.asyncio
    async def test_custom_objective_format_string(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test custom objective format string."""
        custom_objective_format = "Custom objective: {question} -> {answer}"

        benchmark = QuestionAnsweringBenchmark(
            objective_target=mock_prompt_target, objective_format_string=custom_objective_format
        )

        context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)
        await benchmark._setup_async(context=context)

        assert "Custom objective:" in context.generated_objective
        assert "What is the capital of France?" in context.generated_objective
        assert "Paris" in context.generated_objective

    @pytest.mark.asyncio
    async def test_custom_question_format_string(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test custom question format string."""
        custom_question_format = "Please answer: {question}\nChoices: {options}"

        benchmark = QuestionAnsweringBenchmark(
            objective_target=mock_prompt_target, question_asking_format_string=custom_question_format
        )

        context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)
        await benchmark._setup_async(context=context)

        assert "Please answer:" in context.generated_question_prompt
        assert "What is the capital of France?" in context.generated_question_prompt
        assert "Choices:" in context.generated_question_prompt

    @pytest.mark.asyncio
    async def test_custom_options_format_string(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test custom options format string."""
        custom_options_format = "[{index}] - {choice}"

        benchmark = QuestionAnsweringBenchmark(
            objective_target=mock_prompt_target, options_format_string=custom_options_format
        )

        context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)
        await benchmark._setup_async(context=context)

        assert "[0] - London" in context.generated_question_prompt
        assert "[1] - Paris" in context.generated_question_prompt
        assert "[2] - Berlin" in context.generated_question_prompt
        assert "[3] - Madrid" in context.generated_question_prompt


@pytest.mark.usefixtures("patch_central_database")
class TestQuestionAnsweringBenchmarkExecuteAsync:
    """Test class for execute_async method in QuestionAnsweringBenchmark."""

    @pytest.mark.asyncio
    async def test_execute_async_with_required_parameters(
        self,
        mock_prompt_target: MagicMock,
        sample_question_entry: QuestionAnsweringEntry,
        sample_attack_result: AttackResult,
    ) -> None:
        """Test execute_async with only required parameters."""
        with patch("pyrit.executor.benchmark.question_answering.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

            result = await benchmark.execute_async(question_answering_entry=sample_question_entry)

            assert result == sample_attack_result
            mock_attack_instance.execute_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_async_with_optional_parameters(
        self,
        mock_prompt_target: MagicMock,
        sample_question_entry: QuestionAnsweringEntry,
        sample_attack_result: AttackResult,
    ) -> None:
        """Test execute_async with optional parameters."""
        prepended_conversation: list[Message] = []
        memory_labels: dict[str, str] = {"test": "label"}

        with patch("pyrit.executor.benchmark.question_answering.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

            result = await benchmark.execute_async(
                question_answering_entry=sample_question_entry,
                prepended_conversation=prepended_conversation,
                memory_labels=memory_labels,
            )

            assert result == sample_attack_result
            mock_attack_instance.execute_async.assert_called_once()

            call_kwargs = mock_attack_instance.execute_async.call_args.kwargs
            assert call_kwargs["prepended_conversation"] == prepended_conversation
            assert call_kwargs["memory_labels"] == memory_labels

    @pytest.mark.asyncio
    async def test_execute_async_validates_parameters(self, mock_prompt_target: MagicMock) -> None:
        """Test that execute_async validates required parameters."""
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Should raise error when question_answering_entry is missing
        with pytest.raises(ValueError):
            await benchmark.execute_async()


@pytest.mark.usefixtures("patch_central_database")
class TestQuestionAnsweringBenchmarkContextIntegration:
    """Test class for context handling and integration scenarios."""

    @pytest.mark.asyncio
    async def test_context_with_prepended_conversation(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test context with prepended conversation."""
        # Create a proper mock Message
        mock_message_piece = MagicMock(spec=MessagePiece)
        mock_message_piece.conversation_id = "test-conversation"
        mock_message_piece.role = "user"
        mock_message_piece.original_value = "Test message"

        mock_response = MagicMock(spec=Message)
        mock_response.message_pieces = [mock_message_piece]

        prepended_conversation: list[Message] = [mock_response]

        context = QuestionAnsweringBenchmarkContext(
            question_answering_entry=sample_question_entry, prepended_conversation=prepended_conversation
        )

        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Validate and setup context
        benchmark._validate_context(context=context)
        await benchmark._setup_async(context=context)

        # Verify context maintains prepended conversation
        assert context.prepended_conversation == prepended_conversation

    @pytest.mark.asyncio
    async def test_context_with_memory_labels(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test context with memory labels."""
        memory_labels = {"benchmark_type": "qa", "dataset": "test"}

        context = QuestionAnsweringBenchmarkContext(
            question_answering_entry=sample_question_entry, memory_labels=memory_labels
        )

        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Validate and setup context
        benchmark._validate_context(context=context)
        await benchmark._setup_async(context=context)

        # Verify context maintains memory labels
        assert context.memory_labels == memory_labels

    @pytest.mark.asyncio
    async def test_context_generated_fields_initially_empty(
        self, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test that generated fields are initially empty or None."""
        context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)

        assert context.generated_objective == ""
        assert context.generated_question_prompt == ""
        assert context.generated_message is None

    @pytest.mark.asyncio
    async def test_full_workflow_integration(
        self,
        mock_prompt_target: MagicMock,
        sample_question_entry: QuestionAnsweringEntry,
        sample_attack_result: AttackResult,
    ) -> None:
        """Test the full workflow integration from setup to teardown."""
        with patch("pyrit.executor.benchmark.question_answering.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.return_value = sample_attack_result
            mock_attack_class.return_value = mock_attack_instance

            benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)
            context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)

            # Full workflow
            benchmark._validate_context(context=context)
            await benchmark._setup_async(context=context)
            result = await benchmark._perform_async(context=context)
            await benchmark._teardown_async(context=context)

            # Verify all generated fields are populated after setup
            assert context.generated_objective != ""
            assert context.generated_question_prompt != ""
            assert context.generated_message is not None

            # Verify result is correct
            assert result == sample_attack_result


@pytest.mark.usefixtures("patch_central_database")
class TestQuestionAnsweringBenchmarkErrorHandling:
    """Test class for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_question_with_no_choices(self, mock_prompt_target: MagicMock) -> None:
        """Test handling of questions with no choices."""
        entry = QuestionAnsweringEntry(question="What is 2+2?", answer_type="int", correct_answer=4, choices=[])

        context = QuestionAnsweringBenchmarkContext(question_answering_entry=entry)
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        with pytest.raises(ValueError, match="Question must have at least one choice"):
            benchmark._validate_context(context=context)

    @pytest.mark.asyncio
    async def test_single_choice_question(self, mock_prompt_target: MagicMock) -> None:
        """Test handling of questions with only one choice."""
        entry = QuestionAnsweringEntry(
            question="True or False: Python is a programming language.",
            answer_type="int",
            correct_answer=0,
            choices=[QuestionChoice(index=0, text="True")],
        )

        context = QuestionAnsweringBenchmarkContext(question_answering_entry=entry)
        benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)

        # Should validate successfully
        benchmark._validate_context(context=context)
        await benchmark._setup_async(context=context)

        # Check formatting works with single choice
        assert "Option 0: True" in context.generated_question_prompt

    @pytest.mark.asyncio
    async def test_attack_execution_failure(
        self, mock_prompt_target: MagicMock, sample_question_entry: QuestionAnsweringEntry
    ) -> None:
        """Test handling of attack execution failure."""
        with patch("pyrit.executor.benchmark.question_answering.PromptSendingAttack") as mock_attack_class:
            mock_attack_instance = AsyncMock()
            mock_attack_instance.execute_async.side_effect = Exception("Attack failed")
            mock_attack_class.return_value = mock_attack_instance

            benchmark = QuestionAnsweringBenchmark(objective_target=mock_prompt_target)
            context = QuestionAnsweringBenchmarkContext(question_answering_entry=sample_question_entry)

            await benchmark._setup_async(context=context)

            # Should propagate the exception
            with pytest.raises(Exception, match="Attack failed"):
                await benchmark._perform_async(context=context)
