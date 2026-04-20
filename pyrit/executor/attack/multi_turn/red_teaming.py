# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
import logging
from pathlib import Path
from typing import Any, Callable, Optional, Union

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.common.utils import warn_if_set
from pyrit.executor.attack.component import (
    ConversationManager,
    get_adversarial_chat_messages,
)
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import (
    ConversationSession,
    MultiTurnAttackContext,
    MultiTurnAttackStrategy,
)
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ConversationReference,
    ConversationType,
    Message,
    Score,
    SeedPrompt,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target.common.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class RTASystemPromptPaths(enum.Enum):
    """Enum for predefined red teaming attack system prompt paths."""

    TEXT_GENERATION = Path(EXECUTOR_RED_TEAM_PATH, "text_generation.yaml").resolve()
    IMAGE_GENERATION = Path(EXECUTOR_RED_TEAM_PATH, "image_generation.yaml").resolve()
    NAIVE_CRESCENDO = Path(EXECUTOR_RED_TEAM_PATH, "naive_crescendo.yaml").resolve()
    VIOLENT_DURIAN = Path(EXECUTOR_RED_TEAM_PATH, "violent_durian.yaml").resolve()
    CRUCIBLE = Path(EXECUTOR_RED_TEAM_PATH, "crucible.yaml").resolve()


