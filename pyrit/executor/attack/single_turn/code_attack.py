# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib
import uuid
from typing import Any, Literal

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack.core import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import SingleTurnAttackContext
from pyrit.models import Message, SeedPrompt
from pyrit.converter.code_attack_converter import CodeAttackConverter
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# CodeAttackAttack builds its own system prompt and encodes the objective via
# the converter, so callers cannot inject prepended_conversation or next_message.
CodeAttackParameters = AttackParameters.excluding("prepended_conversation", "next_message")


class CodeAttackAttack(PromptSendingAttack):
    """
    Implement the CodeAttack method [@ren2024codeattack].

    The objective is encoded word-by-word into a data-structure initialisation
    sequence (deque appends, list appends, or string assignment) and embedded
    inside a code template that asks the model to complete the code. A system
    prompt frames the session as a code-completion environment. Because the
    harmful intent is expressed as a programming task, natural-language safety
    training fails to trigger consistently.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: AttackConverterConfig | None = None,
        attack_scoring_config: AttackScoringConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        max_attempts_on_failure: int = 0,
        language: Literal["python_stack", "python_list", "python_string", "cpp", "go"] = "python_stack",
        verbose: bool = True,
    ) -> None:
        """
        Args:
            objective_target: The target system to attack.
            attack_converter_config: Optional additional converter configuration.
                The CodeAttack converter is always prepended first.
            attack_scoring_config: Configuration for scoring components.
            prompt_normalizer: Optional normalizer override.
            max_attempts_on_failure: Additional retry attempts after the first
                failure.
            language: Data-structure family to use for encoding. One of
                ``"python_stack"``, ``"python_list"``, ``"python_string"``,
                ``"cpp"``, ``"go"``.
            verbose: When ``True`` (default) the ``_plus`` template variant is
                used, requesting detailed paragraphs. When ``False`` the
                standard variant requests numbered steps. Intentionally a
                no-op for ``"cpp"`` and ``"go"`` (no plus variant exists
                upstream); both values resolve to the same template.
        """
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=CodeAttackParameters,
        )

        code_converter = ConverterConfiguration.from_converters(
            converters=[CodeAttackConverter(language=language, verbose=verbose)]
        )
        self._request_converters = code_converter + self._request_converters

        system_prompt_path = pathlib.Path(EXECUTOR_SEED_PROMPT_PATH) / "code_attack.yaml"
        system_prompt = SeedPrompt.from_yaml_file(system_prompt_path).value
        self._system_prompt = Message.from_system_prompt(system_prompt=system_prompt)

    async def _setup_async(self, *, context: SingleTurnAttackContext[Any]) -> None:
        """
        Prepare the code-completion session context.

        Sets the conversation ID and injects the system prompt that frames
        the target as a code-completion environment.

        Args:
            context: The attack context for this execution.
        """
        context.conversation_id = str(uuid.uuid4())
        context.prepended_conversation = [self._system_prompt]

        await self._conversation_manager.initialize_context_async(
            context=context,
            target=self._objective_target,
            conversation_id=context.conversation_id,
            memory_labels=self._memory_labels,
        )
