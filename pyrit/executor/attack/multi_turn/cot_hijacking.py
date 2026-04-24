# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CoT Hijacking Attack Strategy.

This implements the full Chain-of-Thought Hijacking attack with iterative refinement
as described in: "Chain-of-Thought Hijacking" by Zhao et al. (2025)
https://arxiv.org/abs/2510.26418

Key components:
1. Attack generation: Create hijacking prompts with embedded harmful operations
2. Target evaluation: Send prompts to target model
3. Judgment: Score responses using refusal/safety detection
4. Refinement: Iteratively improve prompts based on feedback

The attack uses multi-turn conversation where:
- An attacker generates jailbreak prompts iteratively
- The objective model (target) provides responses to evaluate
- Scorer scores success (safe=0.1 to unsafe=1.0)
- Loop continues until success or max iterations
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.exceptions import remove_markdown_json
from pyrit.executor.attack.component import ConversationManager
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
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
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY
from pyrit.score.true_false.float_scale_threshold_scorer import FloatScaleThresholdScorer

if TYPE_CHECKING:
    from pyrit.score import Scorer

logger = logging.getLogger(__name__)


CoTHijackingAttackParameters = AttackParameters.excluding("next_message")

SUPPORTED_PUZZLE_TYPES = [
    "category_theory",
    "sudoku",
    "logic_grid",
    "skyscrapers",
    "logic_grid_enhanced",
    "skyscrapers_memetic",
    "test",
]

PUZZLE_PROMPT_PATHS = {
    "logic_grid": Path(EXECUTOR_SEED_PROMPT_PATH) / "cot_hijacking" / "puzzle_generation_logic_grid.yaml",
    "skyscrapers": Path(EXECUTOR_SEED_PROMPT_PATH) / "cot_hijacking" / "puzzle_generation_skyscrapers.yaml",
    "sudoku": Path(EXECUTOR_SEED_PROMPT_PATH) / "cot_hijacking" / "puzzle_generation_sudoku.yaml",
    "category_theory": Path(EXECUTOR_SEED_PROMPT_PATH) / "cot_hijacking" / "puzzle_generation_category_theory.yaml",
    "test": Path(EXECUTOR_SEED_PROMPT_PATH) / "cot_hijacking" / "puzzle_generation_test.yaml",
    "logic_grid_enhanced": Path(EXECUTOR_SEED_PROMPT_PATH)
    / "cot_hijacking"
    / "puzzle_generation_logic_grid_enhanced.yaml",
    "skyscrapers_memetic": Path(EXECUTOR_SEED_PROMPT_PATH)
    / "cot_hijacking"
    / "puzzle_generation_skyscrapers_memetic.yaml",
}


@dataclass
class CoTHijackingAttackContext(MultiTurnAttackContext[Any]):
    """Context for the CoT Hijacking attack strategy."""

    iteration: int = 0
    current_prompt: str = ""
    puzzle_type: str = "logic_grid"
    last_score: Optional[Score] = None
    attack_succeeded: bool = False
    last_target_response: str = ""


