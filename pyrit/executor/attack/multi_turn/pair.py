# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt Automatic Iterative Refinement (PAIR) attack.

Implements PAIR (Chao et al. 2023, arXiv:2310.08419) as a thin subclass of
TreeOfAttacksWithPruningAttack with the PAIR-definitional structural
parameters (no tree branching, no off-topic pruning) hardcoded.
"""

import logging

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackConverterConfig,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TAPAttackScoringConfig,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class PAIRAttack(TreeOfAttacksWithPruningAttack):
    """
    Implement the Prompt Automatic Iterative Refinement (PAIR) attack strategy
    [@chao2023pair].

    PAIR is a structural special case of Tree of Attacks with Pruning
    [@mehrotra2023tap]: parallel "streams" of single-branch iterative
    refinement, with no tree expansion and no off-topic pruning. PyRIT
    implements PAIR as a thin subclass of TreeOfAttacksWithPruningAttack that
    hardcodes the two definitional structural parameters (branching_factor=1
    and on_topic_checking_enabled=False) and reuses TAP's adversarial system
    prompt. The remaining configuration (target, scoring, converters, width
    and depth budget) is exposed exactly as on TAP, with TAP's defaults
    preserved for tree_width (number of parallel streams) and tree_depth
    (refinement iterations per stream).

    References:
        Jailbreaking Black Box Large Language Models in Twenty Queries
        [@chao2023pair]

        Tree of Attacks: Jailbreaking Black-Box LLMs Automatically
        [@mehrotra2023tap]
    """

    @apply_defaults
    def __init__(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        attack_adversarial_config: AttackAdversarialConfig,
        attack_converter_config: AttackConverterConfig | None = None,
        attack_scoring_config: TAPAttackScoringConfig | None = None,
        prompt_normalizer: PromptNormalizer | None = None,
        tree_width: int = 3,
        tree_depth: int = 5,
        desired_response_prefix: str = "Sure, here is",
        batch_size: int = 10,
        prepended_conversation_config: PrependedConversationConfig | None = None,
    ) -> None:
        """
        Initialize the PAIR attack strategy.

        Args:
            objective_target (PromptTarget): The target system to attack.
            attack_adversarial_config (AttackAdversarialConfig): Configuration for the
                adversarial chat component.
            attack_converter_config (AttackConverterConfig | None): Configuration for
                attack converters. Defaults to None.
            attack_scoring_config (TAPAttackScoringConfig | None): Scoring configuration.
                The objective scorer must be a FloatScaleThresholdScorer. If not
                provided, a default FloatScaleThresholdScorer wrapping
                SelfAskScaleScorer (threshold 0.7) is created. Defaults to None.
            prompt_normalizer (PromptNormalizer | None): The prompt normalizer to use.
                Defaults to None.
            tree_width (int): Number of parallel "streams" (N in the PAIR paper).
                Defaults to 3.
            tree_depth (int): Maximum refinement iterations per stream (K in the PAIR
                paper). Defaults to 5.
            desired_response_prefix (str): Expected prefix for successful responses.
                Defaults to "Sure, here is".
            batch_size (int): Number of nodes to process in parallel per batch.
                Defaults to 10.
            prepended_conversation_config (PrependedConversationConfig | None):
                Configuration for prepended-conversation handling. Defaults to None.
        """
        super().__init__(
            objective_target=objective_target,
            attack_adversarial_config=attack_adversarial_config,
            attack_converter_config=attack_converter_config,
            attack_scoring_config=attack_scoring_config,
            prompt_normalizer=prompt_normalizer,
            tree_width=tree_width,
            tree_depth=tree_depth,
            branching_factor=1,
            on_topic_checking_enabled=False,
            desired_response_prefix=desired_response_prefix,
            batch_size=batch_size,
            prepended_conversation_config=prepended_conversation_config,
        )
