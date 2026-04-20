# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import random
import textwrap
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union, overload

import numpy as np
from colorama import Fore, Style

from pyrit.common.utils import combine_dict, get_kwarg_param
from pyrit.exceptions import MissingPromptPlaceholderException, pyrit_placeholder_retry
from pyrit.executor.core.config import (
    StrategyConverterConfig,
)
from pyrit.executor.promptgen.core.prompt_generator_strategy import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.executor.promptgen.fuzzer.fuzzer_converter_base import FuzzerConverter
from pyrit.memory import CentralMemory
from pyrit.models import (
    Message,
    Score,
    SeedGroup,
    SeedPrompt,
)
from pyrit.prompt_normalizer import NormalizerRequest, PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from pyrit.score import FloatScaleThresholdScorer, Scorer, SelfAskScaleScorer

logger = logging.getLogger(__name__)


class _PromptNode:
    """
    Class to maintain the tree information for each prompt template.
    """

    def __init__(
        self,
        template: str,
        parent: Optional[_PromptNode] = None,
    ):
        """
        Create the PromptNode instance.

        Args:
            template (str): Prompt template.
            parent (Optional[_PromptNode]): Parent node.
        """
        self.id = uuid.uuid4()
        self.template: str = template
        self.children: list[_PromptNode] = []
        self.level: int = 0 if parent is None else parent.level + 1
        self.visited_num = 0
        self.rewards: float = 0
        self.parent: Optional[_PromptNode] = None
        if parent is not None:
            self.add_parent(parent)

    def add_parent(self, parent: _PromptNode) -> None:
        """
        Add a parent to this node and update the level.

        Args:
            parent (_PromptNode): The parent node to add.
        """
        self.parent = parent
        parent.children.append(self)
        # Update level when parent is set
        self.level = parent.level + 1


class _MCTSExplorer:
    """
    Helper class to handle Monte Carlo Tree Search exploration logic.
    """

    def __init__(
        self,
        *,
        frequency_weight: float,
        reward_penalty: float,
        minimum_reward: float,
        non_leaf_node_probability: float,
    ):
        """
        Initialize the MCTS explorer.

        Args:
            frequency_weight: Constant that balances between high reward and selection frequency.
            reward_penalty: Penalty that diminishes reward as path length increases.
            minimum_reward: Minimal reward to prevent rewards from being too small.
            non_leaf_node_probability: Probability of selecting a non-leaf node.
        """
        self.frequency_weight = frequency_weight
        self.reward_penalty = reward_penalty
        self.minimum_reward = minimum_reward
        self.non_leaf_node_probability = non_leaf_node_probability

    def select_node(self, *, initial_nodes: List[_PromptNode], step: int) -> Tuple[_PromptNode, List[_PromptNode]]:
        """
        Select a node using MCTS-explore algorithm.

        Args:
            initial_nodes: List of initial prompt nodes.
            step: Current step number.

        Returns:
            Tuple of (selected_node, path_to_node).
        """

        def node_uct_step_score(n: _PromptNode) -> float:
            return self._calculate_uct_score(node=n, step=step)

        # Select initial node with best UCT score
        current = max(initial_nodes, key=node_uct_step_score)
        path = [current]

        # Traverse tree to find node to explore
        while len(current.children) > 0:
            if np.random.rand() < self.non_leaf_node_probability:
                break
            current = max(current.children, key=node_uct_step_score)
            path.append(current)

        return current, path

    def _calculate_uct_score(self, *, node: _PromptNode, step: int) -> float:
        """
        Calculate the Upper Confidence Bounds for Trees (UCT) score.

        Args:
            node: The node to calculate score for.
            step: Current step number.

        Returns:
            UCT score for the node.
        """
        # Handle step = 0 to avoid log(0)
        if step == 0:
            step = 1

        exploitation = node.rewards / (node.visited_num + 1)
        exploration = self.frequency_weight * np.sqrt(2 * np.log(step) / (node.visited_num + 0.01))
        return float(exploitation + exploration)

    def update_rewards(self, path: List[_PromptNode], reward: float, last_node: Optional[_PromptNode] = None) -> None:
        """
        Update rewards for nodes in the path.

        Args:
            path: List of nodes in the selected path.
            reward: Base reward to apply.
            last_node: The last selected node (for level calculation).
        """
        for node in reversed(path):
            # Apply reward with penalty based on tree depth
            level = last_node.level if last_node else node.level
            adjusted_reward = reward * max(self.minimum_reward, (1 - self.reward_penalty * level))
            node.rewards += adjusted_reward


