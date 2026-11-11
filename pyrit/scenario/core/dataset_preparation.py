# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scoped ownership of missing-only provider work shared by scenario callers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Generator
    from typing import Any

    from pyrit.datasets import SeedDatasetProvider
    from pyrit.memory import MemoryInterface
    from pyrit.models import SeedDataset


@dataclass
class _ProviderWork:
    task: asyncio.Task[SeedDataset]
    provider: SeedDatasetProvider
    users: int = 0
    published: bool = False


@dataclass
class _PreparationScope:
    retry_failed: bool
    work: dict[tuple[str, object], _ProviderWork] = field(default_factory=dict)
    closed: bool = False


_current_preparation: ContextVar[tuple[DatasetPreparation, _PreparationScope] | None] = ContextVar(
    "scenario_dataset_preparation", default=None
)
logger = logging.getLogger(__name__)


class DatasetPreparation:
    """Retain provider work for one runtime and memory lifetime, including failed attempts."""

    def __init__(self, memory: MemoryInterface) -> None:
        """Bind preparation to the memory instance that receives its datasets."""
        self.memory = memory
        self._tasks: dict[tuple[str, object], _ProviderWork] = {}
        self._fetches: set[asyncio.Task[SeedDataset]] = set()
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._writes: set[asyncio.Task[None]] = set()
        self._stopping = False

    @property
    def has_active_work(self) -> bool:
        """Whether provider work still needs to drain before memory replacement."""
        return bool(self._writes or self._fetches)

    @contextmanager
    def scope(self, *, retry_failed: bool = False) -> Generator[None]:
        """
        Share missing-only fetches within this caller's scenario configuration path.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        scope = _PreparationScope(retry_failed=retry_failed)
        token = _current_preparation.set((self, scope))
        try:
            yield
        finally:
            _current_preparation.reset(token)
            scope.closed = True
            for key, work in scope.work.items():
                self._release_work(key=key, work=work)

    async def fetch_async(
        self,
        *,
        name: str,
        provider: SeedDatasetProvider,
        registered: bool,
        retry_failed: bool,
        fetch: Callable[[], Coroutine[Any, Any, SeedDataset]],
    ) -> SeedDataset | None:
        """
        Join an equivalent provider call; only an explicit retry replaces a failed attempt.

        Returns:
            SeedDataset | None: The shared provider result, or None if another caller stored it.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        # A missing snapshot must not outlive publication and eviction of its shared work.
        async with self._write_locks.setdefault(name, asyncio.Lock()):
            if self._stopping:
                raise RuntimeError("Dataset preparation is stopping.")
            stored = await self.memory.get_seed_dataset_names_async()
            if self._stopping:
                raise RuntimeError("Dataset preparation is stopping.")
            if name in stored:
                return None
            # Registry providers are constructed without arguments within this runtime.
            # Configured providers are live objects: only the same instance is known equivalent.
            key = (name, type(provider) if registered else id(provider))
            work = self._tasks.get(key)
            if (
                work is not None
                and retry_failed
                and work.task.done()
                and (work.task.cancelled() or work.task.exception() is not None)
            ):
                work = None
            if work is None:
                work = _ProviderWork(task=asyncio.create_task(fetch()), provider=provider)
                self._tasks[key] = work
                self._fetches.add(work.task)
                work.task.add_done_callback(partial(self._fetch_done, key=key, work=work))
            current = _current_preparation.get()
            if current is not None and current[0] is self:
                scope = current[1]
                previous = scope.work.get(key)
                if previous is not work:
                    scope.work[key] = work
                    if not scope.closed:
                        if previous is not None:
                            self._release_work(key=key, work=previous)
                        work.users += 1
        dataset = await asyncio.shield(work.task)
        return None if work.published else dataset

    def _fetch_done(self, task: asyncio.Task[SeedDataset], *, key: tuple[str, object], work: _ProviderWork) -> None:
        failed = task.cancelled() or task.exception() is not None
        self._fetches.discard(task)
        if work.published or (not failed and work.users == 0):
            self._forget_work(key=key, work=work)

    def _forget_work(self, *, key: tuple[str, object], work: _ProviderWork) -> None:
        if self._tasks.get(key) is work:
            del self._tasks[key]

    def _release_work(self, *, key: tuple[str, object], work: _ProviderWork) -> None:
        work.users -= 1
        if work.users == 0 and work.task.done() and not work.task.cancelled() and work.task.exception() is None:
            self._forget_work(key=key, work=work)

    async def store_missing_async(self, *, name: str, dataset: SeedDataset) -> None:
        """
        Serialize missing-only commits so overlapping callers cannot insert the same seed IDs.

        Raises:
            RuntimeError: If runtime shutdown has closed admission.
        """
        if self._stopping:
            raise RuntimeError("Dataset preparation is stopping.")
        current = _current_preparation.get()
        owned = current[1].work if current is not None and current[0] is self else {}
        candidate = next(((key, work) for key, work in owned.items() if key[0] == name), None)
        work = candidate[1] if candidate is not None else None
        if work is not None:
            work.users += 1
        task = asyncio.create_task(self._store_missing_async(name=name, dataset=dataset, work=work))
        self._writes.add(task)
        waiter = asyncio.shield(task)
        task.add_done_callback(
            partial(self._write_done, name=name, candidate=candidate, waiter=waiter, caller=asyncio.current_task())
        )
        await waiter

    def _write_done(
        self,
        task: asyncio.Task[None],
        *,
        name: str,
        candidate: tuple[tuple[str, object], _ProviderWork] | None,
        waiter: asyncio.Future[None],
        caller: asyncio.Task[Any] | None,
    ) -> None:
        self._writes.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None and (waiter.cancelled() or (caller is not None and caller.cancelling())):
                logger.error(
                    "Dataset preparation store failed for %s after its caller was cancelled.", name, exc_info=error
                )
        if candidate is not None:
            self._release_work(key=candidate[0], work=candidate[1])

    async def _store_missing_async(self, *, name: str, dataset: SeedDataset, work: _ProviderWork | None) -> None:
        async with self._write_locks.setdefault(name, asyncio.Lock()):
            if name not in await self.memory.get_seed_dataset_names_async():
                if work is not None and work.published:
                    raise ValueError(f"Dataset '{name}' was removed during preparation. Retry loading it.")
                await self.memory.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="DatasetConfiguration")
            for key, candidate in list(self._tasks.items()):
                if key[0] == name:
                    candidate.published = True
                    self._forget_work(key=key, work=candidate)
            if work is not None:
                work.published = True

    async def close_async(self) -> None:
        """Stop admission and drain providers, including writes offloaded by a cancelled caller."""
        self._stopping = True
        pending = [task for task in (*self._fetches, *self._writes) if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._write_locks.clear()


def current_dataset_preparation(memory: MemoryInterface) -> tuple[DatasetPreparation, bool] | None:
    """Return only the preparation owner for the caller's exact memory instance."""
    current = _current_preparation.get()
    return (current[0], current[1].retry_failed) if current is not None and current[0].memory is memory else None
