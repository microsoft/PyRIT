# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Regression coverage for scenario unit-test dataset isolation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedDataset, SeedObjective
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.core.dataset_configuration import DatasetSource


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("dataset_name", ["harmbench", "foundry_red_team", "airt_scam"])
async def test_missing_dataset_preparation_fails_before_provider_discovery_async(dataset_name: str) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name=dataset_name)])
    with pytest.raises(AssertionError, match="Unit tests must not resolve live dataset providers"):
        await config.prepare_async()


async def test_inline_preparation_does_not_resolve_providers_async(block_dataset_provider_lookup: AsyncMock) -> None:
    config = DatasetAttackConfiguration(seeds=[SeedObjective(value="inline objective")])
    await config.prepare_async()
    groups = await config.get_attack_seed_groups_async()
    assert [group.objective.value for group in groups] == ["inline objective"]
    block_dataset_provider_lookup.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_preparation_can_explicitly_substitute_fake_providers_async(
    block_dataset_provider_lookup: AsyncMock,
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.fetch_dataset_async = AsyncMock(
        return_value=SeedDataset(
            dataset_name="isolated",
            seeds=[SeedObjective(value="fake provider objective", dataset_name="isolated")],
        )
    )
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="isolated")])
    with patch.object(
        SeedDatasetProvider, "get_providers_by_name_async", return_value={"isolated": provider}
    ) as lookup:
        await config.prepare_async()
        lookup.assert_awaited_once()

    groups = await config.get_attack_seed_groups_async()
    assert [group.objective.value for group in groups] == ["fake provider objective"]
    provider.fetch_dataset_async.assert_awaited_once()
    block_dataset_provider_lookup.assert_not_awaited()
    with pytest.raises(AssertionError, match="Unit tests must not resolve live dataset providers"):
        await SeedDatasetProvider.get_providers_by_name_async(dataset_names=["another"])


@pytest.mark.parametrize("entry_point", ["fetch", "names", "providers"])
async def test_provider_discovery_entry_points_fail_before_imports_async(entry_point: str) -> None:
    with pytest.raises(AssertionError, match="Unit tests must not resolve live dataset providers"):
        if entry_point == "fetch":
            await SeedDatasetProvider.fetch_datasets_async(dataset_names=["garak_system_prompt_extraction"])
        elif entry_point == "names":
            await SeedDatasetProvider.get_all_dataset_names_async()
        else:
            SeedDatasetProvider.get_all_providers()
