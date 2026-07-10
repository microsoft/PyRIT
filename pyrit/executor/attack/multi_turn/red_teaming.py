# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.common.utils import warn_if_set
from pyrit.exceptions import ComponentRole, execution_context
from pyrit.executor.attack.component import (
    ConversationManager,
    _AdversarialConversationManager,
    get_adversarial_chat_messages,
)
from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
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
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    Conversation,
    ConversationReference,
    ConversationType,
    Message,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.prompt_target.common.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# RedTeamingAttack sets a system prompt on its adversarial target and drives a multi-turn dialogue
# through it. Both capabilities must be natively supported — adaptation would silently change the
# semantics (e.g. history-squash normalization would collapse the dialogue into a single turn).
_ADVERSARIAL_REQUIREMENTS = TargetRequirements(
    native_required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT}),
)


class RTASystemPromptPaths(enum.Enum):
    """Enum for predefined red teaming attack system prompt paths."""

    TEXT_GENERATION = Path(EXECUTOR_RED_TEAM_PATH, "text_generation.yaml").resolve()
    IMAGE_GENERATION = Path(EXECUTOR_RED_TEAM_PATH, "image_generation.yaml").resolve()
    NAIVE_CRESCENDO = Path(EXECUTOR_RED_TEAM_PATH, "naive_crescendo.yaml").resolve()
    VIOLENT_DURIAN = Path(EXECUTOR_RED_TEAM_PATH, "violent_durian.yaml").resolve()


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

    The strategy supports customization through system prompts, seed prompts, and converters,
    allowing for various attack techniques and scenarios.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_adversarial_config: AttackAdversarialConfig,
        attack_converter_config: AttackConverterConfig | None = None,
        attack_scoring_config: AttackScoringConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        max_turns: int = 10,
        score_last_turn_only: bool = False,
    ) -> None:
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
        # The adversarial target must natively support multi-turn dialogue and system prompts;
        # the class-level ``TARGET_REQUIREMENTS`` only covers ``objective_target``.
        try:
            _ADVERSARIAL_REQUIREMENTS.validate(target=self._adversarial_chat)
        except ValueError as exc:
            raise ValueError(f"RedTeamingAttack {exc}") from exc

        # Router that decides — based on each target's declared capabilities —
        # whether prior media should travel back to the adversarial chat or
        # forward to the objective target, and that fills in adversarial
        # placeholders when ``next_message`` carries seed media.
        self._modality_router = _ModalityFeedbackRouter(
            adversarial_chat=self._adversarial_chat,
            objective_target=objective_target,
        )

        # The manager owns adversarial-prompt resolution: it resolves the system prompt, coerces the
        # first / next-message templates (applying the canonical defaults when unset), and fails fast
        # when a response schema is declared on both the system prompt and the first message.
        self._resolved_adversarial = _AdversarialConversationManager.resolve_config(
            config=attack_adversarial_config,
            default_system_prompt_path=RTASystemPromptPaths.TEXT_GENERATION.value,
            system_prompt_required_parameters=["objective"],
            system_prompt_error_message="Adversarial seed prompt must have an objective",
            resolve_user_messages=True,
        )
        self._adversarial_chat_system_prompt_template = self._resolved_adversarial.system_prompt
        self._adversarial_chat_first_message = self._resolved_adversarial.first_message
        self._adversarial_prompt_template = self._resolved_adversarial.next_message_template

        # Initialize utilities
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()

        self._conversation_manager = ConversationManager()

        # set the maximum number of turns for the attack
        if max_turns <= 0:
            raise ValueError("Maximum turns must be a positive integer.")

        self._max_turns = max_turns
        self._score_last_turn_only = score_last_turn_only

    def get_attack_scoring_config(self) -> AttackScoringConfig | None:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            AttackScoringConfig | None: The scoring configuration with objective scorer
                and use_score_as_feedback.
        """
        return AttackScoringConfig(
            objective_scorer=self._objective_scorer,
            use_score_as_feedback=self._use_score_as_feedback,
        )

    def get_attack_adversarial_config(self) -> AttackAdversarialConfig | None:
        """
        Get the effective adversarial configuration used by this strategy.

        Returns:
            AttackAdversarialConfig | None: The adversarial target with its resolved system prompt
                and first-message seed prompt.
        """
        adversarial_chat = getattr(self, "_adversarial_chat", None)
        if adversarial_chat is None:
            return None
        return AttackAdversarialConfig(
            target=adversarial_chat,
            system_prompt=self._adversarial_chat_system_prompt_template,
            first_message=self._adversarial_chat_first_message,
            adversarial_prompt_template=self._adversarial_prompt_template,
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

        # Fail fast if the objective target requires media on turn 0 but
        # ``next_message`` does not supply any (i.e. edit-only mode without a seed).
        self._modality_router.validate_first_turn_seed(next_message=context.next_message)

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

        # The adversarial conversation manager owns rendering and setting the system prompt.
        # ``set_system_prompt`` rejects any conversation that already has messages, so this must run
        # before we hydrate the adversarial chat with the swapped prepended turns below.
        self._build_adversarial_manager(context=context).set_adversarial_system_prompt()

        # Set up adversarial chat with prepended conversation
        if context.prepended_conversation:
            # Get adversarial messages with swapped roles
            adversarial_messages = get_adversarial_chat_messages(
                prepended_conversation=context.prepended_conversation,
                adversarial_chat_conversation_id=context.session.adversarial_chat_conversation_id,
            )
            if context.memory_labels:
                for msg in adversarial_messages:
                    for piece in msg.message_pieces:
                        piece.labels = context.memory_labels

            self._memory.add_conversation_to_memory(
                conversation=Conversation(
                    conversation_id=context.session.adversarial_chat_conversation_id,
                    target_identifier=self._adversarial_chat.get_identifier(),
                )
            )
            for msg in adversarial_messages:
                self._memory.add_message_to_memory(request=msg)

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

        # Build the adversarial conversation manager once for this execution and reuse it across
        # turns. Its conversation scope (ids, objective, memory labels) is fixed for the run, so
        # there is no need to rebuild it each turn.
        adversarial_manager = self._build_adversarial_manager(context=context)

        # Execute conversation turns
        while context.executed_turns < self._max_turns and (self._score_last_turn_only or not achieved_objective):
            logger.info(f"Executing turn {context.executed_turns + 1}/{self._max_turns}")

            # Determine what to send next
            message_to_send = await self._generate_next_prompt_async(
                context=context, adversarial_manager=adversarial_manager
            )

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
            atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=self.get_identifier()),
            conversation_id=context.session.conversation_id,
            objective=context.objective,
            outcome=(AttackOutcome.SUCCESS if achieved_objective else AttackOutcome.FAILURE),
            executed_turns=context.executed_turns,
            last_response=context.last_response.get_piece() if context.last_response else None,
            last_score=context.last_score,
            related_conversations=context.related_conversations,
            labels=context.memory_labels,
        )

    async def _teardown_async(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """Clean up after attack execution."""
        # Nothing to be done here, no-op

    def _build_adversarial_manager(self, *, context: MultiTurnAttackContext[Any]) -> _AdversarialConversationManager:
        """
        Build the adversarial conversation manager for this execution.

        The manager is scoped to a single run's adversarial conversation — its conversation ids,
        objective, and memory labels come from ``context``. It holds no per-turn mutable state, so
        it is rebuilt where needed (setup and the turn loop) rather than threaded through the context.

        Args:
            context (MultiTurnAttackContext): The attack context supplying the per-run conversation
                ids, objective, and memory labels.

        Returns:
            _AdversarialConversationManager: The manager driving this run's adversarial chat.
        """
        return _AdversarialConversationManager(
            adversarial_target=self._adversarial_chat,
            adversarial_system_prompt=self._adversarial_chat_system_prompt_template,
            adversarial_first_user_message=self._adversarial_chat_first_message,
            adversarial_next_user_message=self._adversarial_prompt_template,
            max_turns=self._max_turns,
            prompt_normalizer=self._prompt_normalizer,
            conversation_id=context.session.adversarial_chat_conversation_id,
            objective=context.objective,
            objective_target_conversation_id=context.session.conversation_id,
            attack_strategy_name=self.__class__.__name__,
            memory_labels=context.memory_labels,
            modality_router=self._modality_router,
            use_score_as_feedback=self._use_score_as_feedback,
        )

    async def _generate_next_prompt_async(
        self,
        context: MultiTurnAttackContext[Any],
        *,
        adversarial_manager: _AdversarialConversationManager | None = None,
    ) -> Message:
        """
        Generate the next message to send to the objective target this turn.

        Delegates the full adversarial contract to the conversation manager, which owns the bypass
        path, first-turn vs. subsequent-turn prompt selection, the send/parse/schema/retry cycle, and
        weaving prior/seed media into the objective message (or filling adversarial placeholders). The
        returned ``objective_message`` is ready to send as-is.

        Args:
            context (MultiTurnAttackContext): The attack context containing the current state and configuration.
            adversarial_manager (_AdversarialConversationManager | None): The manager driving this
                run's adversarial chat. Supplied by ``_perform_async``; when ``None`` (e.g. a direct
                call) it is built on demand from ``context``.

        Returns:
            Message: The message to send to the objective target.

        Raises:
            ValueError: If no response is received from the adversarial chat.
        """
        if adversarial_manager is None:
            adversarial_manager = self._build_adversarial_manager(context=context)

        # A caller-supplied ``next_message`` seeds a single turn; clear it so it is not reused.
        seed_message = context.next_message
        context.next_message = None

        turn = await adversarial_manager.get_next_message_async(
            turn_index=context.executed_turns,
            seed_message=seed_message,
            last_response=context.last_response,
            score=context.last_score,
        )
        return turn.objective_message

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

        # For single-turn targets, rotate conversation_id so each turn starts fresh
        self._rotate_conversation_for_single_turn_target(context=context)

        with execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name=self.__class__.__name__,
            component_identifier=self._objective_target.get_identifier(),
            objective_target_conversation_id=context.session.conversation_id,
            objective=context.objective,
        ):
            # Send the message to the target
            if context.memory_labels:
                for piece in message.message_pieces:
                    piece.labels = context.memory_labels
            response = await self._prompt_normalizer.send_prompt_async(
                message=message,
                conversation_id=context.session.conversation_id,
                request_converter_configurations=self._request_converters,
                response_converter_configurations=self._response_converters,
                target=self._objective_target,
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

    async def _score_response_async(self, *, context: MultiTurnAttackContext[Any]) -> Score | None:
        """
        Evaluate the objective target's response with the objective scorer.

        Checks if the response is blocked before scoring.
        Returns the resulting Score object or None if the response was blocked.

        Args:
            context (MultiTurnAttackContext): The attack context containing the response to score.

        Returns:
            Score | None: The score of the response if available, otherwise None.
        """
        if not context.last_response:
            logger.warning("No response available in context to score")
            return None

        with execution_context(
            component_role=ComponentRole.OBJECTIVE_SCORER,
            attack_strategy_name=self.__class__.__name__,
            component_identifier=self._objective_scorer.get_identifier(),
            objective_target_conversation_id=context.session.conversation_id,
            objective=context.objective,
        ):
            # score_async handles blocked, filtered, other errors
            scoring_results = await self._objective_scorer.score_async(
                message=context.last_response,
                role_filter="assistant",
                objective=context.objective,
            )

        objective_scores = scoring_results
        return objective_scores[0] if objective_scores else None
