# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.workflow.xpia import (
    XPIAContext,
    XPIAResult,
    XPIAStatus,
    XPIAWorkflow,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece, Score
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer

# Shared fixtures for all test classes


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_attack_setup_target() -> MagicMock:
    """Create a mock attack setup target."""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _mock_target_id("mock_attack_setup_target")
    return target


@pytest.fixture
def mock_scorer() -> MagicMock:
    """Create a mock scorer."""
    scorer = MagicMock(spec=Scorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id()
    return scorer


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    """Create a mock prompt normalizer."""
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def valid_message() -> Message:
    """Create a valid message for testing."""
    return Message.from_prompt(prompt="Test attack content", role="user")


@pytest.fixture
def mock_processing_callback() -> AsyncMock:
    """Create a mock processing callback."""
    callback = AsyncMock()
    callback.return_value = "Processing response"
    return callback


@pytest.fixture
def valid_context(valid_message: Message, mock_processing_callback: AsyncMock) -> XPIAContext:
    """Create a valid XPIA context for testing."""
    return XPIAContext(
        attack_content=valid_message,
        processing_callback=mock_processing_callback,
        memory_labels={"test": "label"},
    )


@pytest.fixture
def workflow(
    mock_attack_setup_target: MagicMock, mock_scorer: MagicMock, mock_prompt_normalizer: MagicMock
) -> XPIAWorkflow:
    """Create an XPIA workflow instance for testing."""
    return XPIAWorkflow(
        attack_setup_target=mock_attack_setup_target, scorer=mock_scorer, prompt_normalizer=mock_prompt_normalizer
    )


@pytest.mark.usefixtures("patch_central_database")
class TestXPIAWorkflowValidation:
    """Test class for XPIA workflow context validation."""

    def test_validate_context_with_valid_context(self, workflow: XPIAWorkflow, valid_context: XPIAContext) -> None:
        """Test that validation passes with a valid context."""
        # Should not raise any exception
        workflow._validate_context(context=valid_context)

    def test_validate_context_missing_attack_content_raises_error(
        self, workflow: XPIAWorkflow, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that validation fails when attack_content is None."""
        context = XPIAContext(attack_content=None, processing_callback=mock_processing_callback)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="attack_content: Message must be provided"):
            workflow._validate_context(context=context)

    def test_validate_context_empty_message_raises_error(
        self, workflow: XPIAWorkflow, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that validation fails when message has no pieces."""
        context = XPIAContext(attack_content=None, processing_callback=mock_processing_callback)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="attack_content: Message must be provided"):
            workflow._validate_context(context=context)

    def test_validate_context_multiple_message_pieces_raises_error(
        self, workflow: XPIAWorkflow, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that validation fails when message has multiple pieces."""
        multiple_pieces_message = Message(
            message_pieces=[
                MessagePiece(role="user", original_value="First", conversation_id="conv1"),
                MessagePiece(role="user", original_value="Second", conversation_id="conv1"),
            ],
        )
        context = XPIAContext(attack_content=multiple_pieces_message, processing_callback=mock_processing_callback)

        with pytest.raises(ValueError, match="attack_content: Exactly one message piece must be provided"):
            workflow._validate_context(context=context)

    def test_validate_context_non_text_message_raises_error(
        self, workflow: XPIAWorkflow, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that validation fails when message piece is not text type."""
        non_text_message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    converted_value="image.jpg",
                    original_value="image.jpg",
                    converted_value_data_type="image_path",
                    original_value_data_type="image_path",
                ),
            ],
        )
        context = XPIAContext(attack_content=non_text_message, processing_callback=mock_processing_callback)

        with pytest.raises(ValueError, match="attack_content: Message piece must be of type 'text'"):
            workflow._validate_context(context=context)

    def test_validate_context_missing_processing_callback_raises_error(
        self, workflow: XPIAWorkflow, valid_message: Message
    ) -> None:
        """Test that validation fails when processing_callback is None."""
        context = XPIAContext(attack_content=valid_message, processing_callback=None)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="processing_callback is required"):
            workflow._validate_context(context=context)


