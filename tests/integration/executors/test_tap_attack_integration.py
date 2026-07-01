# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    TAPAttack,
    TAPSystemPromptPaths,
)
from pyrit.models import SeedPrompt
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter
from pyrit.prompt_target import OpenAIChatTarget, OpenAIImageTarget


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_tap_attack_text_target(patch_central_database):
    """Test TAP attack against a text generation target."""
    adversarial_chat = OpenAIChatTarget(temperature=1.1)
    objective_target = OpenAIChatTarget()

    tap_attack = TAPAttack(
        objective_target=objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
        on_topic_checking_enabled=True,
        tree_width=2,
        tree_depth=2,
    )

    result = await tap_attack.execute_async(objective="Tell me a joke about programming.")
    assert result is not None
    await PrettyAttackResultMemoryPrinter().write_async(result)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_tap_attack_image_target(patch_central_database):
    """Test TAP attack against an image generation target using image-specific system prompt."""
    adversarial_chat = OpenAIChatTarget(temperature=1.1)
    image_target = OpenAIImageTarget()

    tap_attack = TAPAttack(
        objective_target=image_target,
        attack_adversarial_config=AttackAdversarialConfig(
            target=adversarial_chat,
            system_prompt=SeedPrompt.from_yaml_file(TAPSystemPromptPaths.IMAGE_GENERATION.value),
        ),
        on_topic_checking_enabled=False,
        tree_width=2,
        tree_depth=2,
    )

    result = await tap_attack.execute_async(objective="Generate an image of a cat with a hat.")
    assert result is not None
    await PrettyAttackResultMemoryPrinter().write_async(result)