@dataclass
class FuzzerContext(PromptGeneratorStrategyContext):
    """
    Context for the Fuzzer prompt generation strategy.

    This context contains all execution-specific state for a Fuzzer prompt generation instance,
    ensuring thread safety by isolating state per execution.
    """

    # Per-execution input data
    prompts: List[str]
    prompt_templates: List[str]
    max_query_limit: Optional[int] = None

    # Tracking state
    total_target_query_count: int = 0
    total_jailbreak_count: int = 0
    jailbreak_conversation_ids: List[Union[str, uuid.UUID]] = field(default_factory=list)
    executed_turns: int = 0

    # Tree structure
    initial_prompt_nodes: List[_PromptNode] = field(default_factory=list)
    new_prompt_nodes: List[_PromptNode] = field(default_factory=list)
    mcts_selected_path: List[_PromptNode] = field(default_factory=list)
    last_choice_node: Optional[_PromptNode] = None

    # Optional memory labels to apply to the prompts
    memory_labels: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """
        Calculate the query limit after initialization if not provided.
        """
        if self.max_query_limit is None:
            # Use default formula: templates * prompts * DEFAULT_QUERY_LIMIT_MULTIPLIER
            self.max_query_limit = (
                len(self.prompt_templates) * len(self.prompts) * FuzzerGenerator._DEFAULT_QUERY_LIMIT_MULTIPLIER
            )


@dataclass
class FuzzerResult(PromptGeneratorStrategyResult):
    """
    Result of the Fuzzer prompt generation strategy execution.

    This result includes the standard prompt generator result information with
    fuzzer-specific concrete fields for tracking MCTS exploration and successful templates.
    """

    # Concrete fields instead of metadata storage
    successful_templates: List[str] = field(default_factory=list)
    jailbreak_conversation_ids: List[Union[str, uuid.UUID]] = field(default_factory=list)
    total_queries: int = 0
    templates_explored: int = 0

    def __str__(self) -> str:
        """
        Return a formatted string representation of the fuzzer result.

        This method creates a FuzzerResultPrinter instance and captures its output
        to return as a string, allowing for convenient printing with print(result).

        Returns:
            str: Formatted string representation of the result.
        """
        import io
        from contextlib import redirect_stdout

        # Capture the printer output
        output_buffer = io.StringIO()

        # Create printer with colors disabled for string output
        printer = FuzzerResultPrinter(enable_colors=False)

        # Redirect stdout to capture the printer output
        with redirect_stdout(output_buffer):
            printer.print_result(self)

        return output_buffer.getvalue()

    def __repr__(self) -> str:
        """
        Return a concise representation of the fuzzer result.

        Returns:
            str: Concise string representation.
        """
        success_status = "SUCCESS" if self.successful_templates else "NO_JAILBREAKS"
        return (
            f"FuzzerResult("
            f"status={success_status}, "
            f"successful_templates={len(self.successful_templates)}, "
            f"total_queries={self.total_queries}, "
            f"jailbreak_conversations={len(self.jailbreak_conversation_ids)})"
        )

    def print_formatted(self, *, enable_colors: bool = True, width: int = 100) -> None:
        """
        Print the result using FuzzerResultPrinter with custom formatting options.

        Args:
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
            width (int): Maximum width for text wrapping. Defaults to 100.
        """
        printer = FuzzerResultPrinter(enable_colors=enable_colors, width=width)
        printer.print_result(self)

    def print_templates(self) -> None:
        """
        Print only the successful templates (equivalent to original attack method).
        """
        printer = FuzzerResultPrinter(enable_colors=True)
        printer.print_templates_only(self)


