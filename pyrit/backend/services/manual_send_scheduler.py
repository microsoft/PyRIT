# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded admission and execution shared by manual messages in one backend process."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.converter import Converter


class ManualSendConflictError(ValueError):
    """A manual operation already owns this conversation."""


class ManualSendQueueFullError(ValueError):
    """The bounded manual-message admission budget is exhausted."""


class ManualSendScheduler:
    """Bound manual operations, with separate guards for shared converter state and metadata."""

    DEFAULT_MAX_CONCURRENCY = 4
    DEFAULT_MAX_OPERATIONS = 64

    def __init__(
        self,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
    ) -> None:
        """Initialize bounded ownership and a shared FIFO execution budget."""
        if max_concurrency < 1 or max_operations < max_concurrency:
            raise ValueError("Manual-send limits require 1 <= max_concurrency <= max_operations")
        self._max_concurrency = max_concurrency
        self._max_operations = max_operations
        self._conversations: set[str] = set()
        self._converters: set[int] = set()
        self._metadata_updates: set[str] = set()
        self._condition = asyncio.Condition()
        self._queue: deque[object] = deque()
        self._active = 0
        self._closing = False

    def stop_admission(self) -> None:
        """Reject new operations while existing owners finish cancellation cleanup."""
        self._closing = True

    def has_active_work(self) -> bool:
        """Return whether any manual operation still owns a conversation."""
        return bool(self._conversations)

    @contextmanager
    def reserve(self, *, conversation_id: str) -> Iterator[None]:
        """
        Own a conversation from admission through completion, including failures.

        Yields:
            None: The operation's bounded reservation.

        Raises:
            ManualSendConflictError: If a manual operation already owns the conversation.
            ManualSendQueueFullError: If accepting the operation would exceed the budget.
        """
        if self._closing:
            raise ManualSendQueueFullError("Manual message operations are shutting down")
        if conversation_id in self._conversations:
            raise ManualSendConflictError("A manual message operation is already in progress for this conversation")
        if len(self._conversations) >= self._max_operations:
            raise ManualSendQueueFullError("The manual-send queue is full. Wait for an active operation to finish")
        self._conversations.add(conversation_id)
        try:
            yield
        finally:
            self._conversations.remove(conversation_id)

    @asynccontextmanager
    async def operation_async(self) -> AsyncIterator[None]:
        """
        Hold an execution slot, releasing the slot or waiting ticket on every exit.

        Yields:
            None: The operation's execution slot.
        """
        ticket = object()
        acquired = False
        async with self._condition:
            self._queue.append(ticket)
            try:
                await self._condition.wait_for(
                    lambda: self._queue[0] is ticket and self._active < self._max_concurrency
                )
                self._queue.popleft()
                self._active += 1
                acquired = True
                self._condition.notify_all()
            finally:
                if not acquired:
                    self._queue.remove(ticket)
                    self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def conversion_async(self, converter: "Converter") -> AsyncIterator[None]:
        """
        Protect one shared converter instance only while it is converting.

        Yields:
            None: Ownership of the converter invocation.
        """
        key = id(converter)
        async with self._condition:
            await self._condition.wait_for(lambda: key not in self._converters)
            self._converters.add(key)
        try:
            yield
        finally:
            async with self._condition:
                self._converters.remove(key)
                self._condition.notify_all()

    @asynccontextmanager
    async def metadata_update_async(self, *, attack_result_id: str) -> AsyncIterator[None]:
        """
        Serialize the complete metadata read/merge/write for one attack.

        Yields:
            None: Ownership of the attack's metadata update.
        """
        async with self._condition:
            await self._condition.wait_for(lambda: attack_result_id not in self._metadata_updates)
            self._metadata_updates.add(attack_result_id)
        try:
            yield
        finally:
            async with self._condition:
                self._metadata_updates.remove(attack_result_id)
                self._condition.notify_all()


@lru_cache(maxsize=1)
def get_manual_send_scheduler() -> ManualSendScheduler:
    """Return the process-wide manual-message scheduler."""
    return ManualSendScheduler()
