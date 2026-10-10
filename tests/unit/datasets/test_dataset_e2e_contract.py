# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise the real dataset E2E assertions without starting its backend fixture."""

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedDataset
from tests.end_to_end import test_all_datasets as dataset_tests

_PROVIDER_NAME = "LocalDataset_latent_injection_tasks"


@pytest.fixture
async def task_dataset_async() -> SeedDataset:
    provider = SeedDatasetProvider.get_all_providers()[_PROVIDER_NAME]()
    return await provider.fetch_dataset_async(cache=False)


async def _validate_dataset_async(dataset: SeedDataset) -> None:
    provider_cls = SeedDatasetProvider.get_all_providers()[_PROVIDER_NAME]
    with patch.object(dataset_tests, "_fetch_with_retry_async", new=AsyncMock(return_value=dataset)):
        await dataset_tests.TestAllDatasets().test_fetch_dataset(name=_PROVIDER_NAME, provider_cls=provider_cls)


@pytest.mark.usefixtures("patch_central_database")
async def test_local_tasks_pass_actual_e2e_validation_async() -> None:
    provider_cls = SeedDatasetProvider.get_all_providers()[_PROVIDER_NAME]
    await dataset_tests.TestAllDatasets().test_fetch_dataset(name=_PROVIDER_NAME, provider_cls=provider_cls)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("family", "prefix_index"), [("report", 0), ("resume", 0), ("resume", 1), ("resume", 2), ("resume", 3)]
)
async def test_blanked_task_prefix_fails_actual_e2e_validation_async(
    *, task_dataset_async: SeedDataset, family: str, prefix_index: int
) -> None:
    dataset = task_dataset_async.model_copy(deep=True)
    prefixes = [seed for seed in dataset.seeds if seed.value and seed.metadata["family"] == family]
    assert len(prefixes) == {"report": 1, "resume": 4}[family]
    prefixes[prefix_index].value = ""
    assert sum(not seed.value for seed in dataset.seeds) == 4
    with pytest.raises(AssertionError, match="expected 3 empty, got 4"):
        await _validate_dataset_async(dataset)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("empty_count", [0, 1, 2])
async def test_missing_empty_tasks_fail_actual_e2e_validation_async(
    *, task_dataset_async: SeedDataset, empty_count: int
) -> None:
    dataset = task_dataset_async.model_copy(deep=True)
    empty_seeds = [seed for seed in dataset.seeds if not seed.value]
    for seed in empty_seeds[empty_count:]:
        seed.value = "Unexpected task prefix"
    assert sum(not seed.value for seed in dataset.seeds) == empty_count
    with pytest.raises(AssertionError, match=f"expected 3 empty, got {empty_count}"):
        await _validate_dataset_async(dataset)


@pytest.mark.usefixtures("patch_central_database")
async def test_extra_empty_task_fails_actual_e2e_validation_async(task_dataset_async: SeedDataset) -> None:
    dataset = task_dataset_async.model_copy(deep=True)
    empty_seed = next(seed for seed in dataset.seeds if not seed.value)
    dataset.seeds.append(empty_seed.model_copy(deep=True))
    assert sum(not seed.value for seed in dataset.seeds) == 4
    with pytest.raises(AssertionError, match="expected 3 empty, got 4"):
        await _validate_dataset_async(dataset)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("dataset_name", ["garak_latent_injection_triggers", "unrelated_dataset"])
async def test_other_dataset_rejects_empty_seed_async(dataset_name: str) -> None:
    provider = SeedDatasetProvider.get_all_providers()["LocalDataset_latent_injection_triggers"]()
    dataset = await provider.fetch_dataset_async(cache=False)
    dataset.dataset_name = dataset_name
    for seed in dataset.seeds:
        seed.dataset_name = dataset_name
    assert all(seed.value for seed in dataset.seeds)
    await _validate_dataset_async(dataset)

    dataset.seeds[0].value = ""
    with pytest.raises(AssertionError, match="expected 0 empty, got 1"):
        await _validate_dataset_async(dataset)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("empty_seed", [False, True])
async def test_seed_dataset_name_mismatch_fails_actual_e2e_validation_async(
    *, task_dataset_async: SeedDataset, empty_seed: bool
) -> None:
    dataset = task_dataset_async.model_copy(deep=True)
    seed = next(seed for seed in dataset.seeds if bool(seed.value) != empty_seed)
    seed.dataset_name = "unrelated_dataset"
    with pytest.raises(AssertionError, match="dataset_name mismatch"):
        await _validate_dataset_async(dataset)
