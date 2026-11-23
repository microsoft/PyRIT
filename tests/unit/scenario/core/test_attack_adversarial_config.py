# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core._attack_adversarial_config import _AttackAdversarialConfigHelper


def _helper(
    *, uses_adversarial: bool = True, has_custom_adversarial_prompt: bool = False, **prompts
) -> _AttackAdversarialConfigHelper:
    return _AttackAdversarialConfigHelper(
        factory_name="test_factory",
        uses_adversarial=uses_adversarial,
        has_custom_adversarial_prompt=has_custom_adversarial_prompt,
        **prompts,
    )


def _unused_resolver() -> PromptTarget:
    raise AssertionError("default target should not be resolved")


@pytest.mark.parametrize(
    "create_time_prompt",
    [
        {"create_time_system_prompt": "create-time system prompt"},
        {"create_time_seed_prompt": "create-time seed prompt"},
        {"create_time_prompt_template": "create-time template"},
    ],
)
def test_validate_prompt_conflict_raises_when_custom_prompt_baked(create_time_prompt):
    helper = _helper(has_custom_adversarial_prompt=True, adversarial_system_prompt="baked system prompt")
    with pytest.raises(ValueError, match="custom adversarial prompt is already baked"):
        helper.validate_prompt_conflict(**create_time_prompt)


@pytest.mark.parametrize(
    ("baked_field", "baked", "create_time_field", "create_time", "config_field"),
    [
        (
            "adversarial_system_prompt",
            "baked system prompt",
            "create_time_system_prompt",
            "ignored create-time prompt",
            "system_prompt",
        ),
        (
            "adversarial_seed_prompt",
            SeedPrompt(value="baked seed {{ objective }}", data_type="text"),
            "create_time_seed_prompt",
            SeedPrompt(value="create seed {{ objective }}", data_type="text"),
            "first_message",
        ),
    ],
)
def test_baked_prompt_takes_precedence_over_create_time(
    baked_field, baked, create_time_field, create_time, config_field
):
    helper = _helper(
        has_custom_adversarial_prompt=True,
        adversarial_chat=MagicMock(spec=PromptTarget),
        **{baked_field: baked},
    )
    config = helper.build_adversarial_config(
        default_target_resolver=_unused_resolver, **{create_time_field: create_time}
    )
    assert getattr(config, config_field) == baked


def test_prefix_propagated_to_config():
    helper = _helper(
        adversarial_chat=MagicMock(spec=PromptTarget),
        adversarial_system_prompt_prefix="prefix guidance",
    )
    config = helper.build_adversarial_config(default_target_resolver=_unused_resolver)
    assert config.system_prompt_prefix == "prefix guidance"


@pytest.mark.parametrize(
    ("accepted_params", "uses_adversarial", "create_time_inputs", "expected"),
    [
        ({"objective_target"}, True, {"create_time_target": MagicMock(spec=PromptTarget)}, False),
        ({"attack_adversarial_config"}, True, {}, True),
        ({"attack_adversarial_config"}, False, {"create_time_target": MagicMock(spec=PromptTarget)}, True),
        ({"attack_adversarial_config"}, False, {"create_time_system_prompt": "sys prompt"}, True),
        ({"attack_adversarial_config"}, False, {}, False),
    ],
)
def test_should_build_adversarial_config(accepted_params, uses_adversarial, create_time_inputs, expected):
    helper = _helper(uses_adversarial=uses_adversarial)
    assert helper.should_build_adversarial_config(accepted_params=accepted_params, **create_time_inputs) is expected