class CoTHijackingAttack(MultiTurnAttackStrategy[CoTHijackingAttackContext, AttackResult]):
    """
    CoT Hijacking Attack Strategy.

    Implements the Chain-of-Thought Hijacking technique which embeds harmful objectives
    within complex puzzle frameworks. Models solving these puzzles in reasoning mode
    naturally generate harmful content in the practical example section.

    Accepts either:
    - A TrueFalseScorer via attack_scoring_config (used directly)
    - A FloatScaleScorer via float_scale_scorer (wrapped in FloatScaleThresholdScorer)
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        attack_adversarial_config: AttackAdversarialConfig,
        attack_converter_config: Optional[AttackConverterConfig] = None,
        attack_scoring_config: Optional[AttackScoringConfig] = None,
        prompt_normalizer: Optional[PromptNormalizer] = None,
        max_iterations: int = 10,
        puzzle_types: Optional[list[str]] = None,
        success_threshold: float = 1.0,
        float_scale_scorer: Optional[FloatScaleScorer] = None,
    ) -> None:
        """
        Initialize the CoT Hijacking attack strategy.

        Args:
            objective_target: The target model to attack (must support multi-turn).
            attack_adversarial_config: Configuration for the adversarial prompt-generating model.
            attack_converter_config: Optional configuration for prompt/response converters.
            attack_scoring_config: Optional configuration for scoring the target model's responses.
            prompt_normalizer: Optional prompt normalizer to use for prompt formatting and sending.
            max_iterations: Maximum number of attack iterations to attempt.
            puzzle_types: List of puzzle types to use for prompt generation.
            success_threshold: Float threshold for success when using a FloatScaleScorer.
            float_scale_scorer: Optional FloatScaleScorer to use directly. If provided,
                it is wrapped in FloatScaleThresholdScorer using success_threshold.
                Use this instead of attack_scoring_config when you have a FloatScaleScorer,
                since AttackScoringConfig only accepts TrueFalseScorer.
                If both are provided, float_scale_scorer takes precedence.

        Note:
            This attack is specifically designed for reasoning models (e.g. DeepSeek-R1, o1)
            that exhibit chain-of-thought reasoning behavior. Results may be less effective
            on non-reasoning models. Once TargetCapabilities is expanded to include
            supports_reasoning, a hard validation will be added here.

        Args:
            float_scale_scorer: Optional FloatScaleScorer to use directly. If provided,
                it is wrapped in FloatScaleThresholdScorer using success_threshold.
                Use this instead of attack_scoring_config when you have a FloatScaleScorer,
                since AttackScoringConfig only accepts TrueFalseScorer.
                If both are provided, float_scale_scorer takes precedence.

        Raises:
            ValueError: If the adversarial target is not a PromptChatTarget.
        """
        # TODO: Add hard validation once TargetCapabilities is expanded to include
        # supports_reasoning. At that point, replace this
        # with: if not objective_target._capabilities.supports_reasoning: raise ValueError(...)

        super().__init__(
            objective_target=objective_target,
            logger=logger,
            context_type=CoTHijackingAttackContext,
            params_type=CoTHijackingAttackParameters,
        )

        if not (0 <= success_threshold <= 1):
            raise ValueError("success_threshold must be between 0 and 1")

        self._success_threshold = success_threshold

        self._adversarial_chat = attack_adversarial_config.target
        if not isinstance(self._adversarial_chat, PromptChatTarget):
            raise ValueError("Adversarial target must be a PromptChatTarget")

        attack_converter_config = attack_converter_config or AttackConverterConfig()
        self._request_converters = attack_converter_config.request_converters
        self._response_converters = attack_converter_config.response_converters

        attack_scoring_config = attack_scoring_config or AttackScoringConfig()
        self._attack_scoring_config = attack_scoring_config

        self._objective_scorer: Optional[Scorer] = None

        if float_scale_scorer is not None:
            # wrap in threshold scorer
            self._objective_scorer = FloatScaleThresholdScorer(
                scorer=float_scale_scorer,
                threshold=success_threshold,
            )
        elif attack_scoring_config.objective_scorer is not None:
            # TrueFalseScorer from config — use directly, no wrapping needed
            self._objective_scorer = attack_scoring_config.objective_scorer
        else:
            self._objective_scorer = None

        self._auxiliary_scorers = attack_scoring_config.auxiliary_scorers

        self._max_iterations = max_iterations
        self._puzzle_types = puzzle_types or list(SUPPORTED_PUZZLE_TYPES)

        for ptype in self._puzzle_types:
            if ptype not in SUPPORTED_PUZZLE_TYPES:
                raise ValueError(f"Unknown puzzle_type: {ptype}. Supported types: {SUPPORTED_PUZZLE_TYPES}")

        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_manager = ConversationManager(
            attack_identifier=self.get_identifier(),
            prompt_normalizer=self._prompt_normalizer,
        )

    def get_attack_scoring_config(self) -> Optional[AttackScoringConfig]:
        """
        Get the attack scoring configuration used by this strategy.

        Returns:
            Optional[AttackScoringConfig]: The scoring configuration.
        """
        return self._attack_scoring_config

    def _validate_context(self, *, context: CoTHijackingAttackContext) -> None:
        """
        Validate the context before executing the attack.

        Raises:
            ValueError: If the attack objective is not provided or is empty.
        """
        if not context.params.objective or context.params.objective.isspace():
            raise ValueError("Attack objective must be provided and non-empty")

    async def _setup_async(self, *, context: CoTHijackingAttackContext) -> None:
        """
        Set up the attack by preparing conversation context.

        Raises:
            ValueError: If the objective target does not support multi-turn conversations.
        """
        if not isinstance(self._objective_target, PromptChatTarget):
            raise ValueError(
                "CoTHijackingAttack requires a multi-turn target. The attack relies on "
                "iterative prompt refinement across turns. "
            )

        context.session = ConversationSession()

        await self._conversation_manager.initialize_context_async(
            context=context,
            target=self._objective_target,
            conversation_id=context.session.conversation_id,
            request_converters=self._request_converters,
            memory_labels=self._memory_labels,
        )

        self._logger.info(f"CoT Hijacking attack initialized for: {context.params.objective}")

    async def _perform_async(self, *, context: CoTHijackingAttackContext) -> AttackResult:
        self._logger.info(f"Starting CoT Hijacking attack")
        self._logger.info(f"Objective: {context.params.objective[:80]}...")
        self._logger.info(f"Max iterations: {self._max_iterations}")

        response = None

        for iteration in range(self._max_iterations):
            context.iteration = iteration + 1
            puzzle_type = self._puzzle_types[iteration % len(self._puzzle_types)]
            context.puzzle_type = puzzle_type

            # Generate attack prompt with feedback from previous round
            attack_prompt = await self._generate_attack_prompt_async(
                context=context,
                puzzle_type=puzzle_type,
                iteration=iteration,
                previous_response=context.last_target_response,
                previous_score=str(self._extract_raw_score(context.last_score)) if context.last_score else "",
            )
            context.current_prompt = attack_prompt

            # Send to target
            message = Message.from_prompt(prompt=attack_prompt, role="user")
            response = await self._send_prompt_to_target_async(message=message, context=context)

            if not response:
                self._logger.warning("Target response was blocked or empty")
                continue

            # Store response text for next iteration's feedback
            context.last_target_response = str(response.get_value()) if response else ""

            # Score response
            score_obj = await self._score_response_async(message=response, context=context)

            if score_obj is not None:
                context.last_score = score_obj

                if bool(score_obj.get_value()):
                    self._logger.info("Attack succeeded!")
                    return AttackResult(
                        conversation_id=context.session.conversation_id,
                        objective=context.params.objective,
                        atomic_attack_identifier=self.get_identifier(),
                        last_response=response.message_pieces[0]
                        if response and getattr(response, "message_pieces", None)
                        else None,
                        last_score=context.last_score,
                        executed_turns=context.iteration,
                        outcome=AttackOutcome.SUCCESS,
                    )
            else:
                self._logger.warning("Scoring returned None, continuing without score update")

        return AttackResult(
            conversation_id=context.session.conversation_id,
            objective=context.params.objective,
            atomic_attack_identifier=self.get_identifier(),
            last_response=response.message_pieces[0]
            if response and getattr(response, "message_pieces", None)
            else None,
            last_score=context.last_score,
            executed_turns=context.iteration if context.iteration else 0,
            outcome=AttackOutcome.FAILURE,
        )

    # Generation of Attack Prompt using adversarial model
    async def _generate_attack_prompt_async(
        self,
        *,
        context: CoTHijackingAttackContext,
        puzzle_type: str,
        iteration: int,
        previous_response: str = "",
        previous_score: str = "",
    ) -> str:
        # Step 1: render the meta-prompt for the adversarial model
        prompt_path = PUZZLE_PROMPT_PATHS[puzzle_type]
        import yaml
        from jinja2 import Environment

        # Load raw YAML to get un-rendered template text
        with open(prompt_path) as f:
            raw_yaml = yaml.safe_load(f)
        raw_template_value = raw_yaml["value"]

        # Render directly with Jinja2
        env = Environment()
        template = env.from_string(raw_template_value)
        meta_prompt = template.render(
            objective=context.params.objective,
            puzzle_type=puzzle_type,
            previous_response=previous_response if previous_response else "",
            previous_score=previous_score if previous_score else "",
        )

        # Step 2: send meta-prompt to adversarial model to generate the actual jailbreak prompt

        message = Message.from_prompt(prompt=meta_prompt, role="user")

        try:
            response = await self._prompt_normalizer.send_prompt_async(
                message=message,
                target=self._adversarial_chat,
                attack_identifier=self.get_identifier(),
                labels=context.memory_labels,
            )
            response_text = response.get_value()
            response_text = remove_markdown_json(response_text)
            parsed = json.loads(response_text)

            # Step 3: extract the jailbreak prompt P from the JSON
            return str(parsed.get("prompt", meta_prompt))
        except Exception as e:
            self._logger.warning(f"Adversarial model failed to generate prompt: {e}. Falling back to meta-prompt.")
            return meta_prompt

    # Target Model Interaction
    async def _send_prompt_to_target_async(
        self, *, message: Message, context: CoTHijackingAttackContext
    ) -> Optional[Message]:
        """
        Send prompt to objective target and get response.

        Args:
            message (Message): The message to send to the objective target.
            context (CoTHijackingAttackContext): The attack context containing configuration.

        Returns:
            Optional[Message]: The response from the objective target, or None if no response.
        """
        objective_target_type = self._objective_target.get_identifier().class_name

        value = message.get_value()
        if isinstance(value, list):
            value = "\n".join(str(item) for item in value)
        prompt_preview = value[:100] if value else ""
        self._logger.debug(f"Sending prompt to {objective_target_type}: {prompt_preview}...")

        self._logger.debug(f"Sending prompt to {objective_target_type}: {prompt_preview}...")

        try:
            response = await self._prompt_normalizer.send_prompt_async(
                message=message,
                target=self._objective_target,
                conversation_id=context.session.conversation_id,
                request_converter_configurations=self._request_converters,
                response_converter_configurations=self._response_converters,
                attack_identifier=self.get_identifier(),
                labels=context.memory_labels,
            )
        except Exception as e:
            self._logger.warning(f"Failed to send prompt to target: {e}")
            return None

        if not response:
            self._logger.warning("No response received from objective target")
            return None

        return response

    # Response Scoring
    async def _score_response_async(self, *, message: Message, context: CoTHijackingAttackContext) -> Optional[Score]:
        """
        Score the response using whichever scorer was configured.

        Returns:
            Optional[Score]: The first score object from the scorer, or None if scoring fails.
        """
        if not self._objective_scorer:
            return None
        try:
            score_list = await self._objective_scorer.score_async(message, objective=context.params.objective)
            return score_list[0] if score_list else None
        except Exception as e:
            self._logger.warning(f"Scoring failed: {e}")
            return None

    def _extract_raw_score(self, score_obj: Score) -> float:
        """
        Extract original float from FloatScaleThresholdScorer metadata, or
        convert boolean to float for TrueFalseScorer.

        Returns:
            float: The extracted raw float score.
        """
        if score_obj.score_metadata and ORIGINAL_FLOAT_VALUE_KEY in score_obj.score_metadata:
            return float(score_obj.score_metadata[ORIGINAL_FLOAT_VALUE_KEY])
        return 1.0 if score_obj.get_value() else 0.0

    async def _teardown_async(self, *, context: CoTHijackingAttackContext) -> None:
        """
        Teardown phase of the attack (cleanup operations).

        This is called after the attack completes to perform any cleanup
        or finalization operations. For CoT Hijacking, this is typically a no-op
        """
        # No special cleanup needed for CoT Hijacking attack
