# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime-scoped sharing does not change source policies, storage, or SDK behavior."""

import asyncio
from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.async_utils import get_defined_tasks, wait_for_completion_async, wait_for_event_async

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
async def test_stale_missing_snapshot_cannot_admit_another_fetch_after_publication() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("publication")
    fetched, missing_read, allow_store, release_read = (asyncio.Event() for _ in range(4))
    writer_started = asyncio.Event()
    stale_reader = ContextVar("stale_reader", default=False)
    publishing = ContextVar("publishing", default=False)
    lock = asyncio.Lock()
    owner._write_locks["publication"] = lock
    original_names = memory.get_seed_dataset_names_async
    original_acquire = lock.acquire
    writer_blocked: bool | None = None

    async def names_async() -> list[str]:
        snapshot = await original_names()
        if stale_reader.get():
            assert snapshot == []
            missing_read.set()
            await release_read.wait()
        return list(snapshot)

    async def acquire_async() -> bool:
        nonlocal writer_blocked
        if publishing.get():
            writer_blocked = lock.locked()
            writer_started.set()
        return await original_acquire()

    async def prepare_async(*, first: bool) -> None:
        with owner.scope():
            token = stale_reader.set(not first)
            try:
                dataset = await owner.fetch_async(
                    name="publication",
                    provider=provider,
                    registered=False,
                    retry_failed=False,
                    fetch=provider.fetch_dataset_async,
                )
            finally:
                stale_reader.reset(token)
            if first:
                fetched.set()
                await allow_store.wait()
            if dataset is not None:
                token = publishing.set(first)
                try:
                    await owner.store_missing_async(name="publication", dataset=dataset)
                finally:
                    publishing.reset(token)

    tasks: list[asyncio.Task[None]] = []
    try:
        with (
            patch.object(memory, "get_seed_dataset_names_async", side_effect=names_async),
            patch.object(lock, "acquire", side_effect=acquire_async),
        ):
            first = asyncio.create_task(prepare_async(first=True))
            tasks.append(first)
            await wait_for_event_async(event=fetched, tasks=tasks)
            tasks.append(asyncio.create_task(prepare_async(first=False)))
            await wait_for_event_async(event=missing_read, tasks=tasks)
            allow_store.set()
            await wait_for_event_async(event=writer_started, tasks=tasks)
            if writer_blocked:
                assert not first.done()
            else:
                # Without admission synchronization, publish before the captured read resumes.
                await wait_for_completion_async(future=first)
                assert await original_names() == ["publication"]
            assert not release_read.is_set()
            release_read.set()
            await asyncio.gather(*tasks)
            provider.fetch_dataset_async.assert_awaited_once()
    finally:
        allow_store.set()
        release_read.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_deleted_prepared_dataset_fetches_fresh_data_and_shares_overlapping_waiters() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("deleted")
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="deleted", provider=provider)])
    entered, release = asyncio.Event(), asyncio.Event()
    refreshed = SeedDataset(
        dataset_name="deleted", seeds=[SeedObjective(value="New provider contents", dataset_name="deleted")]
    )

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        entered.set()
        await release.wait()
        return refreshed

    tasks: list[asyncio.Task[None]] = []
    try:
        with owner.scope():
            await config.prepare_async()
            provider.fetch_dataset_async.assert_awaited_once()
            await memory.delete_dataset_async(dataset_name="deleted")
            provider.fetch_dataset_async.side_effect = fetch_async
            tasks.append(asyncio.create_task(config.prepare_async()))
            await wait_for_event_async(event=entered, tasks=tasks)
            tasks.append(asyncio.create_task(config.with_overrides(max_total=1).prepare_async()))
            release.set()
            await asyncio.gather(*tasks)
            assert provider.fetch_dataset_async.await_count == 2
            assert [seed.value for seed in await memory.get_seeds_async(dataset_name="deleted")] == [
                "New provider contents"
            ]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_completed_fetch_remains_shared_until_its_held_store_commits() -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("storing")
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="storing", provider=provider)])
    store_entered, second_waiter, release_store = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_write = memory.add_seed_datasets_to_memory_async
    original_names = memory.get_seed_dataset_names_async
    overlapping = ContextVar("overlapping", default=False)

    async def write_async(*, datasets: list[SeedDataset], added_by: str) -> None:
        store_entered.set()
        await release_store.wait()
        await original_write(datasets=datasets, added_by=added_by)

    async def names_async() -> list[str]:
        names = await original_names()
        if overlapping.get() and not second_waiter.is_set():
            assert names == []
            second_waiter.set()
        return list(names)

    async def prepare_async(*, second: bool = False) -> None:
        token = overlapping.set(second)
        try:
            with owner.scope():
                await config.with_overrides().prepare_async()
        finally:
            overlapping.reset(token)

    tasks: list[asyncio.Task[None]] = []
    try:
        with (
            patch.object(memory, "add_seed_datasets_to_memory_async", side_effect=write_async),
            patch.object(memory, "get_seed_dataset_names_async", side_effect=names_async),
        ):
            tasks.append(asyncio.create_task(prepare_async()))
            await wait_for_event_async(event=store_entered, tasks=tasks)
            assert await memory.get_seed_dataset_names_async() == []
            provider.fetch_dataset_async.assert_awaited_once()
            tasks.append(asyncio.create_task(prepare_async(second=True)))
            await wait_for_event_async(event=second_waiter, tasks=tasks)
            provider.fetch_dataset_async.assert_awaited_once()
            assert not release_store.is_set()
            release_store.set()
            await asyncio.gather(*tasks)
            provider.fetch_dataset_async.assert_awaited_once()
    finally:
        release_store.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close_async()


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
@pytest.mark.parametrize("cancel", [False, True])
async def test_store_failure_surfaces_after_cancellation_and_remains_retryable(
    *, cancel: bool, caplog: pytest.LogCaptureFixture
) -> None:
    memory = CentralMemory.get_memory_instance()
    owner = DatasetPreparation(memory)
    provider = _provider("write_failure")
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="write_failure", provider=provider)])
    entered, release = asyncio.Event(), asyncio.Event()
    failure = RuntimeError("Synthetic store failed")
    failed_write: asyncio.Task[object] | None = None

    async def write_async(*, datasets: list[SeedDataset], added_by: str) -> None:
        nonlocal failed_write
        failed_write = asyncio.current_task()
        entered.set()
        await release.wait()
        raise failure

    async def prepare_async() -> None:
        with owner.scope():
            await config.prepare_async()

    waiter: asyncio.Task[None] | None = None
    try:
        with patch.object(memory, "add_seed_datasets_to_memory_async", side_effect=write_async):
            waiter = asyncio.create_task(prepare_async())
            await wait_for_event_async(event=entered, tasks=[waiter])
            if cancel:
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter
            release.set()
            assert failed_write is not None
            if not cancel:
                with pytest.raises(RuntimeError, match="Synthetic store failed") as raised:
                    await wait_for_completion_async(future=waiter)
                assert raised.value is failure
            await asyncio.gather(failed_write, return_exceptions=True)
        orphan_errors = [record for record in caplog.records if "after its caller was cancelled" in record.message]
        assert bool(orphan_errors) is cancel
        if cancel:
            assert orphan_errors[0].exc_info is not None and orphan_errors[0].exc_info[1] is failure
        assert await memory.get_seed_dataset_names_async() == []
        await prepare_async()
        assert provider.fetch_dataset_async.await_count == 2
        assert await memory.get_seed_dataset_names_async() == ["write_failure"]
    finally:
        release.set()
        await asyncio.gather(*get_defined_tasks(waiter), return_exceptions=True)
        await owner.close_async()


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
