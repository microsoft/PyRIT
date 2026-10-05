# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote.harmbench_dataset import _HarmBenchDataset
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
from pyrit.models import SeedDataset, SeedObjective, SeedOrigin, SeedPrompt, SeedSimulatedConversation


@pytest.mark.parametrize("cache", [True, False])
async def test_fetch_tags_all_seeds_and_preserves_dataset_async(cache: bool) -> None:
    provider = _HarmBenchDataset()
    seeds = [
        SeedObjective(value="Goal", origin=SeedOrigin.USER, metadata={"label": "kept"}),
        SeedPrompt(value="Prompt", origin=SeedOrigin.LOCAL),
        SeedSimulatedConversation(adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation.")),
    ]
    dataset = SeedDataset(
        dataset_name=provider.dataset_name,
        seeds=[*seeds, {"value": "Dictionary goal", "seed_type": "objective", "origin": "generated"}],
    )
    before = [seed.model_dump(exclude={"origin"}) for seed in dataset.seeds]
    with patch.object(provider, "_fetch_dataset_async", new_callable=AsyncMock, return_value=dataset) as fetch:
        result = await provider.fetch_dataset_async(cache=cache)

    fetch.assert_awaited_once()
    assert fetch.call_args.kwargs == {"cache": cache}
    assert result is dataset
    assert all(seed.origin is SeedOrigin.REMOTE for seed in result.seeds)
    assert [seed.model_dump(exclude={"origin"}) for seed in result.seeds] == before


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("cache", [True, False])
async def test_remote_provider_origin_ignores_file_and_cache_async(*, local: bool, cache: bool) -> None:
    provider = _HarmBenchDataset(source_type="file" if local else "public_url")
    with patch.object(
        provider, "_fetch_from_url", return_value=[{"Behavior": "A goal", "SemanticCategory": "harmful"}]
    ):
        dataset = await provider.fetch_dataset_async(cache=cache)
    assert dataset.seeds
    assert all(seed.origin is SeedOrigin.REMOTE for seed in dataset.seeds)


@pytest.mark.parametrize("failure", [ValueError("Invalid dataset"), asyncio.CancelledError()])
async def test_fetch_propagates_failure_async(failure: BaseException) -> None:
    provider = _HarmBenchDataset()
    with (
        patch.object(provider, "_fetch_dataset_async", new_callable=AsyncMock, side_effect=failure) as fetch,
        pytest.raises(type(failure)) as error,
    ):
        await provider.fetch_dataset_async()
    assert error.value is failure
    fetch.assert_awaited_once()


def test_remote_loader_requires_fetch_implementation() -> None:
    class IncompleteLoader(_RemoteDatasetLoader):
        @property
        def dataset_name(self) -> str:
            return "incomplete"

    assert IncompleteLoader.__name__ not in SeedDatasetProvider._registry
    with pytest.raises(TypeError, match="_fetch_dataset_async"):
        IncompleteLoader()  # type: ignore[abstract]
