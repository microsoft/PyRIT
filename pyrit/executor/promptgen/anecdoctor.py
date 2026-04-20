# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, overload

import yaml

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.common.utils import combine_dict, get_kwarg_param
from pyrit.executor.core.config import StrategyConverterConfig
from pyrit.executor.promptgen.core import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.models import (
    Message,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


@dataclass
class AnecdoctorContext(PromptGeneratorStrategyContext):
    """
    Context specific to Anecdoctor prompt generation.

    Contains all parameters needed for executing Anecdoctor prompt generation,
    including the evaluation data, language settings, and conversation ID.
    """

    # The data in ClaimsReview format to use in constructing the prompt
    evaluation_data: List[str]

    # The language of the content to generate (e.g., "english", "spanish")
    language: str

    # The type of content to generate (e.g., "viral tweet", "news article")
    content_type: str

    # Conversation ID for the main generation process (generated if not provided)
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Optional memory labels to apply to the prompts
    memory_labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class AnecdoctorResult(PromptGeneratorStrategyResult):
    """
    Result of Anecdoctor prompt generation.

    Contains the generated content from the misinformation prompt generation.

    Args:
        generated_content (Message): The generated content from the prompt generation.
    """

    generated_content: Message


class AnecdoctorGenerator(PromptGeneratorStrategy[AnecdoctorContext, AnecdoctorResult]):
    """
    Implementation of the Anecdoctor prompt generation strategy.

    The Anecdoctor generator creates misinformation content by either:
    1. Using few-shot examples directly (default mode when processing_model is not provided)
    2. First extracting a knowledge graph from examples, then using it (when processing_model is provided)

    This generator is designed to test a model's susceptibility to generating false or
    misleading content when provided with examples in ClaimsReview format. The generator
    can optionally use a processing model to extract a knowledge graph representation
    of the examples before generating the final content.

    The generation flow consists of:
    1. (Optional) Extract knowledge graph from evaluation data using processing model
    2. Format a system prompt based on language and content type
    3. Send formatted examples (or knowledge graph) to target model
    4. Return the generated content as the result
    """

    # Class-level constants for YAML file paths
    _ANECDOCTOR_BUILD_KG_YAML = "anecdoctor_build_knowledge_graph.yaml"
    _ANECDOCTOR_USE_KG_YAML = "anecdoctor_use_knowledge_graph.yaml"
    _ANECDOCTOR_USE_FEWSHOT_YAML = "anecdoctor_use_fewshot.yaml"
    _ANECDOCTOR_PROMPT_PATH = Path("anecdoctor")

    def __init__(
        self,
        *,
        objective_target: PromptChatTarget,
        processing_model: Optional[PromptChatTarget] = None,
        converter_config: Optional[StrategyConverterConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
    ) -> None:
        """
        Initialize the Anecdoctor prompt generation strategy.

        Args:
            objective_target (PromptChatTarget): The chat model to be used for prompt generation.
            processing_model (Optional[PromptChatTarget]): The model used for knowledge graph extraction.
                If provided, the generator will extract a knowledge graph from the examples before generation.
                If None, the generator will use few-shot examples directly.
            converter_config (Optional[StrategyConverterConfig]): Configuration for prompt converters.
            prompt_normalizer (Optional[PromptNormalizer]): Normalizer for handling prompts.
        """
        # Initialize base class
        super().__init__(logger=logger, context_type=AnecdoctorContext)

        # Store configuration
        self._objective_target = objective_target
        self._processing_model = processing_model
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()

        # Initialize converter configuration
        converter_config = converter_config or StrategyConverterConfig()
        self._request_converters = converter_config.request_converters
        self._response_converters = converter_config.response_converters

        # Prepare the system prompt template based on whether we're using knowledge graph
        if self._processing_model:
            self._system_prompt_template = self._load_prompt_from_yaml(yaml_filename=self._ANECDOCTOR_USE_KG_YAML)
        else:
            self._system_prompt_template = self._load_prompt_from_yaml(yaml_filename=self._ANECDOCTOR_USE_FEWSHOT_YAML)

    def _validate_context(self, *, context: AnecdoctorContext) -> None:
        """
        Validate the context before executing the prompt generation.

        Args:
            context (AnecdoctorContext): The generation context to validate.

        Raises:
            ValueError: If the context is invalid.
        """
        if not context.content_type:
            raise ValueError("content_type must be provided in the context")

        if not context.language:
            raise ValueError("language must be provided in the context")

        if not context.evaluation_data:
            raise ValueError("evaluation_data cannot be empty")

    async def _setup_async(self, *, context: AnecdoctorContext) -> None:
        """
        Set up the prompt generation by preparing the system prompt and configuration.

        This method generates a new conversation ID for the generation process and configures
        the target model with the appropriate system prompt based on the language
        and content type specified in the context.

        Args:
            context (AnecdoctorContext): The generation context containing parameters.
        """
        context.conversation_id = str(uuid.uuid4())

        # Combine memory labels from context and prompt generation strategy
        context.memory_labels = combine_dict(self._memory_labels, context.memory_labels)

        # Format the system prompt with language and content type
        system_prompt = self._system_prompt_template.format(language=context.language, type=context.content_type)

        # Configure the target with the system prompt
        self._objective_target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=context.conversation_id,
            attack_identifier=self.get_identifier(),
            labels=context.memory_labels,
        )

    async def _perform_async(self, *, context: AnecdoctorContext) -> AnecdoctorResult:
        """
        Execute the Anecdoctor prompt generation.

        This method prepares the examples (either as few-shot examples or via knowledge
        graph extraction), sends them to the target model, and returns the generated
        misinformation content.

        Args:
            context (AnecdoctorContext): The generation context containing all parameters.

        Returns:
            AnecdoctorResult: The result containing the generated misinformation content.

        Raises:
            RuntimeError: If no response is received from the target model.
        """
        self._logger.info("Starting Anecdoctor prompt generation")
        self._logger.debug(f"Using knowledge graph: {self._processing_model is not None}")
        self._logger.debug(f"Language: {context.language}, Content type: {context.content_type}")

        formatted_examples = await self._prepare_examples_async(context=context)
        response = await self._send_examples_to_target_async(formatted_examples=formatted_examples, context=context)

        if not response:
            raise RuntimeError("Failed to get response from target model")

        return AnecdoctorResult(
            generated_content=response,
        )

    async def _teardown_async(self, *, context: AnecdoctorContext) -> None:
        """
        Clean up after prompt generation execution.

        Currently no cleanup is required for this prompt generation strategy.

        Args:
            context (AnecdoctorContext): The generation context.
        """
        # Nothing to clean up for this prompt generation
        pass

    async def _prepare_examples_async(self, *, context: AnecdoctorContext) -> str:
        """
        Prepare the formatted examples, either directly or via knowledge graph extraction.

        If a processing model is configured, this method will extract a knowledge graph
        from the evaluation data. Otherwise, it will format the examples directly for
        few-shot prompting.

        Args:
            context (AnecdoctorContext): The generation context containing evaluation data.

        Returns:
            str: The formatted examples ready to send to the target model.
        """
        if self._processing_model:
            # Extract knowledge graph from examples using the processing model
            return await self._extract_knowledge_graph_async(context=context)
        else:
            # Use few-shot examples directly without knowledge graph extraction
            return self._format_few_shot_examples(evaluation_data=context.evaluation_data)

    async def _send_examples_to_target_async(
        self, *, formatted_examples: str, context: AnecdoctorContext
    ) -> Optional[Message]:
        """
        Send the formatted examples to the target model.

        Creates a message from the formatted examples and sends it to the
        objective target model using the configured converters and normalizer.

        Args:
            formatted_examples (str): The formatted examples to send.
            context (AnecdoctorContext): The generation context containing conversation metadata.

        Returns:
            Optional[Message]: The response from the target model,
                or None if the request failed.
        """
        # Create message from the formatted examples
        message = Message.from_prompt(prompt=formatted_examples, role="user")

        # Send to target model with configured converters
        return await self._prompt_normalizer.send_prompt_async(
            message=message,
            target=self._objective_target,
            conversation_id=context.conversation_id,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            labels=context.memory_labels,
            attack_identifier=self.get_identifier(),
        )

    def _load_prompt_from_yaml(self, *, yaml_filename: str) -> str:
        """
        Load a prompt template from a YAML file.

        Constructs the full file path using the datasets path and reads the YAML
        file to extract the prompt template from the 'value' key.

        Args:
            yaml_filename (str): Name of the YAML file to load.

        Returns:
            str: The prompt template string from the 'value' key.

        Raises:
            FileNotFoundError: If the YAML file doesn't exist.
            yaml.YAMLError: If the YAML file is malformed.
            KeyError: If the 'value' key is not found in the YAML data.
        """
        prompt_path = Path(EXECUTOR_SEED_PROMPT_PATH, self._ANECDOCTOR_PROMPT_PATH, yaml_filename)
        prompt_data = prompt_path.read_text(encoding="utf-8")
        yaml_data = yaml.safe_load(prompt_data)
        return str(yaml_data["value"])

    def _format_few_shot_examples(self, *, evaluation_data: List[str]) -> str:
        """
        Format the evaluation data as few-shot examples.

        Args:
            evaluation_data (List[str]): The evaluation data to format.

        Returns:
            str: Formatted string with examples prefixed by "### examples".
        """
        return "### examples\n" + "\n".join(evaluation_data)

    async def _extract_knowledge_graph_async(self, *, context: AnecdoctorContext) -> str:
        """
        Extract a knowledge graph from the evaluation data using the processing model.

        Args:
            context (AnecdoctorContext): The generation context containing evaluation data.

        Returns:
            str: The extracted knowledge graph as a formatted string.

        Raises:
            RuntimeError: If knowledge graph extraction fails.
        """
        # Processing model is guaranteed to exist when this method is called
        assert self._processing_model is not None

        self._logger.debug("Extracting knowledge graph from evaluation data")

        # Load and format the KG extraction prompt
        kg_prompt_template = self._load_prompt_from_yaml(yaml_filename=self._ANECDOCTOR_BUILD_KG_YAML)
        kg_system_prompt = kg_prompt_template.format(language=context.language)

        # Create a separate conversation ID for KG extraction
        kg_conversation_id = str(uuid.uuid4())

        # Set system prompt on processing model
        self._processing_model.set_system_prompt(
            system_prompt=kg_system_prompt,
            conversation_id=kg_conversation_id,
            attack_identifier=self.get_identifier(),
            labels=self._memory_labels,
        )

        # Format examples for knowledge graph extraction using few-shot format
        formatted_examples = self._format_few_shot_examples(evaluation_data=context.evaluation_data)

        # Create message for the processing model
        message = Message.from_prompt(prompt=formatted_examples, role="user")

        # Send to processing model with configured converters
        kg_response = await self._prompt_normalizer.send_prompt_async(
            message=message,
            target=self._processing_model,
            conversation_id=kg_conversation_id,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            labels=self._memory_labels,
            attack_identifier=self.get_identifier(),
        )

        if not kg_response:
            raise RuntimeError("Failed to extract knowledge graph: no response from processing model")

        return kg_response.get_value()

    @overload
    async def execute_async(
        self,
        *,
        content_type: str,
        language: str,
        evaluation_data: List[str],
        memory_labels: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> AnecdoctorResult: ...

    @overload
    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AnecdoctorResult: ...

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AnecdoctorResult:
        """
        Execute the prompt generation strategy asynchronously with the provided parameters.

        Args:
            content_type (str): The type of content to generate (e.g., "viral tweet", "news article").
            language (str): The language of the content to generate (e.g., "english", "spanish").
            evaluation_data (List[str]): The data in ClaimsReview format to use in constructing the prompt.
            memory_labels (Optional[Dict[str, str]]): Memory labels for the generation context.
            **kwargs: Additional parameters for the generation.

        Returns:
            AnecdoctorResult: The result of the anecdoctor generation.
        """
        # Validate parameters before creating context
        content_type = get_kwarg_param(kwargs=kwargs, param_name="content_type", expected_type=str)
        language = get_kwarg_param(kwargs=kwargs, param_name="language", expected_type=str)
        evaluation_data = get_kwarg_param(kwargs=kwargs, param_name="evaluation_data", expected_type=list)
        return await super().execute_async(
            **kwargs, content_type=content_type, language=language, evaluation_data=evaluation_data
        )
