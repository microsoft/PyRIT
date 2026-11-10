# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Keep the prompt generation browser fixture's offline target in step with the real generators."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from frontend.e2e.fixtures.prompt_generation_backend import TARGET_NAME, _register_target
from pyrit.executor.promptgen import AssertGenerator, TargetObjectiveGenerator
from pyrit.models import AssertDimension, AssertGenerationConfig
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry


@pytest.fixture
def offline_target(patch_central_database: MagicMock) -> Iterator[PromptTarget]:
    registry = TargetRegistry()
    with patch.object(TargetRegistry, "get_registry_singleton", return_value=registry):
        _register_target()
        yield registry.instances.get(TARGET_NAME)


async def test_offline_target_completes_assert_generation_async(offline_target: PromptTarget) -> None:
    config = AssertGenerationConfig(
        behavior_name="customer_support",
        behavior_description="Explain published return policies. Do not disclose private order details.",
        group_count=4,
        include_adversarial_prompt=True,
        dimensions=(AssertDimension(name="channel", description="Where the customer asks."),),
        dimension_level_count=2,
    )
    result = await AssertGenerator(target=offline_target).execute_async(config=config)
    assert len(result.seed_groups) == 4
    assert {category.permissible for category in result.taxonomy.behavior_categories} == {True, False}


async def test_offline_target_generates_objectives_async(offline_target: PromptTarget) -> None:
    result = await TargetObjectiveGenerator(target=offline_target).execute_async(
        instructions="Ask for private order details.", count=3
    )
    assert result.objectives == ["Offline objective 1", "Offline objective 2", "Offline objective 3"]