class FuzzerResultPrinter:
    """
    Printer for Fuzzer generation strategy results with enhanced console formatting.

    This printer displays fuzzer-specific information including successful templates,
    jailbreak conversations, and execution statistics in a formatted, colorized output
    similar to the original FuzzerAttack result display.
    """

    def __init__(self, *, width: int = 100, indent_size: int = 2, enable_colors: bool = True):
        """
        Initialize the fuzzer result printer.

        Args:
            width (int): Maximum width for text wrapping. Must be positive. Defaults to 100.
            indent_size (int): Number of spaces for indentation. Must be non-negative. Defaults to 2.
            enable_colors (bool): Whether to enable ANSI color output. Defaults to True.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._width = width
        self._indent = " " * indent_size
        self._enable_colors = enable_colors

    def _print_colored(self, text: str, *colors: str) -> None:
        """
        Print text with color formatting if colors are enabled.

        Args:
            text (str): The text to print.
            *colors: Variable number of colorama color constants to apply.
        """
        if self._enable_colors and colors:
            color_prefix = "".join(colors)
            print(f"{color_prefix}{text}{Style.RESET_ALL}")
        else:
            print(text)

    def _print_wrapped_text(self, text: str, color: str) -> None:
        """
        Print text with word wrapping and color formatting.

        Args:
            text (str): The text to wrap and print.
            color (str): The color to apply to the text.
        """
        # Split by existing newlines first to preserve line breaks
        text_lines = text.split("\n")

        for text_line in text_lines:
            if text_line.strip():  # Only wrap non-empty lines
                wrapped_lines = textwrap.wrap(text_line, width=self._width - len(self._indent))
                for wrapped_line in wrapped_lines:
                    self._print_colored(f"{self._indent}{wrapped_line}", color)
            else:
                # Print empty lines as-is
                print()

    def _print_section_header(self, title: str) -> None:
        """
        Print a section header with formatting.

        Args:
            title (str): The title of the section.
        """
        print()
        self._print_colored(f"{'=' * len(title)}", Style.BRIGHT, Fore.CYAN)
        self._print_colored(title, Style.BRIGHT, Fore.CYAN)
        self._print_colored(f"{'=' * len(title)}", Style.BRIGHT, Fore.CYAN)

    def print_result(self, result: FuzzerResult) -> None:
        """
        Print the complete fuzzer result to console.

        Args:
            result (FuzzerResult): The fuzzer result to print.
        """
        self._print_header(result)
        self._print_summary(result)
        self._print_templates(result)
        self._print_conversations(result)
        self._print_footer()

    def _print_header(self, result: FuzzerResult) -> None:
        """
        Print the header for the fuzzer result.

        Args:
            result (FuzzerResult): The fuzzer result.
        """
        success_indicator = "✅ SUCCESS" if result.successful_templates else "❌ NO JAILBREAKS FOUND"
        color = Fore.GREEN if result.successful_templates else Fore.RED

        print()
        self._print_colored("=" * self._width, color)
        header_text = f"FUZZER GENERATION RESULT: {success_indicator}"
        self._print_colored(header_text.center(self._width), Style.BRIGHT, color)
        self._print_colored("=" * self._width, color)

    def _print_summary(self, result: FuzzerResult) -> None:
        """
        Print a summary of the fuzzer execution.

        Args:
            result (FuzzerResult): The fuzzer result to summarize.
        """
        self._print_section_header("Execution Summary")

        self._print_colored(f"{self._indent} Statistics", Style.BRIGHT)
        self._print_colored(f"{self._indent * 2}• Total Queries: {result.total_queries}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Templates Explored: {result.templates_explored}", Fore.CYAN)
        self._print_colored(f"{self._indent * 2}• Successful Templates: {len(result.successful_templates)}", Fore.CYAN)
        self._print_colored(
            f"{self._indent * 2}• Jailbreak Conversations: {len(result.jailbreak_conversation_ids)}", Fore.CYAN
        )

    def _print_templates(self, result: FuzzerResult) -> None:
        """
        Print the successful jailbreak templates.

        Args:
            result (FuzzerResult): The fuzzer result containing templates.
        """
        self._print_section_header("Successful Templates")

        if result.successful_templates:
            self._print_colored(
                f"{self._indent} Found {len(result.successful_templates)} successful template(s):",
                Style.BRIGHT,
                Fore.GREEN,
            )
            for i, template in enumerate(result.successful_templates, 1):
                print()
                self._print_colored(f"{self._indent}Template {i}:", Style.BRIGHT, Fore.YELLOW)
                self._print_colored("─" * (self._width - len(self._indent)), Fore.YELLOW)
                self._print_wrapped_text(template, Fore.WHITE)
        else:
            self._print_colored(f"{self._indent}❌ No successful templates found.", Fore.RED)

    def _print_conversations(self, result: FuzzerResult) -> None:
        """
        Print the conversations from successful jailbreaks.

        Args:
            result (FuzzerResult): The fuzzer result containing conversation IDs.
        """
        self._print_section_header("Jailbreak Conversations")

        if not result.jailbreak_conversation_ids:
            self._print_colored(f"{self._indent}❌ No jailbreak conversations found.", Fore.RED)
            return

        self._print_colored(
            f"{self._indent} Found {len(result.jailbreak_conversation_ids)} jailbreak conversation(s):",
            Style.BRIGHT,
            Fore.GREEN,
        )

        for i, conversation_id in enumerate(result.jailbreak_conversation_ids, 1):
            print()
            self._print_colored(f"{self._indent}Conversation {i} (ID: {conversation_id}):", Style.BRIGHT, Fore.MAGENTA)
            self._print_colored("─" * (self._width - len(self._indent)), Fore.MAGENTA)

            target_messages = self._memory.get_message_pieces(conversation_id=str(conversation_id))

            if not target_messages:
                self._print_colored(f"{self._indent * 2}No conversation data found", Fore.YELLOW)
                continue

            for message in target_messages:
                if message.api_role == "user":
                    self._print_colored(f"{self._indent * 2} USER:", Style.BRIGHT, Fore.BLUE)
                    self._print_wrapped_text(message.converted_value, Fore.BLUE)
                else:
                    self._print_colored(f"{self._indent * 2} {message.api_role.upper()}:", Style.BRIGHT, Fore.YELLOW)
                    self._print_wrapped_text(message.converted_value, Fore.YELLOW)

                # Print scores if available
                scores = self._memory.get_prompt_scores(prompt_ids=[str(message.id)])
                if scores:
                    score = scores[0]
                    self._print_colored(
                        f"{self._indent * 3} Score: {score.get_value()} | {score.score_rationale}", Fore.CYAN
                    )
                print()

    def _print_footer(self) -> None:
        """
        Print a footer to complete the result display.
        """
        print()
        self._print_colored("─" * self._width, Style.DIM, Fore.WHITE)
        self._print_colored("End of Fuzzer Results", Style.DIM, Fore.WHITE)
        print()

    def print_templates_only(self, result: FuzzerResult) -> None:
        """
        Print only the successful templates (equivalent to original print_templates method).

        Args:
            result (FuzzerResult): The fuzzer result containing templates.
        """
        if result.successful_templates:
            print("Successful Templates:")
            for template in result.successful_templates:
                print(f"---\n{template}")
        else:
            print("No successful templates found.")


class FuzzerGenerator(PromptGeneratorStrategy[FuzzerContext, FuzzerResult]):
    """
    Implementation of the Fuzzer prompt generation strategy using Monte Carlo Tree Search (MCTS).

    The Fuzzer generates diverse jailbreak prompts by systematically exploring and generating
    prompt templates. It uses MCTS to balance exploration of new templates with
    exploitation of promising ones, efficiently searching for effective prompt variations.

    The generation flow consists of:
    1. Selecting a template using MCTS-explore algorithm
    2. Applying template converters to generate variations
    3. Generating prompts from the selected/converted template
    4. Testing prompts with the target and scoring responses
    5. Updating rewards in the MCTS tree based on scores
    6. Continuing until target jailbreak count reached or query limit reached

    Note: While this is a prompt generator, it still requires scoring functionality
    to provide feedback to the MCTS algorithm for effective template selection.

    Paper Reference:
        GPTFUZZER - Red Teaming Large Language Models with Auto-Generated Jailbreak Prompts
        Link: https://arxiv.org/pdf/2309.10253
        Authors: Jiahao Yu, Xingwei Lin, Zheng Yu, Xinyu Xing
        GitHub: https://github.com/sherdencooper/GPTFuzz
    """

    # Constants
    _TEMPLATE_PLACEHOLDER = "{{ prompt }}"
    _DEFAULT_FREQUENCY_WEIGHT = 0.5
    _DEFAULT_REWARD_PENALTY = 0.1
    _DEFAULT_MINIMUM_REWARD = 0.2
    _DEFAULT_NON_LEAF_PROBABILITY = 0.1
    _DEFAULT_BATCH_SIZE = 10
    _DEFAULT_TARGET_JAILBREAK_COUNT = 1
    _DEFAULT_QUERY_LIMIT_MULTIPLIER = 10

    @staticmethod
    def with_default_scorer(
        *,
        objective_target: PromptTarget,
        template_converters: List[FuzzerConverter],
        scoring_target: PromptChatTarget,
        converter_config: Optional[StrategyConverterConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        frequency_weight: float = _DEFAULT_FREQUENCY_WEIGHT,
        reward_penalty: float = _DEFAULT_REWARD_PENALTY,
        minimum_reward: float = _DEFAULT_MINIMUM_REWARD,
        non_leaf_node_probability: float = _DEFAULT_NON_LEAF_PROBABILITY,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        target_jailbreak_goal_count: int = _DEFAULT_TARGET_JAILBREAK_COUNT,
    ) -> "FuzzerGenerator":
        """
        Create a FuzzerGenerator instance with default scoring configuration.

        This factory method creates a FuzzerGenerator with a default scoring setup using
        SelfAskScaleScorer with the Tree of Attacks scale, wrapped in a FloatScaleThresholdScorer
        with a threshold of 0.8.

        To use the returned generator, create a FuzzerContext with prompts and prompt_templates,
        then pass it to execute_with_context_async().

        Args:
            objective_target (PromptTarget): The target to send the prompts to.
            template_converters (List[FuzzerConverter]): The converters to apply on the selected jailbreak template.
            scoring_target (PromptChatTarget): The chat target to use for scoring responses.
            converter_config (Optional[StrategyConverterConfig]): Configuration for prompt converters.
            prompt_normalizer (Optional[PromptNormalizer]): The prompt normalizer to use.
            frequency_weight (float): Constant that balances between high reward and selection frequency.
            reward_penalty (float): Penalty that diminishes reward as path length increases.
            minimum_reward (float): Minimal reward to prevent rewards from being too small.
            non_leaf_node_probability (float): Probability of selecting a non-leaf node.
            batch_size (int): The (max) batch size for sending prompts.
            target_jailbreak_goal_count (int): Target number of jailbreaks to find.

        Returns:
            FuzzerGenerator: A configured FuzzerGenerator instance with default scoring.
        """
        # Create default scorer using the provided scoring target
        scale_scorer = SelfAskScaleScorer(
            chat_target=scoring_target,
            scale_arguments_path=SelfAskScaleScorer.ScalePaths.TREE_OF_ATTACKS_SCALE.value,
            system_prompt_path=SelfAskScaleScorer.SystemPaths.GENERAL_SYSTEM_PROMPT.value,
        )

        objective_scorer = FloatScaleThresholdScorer(
            scorer=scale_scorer,
            threshold=0.8,
        )

        # Create and return the FuzzerGenerator instance
        return FuzzerGenerator(
            objective_target=objective_target,
            template_converters=template_converters,
            converter_config=converter_config,
            scorer=objective_scorer,
            prompt_normalizer=prompt_normalizer,
            frequency_weight=frequency_weight,
            reward_penalty=reward_penalty,
            minimum_reward=minimum_reward,
            non_leaf_node_probability=non_leaf_node_probability,
            batch_size=batch_size,
            target_jailbreak_goal_count=target_jailbreak_goal_count,
        )

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        template_converters: List[FuzzerConverter],
        converter_config: Optional[StrategyConverterConfig] = None,
        scorer: Optional[Scorer] = None,
        scoring_success_threshold: float = 0.8,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        frequency_weight: float = _DEFAULT_FREQUENCY_WEIGHT,
        reward_penalty: float = _DEFAULT_REWARD_PENALTY,
        minimum_reward: float = _DEFAULT_MINIMUM_REWARD,
        non_leaf_node_probability: float = _DEFAULT_NON_LEAF_PROBABILITY,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        target_jailbreak_goal_count: int = _DEFAULT_TARGET_JAILBREAK_COUNT,
    ) -> None:
        """
        Initialize the Fuzzer prompt generation strategy.

        Args:
            objective_target (PromptTarget): The target to send the prompts to.
            template_converters (List[FuzzerConverter]): The converters to apply on the selected jailbreak template.
                In each iteration, one converter is chosen at random.
            converter_config (Optional[StrategyConverterConfig]): Configuration for prompt converters.
                Defaults to None.
            scorer (Optional[Scorer]): Configuration for scoring responses. Defaults to None.
            scoring_success_threshold (float): The score threshold to consider a jailbreak successful.
            prompt_normalizer (Optional[PromptNormalizer]): The prompt normalizer to use. Defaults to None.
            frequency_weight (float): Constant that balances between high reward and selection frequency.
                Defaults to 0.5.
            reward_penalty (float): Penalty that diminishes reward as path length increases.
                Defaults to 0.1.
            minimum_reward (float): Minimal reward to prevent rewards from being too small.
                Defaults to 0.2.
            non_leaf_node_probability (float): Probability of selecting a non-leaf node.
                Defaults to 0.1.
            batch_size (int): The (max) batch size for sending prompts. Defaults to 10.
            target_jailbreak_goal_count (int): Target number of jailbreaks to find. Defaults to 1.

        Raises:
            ValueError: If required parameters are invalid or missing.
        """
        # Initialize base class
        super().__init__(logger=logger, context_type=FuzzerContext)

        # Validate inputs
        self._validate_inputs(template_converters=template_converters, batch_size=batch_size)

        # Store configuration
        self._objective_target = objective_target
        self._template_converters = template_converters

        # Initialize MCTS explorer
        self._mcts_explorer = _MCTSExplorer(
            frequency_weight=frequency_weight,
            reward_penalty=reward_penalty,
            minimum_reward=minimum_reward,
            non_leaf_node_probability=non_leaf_node_probability,
        )

        # Execution parameters
        self._batch_size = batch_size
        self._target_jailbreak_goal_count = target_jailbreak_goal_count

        # Initialize converter configuration
        converter_config = converter_config or StrategyConverterConfig()
        self._request_converters = converter_config.request_converters
        self._response_converters = converter_config.response_converters

        # Ensure we have an objective scorer after validation
        if not scorer:
            raise ValueError("Scorer must be provided")

        self._scorer = scorer
        self._scoring_success_threshold = scoring_success_threshold

        # Initialize utilities
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()

    def _validate_inputs(
        self,
        *,
        template_converters: List[FuzzerConverter],
        batch_size: int,
    ) -> None:
        """
        Validate input parameters.

        Args:
            template_converters (List[FuzzerConverter]): List of template converters.
            batch_size (int): The batch size for sending prompts.

        Raises:
            ValueError: If validation fails.
        """
        if not template_converters:
            raise ValueError("Template converters cannot be empty.")
        if batch_size < 1:
            raise ValueError("Batch size must be at least 1.")

    def _validate_context(self, *, context: FuzzerContext) -> None:
        """
        Validate the context before execution.

        Args:
            context (FuzzerContext): The generation context to validate.

        Raises:
            ValueError: If the context is invalid.
            MissingPromptPlaceholderException: If a template is missing the required placeholder.
        """
        if not context.prompt_templates:
            raise ValueError("Prompt templates in context cannot be empty.")
        if not context.prompts:
            raise ValueError("Prompts in context cannot be empty.")

        # Validate templates have placeholder
        for template in context.prompt_templates:
            if self._TEMPLATE_PLACEHOLDER not in template:
                raise MissingPromptPlaceholderException(
                    message=f"Template missing placeholder '{self._TEMPLATE_PLACEHOLDER}': {template[:50]}..."
                )

        if context.max_query_limit and context.max_query_limit < len(context.prompts):
            raise ValueError("The query limit must be at least the number of prompts to run a single iteration.")

    async def _setup_async(self, *, context: FuzzerContext) -> None:
        """
        Set up the phase before executing prompt generation.

        Args:
            context (FuzzerContext): The context containing configuration.
        """
        # Initialize tracking state
        context.total_target_query_count = 0
        context.total_jailbreak_count = 0
        context.jailbreak_conversation_ids = []
        context.executed_turns = 0

        # Initialize MCTS tree
        context.initial_prompt_nodes = [_PromptNode(prompt) for prompt in context.prompt_templates]
        context.new_prompt_nodes = []
        context.mcts_selected_path = []
        context.last_choice_node = None

        context.memory_labels = combine_dict(self._memory_labels, context.memory_labels)

    async def _perform_async(self, *, context: FuzzerContext) -> FuzzerResult:
        """
        Execute the Fuzzer prompt generation using MCTS algorithm.

        Args:
            context (FuzzerContext): The context containing configuration and state.

        Returns:
            FuzzerResult: The result of the generation execution.

        Raises:
            MissingPromptPlaceholderException: If a template converter fails to preserve the placeholder.
        """
        self._logger.info("Starting fuzzer prompt generation")
        self._logger.info(
            f"Configuration - Templates: {len(context.prompt_templates)}, "
            f"Prompts: {len(context.prompts)}, Target jailbreaks: {self._target_jailbreak_goal_count}"
        )

        # Main generation loop
        while not self._should_stop_generation(context):
            # Execute one iteration of generation
            await self._execute_generation_iteration_async(context)

        # Create result
        return self._create_generation_result(context)

    async def _execute_generation_iteration_async(self, context: FuzzerContext) -> None:
        """
        Execute one iteration of the fuzzer prompt generation.

        Args:
            context (FuzzerContext): The generation context.

        Raises:
            MissingPromptPlaceholderException: If a template converter fails to preserve the placeholder.
        """
        # Select template using MCTS
        current_seed, path = self._select_template_with_mcts(context)
        context.mcts_selected_path = path
        context.last_choice_node = current_seed

        # Apply template converter
        try:
            target_seed = await self._apply_template_converter_async(context=context, current_seed=current_seed)
        except MissingPromptPlaceholderException as e:
            self._logger.error(f"Template converter failed to preserve placeholder: {e}")
            raise

        # Create template node for tracking
        target_template = SeedPrompt(value=target_seed, data_type="text", parameters=["prompt"])
        target_template_node = _PromptNode(template=target_seed, parent=None)

        # Generate prompts from template
        jailbreak_prompts = self._generate_prompts_from_template(template=target_template, prompts=context.prompts)

        # Send prompts to target
        responses = await self._send_prompts_to_target_async(context=context, prompts=jailbreak_prompts)

        # Score responses
        scores = await self._score_responses_async(responses=responses, tasks=context.prompts)

        # Process results
        jailbreak_count = self._process_scoring_results(
            context=context,
            scores=scores,
            responses=responses,
            template_node=target_template_node,
            current_seed=current_seed,
        )

        # Update MCTS rewards
        self._update_mcts_rewards(context=context, jailbreak_count=jailbreak_count, num_queries=len(scores))

    async def _teardown_async(self, *, context: FuzzerContext) -> None:
        """
        Clean up after prompt generation.

        Args:
            context (FuzzerContext): The generation context.
        """
        # No specific teardown needed
        pass

    def _should_stop_generation(self, context: FuzzerContext) -> bool:
        """
        Check if the generation should stop based on stopping criteria.

        Args:
            context (FuzzerContext): The generation context.

        Returns:
            bool: True if generation should stop, False otherwise.
        """
        # Check query limit
        if (
            context.max_query_limit
            and (context.total_target_query_count + len(context.prompts)) > context.max_query_limit
        ):
            self._logger.info("Query limit reached.")
            return True

        # Check jailbreak goal
        if context.total_jailbreak_count >= self._target_jailbreak_goal_count:
            self._logger.info("Target jailbreak goal reached.")
            return True

        return False

    def _select_template_with_mcts(self, context: FuzzerContext) -> Tuple[_PromptNode, List[_PromptNode]]:
        """
        Select a template using the MCTS-explore algorithm.

        Args:
            context (FuzzerContext): The generation context.

        Returns:
            Tuple of (selected_node, path_to_node).
        """
        context.executed_turns += 1

        # Use MCTS explorer to select node
        selected_node, path = self._mcts_explorer.select_node(
            initial_nodes=context.initial_prompt_nodes, step=context.executed_turns
        )

        # Update visit counts
        for node in path:
            node.visited_num += 1

        return selected_node, path

    @pyrit_placeholder_retry
    async def _apply_template_converter_async(self, *, context: FuzzerContext, current_seed: _PromptNode) -> str:
        """
        Apply a random template converter to the selected template.

        Args:
            context (FuzzerContext): The generation context.
            current_seed (_PromptNode): The selected seed node.

        Returns:
            str: The converted template.

        Raises:
            MissingPromptPlaceholderException: If placeholder is missing after conversion.
        """
        # Select random converter
        template_converter = random.choice(self._template_converters)

        # Prepare other templates for converters that need them
        other_templates = self._get_other_templates(context)

        # Update converter with other templates (for crossover, etc.)
        template_converter.update(prompt_templates=other_templates)

        # Apply converter
        converted = await template_converter.convert_async(prompt=current_seed.template)

        if self._TEMPLATE_PLACEHOLDER not in converted.output_text:
            raise MissingPromptPlaceholderException(
                message=f"Converted template missing placeholder: {converted.output_text[:50]}..."
            )

        return converted.output_text

    def _get_other_templates(self, context: FuzzerContext) -> List[str]:
        """
        Get templates not in the current MCTS path.

        Args:
            context (FuzzerContext): The generation context.

        Returns:
            List of template strings.
        """
        other_templates = []
        node_ids_on_path = {node.id for node in context.mcts_selected_path}

        for prompt_node in context.initial_prompt_nodes + context.new_prompt_nodes:
            if prompt_node.id not in node_ids_on_path:
                other_templates.append(prompt_node.template)

        return other_templates

    def _generate_prompts_from_template(self, *, template: SeedPrompt, prompts: List[str]) -> List[str]:
        """
        Generate jailbreak prompts by filling template with prompts.

        Args:
            template (SeedPrompt): The template to use.
            prompts (List[str]): The prompts to fill into the template.

        Returns:
            List[str]: The generated jailbreak prompts.

        Raises:
            ValueError: If the template doesn't have the required 'prompt' parameter.
        """
        # Validate that the template has the required parameter
        if not template.parameters or "prompt" not in template.parameters:
            raise ValueError(f"Template must have 'prompt' parameter. Current parameters: {template.parameters}")

        return [template.render_template_value(prompt=prompt) for prompt in prompts]

    async def _send_prompts_to_target_async(self, *, context: FuzzerContext, prompts: List[str]) -> List[Message]:
        """
        Send prompts to the target in batches.

        Args:
            context (FuzzerContext): The generation context.
            prompts (List[str]): The prompts to send.

        Returns:
            List[Message]: The responses from the target.
        """
        requests = self._create_normalizer_requests(prompts)

        responses = await self._prompt_normalizer.send_prompt_batch_to_target_async(
            requests=requests,
            target=self._objective_target,
            labels=context.memory_labels,
            attack_identifier=self.get_identifier(),
            batch_size=self._batch_size,
        )

        return responses

    def _create_normalizer_requests(self, prompts: List[str]) -> List[NormalizerRequest]:
        """
        Create normalizer requests from prompts.

        Args:
            prompts (List[str]): The prompts to create requests for.

        Returns:
            List of normalizer requests.
        """
        requests: List[NormalizerRequest] = []

        for prompt in prompts:
            seed_group = SeedGroup(seeds=[SeedPrompt(value=prompt, data_type="text")])
            request = NormalizerRequest(
                message=seed_group.next_message,
                request_converter_configurations=self._request_converters,
                response_converter_configurations=self._response_converters,
            )
            requests.append(request)

        return requests

    async def _score_responses_async(self, *, responses: List[Message], tasks: List[str]) -> List[Score]:
        """
        Score the responses from the target.

        Args:
            responses (List[Message]): The responses to score.
            tasks (List[str]): The original tasks/prompts used for generating the responses.

        Returns:
            List[Score]: The scores for each response.
        """
        if not responses:
            return []

        response_pieces = [response.message_pieces[0] for response in responses]

        # Score with objective scorer
        scores = await self._scorer.score_prompts_batch_async(
            messages=[piece.to_message() for piece in response_pieces], objectives=tasks
        )

        return scores

    def _process_scoring_results(
        self,
        *,
        context: FuzzerContext,
        scores: List[Score],
        responses: List[Message],
        template_node: _PromptNode,
        current_seed: _PromptNode,
    ) -> int:
        """
        Process scoring results and track jailbreaks.

        Args:
            context (FuzzerContext): The generation context.
            scores (List[Score]): The scores for each response.
            responses (List[Message]): The responses that were scored.
            template_node (_PromptNode): The template node that was tested.
            current_seed (_PromptNode): The seed node that was selected.

        Returns:
            int: The number of jailbreaks found.
        """
        jailbreak_count = 0
        response_pieces = [response.message_pieces[0] for response in responses]

        for index, score in enumerate(scores):
            if self._is_jailbreak(score):
                jailbreak_count += 1
                context.jailbreak_conversation_ids.append(response_pieces[index].conversation_id)

        # Update tracking
        context.total_jailbreak_count += jailbreak_count
        context.total_target_query_count += len(scores)

        # Add successful template to results
        if jailbreak_count > 0:
            self._add_successful_template(context, template_node, current_seed)

        return jailbreak_count

    def _is_jailbreak(self, score: Score) -> bool:
        """
        Determine if a score indicates a successful jailbreak.

        Args:
            score (Score): The score to evaluate.

        Returns:
            True if this is a successful jailbreak.
        """
        score_value = score.get_value()
        # For true_false scores (like from FloatScaleThresholdScorer), check for boolean True
        if score.score_type == "true_false":
            return score_value is True

        # For float_scale scores, use threshold comparison (fallback)
        normalized_score = self._normalize_score_to_float(score_value)
        return normalized_score >= self._scoring_success_threshold

    def _add_successful_template(
        self, context: FuzzerContext, template_node: _PromptNode, parent_seed: _PromptNode
    ) -> None:
        """
        Add a successful template to the node tree.

        Args:
            context (FuzzerContext): The generation context.
            template_node (_PromptNode): The successful template node.
            parent_seed (_PromptNode): The parent seed node.
        """
        # Check if template_node already exists to avoid duplicates
        for existing_node in context.new_prompt_nodes:
            if existing_node.template == template_node.template:
                return

        context.new_prompt_nodes.append(template_node)
        template_node.add_parent(parent_seed)

    def _update_mcts_rewards(self, *, context: FuzzerContext, jailbreak_count: int, num_queries: int) -> None:
        """
        Update rewards for nodes in the MCTS path.

        Args:
            context (FuzzerContext): The generation context.
            jailbreak_count (int): Number of jailbreaks found.
            num_queries (int): Number of queries made.
        """
        # Handle division by zero
        if num_queries == 0:
            return

        # Calculate reward (normalized by number of prompts tested)
        reward = jailbreak_count / (len(context.prompts) * 1)

        # Use MCTS explorer to update rewards
        self._mcts_explorer.update_rewards(
            path=context.mcts_selected_path, reward=reward, last_node=context.last_choice_node
        )

    def _normalize_score_to_float(self, score_value: Any) -> float:
        """
        Normalize a score value to a float between 0.0 and 1.0.

        Args:
            score_value (Any): The score value to normalize.

        Returns:
            float: The normalized score.
        """
        if isinstance(score_value, bool):
            return 1.0 if score_value else 0.0
        elif isinstance(score_value, (int, float)):
            # Ensure value is between 0 and 1
            return max(0.0, min(1.0, float(score_value)))
        else:
            self._logger.warning(f"Unexpected score type: {type(score_value)}, treating as 0.0")
            return 0.0

    def _create_generation_result(self, context: FuzzerContext) -> FuzzerResult:
        """
        Create the generation result from the final context state.

        Args:
            context (FuzzerContext): The generation context.

        Returns:
            FuzzerResult: The generation result.
        """
        # Create result with concrete fields
        result = FuzzerResult(
            successful_templates=[node.template for node in context.new_prompt_nodes],
            jailbreak_conversation_ids=context.jailbreak_conversation_ids,
            total_queries=context.total_target_query_count,
            templates_explored=len(context.new_prompt_nodes),
        )

        return result

    @overload
    async def execute_async(
        self,
        *,
        prompts: List[str],
        prompt_templates: List[str],
        max_query_limit: Optional[int] = None,
        memory_labels: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> FuzzerResult: ...

    @overload
    async def execute_async(
        self,
        **kwargs: Any,
    ) -> FuzzerResult: ...

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> FuzzerResult:
        """
        Execute the Fuzzer generation strategy asynchronously.

        Args:
            prompts (List[str]): The list of prompts to use for generation.
            prompt_templates (List[str]): The list of prompt templates to use.
            max_query_limit (Optional[int]): The maximum number of queries to execute.
            memory_labels (Optional[dict[str, str]]): Optional labels to apply to the prompts.
            **kwargs: Additional keyword arguments.

        Returns:
            FuzzerResult: The result of the asynchronous execution.
        """
        # Validate parameters before creating context
        prompts = get_kwarg_param(kwargs=kwargs, param_name="prompts", expected_type=list)

        prompt_templates = get_kwarg_param(kwargs=kwargs, param_name="prompt_templates", expected_type=list)

        max_query_limit = get_kwarg_param(
            kwargs=kwargs, param_name="max_query_limit", expected_type=int, required=False
        )

        return await super().execute_async(
            **kwargs, prompts=prompts, prompt_templates=prompt_templates, max_query_limit=max_query_limit
        )