class RedTeamingAttack(MultiTurnAttackStrategy[MultiTurnAttackContext[Any], AttackResult]):
    """
    Implementation of multi-turn red teaming attack strategy.

    This class orchestrates an iterative attack process where an adversarial chat model generates
    prompts to send to a target system, attempting to achieve a specified objective. The strategy
    evaluates each target response using a scorer to determine if the objective has been met.

    The attack flow consists of:
    1. Generating adversarial prompts based on previous responses and scoring feedback.
    2. Sending prompts to the target system through optional converters.
    3. Scoring target responses to assess objective achievement.
    4. Using scoring feedback to guide subsequent prompt generation.
    5. Continuing until the objective is achieved or maximum turns are reached.

    The strategy supports customization through system prompts, seed prompts, and prompt converters,
    allowing for various attack techniques and scenarios.
    """

    DEFAULT_ADVERSARIAL_PROMPT_IF_OBJECTIVE_TARGET_IS_BLOCKED = (
        "Request to target failed: blocked. Please rewrite your prompt to avoid getting blocked next time."
    )

    DEFAULT_ERR_MSG_IF_OBJECTIVE_TARGET_HAS_NON_TEXT_RESPONSE = (
        "The attack target does not respond with text output, "
        "so the scoring rationale is the only textual feedback "
        "that can be passed to the red teaming chat. "
    )

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        attack_adversarial_config: AttackAdversarialConfig,
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        max_turns: int = 10,
        score_last_turn_only: bool = False,
    ):
        """
        Initialize the red teaming attack strategy.

        Args:
            objective_target: The target system to attack.
            attack_adversarial_config: Configuration for the adversarial component.
            attack_converter_config: Configuration for attack converters. Defaults to None.
            attack_scoring_config: Configuration for attack scoring. Defaults to None.
            prompt_normalizer: The prompt normalizer to use for sending prompts. Defaults to None.
            max_turns (int): Maximum number of turns for the attack. Defaults to 10.
            score_last_turn_only (bool): If True, only score the final turn instead of every turn.
                This reduces LLM calls when intermediate scores are not needed (e.g., for
                generating simulated conversations). The attack will run for exactly max_turns
                when this is enabled. Defaults to False.

        Raises:
            ValueError: If objective_scorer is not provided in attack_scoring_config.
        """
        # Initialize base class
        super().__init__(objective_target=objective_target, logger=logger, context_type=MultiTurnAttackContext)
        self._memory = CentralMemory.get_memory_instance()

        # Initialize converter configuration
        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters

        # Initialize scoring configuration
        attack_scoring_config = attack_scoring_config or AttackScoringConfig()
        if attack_scoring_config.objective_scorer is None:
            raise ValueError("Objective scorer must be provided in the attack scoring configuration.")

        # Check for unused optional parameters and warn if they are set
        warn_if_set(config=attack_scoring_config, log=self._logger, unused_fields=["refusal_scorer"])

        self._objective_scorer = attack_scoring_config.objective_scorer
        self._use_score_as_feedback = attack_scoring_config.use_score_as_feedback

        # Initialize adversarial configuration
        self._adversarial_chat = attack_adversarial_config.target
        system_prompt_template_path = (
            attack_adversarial_config.system_prompt_path or RTASystemPromptPaths.TEXT_GENERATION.value
        )
        self._adversarial_chat_system_prompt_template = SeedPrompt.from_yaml_with_required_parameters(
            template_path=system_prompt_template_path,
            required_parameters=["objective"],
            error_message="Adversarial seed prompt must have an objective",
        )
        self._set_adversarial_chat_seed_prompt(seed_prompt=attack_adversarial_config.seed_prompt)

        # Initialize utilities
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()

        self._conversation_manager = ConversationManager(attack_identifier=self.get_identifier())

        # set the maximum number of turns for the attack
        if max_turns <= 0:
            raise ValueError("Maximum turns must be a positive integer.")

        self._max_turns = max_turns
        self._score_last_turn_only = score_last_turn_only

    def get_attack_scoring_config(self) -> Optional[AttackScoringConfig]:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            Optional[AttackScoringConfig]: The scoring configuration with objective scorer
                and use_score_as_feedback.
        """
        return AttackScoringConfig(
            objective_scorer=self._objective_scorer,
            use_score_as_feedback=self._use_score_as_feedback,
        )

    def _validate_context(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """
        Validate the context before executing the attack.

        Args:
            context (MultiTurnAttackContext): The context to validate.

        Raises:
            ValueError: If the context is invalid.
        """
        validators: list[tuple[Callable[[], bool], str]] = [
            # conditions that must be met for the attack to proceed
            (lambda: bool(context.objective), "Attack objective must be provided"),
            (lambda: context.executed_turns < self._max_turns, "Already exceeded max turns"),
        ]

        for validator, error_msg in validators:
            if not validator():
                raise ValueError(error_msg)

    async def _setup_async(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """
        Prepare the strategy for execution.

        1. Initializes the conversation session and context.
        2. Updates turn counts from prepended conversation.
        3. Retrieves the last assistant message's evaluation score if available.
        4. Sets up adversarial chat with prepended messages and system prompt.

        Args:
            context (MultiTurnAttackContext): Attack context with configuration

        Raises:
            ValueError: If the system prompt is not defined
        """
        # Ensure the context has a session
        context.session = ConversationSession()

        logger.debug(f"Conversation session ID: {context.session.conversation_id}")
        logger.debug(f"Adversarial chat conversation ID: {context.session.adversarial_chat_conversation_id}")

        # Track the adversarial chat conversation ID using related_conversations
        context.related_conversations.add(
            ConversationReference(
                conversation_id=context.session.adversarial_chat_conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
        )

        # Initialize context with prepended conversation (handles memory labels, turns, next_message, last_score)
        await self._conversation_manager.initialize_context_async(
            context=context,
            target=self._objective_target,
            conversation_id=context.session.conversation_id,
            request_converters=self._request_converters,
            max_turns=self._max_turns,
            memory_labels=self._memory_labels,
        )

        # Set up adversarial chat with prepended conversation
        if context.prepended_conversation:
            # Get adversarial messages with swapped roles
            adversarial_messages = get_adversarial_chat_messages(
                prepended_conversation=context.prepended_conversation,
                adversarial_chat_conversation_id=context.session.adversarial_chat_conversation_id,
                attack_identifier=self.get_identifier(),
                adversarial_chat_target_identifier=self._adversarial_chat.get_identifier(),
                labels=context.memory_labels,
            )

            for msg in adversarial_messages:
                self._memory.add_message_to_memory(request=msg)

        adversarial_system_prompt = self._adversarial_chat_system_prompt_template.render_template_value(
            objective=context.objective,
            max_turns=self._max_turns,
        )
        if not adversarial_system_prompt:
            raise ValueError("Adversarial chat system prompt must be defined")

        self._adversarial_chat.set_system_prompt(
            system_prompt=adversarial_system_prompt,
            conversation_id=context.session.adversarial_chat_conversation_id,
            attack_identifier=self.get_identifier(),
            labels=context.memory_labels,
        )

    async def _perform_async(self, *, context: MultiTurnAttackContext[Any]) -> AttackResult:
        """
        Execute the red teaming attack by iteratively generating prompts,
        sending them to the target, and scoring the responses in a loop
        until the objective is achieved or the maximum turns are reached.

        Args:
            context (MultiTurnAttackContext): The attack context containing configuration and state.

        Returns:
            AttackResult: The result of the attack execution.
        """
        # Log the attack configuration
        logger.info(f"Starting red teaming attack with objective: {context.objective}")
        logger.info(f"Max turns: {self._max_turns}")

        # Attack Execution Steps:
        # 1) Generate adversarial prompt based on previous feedback or custom prompt
        # 2) Send the generated prompt to the target system
        # 3) Evaluate the target's response using the objective scorer
        # 4) Check if the attack objective has been achieved
        # 5) Repeat steps 1-4 until objective is achieved or max turns are reached

        # Track achievement status locally to avoid concurrency issues
        achieved_objective = False

        # Execute conversation turns
        while context.executed_turns < self._max_turns and (self._score_last_turn_only or not achieved_objective):
            logger.info(f"Executing turn {context.executed_turns + 1}/{self._max_turns}")

            # Determine what to send next
            message_to_send = await self._generate_next_prompt_async(context=context)

            # Send the generated message to the objective target
            context.last_response = await self._send_prompt_to_objective_target_async(
                context=context, message=message_to_send
            )

            # Determine if this is the last turn
            is_last_turn = context.executed_turns + 1 >= self._max_turns

            # Score the response (conditionally based on score_last_turn_only)
            if not self._score_last_turn_only or is_last_turn:
                context.last_score = await self._score_response_async(context=context)
                # Check if objective achieved
                achieved_objective = bool(context.last_score.get_value()) if context.last_score else False
            else:
                # Skip scoring on intermediate turns when score_last_turn_only is True
                context.last_score = None

            # Increment the executed turns
            context.executed_turns += 1

        # Prepare the result
        return AttackResult(
            attack_identifier=self.get_identifier(),
            conversation_id=context.session.conversation_id,
            objective=context.objective,
            outcome=(AttackOutcome.SUCCESS if achieved_objective else AttackOutcome.FAILURE),
            executed_turns=context.executed_turns,
            last_response=context.last_response.get_piece() if context.last_response else None,
            last_score=context.last_score,
            related_conversations=context.related_conversations,
        )

    async def _teardown_async(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """Clean up after attack execution."""
        # Nothing to be done here, no-op
        pass

    async def _generate_next_prompt_async(self, context: MultiTurnAttackContext[Any]) -> Message:
        """
        Generate the next prompt to be sent to the target during the red teaming attack.

        This method is called each turn to obtain fresh adversarial text based on previous feedback,
        error states, or the custom prompt if it is available. It integrates feedback from the
        scorer when available, and handles blocked or error responses by returning fallback prompts.

        Args:
            context (MultiTurnAttackContext): The attack context containing the current state and configuration.

        Returns:
            Message: The message to send to the objective target, preserving multimodal content
                when provided via next_message.

        Raises:
            ValueError: If no response is received from the adversarial chat.
        """
        # If custom message provided, use it and bypass adversarial chat generation
        # Return the full Message to preserve multimodal content (images, audio, etc.)
        if context.next_message:
            logger.debug("Using custom message, bypassing adversarial chat")
            message = context.next_message
            # Clear to prevent reuse
            context.next_message = None
            return message

        # Generate prompt using adversarial chat
        logger.debug(f"Generating prompt for turn {context.executed_turns + 1}")

        # Prepare prompt for the adversarial chat
        prompt_text = await self._build_adversarial_prompt(context)

        # Send the prompt to the adversarial chat and get the response
        logger.debug(f"Sending prompt to adversarial chat: {prompt_text[:50]}...")
        prompt_message = Message.from_prompt(prompt=prompt_text, role="user")

        response = await self._prompt_normalizer.send_prompt_async(
            message=prompt_message,
            conversation_id=context.session.adversarial_chat_conversation_id,
            target=self._adversarial_chat,
            attack_identifier=self.get_identifier(),
            labels=context.memory_labels,
        )

        # Check if the response is valid
        if response is None:
            raise ValueError("Received no response from adversarial chat")

        # Return as a user message for sending to objective target
        return Message.from_prompt(prompt=response.get_value(), role="user")

    async def _build_adversarial_prompt(
        self,
        context: MultiTurnAttackContext[Any],
    ) -> str:
        """
        Build a prompt for the adversarial chat based on the last response.

        Args:
            context (MultiTurnAttackContext): The attack context containing the current state and configuration.

        Returns:
            str: The prompt to be sent to the adversarial chat.
        """
        # If no last response, return the seed prompt (rendered with objective if template exists)
        if not context.last_response:
            return self._adversarial_chat_seed_prompt.render_template_value_silent(objective=context.objective)

        # Get the last assistant piece from the response
        response_piece = context.last_response.get_piece()

        # Delegate to appropriate handler based on data type
        handlers = {
            "text": self._handle_adversarial_text_response,
            "error": self._handle_adversarial_text_response,
        }

        handler = handlers.get(response_piece.converted_value_data_type, self._handle_adversarial_file_response)

        return handler(context=context)

    def _handle_adversarial_text_response(self, *, context: MultiTurnAttackContext[Any]) -> str:
        """
        Handle the text response from the target by appending any
        available scoring feedback to the returned text. If the response
        indicates a block or error, return a fallback message instead.

        Args:
            context (MultiTurnAttackContext): The attack context containing the response and score.

        Returns:
            str: The text to be sent to the adversarial chat in the next turn.
        """
        if not context.last_response:
            return "No response available. Please continue."

        response_piece = context.last_response.get_piece()

        if not response_piece.has_error():
            # if response has no error, we can use the converted value
            prompt_text = response_piece.converted_value
            if not prompt_text:
                logger.warning("Received no converted_value from response")
                return "The previous response was empty. Please continue."

            # if we have feedback, append it to the prompt
            # to provide more context to the adversarial chat
            if self._use_score_as_feedback and context.last_score:
                prompt_text += f"\n\n{context.last_score.score_rationale}"
            return prompt_text

        elif response_piece.is_blocked():
            return RedTeamingAttack.DEFAULT_ADVERSARIAL_PROMPT_IF_OBJECTIVE_TARGET_IS_BLOCKED

        return f"Request to target failed: {response_piece.response_error}"

    def _handle_adversarial_file_response(self, *, context: MultiTurnAttackContext[Any]) -> str:
        """
        Handle the file response from the target.

        If the response indicates an error, raise a RuntimeError. When scoring is disabled or no
        scoring rationale is provided, raise a ValueError. Otherwise, return the textual feedback as the prompt.

        Args:
            context (MultiTurnAttackContext): The attack context containing the response and score.

        Returns:
            str: The suitable feedback or error message to pass back to the adversarial chat.

        Raises:
            RuntimeError: If the target response indicates an error.
            ValueError: If scoring is disabled or no scoring rationale is available.
        """
        if not context.last_response:
            return "No response available. Please continue."

        response_piece = context.last_response.get_piece()

        if response_piece.has_error():
            raise RuntimeError(
                "Request to target failed despite the returned data type "
                f"{response_piece.converted_value_data_type}: "
                f"{response_piece.response_error}"
            )

        if not self._use_score_as_feedback:
            # If scoring is not used as feedback, we cannot use the score rationale
            # to provide feedback to the adversarial chat
            raise ValueError(
                f"{RedTeamingAttack.DEFAULT_ERR_MSG_IF_OBJECTIVE_TARGET_HAS_NON_TEXT_RESPONSE}"
                "However, the use_score_as_feedback flag is set to False so it cannot be utilized."
            )

        feedback = context.last_score.score_rationale if context.last_score else None
        if not feedback:
            raise ValueError(
                f"{RedTeamingAttack.DEFAULT_ERR_MSG_IF_OBJECTIVE_TARGET_HAS_NON_TEXT_RESPONSE}"
                "However, no scoring rationale was provided by the scorer."
            )

        return feedback

    async def _send_prompt_to_objective_target_async(
        self,
        *,
        context: MultiTurnAttackContext[Any],
        message: Message,
    ) -> Message:
        """
        Send a message to the target system.

        Sends the message to the target via the prompt normalizer,
        and returns the response as a Message.

        Args:
            context (MultiTurnAttackContext): The current attack context.
            message (Message): The message to send to the target, which may contain
                multimodal content (text, images, audio, etc.).

        Returns:
            Message: The system's response to the message.

        Raises:
            ValueError: If no response is received from the target system.
        """
        logger.info(f"Sending prompt to target: {message.get_value()[:50]}...")

        # Send the message to the target
        response = await self._prompt_normalizer.send_prompt_async(
            message=message,
            conversation_id=context.session.conversation_id,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            target=self._objective_target,
            labels=context.memory_labels,
            attack_identifier=self.get_identifier(),
        )

        if response is None:
            # Easiest way to handle this is to raise an error
            # since we cannot continue without a response
            # A proper way to handle this would be to either retry or mark the return as Optional and return None
            # but this would require a lot of changes in the code
            raise ValueError(
                "Received no response from the target system. "
                "Please check the target configuration and ensure it is reachable."
            )

        return response

    async def _score_response_async(self, *, context: MultiTurnAttackContext[Any]) -> Optional[Score]:
        """
        Evaluate the objective target's response with the objective scorer.

        Checks if the response is blocked before scoring.
        Returns the resulting Score object or None if the response was blocked.

        Args:
            context (MultiTurnAttackContext): The attack context containing the response to score.

        Returns:
            Optional[Score]: The score of the response if available, otherwise None.
        """
        if not context.last_response:
            logger.warning("No response available in context to score")
            return None

        # score_async handles blocked, filtered, other errors
        scoring_results = await self._objective_scorer.score_async(
            message=context.last_response,
            role_filter="assistant",
            objective=context.objective,
        )
        objective_scores = scoring_results
        return objective_scores[0] if objective_scores else None

    def _set_adversarial_chat_seed_prompt(self, *, seed_prompt: Union[str, SeedPrompt]) -> None:
        """
        Set the seed prompt for the adversarial chat.

        Args:
            seed_prompt (Union[str, SeedPrompt]): The seed prompt to set for the adversarial chat.

        Raises:
            ValueError: If the seed prompt is not a string or SeedPrompt object.
        """
        if isinstance(seed_prompt, str):
            self._adversarial_chat_seed_prompt = SeedPrompt(value=seed_prompt, data_type="text")
        elif isinstance(seed_prompt, SeedPrompt):
            self._adversarial_chat_seed_prompt = seed_prompt
        else:
            raise ValueError("Seed prompt must be a string or SeedPrompt object.")
