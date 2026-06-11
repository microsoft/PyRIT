# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import (
    SingleTurnAttackContext,
)
from pyrit.models import (
    Message,
    SeedDataset,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# SkeletonKeyAttack generates prepended_conversation internally from the skeleton key prompt and acceptance response.
SkeletonKeyAttackParameters = AttackParameters.excluding("prepended_conversation", "next_message")


class SkeletonKeyAttack(PromptSendingAttack):
    """
    Implementation of the skeleton key jailbreak attack strategy.

    This attack prepends a simulated skeleton key exchange to the conversation context before
    sending the actual objective prompt in a single turn. The prepended exchange consists of
    the skeleton key prompt (user) and a simulated acceptance response (assistant), priming
    the target to bypass its safety mechanisms.

    The attack flow consists of:
    1. Prepending [skeleton key prompt (user) + acceptance response (assistant)] as conversation history.
    2. Sending the actual objective prompt to the primed target.
    3. Evaluating the response using configured scorers to determine success.

    Learn more about the attack [@microsoft2024skeletonkey].
    """

    DEFAULT_SKELETON_KEY_PROMPT_PATH: Path = Path(EXECUTOR_SEED_PROMPT_PATH) / "skeleton_key" / "skeleton_key.prompt"
    DEFAULT_SKELETON_KEY_ACCEPTANCE_PATH: Path = (
        Path(EXECUTOR_SEED_PROMPT_PATH) / "skeleton_key" / "skeleton_key_acceptance.prompt"
    )

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: AttackConverterConfig | None = None,
        attack_scoring_config: AttackScoringConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        skeleton_key_prompt: str | None = None,
        skeleton_key_acceptance: str | None = None,
        max_attempts_on_failure: int = 0,
    ) -> None:
        """
        Initialize the skeleton key attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack.
            attack_converter_config (AttackConverterConfig | None): Configuration for prompt converters.
            attack_scoring_config (AttackScoringConfig | None): Configuration for scoring components.
            prompt_normalizer (PromptNormalizer | None): Normalizer for handling prompts.
            skeleton_key_prompt (str | None): The skeleton key prompt to prepend as the user turn.
                If not provided, uses the default skeleton key prompt.
            skeleton_key_acceptance (str | None): The simulated assistant acceptance response to prepend.
                If not provided, uses the default acceptance response.
            max_attempts_on_failure (int): Maximum number of attempts to retry on failure.
        """
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=SkeletonKeyAttackParameters,
        )

        self._skeleton_key_prompt = (
            skeleton_key_prompt
            if skeleton_key_prompt is not None
            else SeedDataset.from_yaml_file(self.DEFAULT_SKELETON_KEY_PROMPT_PATH).prompts[0].value
        )

        self._skeleton_key_acceptance = (
            skeleton_key_acceptance
            if skeleton_key_acceptance is not None
            else SeedDataset.from_yaml_file(self.DEFAULT_SKELETON_KEY_ACCEPTANCE_PATH).prompts[0].value
        )

    async def _setup_async(self, *, context: SingleTurnAttackContext[Any]) -> None:
        """
        Set up the attack by prepending the skeleton key exchange to the conversation context.

        Args:
            context (SingleTurnAttackContext): The attack context containing attack parameters.
        """
        context.prepended_conversation = [
            Message.from_prompt(prompt=self._skeleton_key_prompt, role="user"),
            Message.from_prompt(prompt=self._skeleton_key_acceptance, role="assistant"),
        ]

        await super()._setup_async(context=context)
