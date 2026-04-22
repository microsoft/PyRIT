# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from typing import Any, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.path import DATASETS_PATH, JAILBREAK_TEMPLATES_PATH
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import SingleTurnAttackContext
from pyrit.models import AttackResult, Message, SeedPrompt
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# ManyShotJailbreakAttack does not support prepended conversations
# as it constructs its own prompt format with examples.
ManyShotJailbreakParameters = AttackParameters.excluding("prepended_conversation", "next_message")

_MANY_SHOT_EXAMPLES_PATH = DATASETS_PATH / "jailbreak" / "many_shot_examples.json"


def load_many_shot_jailbreaking_dataset() -> list[dict[str, str]]:
    """
    Load the bundled many-shot jailbreaking examples from the local dataset file.

    Returns:
        list[dict[str, str]]: A list of many-shot jailbreaking examples.
    """
    with open(_MANY_SHOT_EXAMPLES_PATH, encoding="utf-8") as f:
        data: list[dict[str, str]] = json.load(f)
        return data


def fetch_many_shot_jailbreaking_dataset() -> list[dict[str, str]]:
    """
    Load many-shot jailbreaking examples (deprecated, use load_many_shot_jailbreaking_dataset).

    Returns:
        list[dict[str, str]]: A list of many-shot jailbreaking examples.
    """
    print_deprecation_message(
        old_item=fetch_many_shot_jailbreaking_dataset,
        new_item=load_many_shot_jailbreaking_dataset,
        removed_in="0.14.0",
    )
    return load_many_shot_jailbreaking_dataset()


class ManyShotJailbreakAttack(PromptSendingAttack):
    """
    Implement the Many Shot Jailbreak method [@anthropic2024manyshot].

    Prepends the seed prompt with a faux dialogue between a human and an AI, using examples from a dataset
    to demonstrate successful jailbreaking attempts. This method leverages the model's ability to learn from
    examples to bypass safety measures.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        max_attempts_on_failure: int = 0,
        example_count: int = 100,
        many_shot_examples: Optional[list[dict[str, str]]] = None,
    ) -> None:
        """
        Args:
            objective_target (PromptTarget): The target system to attack.
            attack_converter_config (AttackConverterConfig, Optional): Configuration for the prompt converters.
            attack_scoring_config (AttackScoringConfig, Optional): Configuration for scoring components.
            prompt_normalizer (PromptNormalizer, Optional): Normalizer for handling prompts.
            max_attempts_on_failure (int, Optional): Maximum number of attempts to retry on failure. Defaults to 0.
            example_count (int): The number of examples to include from many_shot_examples or the Many
                Shot Jailbreaking dataset. Defaults to the first 100.
            many_shot_examples (list[dict[str, str]], Optional): The many shot jailbreaking examples to use.
                If not provided, takes the first `example_count` examples from Many Shot Jailbreaking dataset.

        Raises:
            ValueError: If many_shot_examples is empty.
        """
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=ManyShotJailbreakParameters,
        )

        # Template for the faux dialogue to be prepended
        template_path = JAILBREAK_TEMPLATES_PATH / "multi_parameter" / "many_shot_template.yaml"
        self._template = SeedPrompt.from_yaml_file(template_path)
        # Fetch the Many Shot Jailbreaking example dataset
        self._examples = (
            many_shot_examples[:example_count]
            if (many_shot_examples is not None)
            else load_many_shot_jailbreaking_dataset()[:example_count]
        )
        if not self._examples:
            raise ValueError("Many shot examples must be provided.")

    async def _perform_async(self, *, context: SingleTurnAttackContext[Any]) -> AttackResult:
        """
        Perform the ManyShotJailbreakAttack.

        Args:
            context (SingleTurnAttackContext): The attack context containing attack parameters.

        Returns:
            AttackResult: The result of the attack.
        """
        many_shot_prompt = self._template.render_template_value(prompt=context.objective, examples=self._examples)
        context.next_message = Message.from_prompt(prompt=many_shot_prompt, role="user")

        return await super()._perform_async(context=context)
