# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any, Literal, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.exceptions import ComponentRole, execution_context
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.executor.attack.single_turn.single_turn_attack_strategy import SingleTurnAttackContext
from pyrit.models import (
    AttackResult,
    ConversationReference,
    ConversationType,
    Message,
    build_atomic_attack_identifier,
)
from pyrit.prompt_converter.bijection_converter import BijectionConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# BijectionLearningAttack constructs its own encoded messages, so callers
# cannot inject pre-built next_message or prepended_conversation.
BijectionLearningParameters = AttackParameters.excluding("prepended_conversation", "next_message")


class BijectionLearningAttack(PromptSendingAttack):
    """
    Implement the Bijection Learning jailbreak [@liu2024bijectionlearning].

    Each attempt generates a fresh random bijection and threads two paired
    converters through PyRIT's normal converter pipeline:

    * **Request side** – a ``BijectionConverter(direction="encode")`` appended
      after any user-supplied request converters.  It wraps the objective in the
      teaching preamble and encodes it before the prompt reaches the target.
    * **Response side** – a matching ``BijectionConverter(direction="decode")``
      built from that same attempt's mapping, prepended before any user-supplied
      response converters.  The normalizer applies it to the raw target response
      so the scorer always receives decoded plaintext.

    Repeating with independent mappings (best-of-n) more than doubles the
    single-attempt attack success rate reported in the paper.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        max_attempts_on_failure: int = 0,
        mapping_type: Literal["letter", "digit"] = "digit",
        fixed_points: int = 13,
        digit_length: int = 2,
        num_teaching_shots: int = 5,
    ) -> None:
        """
        Args:
            objective_target: The target system to attack.
            attack_converter_config: Optional additional converter configuration.
                User-supplied request converters run *before* bijection encoding;
                user-supplied response converters run *after* bijection decoding.
            attack_scoring_config: Scoring configuration.
            prompt_normalizer: Optional normalizer override.
            max_attempts_on_failure: Additional attempts after the first
                failure (best-of-n sampling).  Each attempt uses a fresh random
                bijection mapping.
            mapping_type: ``"letter"`` or ``"digit"`` — forwarded to
                ``BijectionConverter``.
            fixed_points: Letters that map to themselves (0–25).  Lower values
                yield more complex encodings.
            digit_length: Numeric code length for ``mapping_type="digit"``.
            num_teaching_shots: Benign teaching pairs prepended to the query.
        """
        super().__init__(
            objective_target=objective_target,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=max_attempts_on_failure,
            params_type=BijectionLearningParameters,
        )
        self._mapping_type = mapping_type
        self._fixed_points = fixed_points
        self._digit_length = digit_length
        self._num_teaching_shots = num_teaching_shots

    async def _perform_async(self, *, context: SingleTurnAttackContext[Any]) -> AttackResult:
        """
        Run the bijection learning attack loop.

        Each iteration:
        1. Creates a fresh ``BijectionConverter(direction="encode")`` — new
           random mapping for this attempt.
        2. Builds a paired ``BijectionConverter(direction="decode")`` from the
           same mapping.
        3. Calls the normalizer with the objective as plain text, the encode
           converter appended to request converters, and the decode converter
           prepended to response converters.  The normalizer handles all
           transformation; the scorer receives decoded plaintext.
        4. Scores and breaks on success; otherwise resets the conversation for
           the next attempt.

        Returns:
            AttackResult: The outcome, last response, and score for the attempt.
        """
        self._logger.info(f"Starting {self.__class__.__name__} with objective: {context.objective}")

        response: Optional[Message] = None
        score = None

        for attempt in range(self._max_attempts_on_failure + 1):
            self._logger.debug(f"Attempt {attempt + 1}/{self._max_attempts_on_failure + 1}")

            # Fresh random bijection for this attempt.
            encode_converter = BijectionConverter(
                direction="encode",
                mapping_type=self._mapping_type,
                fixed_points=self._fixed_points,
                digit_length=self._digit_length,
                num_teaching_shots=self._num_teaching_shots,
                append_description=True,
            )
            # Paired decoder built from THIS attempt's mapping.
            decode_converter = BijectionConverter(
                direction="decode",
                custom_mapping=encode_converter.mapping,
            )

            # Append the encode converter AFTER user-supplied request converters
            # so bijection encoding is the last transform before the target.
            request_configs = self._request_converters + PromptConverterConfiguration.from_converters(
                converters=[encode_converter]
            )
            # Prepend the decode converter BEFORE user-supplied response converters
            # so the scorer always receives decoded plaintext.
            response_configs = (
                PromptConverterConfiguration.from_converters(converters=[decode_converter]) + self._response_converters
            )

            # Send the plain objective; encoding is handled by the request converter.
            message = Message.from_prompt(prompt=context.objective, role="user")

            with execution_context(
                component_role=ComponentRole.OBJECTIVE_TARGET,
                attack_strategy_name=self.__class__.__name__,
                attack_identifier=self.get_identifier(),
                component_identifier=self._objective_target.get_identifier(),
                objective_target_conversation_id=context.conversation_id,
                objective=context.params.objective,
            ):
                response = await self._prompt_normalizer.send_prompt_async(
                    message=message,
                    target=self._objective_target,
                    conversation_id=context.conversation_id,
                    request_converter_configurations=request_configs,
                    response_converter_configurations=response_configs,
                    attack_identifier=self.get_identifier(),
                )

            if not response:
                self._logger.warning(f"No response on attempt {attempt + 1} (likely filtered)")
                if attempt < self._max_attempts_on_failure:
                    context.related_conversations.add(
                        ConversationReference(
                            conversation_id=context.conversation_id,
                            conversation_type=ConversationType.PRUNED,
                        )
                    )
                    await self._setup_async(context=context)
                continue

            # The response's converted_value is already decoded by the response
            # converter; pass it directly to the scorer.
            score = await self._evaluate_response_async(response=response, objective=context.objective)

            if not self._objective_scorer:
                break

            if score and score.get_value():
                break

            if attempt < self._max_attempts_on_failure:
                context.related_conversations.add(
                    ConversationReference(
                        conversation_id=context.conversation_id,
                        conversation_type=ConversationType.PRUNED,
                    )
                )
                await self._setup_async(context=context)

        outcome, outcome_reason = self._determine_attack_outcome(response=response, score=score, context=context)

        return AttackResult(
            conversation_id=context.conversation_id,
            objective=context.objective,
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=self.get_identifier()),
            last_response=response.get_piece() if response else None,
            last_score=score,
            related_conversations=context.related_conversations,
            outcome=outcome,
            outcome_reason=outcome_reason,
            executed_turns=1,
            labels=context.memory_labels,
        )
