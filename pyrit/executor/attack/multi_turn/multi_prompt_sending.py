# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, Type

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.utils import get_kwarg_param
from pyrit.executor.attack.component import ConversationManager
from pyrit.executor.attack.core.attack_config import (
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import (
    ConversationSession,
    MultiTurnAttackContext,
    MultiTurnAttackStrategy,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    Score,
    SeedAttackGroup,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptChatTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MultiPromptSendingAttackParameters(AttackParameters):
    """
    Parameters for MultiPromptSendingAttack.

    Extends AttackParameters to include user_messages field for multi-turn attacks.
    Only accepts objective and user_messages fields.
    """

    user_messages: Optional[List[Message]] = None

    @classmethod
    async def from_seed_group_async(
        cls: Type["MultiPromptSendingAttackParameters"],
        seed_group: SeedAttackGroup,
        *,
        adversarial_chat: Optional["PromptChatTarget"] = None,
        objective_scorer: Optional["TrueFalseScorer"] = None,
        **overrides: Any,
    ) -> "MultiPromptSendingAttackParameters":
        """
        Create parameters from a SeedGroup, extracting user messages.

        Args:
            seed_group: The seed group to extract parameters from.
            adversarial_chat: Not used by this attack type.
            objective_scorer: Not used by this attack type.
            **overrides: Field overrides to apply.

        Returns:
            MultiPromptSendingAttackParameters instance.

        Raises:
            ValueError: If seed_group has no objective, no user messages, or if overrides contain invalid fields.
        """
        # Extract objective (required)
        if seed_group.objective is None:
            raise ValueError("SeedGroup must have an objective")

        # Extract messages from seed group (required)
        user_messages = seed_group.user_messages
        if not user_messages:
            raise ValueError(
                "SeedGroup must have user_messages for MultiPromptSendingAttack. "
                "This attack requires multi-turn message sequences."
            )

        # Validate overrides only contain valid fields
        valid_fields = {"objective", "user_messages", "memory_labels"}
        invalid_fields = set(overrides.keys()) - valid_fields
        if invalid_fields:
            raise ValueError(
                f"MultiPromptSendingAttackParameters does not accept: {invalid_fields}. Only accepts: {valid_fields}"
            )

        # Build parameters with only objective, user_messages, and memory_labels
        return cls(
            objective=seed_group.objective.value,
            memory_labels=overrides.get("memory_labels", {}),
            user_messages=user_messages,
        )


class MultiPromptSendingAttack(MultiTurnAttackStrategy[MultiTurnAttackContext[Any], AttackResult]):
    """
    Implementation of multi-prompt sending attack strategy.

    This class orchestrates a multi-turn attack where a series of predefined malicious
    prompts are sent sequentially to try to achieve a specific objective against a target
    system. The strategy evaluates the final target response using optional scorers to
    determine if the objective has been met.

    The attack flow consists of:
    1. Sending each predefined prompt to the target system in sequence.
    2. Continuing until all predefined prompts are sent.
    3. Evaluating the final response with scorers if configured.
    4. Returning the attack result with achievement status.

    Note: This attack always runs all predefined prompts regardless of whether the
    objective is achieved early in the sequence.

    The strategy supports customization through prepended conversations, converters,
    and multiple scorer types for comprehensive evaluation.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
    ) -> None:
        """
        Initialize the multi-prompt sending attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack.
            attack_converter_config (Optional[AttackConverterConfig]): Configuration for prompt converters.
            attack_scoring_config (Optional[AttackScoringConfig]): Configuration for scoring components.
            prompt_normalizer (Optional[PromptNormalizer]): Normalizer for handling prompts.

        Raises:
            ValueError: If the objective scorer is not a true/false scorer.
        """
        # Initialize base class with custom parameters type
        super().__init__(
            objective_target=objective_target,
            logger=logger,
            context_type=MultiTurnAttackContext,
            params_type=MultiPromptSendingAttackParameters,
        )

        # Initialize the converter configuration
        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters

        # Initialize scoring configuration
        attack_scoring_config = attack_scoring_config or AttackScoringConfig()

        self._auxiliary_scorers = attack_scoring_config.auxiliary_scorers
        self._objective_scorer = attack_scoring_config.objective_scorer

        # Initialize prompt normalizer and conversation manager
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_manager = ConversationManager(
            attack_identifier=self.get_identifier(),
            prompt_normalizer=self._prompt_normalizer,
        )

    def get_attack_scoring_config(self) -> Optional[AttackScoringConfig]:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            Optional[AttackScoringConfig]: The scoring configuration with objective and auxiliary scorers.
        """
        return AttackScoringConfig(
            objective_scorer=self._objective_scorer,
            auxiliary_scorers=self._auxiliary_scorers,
        )

    def _validate_context(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """
        Validate the context before executing the attack.

        Args:
            context (MultiTurnAttackContext): The attack context containing parameters and objective.

        Raises:
            ValueError: If the context is invalid.
        """
        if not context.objective or context.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty in the context")

        if not context.params.user_messages or len(context.params.user_messages) == 0:
            raise ValueError("User messages must be provided and non-empty in the params")

    async def _setup_async(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """
        Set up the attack by preparing conversation context.

        Args:
            context (MultiTurnAttackContext): The attack context containing attack parameters.
        """
        # Ensure the context has a session (like red_teaming.py does)
        context.session = ConversationSession()

        # Initialize context with prepended conversation and merged labels
        await self._conversation_manager.initialize_context_async(
            context=context,
            target=self._objective_target,
            conversation_id=context.session.conversation_id,
            request_converters=self._request_converters,
            memory_labels=self._memory_labels,
        )

    async def _perform_async(self, *, context: MultiTurnAttackContext[Any]) -> AttackResult:
        """
        Perform the multi-prompt sending attack.

        Args:
            context: The attack context with objective, predefined prompt sequence and parameters.

        Returns:
            AttackResult containing the outcome of the attack.
        """
        # Log the attack configuration
        logger.info(f"Starting {self.__class__.__name__} with objective: {context.objective}")

        # Attack execution steps:
        # 1) Send each predefined malicious prompt to the target sequentially
        # 2) Continue until all the predefined prompts are sent
        # 3) Score the final response using the configured objective scorer
        # 4) Return an AttackResult object that captures the outcome of the attack

        response = None
        score = None

        for message_index, current_message in enumerate(context.params.user_messages):
            logger.info(f"Processing message {message_index + 1}/{len(context.params.user_messages)}")

            # Send the message directly
            response_message = await self._send_prompt_to_objective_target_async(
                current_message=current_message, context=context
            )

            # Update context with latest response (may be None if sending failed)
            if response_message:
                response = response_message
                context.last_response = response
                context.executed_turns += 1
                self._logger.debug(f"Successfully sent message {message_index + 1}")
            else:
                response = None
                self._logger.warning(f"Failed to send message {message_index + 1}, terminating")
                break

        # Score the last response including auxiliary and objective scoring
        if response is not None:
            score = await self._evaluate_response_async(response=response, objective=context.objective)
        else:
            score = None

        # Determine the outcome
        outcome, outcome_reason = self._determine_attack_outcome(response=response, score=score, context=context)

        result = AttackResult(
            conversation_id=context.session.conversation_id,
            objective=context.objective,
            attack_identifier=self.get_identifier(),
            last_response=response.get_piece() if response else None,
            last_score=score,
            related_conversations=context.related_conversations,
            outcome=outcome,
            outcome_reason=outcome_reason,
            executed_turns=context.executed_turns,
        )

        return result

    def _determine_attack_outcome(
        self,
        *,
        response: Optional[Message],
        score: Optional[Score],
        context: MultiTurnAttackContext[Any],
    ) -> tuple[AttackOutcome, Optional[str]]:
        """
        Determine the outcome of the attack based on the response and score.

        Args:
            response (Optional[Message]): The last response from the target (if any).
            score (Optional[Score]): The objective score (if any).
            context (MultiTurnAttackContext): The attack context containing configuration.

        Returns:
            tuple[AttackOutcome, Optional[str]]: A tuple of (outcome, outcome_reason).
        """
        if not self._objective_scorer:
            # No scorer means we can't determine success/failure
            return AttackOutcome.UNDETERMINED, "No objective scorer configured"

        if score and score.get_value():
            # We have a positive score, so it's a success
            return AttackOutcome.SUCCESS, "Objective achieved according to scorer"

        if response:
            # We got response(s) but the final response did not achieve the objective
            return (
                AttackOutcome.FAILURE,
                "Failed to achieve objective",
            )

        # At least one prompt was filtered or failed to get a response
        return AttackOutcome.FAILURE, "At least one prompt was filtered or failed to get a response"

    async def _teardown_async(self, *, context: MultiTurnAttackContext[Any]) -> None:
        """Clean up after attack execution."""
        # Nothing to be done here, no-op
        pass

    async def _send_prompt_to_objective_target_async(
        self, *, current_message: Message, context: MultiTurnAttackContext[Any]
    ) -> Optional[Message]:
        """
        Send the prompt to the target and return the response.

        Args:
            current_message (Message): The message to send.
            context (MultiTurnAttackContext): The attack context containing parameters and labels.

        Returns:
            Optional[Message]: The model's response if successful, or None if
                the request was filtered, blocked, or encountered an error.
        """
        return await self._prompt_normalizer.send_prompt_async(
            message=current_message,
            target=self._objective_target,
            conversation_id=context.session.conversation_id,
            request_converter_configurations=self._request_converters,
            response_converter_configurations=self._response_converters,
            labels=context.memory_labels,  # combined with strategy labels at _setup()
            attack_identifier=self.get_identifier(),
        )

    async def _evaluate_response_async(self, *, response: Message, objective: str) -> Optional[Score]:
        """
        Evaluate the response against the objective using the configured scorers.

        This method first runs all auxiliary scorers (if configured) to collect additional
        metrics, then runs the objective scorer to determine if the attack succeeded.

        Args:
            response (Message): The response from the model.
            objective (str): The natural-language description of the attack's objective.

        Returns:
            Optional[Score]: The score from the objective scorer if configured, or None if
                no objective scorer is set. Note that auxiliary scorer results are not returned
                but are still executed and stored.
        """
        scoring_results = await Scorer.score_response_async(
            response=response,
            auxiliary_scorers=self._auxiliary_scorers,
            objective_scorer=self._objective_scorer if self._objective_scorer else None,
            role_filter="assistant",
            objective=objective,
            skip_on_error_result=True,
        )

        objective_scores = scoring_results["objective_scores"]
        if not objective_scores:
            return None

        return objective_scores[0]

    async def execute_async(
        self,
        **kwargs: Any,
    ) -> AttackResult:
        """
        Execute the attack strategy asynchronously with the provided parameters.

        Returns:
            AttackResult: The result of the attack execution.
        """
        # Validate parameters before creating context
        user_messages = get_kwarg_param(kwargs=kwargs, param_name="user_messages", expected_type=list, required=True)

        return await super().execute_async(**kwargs, user_messages=user_messages)
