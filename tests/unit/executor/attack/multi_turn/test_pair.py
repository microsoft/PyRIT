# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for :class:`pyrit.executor.attack.multi_turn.pair.PAIRAttack`.

PAIRAttack is a thin subclass of :class:`TreeOfAttacksWithPruningAttack` with
two definitional structural parameters hardcoded (no tree branching, no
off-topic pruning). These tests verify the hardcoding holds and that the
exposed configuration surface matches the PAIR-paper-relevant knobs.
"""

import inspect
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackScoringConfig,
    PAIRAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TAPAttackContext,
    TAPAttackScoringConfig,
)
from pyrit.models import ComponentIdentifier
from pyrit.prompt_target import CapabilityName, PromptTarget
from pyrit.score import FloatScaleThresholdScorer, TrueFalseScorer


def _make_mock_objective_target() -> PromptTarget:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(return_value=None)
    target.get_identifier.return_value = ComponentIdentifier(
        class_name="MockTarget",
        class_module="test_module",
    )
    target.capabilities.supports_multi_turn = True
    target.capabilities.output_modalities = frozenset({frozenset(["text"])})
    target.configuration.includes.side_effect = lambda capability: capability == CapabilityName.MULTI_TURN
    target.configuration.capabilities.output_modalities = frozenset({frozenset(["text"])})
    return cast("PromptTarget", target)


def _make_mock_adversarial_chat() -> PromptTarget:
    chat = MagicMock(spec=PromptTarget)
    chat.send_prompt_async = AsyncMock(return_value=None)
    chat.set_system_prompt = MagicMock()
    chat.get_identifier.return_value = ComponentIdentifier(
        class_name="MockChatTarget",
        class_module="test_module",
    )
    return cast("PromptTarget", chat)


def _make_threshold_scorer(threshold: float = 0.7) -> FloatScaleThresholdScorer:
    scorer = MagicMock(spec=FloatScaleThresholdScorer)
    scorer.threshold = threshold
    scorer.scorer_type = "true_false"
    scorer.score_async = AsyncMock(return_value=[])
    scorer.get_identifier.return_value = ComponentIdentifier(
        class_name="FloatScaleThresholdScorer",
        class_module="pyrit.score",
    )
    return cast("FloatScaleThresholdScorer", scorer)


def _make_true_false_scorer() -> TrueFalseScorer:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.scorer_type = "true_false"
    scorer.score_async = AsyncMock(return_value=[])
    scorer.get_identifier.return_value = ComponentIdentifier(
        class_name="MockScorer",
        class_module="test_module",
    )
    return cast("TrueFalseScorer", scorer)


@pytest.fixture
def adversarial_config() -> AttackAdversarialConfig:
    return AttackAdversarialConfig(target=_make_mock_adversarial_chat())


@pytest.fixture
def objective_target() -> PromptTarget:
    return _make_mock_objective_target()


@pytest.mark.usefixtures("patch_central_database")
class TestPAIRAttackInit:
    """Initialization and signature contract tests for PAIRAttack."""

    def test_init_applies_pair_structural_defaults(self, objective_target, adversarial_config):
        """PAIR pins branching_factor=1 and on_topic_checking_enabled=False, and keeps TAP width/depth defaults."""
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
        )

        assert attack._tree_width == 3
        assert attack._tree_depth == 5
        assert attack._branching_factor == 1
        assert attack._on_topic_checking_enabled is False

    def test_branching_factor_is_not_exposed_in_signature(self):
        """branching_factor is definitional for PAIR (always 1) and must not be a public init kwarg."""
        sig = inspect.signature(PAIRAttack.__init__)
        assert "branching_factor" not in sig.parameters, (
            "PAIRAttack must not expose branching_factor — exposing it would let callers turn it into TAP."
        )

    def test_on_topic_checking_is_not_exposed_in_signature(self):
        """on_topic_checking_enabled is definitional for PAIR (always False) and must not be a public init kwarg."""
        sig = inspect.signature(PAIRAttack.__init__)
        assert "on_topic_checking_enabled" not in sig.parameters, (
            "PAIRAttack must not expose on_topic_checking_enabled — it would let callers disable PAIR's no-pruning."
        )

    def test_tree_width_override(self, objective_target, adversarial_config):
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
            tree_width=7,
        )
        assert attack._tree_width == 7
        assert attack._branching_factor == 1

    def test_tree_depth_override(self, objective_target, adversarial_config):
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
            tree_depth=12,
        )
        assert attack._tree_depth == 12
        assert attack._on_topic_checking_enabled is False

    def test_is_subclass_of_tap(self):
        assert issubclass(PAIRAttack, TreeOfAttacksWithPruningAttack)

    def test_pair_uses_tap_context_type(self, objective_target, adversarial_config):
        """PAIR must not introduce a parallel context hierarchy — it reuses TAPAttackContext."""
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
        )
        assert attack._context_type is TAPAttackContext

    def test_pair_validates_adversarial_target_capabilities(self, objective_target):
        """An adversarial target lacking native MULTI_TURN/SYSTEM_PROMPT must be rejected (inherited from TAP)."""
        bad_adversarial = MagicMock(spec=PromptTarget)
        bad_adversarial.get_identifier.return_value = ComponentIdentifier(
            class_name="BadAdversarial",
            class_module="test_module",
        )
        bad_adversarial.configuration = MagicMock()
        bad_adversarial.configuration.includes.return_value = False

        with pytest.raises(ValueError, match="TreeOfAttacksWithPruningAttack"):
            PAIRAttack(
                objective_target=objective_target,
                attack_adversarial_config=AttackAdversarialConfig(target=bad_adversarial),
            )

    def test_pair_rejects_non_float_scale_threshold_scorer(self, objective_target, adversarial_config):
        """TAPAttackScoringConfig requires a FloatScaleThresholdScorer; PAIR inherits this requirement."""
        non_threshold_scorer = _make_true_false_scorer()
        with pytest.raises(ValueError, match="FloatScaleThresholdScorer"):
            PAIRAttack(
                objective_target=objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=AttackScoringConfig(objective_scorer=non_threshold_scorer),
            )

    def test_pair_accepts_tap_scoring_config(self, objective_target, adversarial_config):
        """A pre-built TAPAttackScoringConfig with the right scorer is accepted unchanged."""
        scoring_config = TAPAttackScoringConfig(objective_scorer=_make_threshold_scorer(threshold=0.85))
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )
        result = attack.get_attack_scoring_config()
        assert isinstance(result, TAPAttackScoringConfig)
        assert result.threshold == 0.85


@pytest.mark.usefixtures("patch_central_database")
class TestPAIRAdversarialIdentity:
    """PAIR inherits TAP's adversarial identity wiring."""

    def test_get_attack_adversarial_config_includes_target(self, objective_target, adversarial_config):
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
        )
        config = attack.get_attack_adversarial_config()
        assert config is not None
        assert config.target is adversarial_config.target
        assert config.system_prompt is attack._adversarial_chat_system_seed_prompt
        assert config.first_message is None

    def test_identifier_includes_adversarial_chat_child(self, objective_target, adversarial_config):
        attack = PAIRAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
        )
        identifier = attack.get_identifier()
        assert "adversarial_chat" in identifier.children
        assert identifier.children["adversarial_chat"] == adversarial_config.target.get_identifier.return_value
