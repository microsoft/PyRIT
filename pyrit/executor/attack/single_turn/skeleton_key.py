# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from typing import Any, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import (
    SingleTurnAttackContext,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    SeedDataset,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# SkeletonKeyAttack does not support prepended conversations
# as it manages its own conversation flow with the skeleton key prompt.
SkeletonKeyAttackParameters = AttackParameters.excluding("prepended_conversation", "next_message")


class SkeletonKeyAttack(PromptSendingAttack):
    """
    Implementation of the skeleton key jailbreak attack strategy.

    This attack sends an initial skeleton key prompt to the target, and then follows
    up with a separate attack prompt. If successful, the first prompt makes the target
    comply even with malicious follow-up prompts.

    The attack flow consists of:
    1. Sending a skeleton key prompt to bypass the target's safety mechanisms.
    2. Sending the actual objective prompt to the primed target.
    3. Evaluating the response using configured scorers to determine success.

    Learn more about attack at the link below:
    https://www.microsoft.com/en-us/security/blog/2024/06/26/mitigating-skeleton-key-a-new-type-of-generative-ai-jailbreak-technique/
    """

    # Default skeleton key prompt path
    DEFAULT_SKELETON_KEY_PROMPT_PATH: Path = Path(EXECUTOR_SEED_PROMPT_PATH) / "skeleton_key" / "skeleton_key.prompt"

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        skeleton_key_prompt: Optional[str] = None,
        max_attempts_on_failure: int = 0,
    ) -> None:
        """
        Initialize the skeleton key attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack.
            attack_converter_config (Optional[AttackConverterConfig]): Configuration for prompt converters.
            attack_scoring_config (Optional[AttackScoringConfig]): Configuration for scoring components.
            prompt_normalizer (Optional[PromptNormalizer]): Normalizer for handling prompts.
            skeleton_key_prompt (Optional[str]): The skeleton key prompt to use.
                If not provided, uses the default skeleton key prompt.
            max_attempts_on_failure (int): Maximum number of attempts to retry on failure.
        """
        # Initialize base class
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=SkeletonKeyAttackParameters,
        )

        # Load skeleton key prompt
        self._skeleton_key_prompt = self._load_skeleton_key_prompt(skeleton_key_prompt)

    def _load_skeleton_key_prompt(self, skeleton_key_prompt: Optional[str]) -> str:
        """
        Load the skeleton key prompt from the provided string or default file.

        Args:
            skeleton_key_prompt (Optional[str]): Custom skeleton key prompt if provided.

        Returns:
            str: The skeleton key prompt to use.
        """
        if skeleton_key_prompt:
            return skeleton_key_prompt

        return SeedDataset.from_yaml_file(self.DEFAULT_SKELETON_KEY_PROMPT_PATH).prompts[0].value

    async def _perform_async(self, *, context: SingleTurnAttackContext[Any]) -> AttackResult:
        """
        Execute the skeleton key attack by first sending the skeleton key prompt,
        then sending the objective prompt and evaluating the response.

        Args:
            context: The attack context with objective and parameters.

        Returns:
            AttackResult containing the outcome of the attack.
        """
        self._logger.info(f"Starting skeleton key attack with objective: {context.objective}")

        # Attack Execution Flow:
        # 1) Send skeleton key prompt to prime the target
        # 2) Check if skeleton key was successful (not filtered)
        # 3) If successful, execute the parent's attack flow with the objective
        # 4) Update the result to reflect the two-turn nature of skeleton key

        # Step 1: Send the skeleton key prompt to prime the target
        skeleton_response = await self._send_skeleton_key_prompt_async(context=context)

        # Step 2: Check if skeleton key was filtered or failed
        if not skeleton_response:
            self._logger.info("Attack failed: skeleton key prompt was filtered")
            return self._create_skeleton_key_failure_result(context=context)

        # Step 3: Execute the parent's attack flow to send objective and score
        result = await super()._perform_async(context=context)

        # Step 4: Update result to reflect skeleton key attack specifics
        result.executed_turns = 2  # Two turns: skeleton key + objective

        return result

    async def _send_skeleton_key_prompt_async(self, *, context: SingleTurnAttackContext[Any]) -> Optional[Message]:
        """
        Send the skeleton key prompt to the target to prime it for the attack.

        Args:
            context (SingleTurnAttackContext): The attack context containing configuration.

        Returns:
            Optional[Message]: The response from the target, or None if filtered.
        """
        self._logger.debug("Sending skeleton key prompt to target")

        # Create message for skeleton key
        skeleton_key_message = Message.from_prompt(prompt=self._skeleton_key_prompt, role="user")

        # Send skeleton key prompt
        skeleton_response = await self._send_prompt_to_objective_target_async(
            message=skeleton_key_message, context=context
        )

        if skeleton_response:
            self._logger.debug("Skeleton key prompt accepted by target")
        else:
            self._logger.warning("Skeleton key prompt was filtered or failed")

        return skeleton_response

    def _create_skeleton_key_failure_result(self, *, context: SingleTurnAttackContext[Any]) -> AttackResult:
        """
        Create an attack result for when the skeleton key prompt fails.

        Args:
            context (SingleTurnAttackContext): The attack context.

        Returns:
            AttackResult: The failure result.
        """
        return AttackResult(
            conversation_id=context.conversation_id,
            objective=context.objective,
            attack_identifier=self.get_identifier(),
            last_response=None,
            last_score=None,
            outcome=AttackOutcome.FAILURE,
            outcome_reason="Skeleton key prompt was filtered or failed",
            executed_turns=1,
        )
