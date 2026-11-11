# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime-scoped sharing does not change source policies, storage, or SDK behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.async_utils import wait_for_completion_async, wait_for_event_async

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import DatasetFetchPolicy, SeedDataset, SeedObjective
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.core.dataset_preparation import DatasetPreparation


def _provider(name: str) -> MagicMock:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = name
    provider.fetch_dataset_async = AsyncMock(
        return_value=SeedDataset(
            dataset_name=name, seeds=[SeedObjective(value=f"Example for {name}", dataset_name=name)]
        )
    )
    return provider


@pytest.mark.usefixtures("patch_central_database")
async def test_overlapping_selections_share_provider_work_and_stored_sources_win() -> None:
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Edited", dataset_name="stored")], added_by="test"
    )
    owner = DatasetPreparation(memory)
    shared, other, stored = _provider("shared"), _provider("other"), _provider("stored")
    entered, release = asyncio.Event(), asyncio.Event()
    dataset: SeedDataset = shared.fetch_dataset_async.return_value

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        entered.set()
        await release.wait()
        return dataset

    shared.fetch_dataset_async.side_effect = fetch_async
    first = DatasetAttackConfiguration(sources=[DatasetSource(name="shared"), DatasetSource(name="stored")])
    second = DatasetAttackConfiguration(sources=[DatasetSource(name="shared"), DatasetSource(name="other")])
    tasks: list[asyncio.Task[None]] = []
    try:
        with (
            owner.scope(),
            patch.object(
                SeedDatasetProvider,
                "get_providers_by_name_async",
                return_value={
                    "shared": shared,
                    "other": other,
                    "stored": stored,
                },
            ),
        ):
            tasks.append(asyncio.create_task(first.prepare_async()))
            await wait_for_event_async(event=entered, tasks=tasks)
            tasks.append(asyncio.create_task(second.prepare_async()))
            release.set()
            await asyncio.gather(*tasks)
            await first.with_overrides(max_total=1, filters={"harm_categories": ["missing"]}).prepare_async()
        shared.fetch_dataset_async.assert_awaited_once()
        other.fetch_dataset_async.assert_awaited_once()
        stored.fetch_dataset_async.assert_not_awaited()
        assert [seed.value for seed in await memory.get_seeds_async(dataset_name="stored")] == ["Edited"]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_failure_is_sticky_until_retry_and_configured_provider_identity_is_not_its_name() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    failed, different = _provider("source"), _provider("source")
    failed.fetch_dataset_async.side_effect = ValueError("Offline provider failed")
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="source", provider=failed)])
    try:
        with owner.scope():
            for _ in range(2):
                with pytest.raises(ValueError, match="Offline provider failed"):
                    await config.prepare_async()
            await DatasetAttackConfiguration(sources=[DatasetSource(name="source", provider=different)]).prepare_async()
        failed.fetch_dataset_async.assert_awaited_once()
        different.fetch_dataset_async.assert_awaited_once()
        # A separate name keeps the failure missing so explicit retry must actually call its provider.
        retry_provider = _provider("retry")
        retry_config = DatasetAttackConfiguration(sources=[DatasetSource(name="retry", provider=retry_provider)])
        retry_provider.fetch_dataset_async.side_effect = ValueError("Retry required")
        with owner.scope():
            with pytest.raises(ValueError, match="Retry required"):
                await retry_config.prepare_async()
        retry_provider.fetch_dataset_async.side_effect = None
        with owner.scope(retry_failed=True):
            await retry_config.prepare_async()
        assert retry_provider.fetch_dataset_async.await_count == 2
    finally:
        await owner.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_cancelling_a_waiter_does_not_cancel_provider_and_shutdown_drains_it() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("held")
    entered, release = asyncio.Event(), asyncio.Event()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        entered.set()
        await release.wait()
        return dataset

    provider.fetch_dataset_async.side_effect = fetch_async
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="held", provider=provider)])
    with owner.scope():
        waiter = asyncio.create_task(config.prepare_async())
    closing: asyncio.Task[None] | None = None
    try:
        await wait_for_event_async(event=entered, tasks=[waiter])
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert owner.has_active_work
        closing = asyncio.create_task(owner.close_async())
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(RuntimeError, match="stopping"):
            with owner.scope():
                pass
        release.set()
        await wait_for_completion_async(future=closing)
        assert not owner.has_active_work
        provider.fetch_dataset_async.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(waiter, *([closing] if closing is not None else []), return_exceptions=True)


@pytest.mark.usefixtures("patch_central_database")
async def test_explicit_always_policy_still_refreshes_without_sharing() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("refresh")
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="refresh", provider=provider)],
        fetch=DatasetFetchPolicy.ALWAYS,
    )
    try:
        with owner.scope():
            await config.prepare_async()
            await config.prepare_async()
        assert provider.fetch_dataset_async.await_count == 2
        assert all(call.kwargs == {"cache": False} for call in provider.fetch_dataset_async.await_args_list)
    finally:
        await owner.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_overlapping_commits_recheck_storage_inside_the_source_lock() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    dataset: SeedDataset = _provider("commit").fetch_dataset_async.return_value
    entered, release = asyncio.Event(), asyncio.Event()
    stored = False
    original_store = memory.add_seed_datasets_to_memory_async

    async def names_async() -> list[str]:
        return ["commit"] if stored else []

    async def store_async(*, datasets: list[SeedDataset], added_by: str) -> None:
        nonlocal stored
        entered.set()
        await release.wait()
        await original_store(datasets=datasets, added_by=added_by)
        stored = True

    tasks: list[asyncio.Task[None]] = []
    try:
        with (
            patch.object(memory, "get_seed_dataset_names_async", side_effect=names_async),
            patch.object(memory, "add_seed_datasets_to_memory_async", side_effect=store_async) as store,
        ):
            tasks.append(asyncio.create_task(owner.store_missing_async(name="commit", dataset=dataset)))
            await wait_for_event_async(event=entered, tasks=tasks)
            tasks.append(asyncio.create_task(owner.store_missing_async(name="commit", dataset=dataset)))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert store.await_count == 1
            release.set()
            await asyncio.gather(*tasks)
            assert store.await_count == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close_async()