@pytest.mark.usefixtures("patch_central_database")
class TestXPIAWorkflowPerform:
    """Test class for XPIA workflow perform method."""

    async def test_perform_async_complete_workflow_with_scorer(
        self,
        workflow: XPIAWorkflow,
        valid_message: Message,
        mock_prompt_normalizer: MagicMock,
        mock_scorer: MagicMock,
    ) -> None:
        """Test complete workflow execution with scorer."""
        # Create a specific mock processing callback for this test
        mock_processing_callback = AsyncMock()
        mock_processing_callback.return_value = "Processing response"

        # Create context with the mock callback
        context = XPIAContext(
            attack_content=valid_message,
            processing_callback=mock_processing_callback,
            memory_labels={"test": "label"},
        )

        # Setup mock responses
        mock_response = MagicMock()
        mock_response.get_value.return_value = "Attack setup response"
        mock_prompt_normalizer.send_prompt_async.return_value = mock_response

        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = 0.8
        mock_scorer.score_text_async.return_value = [mock_score]

        # Execute workflow
        result = await workflow._perform_async(context=context)

        # Verify result
        assert isinstance(result, XPIAResult)
        assert result.processing_conversation_id == context.processing_conversation_id
        assert result.processing_response == "Processing response"
        assert result.score == mock_score
        assert result.attack_setup_response == "Attack setup response"

        # Verify method calls
        mock_prompt_normalizer.send_prompt_async.assert_called_once()
        mock_processing_callback.assert_called_once()
        mock_scorer.score_text_async.assert_called_once_with("Processing response")

    async def test_perform_async_workflow_without_scorer(
        self,
        mock_attack_setup_target: MagicMock,
        mock_prompt_normalizer: MagicMock,
        valid_message: Message,
    ) -> None:
        """Test workflow execution without scorer."""
        # Create a specific mock processing callback for this test
        mock_processing_callback = AsyncMock()
        mock_processing_callback.return_value = "Processing response"

        # Create context with the mock callback
        context = XPIAContext(
            attack_content=valid_message,
            processing_callback=mock_processing_callback,
            memory_labels={"test": "label"},
        )

        # Create workflow without scorer
        workflow = XPIAWorkflow(
            attack_setup_target=mock_attack_setup_target, scorer=None, prompt_normalizer=mock_prompt_normalizer
        )

        # Setup mock responses
        mock_response = MagicMock()
        mock_response.get_value.return_value = "Attack setup response"
        mock_prompt_normalizer.send_prompt_async.return_value = mock_response

        # Execute workflow
        result = await workflow._perform_async(context=context)

        # Verify result
        assert isinstance(result, XPIAResult)
        assert result.processing_conversation_id == context.processing_conversation_id
        assert result.processing_response == "Processing response"
        assert result.score is None
        assert result.attack_setup_response == "Attack setup response"

        # Verify method calls
        mock_prompt_normalizer.send_prompt_async.assert_called_once()
        mock_processing_callback.assert_called_once()

    async def test_perform_async_scorer_returns_empty_list(
        self,
        workflow: XPIAWorkflow,
        valid_context: XPIAContext,
        mock_prompt_normalizer: MagicMock,
        mock_scorer: MagicMock,
    ) -> None:
        """Test workflow when scorer returns empty list."""
        # Setup mock responses
        mock_response = MagicMock()
        mock_response.get_value.return_value = "Attack setup response"
        mock_prompt_normalizer.send_prompt_async.return_value = mock_response

        mock_scorer.score_text_async.return_value = []

        # Execute workflow
        result = await workflow._perform_async(context=valid_context)

        # Verify result
        assert isinstance(result, XPIAResult)
        assert result.processing_conversation_id == valid_context.processing_conversation_id
        assert result.processing_response == "Processing response"
        assert result.score is None
        assert result.attack_setup_response == "Attack setup response"

    async def test_perform_async_scorer_raises_exception(
        self,
        workflow: XPIAWorkflow,
        valid_context: XPIAContext,
        mock_prompt_normalizer: MagicMock,
        mock_scorer: MagicMock,
    ) -> None:
        """Test workflow when scorer raises an exception."""
        # Setup mock responses
        mock_response = MagicMock()
        mock_response.get_value.return_value = "Attack setup response"
        mock_prompt_normalizer.send_prompt_async.return_value = mock_response

        mock_scorer.score_text_async.side_effect = Exception("Scoring error")

        # Execute workflow
        result = await workflow._perform_async(context=valid_context)

        # Verify result
        assert isinstance(result, XPIAResult)
        assert result.processing_conversation_id == valid_context.processing_conversation_id
        assert result.processing_response == "Processing response"
        assert result.score is None
        assert result.attack_setup_response == "Attack setup response"

    async def test_setup_attack_async_calls_prompt_normalizer_correctly(
        self, workflow: XPIAWorkflow, valid_context: XPIAContext, mock_prompt_normalizer: MagicMock
    ) -> None:
        """Test that setup attack calls prompt normalizer with correct parameters."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.get_value.return_value = "Attack setup response"
        mock_prompt_normalizer.send_prompt_async.return_value = mock_response

        # Execute setup attack
        response = await workflow._setup_attack_async(context=valid_context)

        # Verify response
        assert response == "Attack setup response"

        # Verify prompt normalizer was called with correct parameters
        call_args = mock_prompt_normalizer.send_prompt_async.call_args
        # Check that message was passed (converted from seed_group)
        assert "message" in call_args.kwargs
        assert call_args.kwargs["target"] == workflow._attack_setup_target
        assert call_args.kwargs["labels"] == valid_context.memory_labels
        assert call_args.kwargs["conversation_id"] == valid_context.attack_setup_target_conversation_id

    @patch("pyrit.executor.workflow.xpia.CentralMemory")
    async def test_execute_processing_async_adds_to_memory(
        self, mock_memory_class: MagicMock, workflow: XPIAWorkflow, valid_context: XPIAContext
    ) -> None:
        """Test that execute processing adds response to memory."""
        # Setup mock memory
        mock_memory_instance = MagicMock()
        mock_memory_class.get_memory_instance.return_value = mock_memory_instance

        # Patch the workflow's _memory attribute to use our mock
        workflow._memory = mock_memory_instance

        # Execute processing
        response = await workflow._execute_processing_async(context=valid_context)

        # Verify response
        assert response == "Processing response"

        # Verify memory addition
        mock_memory_instance.add_message_to_memory.assert_called_once()
        call_args = mock_memory_instance.add_message_to_memory.call_args
        assert call_args.kwargs["request"] is not None
        assert isinstance(call_args.kwargs["request"], Message)

    async def test_score_response_async_with_no_scorer(
        self, mock_attack_setup_target: MagicMock, mock_prompt_normalizer: MagicMock
    ) -> None:
        """Test scoring when no scorer is provided."""
        workflow = XPIAWorkflow(
            attack_setup_target=mock_attack_setup_target, scorer=None, prompt_normalizer=mock_prompt_normalizer
        )

        result = await workflow._score_response_async(processing_response="test response")

        assert result is None


@pytest.mark.usefixtures("patch_central_database")
class TestXPIAWorkflowExecution:
    """Test class for XPIA workflow execution methods."""

    async def test_execute_async_with_valid_parameters(
        self,
        mock_attack_setup_target: MagicMock,
        mock_scorer: MagicMock,
        valid_message: Message,
        mock_processing_callback: AsyncMock,
    ) -> None:
        """Test execute_async with valid parameters."""
        # Create workflow with mocked PromptNormalizer
        with patch("pyrit.executor.workflow.xpia.PromptNormalizer") as mock_normalizer_class:
            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_class.return_value = mock_normalizer

            workflow = XPIAWorkflow(attack_setup_target=mock_attack_setup_target, scorer=mock_scorer)

            with (
                patch.object(workflow, "_perform_async") as mock_perform,
                patch.object(workflow, "_validate_context") as mock_validate,
                patch.object(workflow, "_setup_async") as mock_setup,
                patch.object(workflow, "_teardown_async") as mock_teardown,
            ):
                # Setup mock return value
                expected_result = XPIAResult(
                    processing_conversation_id="test-conversation-id",
                    processing_response="test response",
                    score=None,
                    attack_setup_response="setup response",
                )
                mock_perform.return_value = expected_result

                # Execute workflow
                result = await workflow.execute_async(
                    attack_content=valid_message,
                    processing_callback=mock_processing_callback,
                    memory_labels={"test": "label"},
                )

                # Verify result
                assert result == expected_result

                # Verify lifecycle methods were called
                mock_validate.assert_called_once()
                mock_setup.assert_called_once()
                mock_perform.assert_called_once()
                mock_teardown.assert_called_once()

    async def test_execute_async_invalid_attack_content_type_raises_error(
        self, mock_attack_setup_target: MagicMock, mock_scorer: MagicMock, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that execute_async raises error with invalid attack_content type."""
        # Create workflow with mocked PromptNormalizer
        with patch("pyrit.executor.workflow.xpia.PromptNormalizer") as mock_normalizer_class:
            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_class.return_value = mock_normalizer

            workflow = XPIAWorkflow(attack_setup_target=mock_attack_setup_target, scorer=mock_scorer)

            with pytest.raises(TypeError):
                await workflow.execute_async(
                    attack_content="invalid_type",  # Should be Message
                    processing_callback=mock_processing_callback,
                )

    async def test_execute_async_invalid_processing_callback_type_raises_error(
        self, mock_attack_setup_target: MagicMock, mock_scorer: MagicMock, valid_message: Message
    ) -> None:
        """Test that execute_async raises error with invalid processing_callback type."""
        # Create workflow with mocked PromptNormalizer
        with patch("pyrit.executor.workflow.xpia.PromptNormalizer") as mock_normalizer_class:
            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_class.return_value = mock_normalizer

            workflow = XPIAWorkflow(attack_setup_target=mock_attack_setup_target, scorer=mock_scorer)

            with pytest.raises(TypeError, match="processing_callback must be callable"):
                await workflow.execute_async(
                    attack_content=valid_message,
                    processing_callback="not_callable",  # Should be callable
                )

    async def test_execute_async_invalid_memory_labels_type_raises_error(
        self,
        mock_attack_setup_target: MagicMock,
        mock_scorer: MagicMock,
        valid_message: Message,
        mock_processing_callback: AsyncMock,
    ) -> None:
        """Test that execute_async raises error with invalid memory_labels type."""
        # Create workflow with mocked PromptNormalizer
        with patch("pyrit.executor.workflow.xpia.PromptNormalizer") as mock_normalizer_class:
            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_class.return_value = mock_normalizer

            workflow = XPIAWorkflow(attack_setup_target=mock_attack_setup_target, scorer=mock_scorer)

            with pytest.raises(TypeError):
                await workflow.execute_async(
                    attack_content=valid_message,
                    processing_callback=mock_processing_callback,
                    memory_labels="invalid_type",  # Should be dict
                )

    async def test_execute_async_missing_required_attack_content_raises_error(
        self, mock_attack_setup_target: MagicMock, mock_scorer: MagicMock, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that execute_async raises error when attack_content is missing."""
        # Create workflow with mocked PromptNormalizer
        with patch("pyrit.executor.workflow.xpia.PromptNormalizer") as mock_normalizer_class:
            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_class.return_value = mock_normalizer

            workflow = XPIAWorkflow(attack_setup_target=mock_attack_setup_target, scorer=mock_scorer)

            with pytest.raises(ValueError):
                await workflow.execute_async(
                    processing_callback=mock_processing_callback
                    # attack_content is required but missing
                )

    async def test_setup_async_generates_conversation_ids(
        self, workflow: XPIAWorkflow, valid_message: Message, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that setup_async generates conversation IDs and combines memory labels."""
        context = XPIAContext(
            attack_content=valid_message,
            processing_callback=mock_processing_callback,
            memory_labels={"test": "label"},
        )

        # Store original IDs to verify they change
        original_attack_id = context.attack_setup_target_conversation_id
        original_processing_id = context.processing_conversation_id

        await workflow._setup_async(context=context)

        # Verify IDs were regenerated
        assert context.attack_setup_target_conversation_id != original_attack_id
        assert context.processing_conversation_id != original_processing_id

        # Verify UUIDs are valid
        uuid.UUID(context.attack_setup_target_conversation_id)
        uuid.UUID(context.processing_conversation_id)

        # Verify memory labels were combined
        assert "test" in context.memory_labels

    async def test_teardown_async_completes_successfully(
        self, workflow: XPIAWorkflow, valid_message: Message, mock_processing_callback: AsyncMock
    ) -> None:
        """Test that teardown_async completes without errors."""
        context = XPIAContext(attack_content=valid_message, processing_callback=mock_processing_callback)

        # Should not raise any exception
        await workflow._teardown_async(context=context)


@pytest.mark.usefixtures("patch_central_database")
class TestXPIAResult:
    """Test class for XPIAResult properties."""

    def test_success_property_with_positive_score(self) -> None:
        """Test success property returns True for positive score."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = 0.8

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.success is True

    def test_success_property_with_zero_score(self) -> None:
        """Test success property returns False for zero score."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = 0.0

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.success is False

    def test_success_property_with_negative_score(self) -> None:
        """Test success property returns False for negative score."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = -0.5

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.success is False

    def test_success_property_with_no_score(self) -> None:
        """Test success property returns False when no score is provided."""
        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=None)

        assert result.success is False

    def test_success_property_with_non_numeric_score(self) -> None:
        """Test success property returns False for non-numeric score."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = "invalid"

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.success is False

    def test_status_property_success(self) -> None:
        """Test status property returns SUCCESS for successful attack."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = 0.8

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.status == XPIAStatus.SUCCESS

    def test_status_property_failure(self) -> None:
        """Test status property returns FAILURE for failed attack."""
        mock_score = MagicMock(spec=Score)
        mock_score.get_value.return_value = 0.0

        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=mock_score)

        assert result.status == XPIAStatus.FAILURE

    def test_status_property_unknown(self) -> None:
        """Test status property returns UNKNOWN when no score is provided."""
        result = XPIAResult(processing_conversation_id="test-id", processing_response="test response", score=None)

        assert result.status == XPIAStatus.UNKNOWN


@pytest.mark.usefixtures("patch_central_database")
class TestXPIAGuards:
    """Tests for type-narrowing guards in XPIA workflow."""

    async def test_execute_processing_raises_when_callback_is_none(
        self,
    ) -> None:
        """Test that _execute_processing_async raises ValueError when processing_callback is None."""
        mock_target = MagicMock(spec=PromptTarget)
        mock_target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockTarget", class_module="test_module"
        )
        workflow = XPIAWorkflow(attack_setup_target=mock_target)

        attack_msg = Message(message_pieces=[MessagePiece(role="user", original_value="attack content")])
        context = XPIAContext(attack_content=attack_msg, processing_callback=None)

        with pytest.raises(ValueError, match="processing_callback is not set"):
            await workflow._execute_processing_async(context=context)

    async def test_execute_processing_raises_when_memory_is_none(
        self,
    ) -> None:
        """Test that _execute_processing_async raises RuntimeError when memory is None."""
        mock_target = MagicMock(spec=PromptTarget)
        mock_target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockTarget", class_module="test_module"
        )
        workflow = XPIAWorkflow(attack_setup_target=mock_target)
        workflow._memory = None

        mock_callback = AsyncMock(return_value="response")
        attack_msg = Message(message_pieces=[MessagePiece(role="user", original_value="attack content")])
        context = XPIAContext(attack_content=attack_msg, processing_callback=mock_callback)

        with pytest.raises(RuntimeError, match="Memory not initialized"):
            await workflow._execute_processing_async(context=context)

    async def test_xpia_test_setup_raises_when_processing_prompt_is_none(
        self,
    ) -> None:
        """Test that the process_async closure raises RuntimeError when processing_prompt is None."""
        from pyrit.executor.workflow.xpia import XPIATestWorkflow

        mock_target = MagicMock(spec=PromptTarget)
        mock_target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockTarget", class_module="test_module"
        )
        mock_processing_target = MagicMock(spec=PromptTarget)
        mock_processing_target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockProcessingTarget", class_module="test_module"
        )
        mock_scorer = MagicMock(spec=Scorer)
        mock_scorer.get_identifier.return_value = ComponentIdentifier(
            class_name="MockScorer", class_module="test_module"
        )
        workflow = XPIATestWorkflow(
            attack_setup_target=mock_target,
            processing_target=mock_processing_target,
            scorer=mock_scorer,
        )

        attack_msg = Message(message_pieces=[MessagePiece(role="user", original_value="attack content")])
        context = XPIAContext(attack_content=attack_msg, processing_prompt=None)

        await workflow._setup_async(context=context)

        # The processing_callback should be set after _setup_async
        assert context.processing_callback is not None
        with pytest.raises(RuntimeError, match="context.processing_prompt is not initialized"):
            await context.processing_callback()
