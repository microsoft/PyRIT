# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bound native async analytics work through its actual resource cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from sys import float_info
from time import monotonic
from typing import TYPE_CHECKING, TypeVar

from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.exceptions.analytics_exception import AnalyticsBusyException, AnalyticsTimeoutException
from pyrit.memory.query_control import QueryControl

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(eq=False)
class _Waiter:
    ready: asyncio.Future[None]
    deadline: float


@dataclass(eq=False)
class _Operation:
    """Keep the task strongly owned even after its requesting coroutine has left."""

    control: QueryControl
    finished: asyncio.Future[None]
    abandoned: bool = False
    task: Awaitable[object] | None = None


@dataclass
class _Lane:
    limit: int
    timeout: float
    active: int = 0
    queued: deque[_Waiter] = field(default_factory=deque)
    running: set[_Operation] = field(default_factory=set)


class AnalyticsExecution:
    """
    Reserve independent report and quick-query capacity on one event loop.

    Admission is bounded before creating an operation task. A response deadline or
    caller cancellation signals ``QueryControl`` but does not cancel that task:
    native drivers and session cleanup must finish before its slot is released.
    There is no coalescing, result cache, thread pool, or SQL timeout policy here.
    The reader owns database interruption and session cleanup.
    """

    def __init__(
        self,
        *,
        report_workers: int = 5,
        quick_workers: int = 2,
        max_queue: int = 10,
        queue_timeout: float = 1.0,
        report_timeout: float = 5.0,
        quick_timeout: float = 1.0,
    ) -> None:
        """
        Bind a controller to the running loop without starting database work.

        Args:
            report_workers (int): Concurrent report slots, including cleanup.
            quick_workers (int): Separate slots shared by result pages and facets.
            max_queue (int): Maximum waiting requests per lane, excluding active slots.
            queue_timeout (float): Maximum admission wait in seconds.
            report_timeout (float): Report execution budget after admission, in seconds.
            quick_timeout (float): Result-page/facet execution budget, in seconds.

        Raises:
            ValueError: If a count is not a positive integer or a timeout is not positive and finite.
            RuntimeError: If construction occurs outside a running event loop.
        """
        for name, value in (
            ("report_workers", report_workers),
            ("quick_workers", quick_workers),
            ("max_queue", max_queue),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        for name, timeout in (
            ("queue_timeout", queue_timeout),
            ("report_timeout", report_timeout),
            ("quick_timeout", quick_timeout),
        ):
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= float_info.max:
                raise ValueError(f"{name} must be positive and finite.")
        self._loop = asyncio.get_running_loop()
        self._lanes = {
            True: _Lane(limit=report_workers, timeout=report_timeout),
            False: _Lane(limit=quick_workers, timeout=quick_timeout),
        }
        self._max_queue = max_queue
        self._queue_timeout = queue_timeout
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_closed(self) -> bool:
        """Whether shutdown has drained all started operations, not just their callers."""
        return self._closed

    async def run_async(self, *, report: bool, task: Callable[[QueryControl], Awaitable[T]]) -> T:
        """
        Admit one operation and await its response without blocking the owning loop.

        Args:
            report (bool): Use the report lane rather than the reserved quick lane.
            task (Callable[[QueryControl], Awaitable[T]]): Native async work that honors
                the shared control and does not return before releasing its resources.

        Returns:
            T: This operation's result. Separate calls never share response objects.

        Raises:
            AnalyticsBusyException: If the lane is full, admission expires, or shutdown has begun.
            AnalyticsTimeoutException: If execution exceeds its budget.
            CancelledError: If the caller leaves; running work retains its slot until cleanup finishes.
            RuntimeError: If used from a different event loop.
        """
        self._check_loop()
        lane = self._lanes[report]
        await self._admit_async(lane)
        work = _Operation(
            control=QueryControl(deadline=monotonic() + lane.timeout),
            finished=self._loop.create_future(),
        )
        lane.running.add(work)
        try:
            operation = self._create_task(self._execute_async(task=task, control=work.control))
        except BaseException:
            self._finish(lane=lane, work=work)
            raise
        work.task = operation
        operation.add_done_callback(lambda completed: self._complete(lane=lane, work=work, task=completed))
        try:
            done, _ = await asyncio.wait({operation}, timeout=work.control.remaining)
            if not done:
                self._abandon(work=work, task=operation)
                raise AnalyticsTimeoutException
            return operation.result()
        except asyncio.CancelledError:
            self._abandon(work=work, task=operation)
            raise

    async def close_async(self) -> None:
        """
        Reject admission, signal cancellation, and drain actual work on the owning loop.

        This is idempotent and terminal. It can outlast response deadlines because
        returning early would allow a replacement controller to overlap database
        operations still cleaning up. Cancelling this await, even repeatedly, is
        propagated only after draining. The memory backend itself is not disposed.
        If scheduling the drain fails, admission remains closed and a later
        ``close_async`` call can retry the drain.
        """
        self._check_loop()
        if self._close_task is None:
            self._closing = True
            for lane in self._lanes.values():
                while lane.queued:
                    waiter = lane.queued.popleft()
                    if not waiter.ready.done():
                        waiter.ready.set_exception(AnalyticsBusyException())
                for work in lane.running:
                    work.control.cancel()
            self._close_task = self._create_task(self._drain_async())
        cancellation: asyncio.CancelledError | None = None
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError as error:
                cancellation = error
        self._close_task.result()
        if cancellation is not None:
            raise cancellation

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Analytics must be used and closed on its owning event loop.")

    def _create_task(self, coroutine: Coroutine[object, object, T]) -> asyncio.Task[T]:
        try:
            return self._loop.create_task(coroutine)
        except BaseException:
            coroutine.close()
            raise

    async def _admit_async(self, lane: _Lane) -> None:
        if self._closing:
            raise AnalyticsBusyException
        if lane.active < lane.limit:
            lane.active += 1
            return
        if len(lane.queued) >= self._max_queue:
            raise AnalyticsBusyException
        waiter = _Waiter(ready=self._loop.create_future(), deadline=monotonic() + self._queue_timeout)
        lane.queued.append(waiter)
        try:
            try:
                async with asyncio.timeout(self._queue_timeout):
                    await waiter.ready
            except TimeoutError as error:
                raise AnalyticsBusyException from error
            if self._closing or monotonic() >= waiter.deadline:
                raise AnalyticsBusyException
        except BaseException:
            if waiter in lane.queued:
                lane.queued.remove(waiter)
            elif not waiter.ready.cancelled() and waiter.ready.exception() is None:
                self._release(lane)
            raise

    def _release(self, lane: _Lane) -> None:
        lane.active -= 1
        while lane.queued and not self._closing:
            waiter = lane.queued.popleft()
            if waiter.ready.done():
                continue
            if monotonic() >= waiter.deadline:
                waiter.ready.set_exception(AnalyticsBusyException())
                continue
            lane.active += 1
            waiter.ready.set_result(None)
            break

    def _complete(self, *, lane: _Lane, work: _Operation, task: asyncio.Task[T]) -> None:
        if not task.cancelled():
            task.exception()
        self._finish(lane=lane, work=work)
        if work.abandoned:
            self._log_abandoned_error(task)

    def _finish(self, *, lane: _Lane, work: _Operation) -> None:
        lane.running.remove(work)
        work.task = None
        self._release(lane)
        work.finished.set_result(None)

    def _abandon(self, *, work: _Operation, task: asyncio.Task[T]) -> None:
        work.abandoned = True
        work.control.cancel()
        # Completion can precede cancellation delivery, after its callback consumed the exception.
        if work.finished.done():
            self._log_abandoned_error(task)

    async def _drain_async(self) -> None:
        await gather_with_cleanup_async(work.finished for lane in self._lanes.values() for work in lane.running)
        self._closed = True

    @staticmethod
    async def _execute_async(*, task: Callable[[QueryControl], Awaitable[T]], control: QueryControl) -> T:
        control.check()
        result = await task(control)
        control.check()
        return result

    @staticmethod
    def _log_abandoned_error(task: asyncio.Task[T]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None and not isinstance(error, AnalyticsTimeoutException):
                logger.error("Analytics operation failed after its caller left.", exc_info=error)
