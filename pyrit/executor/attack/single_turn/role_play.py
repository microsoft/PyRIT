# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
import logging
import pathlib
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
    Message,
    SeedDataset,
)
from pyrit.prompt_converter import LLMGenericTextConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget

logger = logging.getLogger(__name__)


# RolePlayAttack generates next_message and prepended_conversation internally,
# so it does not accept these parameters from callers.
RolePlayAttackParameters = AttackParameters.excluding("next_message", "prepended_conversation")


class RolePlayPaths(enum.Enum):
    """Enum for predefined role-play scenario paths."""

    VIDEO_GAME = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "role_play" / "video_game.yaml"
    MOVIE_SCRIPT = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "role_play" / "movie_script.yaml"
    TRIVIA_GAME = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "role_play" / "trivia_game.yaml"
    PERSUASION_SCRIPT = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "role_play" / "persuasion_script.yaml"
    PERSUASION_SCRIPT_WRITTEN = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "role_play" / "persuasion_script_written.yaml"


class RolePlayAttack(PromptSendingAttack):
    """
    Implementation of single-turn role-play attack strategy.

    This class orchestrates a role-play attack where malicious objectives are rephrased
    into role-playing contexts to make them appear more benign and bypass content filters.
    The strategy uses an adversarial chat target to transform the objective into a role-play
    scenario before sending it to the target system.

    The attack flow consists of:
    1. Loading role-play scenarios from a YAML file.
    2. Using an adversarial chat target to rephrase the objective into the role-play context.
    3. Sending the rephrased objective to the target system.
    4. Evaluating the response with scorers if configured.
    5. Retrying on failure up to the configured number of retries.
    6. Returning the attack result

    The strategy supports customization through prepended conversations, converters,
    and multiple scorer types.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        adversarial_chat: PromptChatTarget,
        role_play_definition_path: pathlib.Path,
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        max_attempts_on_failure: int = 0,
    ) -> None:
        """
        Initialize the role-play attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack.
            adversarial_chat (PromptChatTarget): The adversarial chat target used to rephrase
                objectives into role-play scenarios.
            role_play_definition_path (pathlib.Path): Path to the YAML file containing role-play
                definitions (rephrase instructions, user start turn, assistant start turn).
            attack_converter_config (Optional[AttackConverterConfig]): Configuration for prompt converters.
            attack_scoring_config (Optional[AttackScoringConfig]): Configuration for scoring components.
            prompt_normalizer (Optional[PromptNormalizer]): Normalizer for handling prompts.
            max_attempts_on_failure (int): Maximum number of attempts to retry the attack

        Raises:
            ValueError: If the objective scorer is not a true/false scorer.
            FileNotFoundError: If the role_play_definition_path does not exist.
        """
        # Initialize the parent class first
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=RolePlayAttackParameters,
        )

        # Store the adversarial chat for role-play rephrasing
        self._adversarial_chat = adversarial_chat

        # Load role-play definitions
        role_play_definition = SeedDataset.from_yaml_file(role_play_definition_path)

        # Validate role-play definition structure
        self._parse_role_play_definition(role_play_definition)

        # Create the rephrase converter configuration
        self._rephrase_converter = PromptConverterConfiguration.from_converters(
            converters=[
                LLMGenericTextConverter(
                    converter_target=self._adversarial_chat,
                    user_prompt_template_with_objective=self._rephrase_instructions,
                )
            ]
        )

    async def _setup_async(self, *, context: SingleTurnAttackContext[Any]) -> None:
        """
        Set up the attack by preparing conversation context with role-play start
        and converting the objective to role-play format.

        Args:
            context (SingleTurnAttackContext): The attack context containing attack parameters.
        """
        # Get role-play conversation start (turns 0 and 1)
        context.prepended_conversation = await self._get_conversation_start() or []

        # Rephrase the objective using the LLM converter
        # This converts the user's objective into a role-play scenario
        rephrased_objective = await self._rephrase_objective_async(objective=context.objective)

        # Set the rephrased objective as the message
        # This will be used by _get_message() to send the rephrased content to the target
        context.next_message = Message.from_prompt(prompt=rephrased_objective, role="user")

        # Call parent setup which handles conversation ID generation, memory labels, etc.
        await super()._setup_async(context=context)

    async def _rephrase_objective_async(self, *, objective: str) -> str:
        """
        Rephrase the objective into a role-play scenario using the adversarial chat.

        Args:
            objective (str): The original objective to rephrase.

        Returns:
            str: The rephrased objective in role-play format.
        """
        # Use the LLMGenericTextConverter to rephrase the objective
        converter = self._rephrase_converter[0].converters[0]
        result = await converter.convert_async(prompt=objective, input_type="text")
        return result.output_text

    async def _get_conversation_start(self) -> Optional[list[Message]]:
        """
        Get the role-play conversation start messages.

        Returns:
            Optional[list[Message]]: List containing user and assistant start turns
                for the role-play scenario.
        """
        return [
            Message.from_prompt(
                prompt=self._user_start_turn.value,
                role="user",
            ),
            Message.from_prompt(
                prompt=self._assistant_start_turn.value,
                role="assistant",
            ),
        ]

    def _parse_role_play_definition(self, role_play_definition: SeedDataset) -> None:
        """
        Parse and validate the role-play definition structure.

        Args:
            role_play_definition (SeedDataset): The role-play definition dataset to validate.

        Raises:
            ValueError: If the definition does not contain exactly 3 prompts or if any prompt is empty.
        """
        if len(role_play_definition.prompts) != 3:
            raise ValueError(
                f"Role-play definition must contain 3 prompts, but found {len(role_play_definition.prompts)}. "
                "Expected: [rephrase_instructions, user_start_turn, assistant_start_turn]"
            )
        for i, prompt in enumerate(role_play_definition.prompts):
            if not prompt.value or not prompt.value.strip():
                prompt_names = ["rephrase_instructions", "user_start_turn", "assistant_start_turn"]
                raise ValueError(f"Role-play definition prompt '{prompt_names[i]}' cannot be empty")

        self._rephrase_instructions = role_play_definition.prompts[0]
        self._user_start_turn = role_play_definition.prompts[1]
        self._assistant_start_turn = role_play_definition.prompts[2]
