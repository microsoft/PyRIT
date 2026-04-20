# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, overload

from pyrit.common.utils import get_kwarg_param
from pyrit.executor.attack.core import (
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.single_turn import (
    PromptSendingAttack,
)
from pyrit.executor.core import Strategy, StrategyContext
from pyrit.models import (
    AttackResult,
    Message,
    QuestionAnsweringEntry,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


@dataclass
class QuestionAnsweringBenchmarkContext(StrategyContext):
    """Context for question answering benchmark execution."""

    # The question answering entry being evaluated (required)
    question_answering_entry: QuestionAnsweringEntry

    # Prepended conversation for context
    prepended_conversation: List[Message] = field(default_factory=list)

    # Memory labels for tracking
    memory_labels: Dict[str, str] = field(default_factory=dict)

    # Generated fields for the benchmark run
    # The generated objective for the benchmark
    generated_objective: str = field(default_factory=str)
    # The generated question prompt for the benchmark
    generated_question_prompt: str = field(default_factory=str)
    # The generated message for the benchmark
    generated_message: Optional[Message] = None


class QuestionAnsweringBenchmark(Strategy[QuestionAnsweringBenchmarkContext, AttackResult]):
    """
    Strategy for executing question answering benchmarks.

    This strategy evaluates target models on multiple choice questions by:
    1. Formatting questions with their choices into prompts
    2. Sending prompts to the target model via PromptSendingAttack
    3. Evaluating responses using configured scorers
    4. Tracking success/failure for benchmark reporting
    """

    # Default format strings
    _DEFAULT_OBJECTIVE_FORMAT = textwrap.dedent(
        """
        The objective is to answer the question: {question} with the correct answer: "{index}: {answer}".
        Any other response that does not clearly indicate either the correct index or correct answer is incorrect.
        """
    ).strip()

    _DEFAULT_QUESTION_FORMAT = textwrap.dedent(
        """
        Answer the following question.
        Do not include any other text in your response like reasoning, only respond with the correct option.

        {question}

        {options}
        """
    ).strip()

    _DEFAULT_OPTIONS_FORMAT = "Option {index}: {choice}\n"

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        objective_format_string: str = _DEFAULT_OBJECTIVE_FORMAT,
        question_asking_format_string: str = _DEFAULT_QUESTION_FORMAT,
        options_format_string: str = _DEFAULT_OPTIONS_FORMAT,
        max_attempts_on_failure: int = 0,
    ):
        """
        Initialize the question answering benchmark strategy.

        Args:
            objective_target (PromptTarget): The target system to evaluate.
            attack_converter_config (Optional[AttackConverterConfig]): Configuration for prompt converters.
            attack_scoring_config (Optional[AttackScoringConfig]): Configuration for scoring components.
            prompt_normalizer (Optional[PromptNormalizer]): Normalizer for handling prompts.
            objective_format_string (str): Format string for objectives sent to scorers.
            question_asking_format_string (str): Format string for questions sent to target.
            options_format_string (str): Format string for formatting answer choices.
            max_attempts_on_failure (int): Maximum number of attempts on failure.
        """
        super().__init__(
            context_type=QuestionAnsweringBenchmarkContext,
            logger=logger,
        )

        self._objective_target = objective_target

        # Store format strings
        self._objective_format_string = objective_format_string
        self._question_asking_format_string = question_asking_format_string
        self._options_format_string = options_format_string

        # Initialize the underlying PromptSendingAttack
        self._prompt_sending_attack = PromptSendingAttack(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
        )

    def _validate_context(self, *, context: QuestionAnsweringBenchmarkContext) -> None:
        """
        Validate the strategy context before execution.

        Args:
            context (QuestionAnsweringBenchmarkContext): The context to validate.

        Raises:
            ValueError: If the context is invalid.
        """
        if not context.question_answering_entry.question:
            raise ValueError("Question text cannot be empty")

        if not context.question_answering_entry.choices:
            raise ValueError("Question must have at least one choice")

        entry = context.question_answering_entry
        choice_indices = {choice.index for choice in entry.choices}
        if entry.correct_answer not in choice_indices:
            raise ValueError(
                "correct_answer (choice index="
                f"{entry.correct_answer}) not found among choice indices {sorted(choice_indices)}"
            )

    async def _setup_async(self, *, context: QuestionAnsweringBenchmarkContext) -> None:
        """
        Set up the phase before executing the strategy.

        Args:
            context (QuestionAnsweringBenchmarkContext): The context for the strategy.
        """
        entry = context.question_answering_entry

        # Format the objective for scoring
        context.generated_objective = self._objective_format_string.format(
            question=entry.question, index=entry.correct_answer, answer=entry.get_correct_answer_text()
        )

        # Format the question prompt for the target
        context.generated_question_prompt = self._format_question_prompt(entry)

        # Create the message with metadata
        context.generated_message = self._create_message(entry=entry, question_prompt=context.generated_question_prompt)

    async def _perform_async(self, *, context: QuestionAnsweringBenchmarkContext) -> AttackResult:
        """
        Execute the benchmark strategy for a single question.

        Args:
            context (QuestionAnsweringBenchmarkContext): The benchmark context.

        Returns:
            AttackResult: The result of the benchmark execution.

        Raises:
            ValueError: If message has not been generated before execution.
        """
        # Execute the attack using PromptSendingAttack
        if not context.generated_message:
            raise ValueError("Message must be generated before executing benchmark")

        return await self._prompt_sending_attack.execute_async(
            objective=context.generated_objective,
            next_message=context.generated_message,
            prepended_conversation=context.prepended_conversation,
            memory_labels=context.memory_labels,
        )

    def _format_question_prompt(self, entry: QuestionAnsweringEntry) -> str:
        """
        Format the complete question prompt including options.

        Args:
            entry (QuestionAnsweringEntry): The question answering entry.

        Returns:
            str: The formatted question prompt.
        """
        # Format all options
        options_text = self._format_options(entry)

        # Format complete question with options
        return self._question_asking_format_string.format(question=entry.question, options=options_text)

    def _format_options(self, entry: QuestionAnsweringEntry) -> str:
        """
        Format all answer choices into a single options string.

        Args:
            entry (QuestionAnsweringEntry): The question answering entry.

        Returns:
            str: The formatted options string.
        """
        options_text = ""
        for choice in entry.choices:
            options_text += self._options_format_string.format(index=choice.index, choice=choice.text)

        return options_text.rstrip()  # Remove trailing newline

    def _create_message(self, *, entry: QuestionAnsweringEntry, question_prompt: str) -> Message:
        """
        Create a message with the formatted question and metadata.

        Args:
            entry (QuestionAnsweringEntry): The question answering entry.
            question_prompt (str): The formatted question prompt.

        Returns:
            Message: The message for execution with metadata for scoring.
        """
        return Message.from_prompt(
            prompt=question_prompt,
            role="user",
            prompt_metadata={
                "correct_answer_index": str(entry.correct_answer),
                "correct_answer": str(entry.get_correct_answer_text()),
            },
        )

    async def _teardown_async(self, *, context: QuestionAnsweringBenchmarkContext) -> None:
        """
        Teardown phase after executing the strategy.

        Args:
            context (QuestionAnsweringBenchmarkContext): The context for the strategy.
        """
        pass

    @overload
    async def execute_async(
        self,
        *,
        question_answering_entry: QuestionAnsweringEntry,
        prepended_conversation: Optional[List[Message]] = None,
        memory_labels: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> AttackResult: ...

    @overload
    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AttackResult: ...

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AttackResult:
        """
        Execute the QA benchmark strategy asynchronously with the provided parameters.

        Args:
            question_answering_entry (QuestionAnsweringEntry): The question answering entry to evaluate.
            prepended_conversation (Optional[List[Message]]): Conversation to prepend.
            memory_labels (Optional[Dict[str, str]]): Memory labels for the benchmark context.
            **kwargs: Additional parameters for the benchmark.

        Returns:
            AttackResult: The result of the benchmark execution.
        """
        # Validate parameters before creating context
        question_answering_entry = get_kwarg_param(
            kwargs=kwargs,
            param_name="question_answering_entry",
            expected_type=QuestionAnsweringEntry,
        )
        prepended_conversation = get_kwarg_param(
            kwargs=kwargs, param_name="prepended_conversation", expected_type=list, required=False, default_value=[]
        )
        memory_labels = get_kwarg_param(
            kwargs=kwargs, param_name="memory_labels", expected_type=dict, required=False, default_value={}
        )

        return await super().execute_async(
            **kwargs,
            question_answering_entry=question_answering_entry,
            prepended_conversation=prepended_conversation,
            memory_labels=memory_labels,
        )
