# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack import AttackAdversarialConfig
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core._attack_adversarial_config import _AttackAdversarialConfigHelper


class _StubAttack:
    def __init__(self, *, attack_adversarial_config: AttackAdversarialConfig | None = None) -> None:
        pass


class TestTargetPrecedence:
    """Tests for adversarial target precedence."""

    def test_explicit_create_time_target_wins(self):
        baked_target = MagicMock(spec=PromptTarget)
        create_time_target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=baked_target,
        )
        config = helper.build_adversarial_config(create_time_target=create_time_target)
        assert config.target is create_time_target

    def test_baked_adversarial_chat_wins_over_default(self):
        baked_target = MagicMock(spec=PromptTarget)
        default_target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=baked_target,
        )
        config = helper.build_adversarial_config(default_target_resolver=lambda: default_target)
        assert config.target is baked_target

    def test_default_target_resolved_lazily_when_no_target_provided(self):
        default_target = MagicMock(spec=PromptTarget)
        resolver_called = False

        def _resolver():
            nonlocal resolver_called
            resolver_called = True
            return default_target

        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=None,
        )
        assert not resolver_called
        config = helper.build_adversarial_config(default_target_resolver=_resolver)
        assert resolver_called
        assert config.target is default_target


class TestConflictValidation:
    """Tests for target and prompt conflict validation."""

    def test_validate_target_conflict_raises_when_both_baked_and_create_time(self):
        baked_target = MagicMock(spec=PromptTarget)
        create_time_target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=baked_target,
        )
        with pytest.raises(ValueError, match="adversarial chat is already baked"):
            helper.validate_target_conflict(create_time_target=create_time_target)

    def test_validate_target_conflict_passes_when_no_conflict(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=None,
        )
        # Should not raise
        helper.validate_target_conflict(create_time_target=MagicMock(spec=PromptTarget))
        helper.validate_target_conflict(create_time_target=None)

    def test_validate_prompt_conflict_raises_when_custom_baked_and_create_time_supplied(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_system_prompt="baked system prompt",
        )
        assert helper.has_custom_adversarial_prompt is True
        with pytest.raises(ValueError, match="custom adversarial prompt is already baked"):
            helper.validate_prompt_conflict(create_time_system_prompt="create-time system prompt")

        with pytest.raises(ValueError, match="custom adversarial prompt is already baked"):
            helper.validate_prompt_conflict(create_time_seed_prompt="create-time seed prompt")

        with pytest.raises(ValueError, match="custom adversarial prompt is already baked"):
            helper.validate_prompt_conflict(create_time_prompt_template="create-time template")

    def test_validate_prompt_conflict_passes_without_custom_prompts(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
        )
        assert helper.has_custom_adversarial_prompt is False
        # Should not raise when supplying create-time prompts
        helper.validate_prompt_conflict(
            create_time_system_prompt="create-time prompt",
            create_time_seed_prompt="create-time seed",
            create_time_prompt_template="create-time template",
        )


class TestPromptPrecedenceAndAssembly:
    """Tests for prompt precedence and configuration assembly."""

    def test_baked_system_prompt_takes_precedence_over_create_time(self):
        target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=target,
            adversarial_system_prompt="baked system prompt",
        )
        config = helper.build_adversarial_config(create_time_system_prompt="ignored create-time prompt")
        assert config.system_prompt == "baked system prompt"

    def test_create_time_system_prompt_used_when_baked_is_none(self):
        target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=target,
            adversarial_system_prompt=None,
        )
        config = helper.build_adversarial_config(create_time_system_prompt="create-time system prompt")
        assert config.system_prompt == "create-time system prompt"

    def test_baked_seed_prompt_takes_precedence_over_create_time(self):
        target = MagicMock(spec=PromptTarget)
        baked_seed = SeedPrompt(value="baked seed {{ objective }}", data_type="text")
        create_seed = SeedPrompt(value="create seed {{ objective }}", data_type="text")
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=target,
            adversarial_seed_prompt=baked_seed,
        )
        config = helper.build_adversarial_config(create_time_seed_prompt=create_seed)
        assert config.first_message == baked_seed

    def test_baked_prompt_template_takes_precedence_over_create_time(self):
        target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=target,
            adversarial_prompt_template="baked template",
        )
        config = helper.build_adversarial_config(create_time_prompt_template="create template")
        assert config.adversarial_prompt_template == "baked template"

    def test_prefix_propagated_to_config(self):
        target = MagicMock(spec=PromptTarget)
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
            adversarial_chat=target,
            adversarial_system_prompt_prefix="prefix guidance",
        )
        config = helper.build_adversarial_config()
        assert config.system_prompt_prefix == "prefix guidance"


class TestShouldBuildAdversarialConfig:
    """Tests for should_build_adversarial_config condition checks."""

    def test_returns_false_when_param_not_accepted(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
        )
        assert (
            helper.should_build_adversarial_config(
                accepted_params={"objective_target"},
                create_time_target=MagicMock(spec=PromptTarget),
            )
            is False
        )

    def test_returns_true_when_uses_adversarial(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=True,
        )
        assert (
            helper.should_build_adversarial_config(
                accepted_params={"attack_adversarial_config"},
            )
            is True
        )

    def test_returns_true_when_create_time_inputs_supplied(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=False,
        )
        assert (
            helper.should_build_adversarial_config(
                accepted_params={"attack_adversarial_config"},
                create_time_target=MagicMock(spec=PromptTarget),
            )
            is True
        )
        assert (
            helper.should_build_adversarial_config(
                accepted_params={"attack_adversarial_config"},
                create_time_system_prompt="sys prompt",
            )
            is True
        )

    def test_returns_false_when_no_adversarial_setting_or_input(self):
        helper = _AttackAdversarialConfigHelper(
            factory_name="test_factory",
            attack_class=_StubAttack,
            uses_adversarial=False,
        )
        assert (
            helper.should_build_adversarial_config(
                accepted_params={"attack_adversarial_config"},
            )
            is False
        )
