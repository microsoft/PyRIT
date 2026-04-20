# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol, overload

from pyrit.common.utils import combine_dict, get_kwarg_param
from pyrit.executor.core import StrategyConverterConfig
from pyrit.executor.workflow.core import (
    WorkflowContext,
    WorkflowResult,
    WorkflowStrategy,
)
from pyrit.memory import CentralMemory
from pyrit.models import (
    Message,
    MessagePiece,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer

logger = logging.getLogger(__name__)


class XPIAProcessingCallback(Protocol):
    """
    Protocol for processing callback functions used in XPIA workflows.

    Defines the interface for callback functions that execute the processing
    phase of an XPIA attack. The callback should handle the actual execution
    of the processing target and return the response as a string.
    """

    async def __call__(self) -> str:
        """Execute the processing callback and return response text."""
        ...


class XPIAStatus(Enum):
    """
    Enumeration of possible XPIA attack result statuses.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


@dataclass
class XPIAContext(WorkflowContext):
    """
    Context for Cross-Domain Prompt Injection Attack (XPIA) workflow.

    Contains execution-specific parameters needed for each XPIA attack run.
    Immutable objects like targets and scorers are stored in the workflow instance.
    """

    # The attack content as a message containing the attack content
    attack_content: Message

    # Callback to execute after the attack prompt is positioned in the attack location
    processing_callback: Optional[XPIAProcessingCallback] = None

    # Conversation ID for the attack setup target
    attack_setup_target_conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Conversation ID for the processing phase
    processing_conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # The prompt to send to the processing target (for test workflow)
    processing_prompt: Optional[Message] = None

    # Additional labels that can be applied throughout the workflow
    memory_labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class XPIAResult(WorkflowResult):
    """
    Result of XPIA workflow execution.

    Contains the outcome of the cross-domain prompt injection attack, including
    the processing response, optional score, and attack setup response.
    """

    # Conversation ID for the processing phase
    processing_conversation_id: str

    # Response from the processing target
    processing_response: str

    # Score if a scorer was used, None otherwise
    score: Optional[Score] = None

    # Response from the attack setup target
    attack_setup_response: Optional[str] = None

    @property
    def success(self) -> bool:
        """
        Determine if the attack was successful based on the score.

        Returns:
            bool: True if the attack was successful (score exists and has a positive value),
                False otherwise.
        """
        if self.score is None:
            return False
        score_value = self.score.get_value()
        return score_value > 0 if isinstance(score_value, (int, float)) else False

    @property
    def status(self) -> XPIAStatus:
        """
        Get the status of the attack result.

        Returns:
            XPIAStatus: The status of the attack result.
        """
        if self.score is None:
            return XPIAStatus.UNKNOWN
        return XPIAStatus.SUCCESS if self.success else XPIAStatus.FAILURE


class XPIAWorkflow(WorkflowStrategy[XPIAContext, XPIAResult]):
    """
    Implementation of Cross-Domain Prompt Injection Attack (XPIA) workflow.

    This workflow orchestrates an attack where:
    1. An attack prompt is generated and positioned using the attack_setup_target
    2. The processing_callback is executed to trigger the target's processing
    3. The response is optionally scored to determine success

    The workflow supports customization through prompt converters and scorers,
    allowing for various attack techniques and evaluation methods.
    """

    def __init__(
        self,
        *,
        attack_setup_target: PromptTarget,
        scorer: Optional[Scorer] = None,
        converter_config: Optional[StrategyConverterConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        logger: logging.Logger = logger,
    ):
        """
        Initialize the XPIA workflow.

        Args:
            attack_setup_target (PromptTarget): The target that generates the attack prompt
                and gets it into the attack location.
            scorer (Optional[Scorer]): Optional scorer to evaluate the processing response.
                If no scorer is provided the workflow will skip scoring.
            converter_config (Optional[StrategyConverterConfig]): Optional converter
                configuration for request and response converters.
            prompt_normalizer (Optional[PromptNormalizer]): Optional PromptNormalizer
                instance. If not provided, a new one will be created.
            logger (logging.Logger): Logger instance for logging events.
        """
        super().__init__(context_type=XPIAContext, logger=logger)
        self._attack_setup_target = attack_setup_target
        self._scorer = scorer

        converter_config = converter_config or StrategyConverterConfig()
        self._request_converters = converter_config.request_converters
        self._response_converters = converter_config.response_converters

        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._memory = CentralMemory.get_memory_instance()

    def _validate_context(self, *, context: XPIAContext) -> None:
        """
        Validate the XPIA context before execution.

        This method ensures that all required parameters are present and valid
        before proceeding with the attack workflow execution.

        Args:
            context (XPIAContext): The context to validate.

        Raises:
            ValueError: If the context is invalid (missing attack_content or processing_callback).
        """
        self._validate_message(field_name="attack_content", message=context.attack_content)

        if not context.processing_callback:
            raise ValueError("processing_callback is required")

    @staticmethod
    def _validate_message(*, field_name: str, message: Message) -> None:
        """
        Validate the message before execution.

        This method ensures that the message is well-formed and contains
        valid content.

        Args:
            message (Message): The message to validate.
            field_name (str): The name of the field being validated.

        Raises:
            ValueError: If the message is invalid.
        """
        if not message or not message.message_pieces:
            raise ValueError(
                f"{field_name}: Message must be provided with at least one message piece. Received: {message}"
            )

        if len(message.message_pieces) != 1:
            raise ValueError(
                f"{field_name}: Exactly one message piece must be provided. "
                f"Received {len(message.message_pieces)} pieces."
            )

        # Validate the message piece
        piece = message.message_pieces[0]
        if piece.converted_value_data_type != "text":
            raise ValueError(
                f"{field_name}: Message piece must be of type 'text'. "
                f"Received: '{piece.converted_value_data_type}' with value: {piece.converted_value[:50]}..."
            )

    async def _setup_async(self, *, context: XPIAContext) -> None:
        """
        Set up the phase before executing the workflow.

        This method prepares the execution context by generating conversation IDs
        and combining memory labels for the workflow execution.

        Args:
            context (XPIAContext): The context for the workflow. This will be modified
                to include setup-specific configuration.
        """
        context.attack_setup_target_conversation_id = str(uuid.uuid4())
        context.processing_conversation_id = str(uuid.uuid4())
        context.memory_labels = combine_dict(self._memory_labels, context.memory_labels)

    async def _perform_async(self, *, context: XPIAContext) -> XPIAResult:
        """
        Execute the XPIA workflow.

        This method orchestrates the complete XPIA attack by:
        1. Sending the attack content to the attack setup target
        2. Executing the processing callback to trigger target processing
        3. Optionally scoring the processing response to determine success

        Args:
            context (XPIAContext): The context containing workflow parameters including
                attack content, processing callback, and memory labels.

        Returns:
            XPIAResult: The result of the workflow execution containing the processing
                response, optional score, and attack setup response.
        """
        # Step 1: Setup and send attack prompt
        setup_response_text = await self._setup_attack_async(context=context)

        # Step 2: Execute processing callback
        processing_response = await self._execute_processing_async(context=context)

        # Step 3: Score the response if scorer is provided
        score = await self._score_response_async(processing_response=processing_response)

        return XPIAResult(
            processing_conversation_id=context.processing_conversation_id,
            processing_response=processing_response,
            score=score,
            attack_setup_response=setup_response_text,
        )

    async def _setup_attack_async(self, *, context: XPIAContext) -> str:
        """
        Set up and send the attack prompt to the attack setup target.

        This method sends the attack content to the attack setup target
        using configured request converters.

        Args:
            context (XPIAContext): The context containing the attack content and labels.

        Returns:
            str: The response text from the attack setup target.
        """
        attack_content_value = context.attack_content.get_value()
        self._logger.info(
            "Sending the following prompt to the prompt target (after applying prompt "
            f'converter operations) "{attack_content_value}"',
        )

        setup_response = await self._prompt_normalizer.send_prompt_async(
            message=context.attack_content,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            target=self._attack_setup_target,
            labels=context.memory_labels,
            attack_identifier=self.get_identifier(),
            conversation_id=context.attack_setup_target_conversation_id,
        )

        setup_response_text = setup_response.get_value()
        self._logger.info(f'Received the following response from the prompt target: "{setup_response_text}"')

        return setup_response_text

    async def _execute_processing_async(self, *, context: XPIAContext) -> str:
        """
        Execute the processing callback to trigger target processing.

        This method calls the processing callback function to execute the processing
        target and retrieve the response.

        Args:
            context (XPIAContext): The context containing the processing callback.

        Returns:
            str: The response from the processing target.
        """
        processing_response = await context.processing_callback()
        self._memory.add_message_to_memory(
            request=Message(
                message_pieces=[
                    MessagePiece(
                        conversation_id=context.processing_conversation_id,
                        original_value=processing_response,
                        original_value_data_type="text",
                        role="assistant",
                        attack_identifier=self.get_identifier(),
                    )
                ],
            )
        )
        self._logger.info(f'Received the following response from the processing target "{processing_response}"')
        return processing_response

    async def _score_response_async(self, *, processing_response: str) -> Optional[Score]:
        """
        Score the processing response if a scorer is provided.

        This method uses the configured scorer to evaluate the processing response
        and determine the success of the attack. The scoring is executed using
        asyncio to match the legacy implementation behavior.

        Args:
            processing_response (str): The response from the processing target to score.

        Returns:
            Optional[Score]: The score if a scorer is configured, None otherwise.
        """
        if not self._scorer:
            self._logger.info("No scorer provided. Returning raw processing response.")
            return None

        try:
            scores = await self._scorer.score_text_async(processing_response)
            if scores:
                score = scores[0]
                self._logger.info(f"Score of the processing response: {score}")
                return score
            return None
        except Exception as e:
            self._logger.error(f"Error scoring response: {e}", exc_info=True)
            return None

    async def _teardown_async(self, *, context: XPIAContext) -> None:
        """
        Teardown phase after executing the workflow.

        This method performs cleanup operations after the XPIA workflow execution.
        Currently, no specific teardown operations are required for the base workflow,
        but this method can be overridden by subclasses if needed.

        Args:
            context (XPIAContext): The context for the workflow.
        """
        # No specific teardown operations required for base XPIA workflow
        pass

    @overload
    async def execute_async(
        self,
        *,
        attack_content: Message,
        processing_callback: Optional[XPIAProcessingCallback] = None,
        processing_prompt: Optional[Message] = None,
        memory_labels: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> XPIAResult: ...

    @overload
    async def execute_async(
        self,
        **kwargs: Any,
    ) -> XPIAResult: ...

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> XPIAResult:
        """
        Execute the XPIA workflow strategy asynchronously with the provided parameters.

        Args:
            attack_content (Message): The content to use for the attack.
            processing_callback (ProcessingCallback): The callback to execute after the attack prompt is positioned
                in the attack location. This is generic on purpose to allow for flexibility. The callback should
                return the processing response.
            processing_prompt (Optional[Message]): The prompt to send to the processing target. This should
                include placeholders to invoke plugins (if any).
            memory_labels (Optional[Dict[str, str]]): Memory labels for the attack context.
            **kwargs: Additional parameters for the attack.

        Returns:
            XPIAResult: The result of the workflow execution.

        Raises:
            TypeError: If any of the provided parameters are of incorrect type.
        """
        attack_content = get_kwarg_param(kwargs=kwargs, param_name="attack_content", expected_type=Message)

        # _validate_context takes care of the validation
        processing_prompt = get_kwarg_param(
            kwargs=kwargs, param_name="processing_prompt", expected_type=Message, required=False
        )

        processing_callback = kwargs.get("processing_callback")
        if processing_callback is not None and not callable(processing_callback):
            raise TypeError(f"processing_callback must be callable, got {type(processing_callback)}")

        memory_labels = get_kwarg_param(kwargs=kwargs, param_name="memory_labels", expected_type=dict, required=False)

        return await super().execute_async(
            attack_content=attack_content,
            processing_prompt=processing_prompt,
            memory_labels=memory_labels or {},
            **kwargs,
        )


class XPIATestWorkflow(XPIAWorkflow):
    """
    XPIA workflow with automated test processing.

    This variant automatically handles the processing phase by sending
    a predefined prompt to a processing target. It is designed for automated
    testing scenarios where the processing can be scripted rather than manual.

    The workflow creates an automated processing callback that sends the
    processing prompt to the configured processing target and returns the response.
    """

    def __init__(
        self,
        *,
        attack_setup_target: PromptTarget,
        processing_target: PromptTarget,
        scorer: Scorer,
        converter_config: Optional[StrategyConverterConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        logger: logging.Logger = logger,
    ) -> None:
        """
        Initialize the XPIA test workflow.

        Args:
            attack_setup_target (PromptTarget): The target that generates the attack prompt
                and gets it into the attack location.
            processing_target (PromptTarget): The target of the attack which processes the
                processing prompt. This should include references to invoke plugins (if any).
            scorer (Scorer): The scorer to use to score the processing response. This is
                required for test workflows to evaluate attack success.
            converter_config (Optional[StrategyConverterConfig]): Optional converter
                configuration for request and response converters.
            prompt_normalizer (Optional[PromptNormalizer]): Optional PromptNormalizer
                instance. If not provided, a new one will be created.
            logger (logging.Logger): Logger instance for logging events.
        """
        super().__init__(
            attack_setup_target=attack_setup_target,
            scorer=scorer,
            converter_config=converter_config,
            prompt_normalizer=prompt_normalizer,
            logger=logger,
        )
        self._processing_target = processing_target

    def _validate_context(self, *, context: XPIAContext) -> None:
        """
        Validate the XPIA test context.

        This method validates the context for test workflow execution, ensuring
        that both attack content and processing prompt are provided.

        Args:
            context (XPIAContext): The context to validate.

        Raises:
            ValueError: If the context is invalid (missing attack_content or processing_prompt).
        """
        if not context.processing_prompt or not context.processing_prompt.message_pieces:
            raise ValueError("processing_prompt with at least one message piece is required")

        # Skip the base validation for processing_callback since we'll set it ourselves
        self._validate_message(field_name="attack_content", message=context.attack_content)

    async def _setup_async(self, *, context: XPIAContext) -> None:
        """
        Set up for XPIA test workflow execution.

        This method creates an automated processing callback that sends the processing
        prompt to the configured processing target. The callback is attached to the
        context for use during workflow execution.

        Args:
            context (XPIAContext): The execution context containing the processing prompt
                and configuration settings. This context will be modified to include
                the processing callback.
        """

        # Create the processing callback using the test context
        async def process_async() -> str:
            # processing_prompt is validated to be non-None in _validate_context
            assert context.processing_prompt is not None
            response = await self._prompt_normalizer.send_prompt_async(
                message=context.processing_prompt,
                target=self._processing_target,
                request_converter_configurations=self._request_converters,
                response_converter_configurations=self._response_converters,
                labels=context.memory_labels,
                attack_identifier=self.get_identifier(),
                conversation_id=context.processing_conversation_id,
            )

            return response.get_value()

        # Set the processing callback on the context
        context.processing_callback = process_async
        return await super()._setup_async(context=context)


class XPIAManualProcessingWorkflow(XPIAWorkflow):
    """
    XPIA workflow with manual processing intervention.

    This variant pauses execution to allow manual triggering of the
    processing target, then accepts the output via console input.
    This is useful for scenarios where the processing target requires
    manual interaction or cannot be automated.

    The workflow will prompt the operator to manually trigger the processing
    target's execution and paste the output into the console for scoring.
    """

    def __init__(
        self,
        *,
        attack_setup_target: PromptTarget,
        scorer: Scorer,
        converter_config: Optional[StrategyConverterConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        logger: logging.Logger = logger,
    ) -> None:
        """
        Initialize the XPIA manual processing workflow.

        Args:
            attack_setup_target (PromptTarget): The target that generates the attack prompt
                and gets it into the attack location.
            scorer (Scorer): The scorer to use to score the processing response. This is
                required to evaluate the manually provided response.
            converter_config (Optional[StrategyConverterConfig]): Optional converter
                configuration for request and response converters.
            prompt_normalizer (Optional[PromptNormalizer]): Optional PromptNormalizer
                instance. If not provided, a new one will be created.
            logger (logging.Logger): Logger instance for logging events.
        """
        super().__init__(
            attack_setup_target=attack_setup_target,
            scorer=scorer,
            converter_config=converter_config,
            prompt_normalizer=prompt_normalizer,
            logger=logger,
        )

    def _validate_context(self, *, context: XPIAContext) -> None:
        """
        Validate the XPIA manual processing context.

        This method validates the context for manual processing workflow execution.
        Since the processing callback will be created automatically, we only need
        to validate that the attack content is present.

        Args:
            context (XPIAContext): The context to validate.

        Raises:
            ValueError: If the context is invalid (missing attack_content).
        """
        # Skip the base validation for processing_callback since we'll set it ourselves
        self._validate_message(field_name="attack_content", message=context.attack_content)

    async def _setup_async(self, *, context: XPIAContext) -> None:
        """
        Set up for XPIA manual processing workflow execution.

        This method creates a manual input callback that prompts the operator
        to trigger the processing target's execution and paste the output.

        Args:
            context (XPIAContext): The execution context. This context will be
                modified to include the manual processing callback.
        """

        # Create the manual input callback
        async def manual_input_async() -> str:
            prompt = "Please trigger the processing target's execution and paste the output here:"
            return input(prompt)

        # Set the processing callback on the context
        context.processing_callback = manual_input_async
        return await super()._setup_async(context=context)
