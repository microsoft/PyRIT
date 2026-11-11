# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Native, process-death-released ownership of a durable upload reservation."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection

    from pyrit.memory.memory_interface import MemoryInterface


@dataclass
class DatasetUploadLease:
    """A native lock held across the reservation/commit transaction gap."""

    loop: asyncio.AbstractEventLoop
    file: BinaryIO | None = None
    path: Path | None = None
    connection: AsyncConnection | None = None
    resource: str = ""
    closed: bool = False

    async def active_async(self) -> bool:
        """
        Fence publication if the owner lost its database session or closed its file.

        Returns:
            bool: Whether this lease still owns the reservation.
        """
        if self.closed:
            return False
        if self.connection is not None:
            mode = await self.connection.scalar(
                text("SELECT APPLOCK_MODE('public', :resource, 'Session')"), {"resource": self.resource}
            )
            return isinstance(mode, str) and mode == "Exclusive"
        return self.file is not None and not self.file.closed

    async def close_async(self) -> None:
        """Release native ownership without leaving a session lock in a connection pool."""
        if self.closed:
            return
        self.closed = True
        if self.connection is not None:
            try:
                # This is a dedicated physical connection, never a shared request-pool slot.
                await self.connection.invalidate()
            finally:
                await self.connection.close()
        if self.file is not None:
            await asyncio.to_thread(self.file.close)
            # Retain the tiny sidecar, like the durable journal, to prevent inode
            # replacement from creating two different locks for the same operation.


def _try_file_lease(*, database: str, upload_id: UUID, loop: asyncio.AbstractEventLoop) -> DatasetUploadLease | None:
    if database.startswith("file:") or database == ":memory:":
        identity = hashlib.sha256(database.encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / f"pyrit-{identity}-{upload_id}.upload-lock"
    else:
        path = Path(database).with_name(f".{Path(database).name}.{upload_id}.upload-lock")
    file = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if file.seek(0, os.SEEK_END) == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        file.close()
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    return DatasetUploadLease(loop=loop, file=file, path=path)


async def try_upload_lease_async(*, memory: MemoryInterface, upload_id: UUID) -> DatasetUploadLease | None:
    """
    Try native exclusive ownership; contention proves the reservation is still live.

    Returns:
        DatasetUploadLease | None: Acquired lease, or None while another owner is active.

    Raises:
        RuntimeError: If memory is uninitialized or SQL Server refuses the application lock.
    """
    if memory.engine is None:
        raise RuntimeError("Dataset upload leases require initialized memory.")
    loop = asyncio.get_running_loop()
    if memory.engine.dialect.name == "sqlite":
        task = asyncio.create_task(
            asyncio.to_thread(
                _try_file_lease, database=memory.engine.url.database or ":memory:", upload_id=upload_id, loop=loop
            )
        )
    elif memory.engine.dialect.name == "mssql":
        task = asyncio.create_task(_try_sql_server_lease_async(memory=memory, upload_id=upload_id, loop=loop))
    else:
        raise RuntimeError("Dataset upload leases support SQLite and SQL Server only.")
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        lease = task.result()
        if lease is not None:
            close = asyncio.create_task(lease.close_async())
            while not close.done():
                try:
                    await asyncio.shield(close)
                except asyncio.CancelledError:
                    continue
            close.result()
        raise


async def _try_sql_server_lease_async(
    *, memory: MemoryInterface, upload_id: UUID, loop: asyncio.AbstractEventLoop
) -> DatasetUploadLease | None:
    lease = DatasetUploadLease(loop=loop, resource=f"pyrit-dataset-upload:{upload_id}")
    try:
        lease.connection = await memory._get_async_engine().connect()
        # Detach immediately: a live reservation must not exhaust the request pool
        # while it waits for another connection to commit its upload.
        await lease.connection.run_sync(lambda connection: connection.detach())
        result = await lease.connection.scalar(
            text(
                "DECLARE @result int; EXEC @result = sp_getapplock @Resource=:resource, "
                "@LockMode='Exclusive', @LockOwner='Session', @LockTimeout=0; SELECT @result;"
            ),
            {"resource": lease.resource},
        )
        if result is not None and result >= 0:
            return lease
        if result != -1:
            raise RuntimeError(f"SQL Server refused the dataset upload lease ({result}).")
    except BaseException:
        await lease.close_async()
        raise
    await lease.close_async()
    return None
