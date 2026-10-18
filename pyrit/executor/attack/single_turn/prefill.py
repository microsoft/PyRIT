# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.exceptions import ComponentRole, execution_context
from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import (
    SingleTurnAttackContext,
)
from pyrit.models import ComponentIdentifier, Message
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import CapabilityName, PromptTarget, TargetRequirements

logger = logging.getLogger(__name__)

# PrefillAttack builds the conversation internally: the objective is prepended as the user turn and
# the prefill is sent as the trailing assistant turn, so neither may be supplied by the caller.
PrefillAttackParameters = AttackParameters.excluding("prepended_conversation", "next_message")


class PrefillAttack(PromptSendingAttack):
    """
    Implementation of the prefill attack strategy.

    Some targets let the caller write the start of the model's reply: the request ends on an open
    assistant turn and the model continues it. This attack exploits that. It prepends the objective as
    the user turn and sends a trailing assistant message carrying ``prefill_text``, so the target
    continues the primed reply rather than answering from scratch, then scores the continuation.

    The attack flow consists of:
    1. Prepending the objective as the user turn of the conversation.
    2. Sending an assistant message carrying ``prefill_text`` as the trailing turn for the target to
       continue.
    3. Evaluating the continuation using configured scorers to determine success.

    ``TARGET_REQUIREMENTS`` requires the ``supports_prefill`` capability, so a target that ignores or
    rejects a trailing assistant turn (e.g. most hosted chat APIs) raises an error at construction
    rather than silently answering the user turn and looking like it resisted.

    The prefill is stored in memory with a simulated assistant role because the model never wrote it,
    and request converters apply to the objective only, not the prefill. The scorer sees only the
    continuation the target generates, not the prefill text that precedes it. Some chat templates
    rewrite assistant content (for example by dropping reasoning blocks) and may refuse to continue a
    prefill that they cannot reproduce exactly.

    Learn more about prefill attacks [@struppek2026prefill; @dotsinski2026sockpuppetting].
    """

    # Prefill only works on targets that continue a trailing assistant turn; adaptation cannot add
    # that behavior, so it must be natively supported. The objective travels as live prepended history,
    # which is only replayed faithfully by targets with native multi-turn and editable history.
    TARGET_REQUIREMENTS = TargetRequirements(
        native_required=frozenset({CapabilityName.PREFILL, CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY}),
    )

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        prefill_text: str = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: AttackConverterConfig | None = None,
        attack_scoring_config: AttackScoringConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        max_attempts_on_failure: int = 0,
        prepended_conversation_config: PrependedConversationConfig | None = None,
    ) -> None:
        """
        Initialize the prefill attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack. Must natively support the
                ``supports_prefill`` capability.
            prefill_text (str): The text used to prime the start of the target's reply. Sent as the
                trailing assistant turn for the target to continue.
            attack_converter_config (AttackConverterConfig | None): Configuration for converters.
            attack_scoring_config (AttackScoringConfig | None): Configuration for scoring components.
            prompt_normalizer (PromptNormalizer | None): Normalizer for handling prompts.
            max_attempts_on_failure (int): Maximum number of attempts to retry on failure.
            prepended_conversation_config (PrependedConversationConfig | None): Policy for the primed
                exchange this attack prepends.

        Raises:
            ValueError: If ``prefill_text`` is empty, or if the objective target does not natively
                support the ``supports_prefill`` capability.
        """
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=PrefillAttackParameters,
            prepended_conversation_config=prepended_conversation_config,
        )

        if not prefill_text or prefill_text.isspace():
            raise ValueError("prefill_text must be provided and non-empty")

        self._prefill_text = prefill_text

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Include the assistant prefix in the attack's behavioral identity.

        Returns:
            ComponentIdentifier: The identifier including the prefill text.
        """
        return self._create_identifier(params={"prefill_text": self._prefill_text})

    async def _setup_async(self, *, context: SingleTurnAttackContext[Any]) -> None:
        """
        Set up the attack by prepending the objective as the user turn.

        The trailing assistant turn carrying the prefill is sent separately via ``_get_message``.

        Args:
            context (SingleTurnAttackContext): The attack context containing attack parameters.
        """
        context.prepended_conversation = [
            Message.from_prompt(prompt=context.objective, role="user"),
        ]

        await super()._setup_async(context=context)

    def _get_message(self, context: SingleTurnAttackContext[Any]) -> Message:
        """
        Build the trailing assistant message that primes the target's reply.

        Args:
            context (SingleTurnAttackContext): The attack context (unused; the prefill text is fixed
                at construction).

        Returns:
            Message: The assistant-role message carrying the prefill text, marked as simulated so memory
                does not record text the model never wrote as its own reply.
        """
        message = Message.from_prompt(prompt=self._prefill_text, role="assistant")
        message.set_simulated_role()
        return message

    async def _send_prompt_to_objective_target_async(
        self, *, message: Message, context: SingleTurnAttackContext[Any]
    ) -> Message | None:
        """
        Send the prefill to the target without request converters.

        Request converters apply to the objective (the prepended user turn), matching how prepended
        assistant history is left unconverted by default. Converting the prefill would change the very
        tokens the target is asked to continue.

        Args:
            message (Message): The prefill message to send.
            context (SingleTurnAttackContext): The attack context containing parameters and labels.

        Returns:
            Message | None: The target's continuation, or None if the request was filtered or errored.
        """
        with execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name=self.__class__.__name__,
            component_identifier=self._objective_target.get_identifier(),
            objective_target_conversation_id=context.conversation_id,
            objective=context.params.objective,
        ):
            context._record_objective_target_invocation(conversation_id=context.conversation_id)
            return await self._prompt_normalizer.send_prompt_async(
                message=message,
                target=self._objective_target,
                conversation_id=context.conversation_id,
                request_converter_configurations=[],
                response_converter_configurations=self._response_converters,
                normalizer_overrides=self._get_prepended_normalizer_overrides(
                    prepended_history_send_context=context.prepended_history_send_context,
                ),
                send_context=context.prepended_history_send_context,
            )
