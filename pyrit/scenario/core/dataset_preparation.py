# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scoped ownership of missing-only provider work shared by scenario callers."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator
    from typing import Any

    from pyrit.datasets import SeedDatasetProvider
    from pyrit.memory import MemoryInterface
    from pyrit.models import SeedDataset


_current_preparation: ContextVar[tuple[DatasetPreparation, bool] | None] = ContextVar(
    "scenario_dataset_preparation", default=None
)


class DatasetPreparation:
    """Retain provider work for one runtime and memory lifetime, including failed attempts."""

    def __init__(self, memory: MemoryInterface) -> None:
        """Bind preparation to the memory instance that receives its datasets."""
        self.memory = memory
        self._tasks: dict[tuple[str, object], asyncio.Task[SeedDataset]] = {}
        self._providers: dict[int, SeedDatasetProvider] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._writes: set[asyncio.Task[None]] = set()
        self._stopping = False

    @property
    def has_active_work(self) -> bool:
        """Whether provider work still needs to drain before memory replacement."""
        return bool(self._writes) or any(not task.done() for task in self._tasks.values())

    @contextmanager
    def scope(self, *, retry_failed: bool = False) -> Generator[None]:
        """
        Share missing-only fetches within this caller's scenario configuration path.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        token = _current_preparation.set((self, retry_failed))
        try:
            yield
        finally:
            _current_preparation.reset(token)

    async def fetch_async(
        self,
        *,
        name: str,
        provider: SeedDatasetProvider,
        registered: bool,
        retry_failed: bool,
        fetch: Callable[[], Coroutine[Any, Any, SeedDataset]],
    ) -> SeedDataset:
        """
        Join an equivalent provider call; only an explicit retry replaces a failed attempt.

        Returns:
            SeedDataset: The shared provider result.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        # Registry providers are constructed without arguments within this runtime.
        # Configured providers are live objects: only the same instance is known equivalent.
        key = (name, type(provider) if registered else id(provider))
        if not registered:
            self._providers[id(provider)] = provider
        task = self._tasks.get(key)
        if task is not None and retry_failed and task.done() and (task.cancelled() or task.exception() is not None):
            task = None
        if task is None:
            task = asyncio.create_task(fetch())
            self._tasks[key] = task
            task.add_done_callback(lambda completed: completed.exception() if not completed.cancelled() else None)
        return await asyncio.shield(task)

    async def store_missing_async(self, *, name: str, dataset: SeedDataset) -> None:
        """
        Serialize missing-only commits so overlapping callers cannot insert the same seed IDs.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        task = asyncio.create_task(self._store_missing_async(name=name, dataset=dataset))
        self._writes.add(task)
        task.add_done_callback(self._write_done)
        await asyncio.shield(task)

    def _write_done(self, task: asyncio.Task[None]) -> None:
        self._writes.discard(task)
        if not task.cancelled():
            task.exception()

    async def _store_missing_async(self, *, name: str, dataset: SeedDataset) -> None:
        async with self._write_locks.setdefault(name, asyncio.Lock()):
            if name not in await self.memory.get_seed_dataset_names_async():
                await self.memory.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="DatasetConfiguration")

    async def close_async(self) -> None:
        """Stop admission and drain providers, including writes offloaded by a cancelled caller."""
        self._stopping = True
        pending = [task for task in (*self._tasks.values(), *self._writes) if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._providers.clear()
        self._write_locks.clear()


def current_dataset_preparation(memory: MemoryInterface) -> tuple[DatasetPreparation, bool] | None:
    """Return only the preparation owner for the caller's exact memory instance."""
    current = _current_preparation.get()
    return current if current is not None and current[0].memory is memory else None
