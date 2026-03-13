# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.core.config import StrategyConverterConfig
from pyrit.executor.promptgen.anecdoctor import (
    AnecdoctorContext,
    AnecdoctorGenerator,
    AnecdoctorResult,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptChatTarget


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_objective_target() -> PromptChatTarget:
    """Create a mock objective target for testing."""
    mock_target = MagicMock(spec=PromptChatTarget)
    mock_target.set_system_prompt = MagicMock()
    mock_target.get_identifier.return_value = _mock_target_id("mock_objective_target")
    return mock_target


@pytest.fixture
def mock_processing_model() -> PromptChatTarget:
    """Create a mock processing model for testing."""
    mock_model = MagicMock(spec=PromptChatTarget)
    mock_model.set_system_prompt = MagicMock()
    mock_model.get_identifier.return_value = _mock_target_id("MockProcessingModel")
    return mock_model


@pytest.fixture
def mock_prompt_normalizer() -> PromptNormalizer:
    """Create a mock prompt normalizer for testing."""
    mock_normalizer = MagicMock(spec=PromptNormalizer)
    mock_normalizer.send_prompt_async = AsyncMock()
    return mock_normalizer


@pytest.fixture
def sample_evaluation_data() -> list[str]:
    """Sample evaluation data for testing."""
    return [
        "Claim: The earth is flat. Review: FALSE",
        "Claim: Vaccines cause autism. Review: FALSE",
        "Claim: COVID-19 was engineered in a lab. Review: DISPUTED",
    ]


@pytest.fixture
def sample_context(sample_evaluation_data) -> AnecdoctorContext:
    """Create a sample context for testing."""
    return AnecdoctorContext(
        evaluation_data=sample_evaluation_data,
        language="english",
        content_type="viral tweet",
        memory_labels={"test": "label"},
    )


@pytest.fixture
def mock_response() -> Message:
    """Create a mock response for testing."""
    mock_response = MagicMock(spec=Message)
    mock_response.get_piece.return_value = "Generated misinformation content"
    mock_response.get_value.return_value = "Generated misinformation content"
    return mock_response


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorContext:
    """Tests for AnecdoctorContext initialization and validation."""

    def test_context_initialization_with_defaults(self, sample_evaluation_data):
        """Test context initialization with default values."""
        context = AnecdoctorContext(
            evaluation_data=sample_evaluation_data, language="english", content_type="viral tweet"
        )

        assert context.evaluation_data == sample_evaluation_data
        assert context.language == "english"
        assert context.content_type == "viral tweet"
        assert isinstance(context.conversation_id, str)
        assert context.memory_labels == {}

    def test_context_initialization_with_custom_values(self, sample_evaluation_data):
        """Test context initialization with custom values."""
        custom_id = str(uuid.uuid4())
        context = AnecdoctorContext(
            evaluation_data=sample_evaluation_data,
            language="german",
            content_type="news article",
            conversation_id=custom_id,
            memory_labels={"custom": "label"},
        )

        assert context.evaluation_data == sample_evaluation_data
        assert context.language == "german"
        assert context.content_type == "news article"
        assert context.conversation_id == custom_id
        assert context.memory_labels == {"custom": "label"}


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorResult:
    """Tests for AnecdoctorResult."""

    def test_result_initialization(self, mock_response):
        """Test AnecdoctorResult initialization."""
        result = AnecdoctorResult(generated_content=mock_response)

        assert result.generated_content == mock_response


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorInitialization:
    """Tests for AnecdoctorGenerator initialization."""

    def test_init_minimal_parameters(self, mock_objective_target):
        """Test initialization with minimal required parameters."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        assert generator._objective_target == mock_objective_target
        assert generator._processing_model is None
        assert generator._request_converters == []
        assert generator._response_converters == []
        assert isinstance(generator._prompt_normalizer, PromptNormalizer)
        assert hasattr(generator, "_system_prompt_template")

    def test_init_with_processing_model(self, mock_objective_target, mock_processing_model):
        """Test initialization with processing model."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target, processing_model=mock_processing_model)

        assert generator._objective_target == mock_objective_target
        assert generator._processing_model == mock_processing_model

    def test_init_with_custom_normalizer(self, mock_objective_target, mock_prompt_normalizer):
        """Test initialization with custom prompt normalizer."""
        generator = AnecdoctorGenerator(
            objective_target=mock_objective_target, prompt_normalizer=mock_prompt_normalizer
        )

        assert generator._prompt_normalizer == mock_prompt_normalizer

    def test_init_with_converter_config(self, mock_objective_target):
        """Test initialization with converter configuration."""
        mock_converter = MagicMock()
        converter_config = StrategyConverterConfig(
            request_converters=[mock_converter], response_converters=[mock_converter]
        )

        generator = AnecdoctorGenerator(objective_target=mock_objective_target, converter_config=converter_config)

        assert generator._request_converters == [mock_converter]
        assert generator._response_converters == [mock_converter]


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorValidation:
    """Tests for context validation."""

    def test_validate_context_success(self, mock_objective_target, sample_context):
        """Test successful context validation."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        # Should not raise any exception
        generator._validate_context(context=sample_context)

    def test_validate_context_empty_content_type(self, mock_objective_target, sample_context):
        """Test validation fails with empty content type."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        sample_context.content_type = ""

        with pytest.raises(ValueError, match="content_type must be provided in the context"):
            generator._validate_context(context=sample_context)

    def test_validate_context_empty_language(self, mock_objective_target, sample_context):
        """Test validation fails with empty language."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        sample_context.language = ""

        with pytest.raises(ValueError, match="language must be provided in the context"):
            generator._validate_context(context=sample_context)

    def test_validate_context_empty_evaluation_data(self, mock_objective_target, sample_context):
        """Test validation fails with empty evaluation data."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        sample_context.evaluation_data = []

        with pytest.raises(ValueError, match="evaluation_data cannot be empty"):
            generator._validate_context(context=sample_context)


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorSetup:
    """Tests for generator setup."""

    @pytest.mark.asyncio
    async def test_setup_generates_conversation_id(self, mock_objective_target, sample_context):
        """Test setup generates a new conversation ID."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        original_id = sample_context.conversation_id

        await generator._setup_async(context=sample_context)

        # Conversation ID should be updated
        assert sample_context.conversation_id != original_id
        assert isinstance(sample_context.conversation_id, str)

    @pytest.mark.asyncio
    async def test_setup_combines_memory_labels(self, mock_objective_target, sample_context):
        """Test setup combines memory labels from generator and context."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        generator._memory_labels = {"generator": "label"}

        original_labels = sample_context.memory_labels.copy()

        await generator._setup_async(context=sample_context)

        # Should contain both original and generator labels
        expected_labels = {**generator._memory_labels, **original_labels}
        assert sample_context.memory_labels == expected_labels

    @pytest.mark.asyncio
    async def test_setup_formats_system_prompt(self, mock_objective_target, sample_context):
        """Test setup formats system prompt with language and content type."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with patch.object(generator._objective_target, "set_system_prompt") as mock_set:
            await generator._setup_async(context=sample_context)

            mock_set.assert_called_once()
            call_args = mock_set.call_args

            # Verify system prompt contains formatted language and content type
            system_prompt = call_args.kwargs["system_prompt"]
            assert "english" in system_prompt
            assert "viral tweet" in system_prompt


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorExecution:
    """Tests for the main generator execution flow."""

    @pytest.mark.asyncio
    async def test_perform_strategy_without_processing_model(
        self, mock_objective_target, sample_context, mock_response
    ):
        """Test generator execution without processing model (few-shot mode)."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with (
            patch.object(generator, "_prepare_examples_async") as mock_prepare,
            patch.object(generator, "_send_examples_to_target_async") as mock_send,
        ):
            mock_prepare.return_value = "### examples\nFormatted examples"
            mock_send.return_value = mock_response

            result = await generator._perform_async(context=sample_context)

            # Verify method calls
            mock_prepare.assert_called_once_with(context=sample_context)
            mock_send.assert_called_once_with(
                formatted_examples="### examples\nFormatted examples", context=sample_context
            )

            # Verify result
            assert isinstance(result, AnecdoctorResult)
            assert result.generated_content == mock_response

    @pytest.mark.asyncio
    async def test_perform_strategy_with_processing_model(
        self, mock_objective_target, mock_processing_model, sample_context, mock_response
    ):
        """Test generator execution with processing model (knowledge graph mode)."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target, processing_model=mock_processing_model)

        with (
            patch.object(generator, "_extract_knowledge_graph_async") as mock_kg,
            patch.object(generator, "_send_examples_to_target_async") as mock_send,
        ):
            mock_kg.return_value = "Extracted knowledge graph"
            mock_send.return_value = mock_response

            result = await generator._perform_async(context=sample_context)

            # Verify method calls
            mock_kg.assert_called_once_with(context=sample_context)
            mock_send.assert_called_once_with(formatted_examples="Extracted knowledge graph", context=sample_context)

            # Verify result
            assert isinstance(result, AnecdoctorResult)
            assert result.generated_content == mock_response

    @pytest.mark.asyncio
    async def test_perform_strategy_no_response(self, mock_objective_target, sample_context):
        """Test generator execution when no response is received."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with (
            patch.object(generator, "_prepare_examples_async") as mock_prepare,
            patch.object(generator, "_send_examples_to_target_async") as mock_send,
        ):
            mock_prepare.return_value = "### examples\nFormatted examples"
            mock_send.return_value = None

            with pytest.raises(RuntimeError, match="Failed to get response from target model"):
                await generator._perform_async(context=sample_context)

    @pytest.mark.asyncio
    async def test_execute_with_context_full_flow(self, mock_objective_target, sample_context, mock_response):
        """Test full execution flow with context management."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with (
            patch.object(generator, "_prepare_examples_async") as mock_prepare,
            patch.object(generator, "_send_examples_to_target_async") as mock_send,
        ):
            mock_prepare.return_value = "### examples\nFormatted examples"
            mock_send.return_value = mock_response

            result = await generator.execute_with_context_async(context=sample_context)

            # Verify result
            assert isinstance(result, AnecdoctorResult)
            assert result.generated_content == mock_response


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorHelperMethods:
    """Tests for helper methods."""

    def test_format_few_shot_examples(self, mock_objective_target, sample_evaluation_data):
        """Test formatting of few-shot examples."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        result = generator._format_few_shot_examples(evaluation_data=sample_evaluation_data)

        assert "### examples" in result
        for example in sample_evaluation_data:
            assert example in result

    def test_load_prompt_from_yaml(self, mock_objective_target):
        """Test loading prompt from YAML file."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        # Mock the actual YAML content structure
        mock_yaml_content = {"value": "Test prompt template: {language} {type}"}

        with (
            patch("pathlib.Path.read_text", return_value="value: 'Test prompt template: {language} {type}'"),
            patch("yaml.safe_load", return_value=mock_yaml_content),
        ):
            result = generator._load_prompt_from_yaml(yaml_filename="anecdoctor_use_fewshot.yaml")
            assert result == "Test prompt template: {language} {type}"

    @pytest.mark.asyncio
    async def test_prepare_examples_without_processing_model(self, mock_objective_target, sample_context):
        """Test example preparation without processing model."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with patch.object(generator, "_format_few_shot_examples") as mock_format:
            mock_format.return_value = "Formatted examples"

            result = await generator._prepare_examples_async(context=sample_context)

            mock_format.assert_called_once_with(evaluation_data=sample_context.evaluation_data)
            assert result == "Formatted examples"

    @pytest.mark.asyncio
    async def test_prepare_examples_with_processing_model(
        self, mock_objective_target, mock_processing_model, sample_context
    ):
        """Test example preparation with processing model."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target, processing_model=mock_processing_model)

        with patch.object(generator, "_extract_knowledge_graph_async") as mock_extract:
            mock_extract.return_value = "Knowledge graph"

            result = await generator._prepare_examples_async(context=sample_context)

            mock_extract.assert_called_once_with(context=sample_context)
            assert result == "Knowledge graph"

    @pytest.mark.asyncio
    async def test_send_examples_to_target_success(self, mock_objective_target, sample_context, mock_response):
        """Test successful sending of examples to target."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with patch.object(generator._prompt_normalizer, "send_prompt_async") as mock_send:
            mock_send.return_value = mock_response

            result = await generator._send_examples_to_target_async(
                formatted_examples="Test examples", context=sample_context
            )

            assert result == mock_response
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_knowledge_graph(self, mock_objective_target, mock_processing_model, sample_context):
        """Test knowledge graph extraction."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target, processing_model=mock_processing_model)

        mock_kg_response = MagicMock()
        mock_kg_response.get_value.return_value = "Extracted KG data"

        with patch.object(generator._prompt_normalizer, "send_prompt_async") as mock_send:
            mock_send.return_value = mock_kg_response

            result = await generator._extract_knowledge_graph_async(context=sample_context)

            assert result == "Extracted KG data"
            mock_send.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorTeardown:
    """Tests for teardown functionality."""

    @pytest.mark.asyncio
    async def test_teardown_async(self, mock_objective_target, sample_context):
        """Test teardown functionality."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        # Should not raise any exceptions
        await generator._teardown_async(context=sample_context)


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorExecuteAsync:
    """Tests for execute_async overloads."""

    @pytest.mark.asyncio
    async def test_execute_async_with_kwargs(self, mock_objective_target, sample_evaluation_data, mock_response):
        """Test execute_async with keyword arguments."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with (
            patch.object(generator, "_prepare_examples_async") as mock_prepare,
            patch.object(generator, "_send_examples_to_target_async") as mock_send,
        ):
            mock_prepare.return_value = "### examples\nFormatted examples"
            mock_send.return_value = mock_response

            result = await generator.execute_async(
                evaluation_data=sample_evaluation_data, language="english", content_type="viral tweet"
            )

            # Verify result
            assert isinstance(result, AnecdoctorResult)
            assert result.generated_content == mock_response


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_send_examples_failure(self, mock_objective_target, sample_context):
        """Test error handling when sending examples fails."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        with patch.object(generator._prompt_normalizer, "send_prompt_async") as mock_send:
            mock_send.side_effect = Exception("Network error")

            with pytest.raises(Exception, match="Network error"):
                await generator._send_examples_to_target_async(
                    formatted_examples="Test examples", context=sample_context
                )

    @pytest.mark.asyncio
    async def test_knowledge_graph_extraction_failure(
        self, mock_objective_target, mock_processing_model, sample_context
    ):
        """Test error handling when knowledge graph extraction fails."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target, processing_model=mock_processing_model)

        with patch.object(generator._prompt_normalizer, "send_prompt_async") as mock_send:
            mock_send.side_effect = Exception("Processing error")

            with pytest.raises(Exception, match="Processing error"):
                await generator._extract_knowledge_graph_async(context=sample_context)

    @pytest.mark.asyncio
    async def test_validation_error_during_execution(self, mock_objective_target, sample_context):
        """Test validation error during execution."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)
        sample_context.content_type = ""  # Invalid content type

        with pytest.raises(ValueError, match="content_type must be provided"):
            await generator.execute_with_context_async(context=sample_context)


@pytest.mark.usefixtures("patch_central_database")
class TestAnecdoctorGeneratorEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_evaluation_data_handling(self, mock_objective_target):
        """Test handling of empty evaluation data."""
        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        context = AnecdoctorContext(evaluation_data=[], language="english", content_type="viral tweet")

        with pytest.raises(ValueError, match="evaluation_data cannot be empty"):
            generator._validate_context(context=context)

    def test_special_characters_in_data(self, mock_objective_target):
        """Test handling of special characters in evaluation data."""
        evaluation_data = [
            "Claim: Test with émojis 🚀. Review: FALSE",
            "Claim: Special chars @#$%^&*(). Review: TRUE",
            "Claim: Unicode: 测试. Review: DISPUTED",
        ]

        context = AnecdoctorContext(evaluation_data=evaluation_data, language="english", content_type="viral tweet")

        generator = AnecdoctorGenerator(objective_target=mock_objective_target)

        # Should not raise any exceptions
        generator._validate_context(context=context)

        result = generator._format_few_shot_examples(evaluation_data=evaluation_data)
        for data in evaluation_data:
            assert data in result
