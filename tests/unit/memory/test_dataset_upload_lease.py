# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""SQL Server lease protocol without a live cloud or credential dependency."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import mssql
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from pyrit.memory import MemoryInterface
from pyrit.memory.dataset_upload_lease import try_upload_lease_async
from unit.async_utils import TEST_WATCHDOG_SECONDS


def _sql_memory(result: int) -> tuple[MagicMock, MagicMock]:
    memory = MagicMock(spec=MemoryInterface)
    memory.engine = MagicMock(spec=Engine)
    memory.engine.dialect = mssql.dialect()
    engine = MagicMock(spec=AsyncEngine)
    connection = MagicMock(spec=AsyncConnection)
    connection.scalar = AsyncMock(side_effect=[result, "Exclusive", "NoLock"])
    connection.invalidate = AsyncMock()
    connection.close = AsyncMock()
    sync_connection = MagicMock(spec=Connection)
    connection.sync_connection = sync_connection
    connection.run_sync = AsyncMock(side_effect=lambda callback: callback(sync_connection))
    engine.connect = AsyncMock(return_value=connection)
    memory._get_async_engine.return_value = engine
    return memory, connection


@pytest.mark.parametrize("result", [0, 1, -1, -3])
async def test_sql_server_upload_lease_is_session_owned_and_detached(result: int) -> None:
    memory, connection = _sql_memory(result)
    upload_id = uuid4()
    if result == -3:
        with pytest.raises(RuntimeError, match="refused"):
            await try_upload_lease_async(memory=memory, upload_id=upload_id)
        lease = None
    else:
        lease = await try_upload_lease_async(memory=memory, upload_id=upload_id)
    connection.run_sync.assert_awaited_once()
    connection.sync_connection.detach.assert_called_once()
    sql, parameters = connection.scalar.call_args_list[0].args
    assert "@LockOwner='Session'" in str(sql)
    assert "@LockTimeout=0" in str(sql)
    assert parameters == {"resource": f"pyrit-dataset-upload:{upload_id}"}
    if result >= 0:
        assert lease is not None
        assert await lease.active_async()
        assert not await lease.active_async()
        await lease.close_async()
        assert not await lease.active_async()
        await lease.close_async()
    else:
        assert lease is None
    connection.invalidate.assert_awaited_once()
    connection.close.assert_awaited_once()


async def test_cancelled_sql_lease_acquisition_drains_acquire_and_release() -> None:
    memory, connection = _sql_memory(0)
    acquiring, acquired, closing, closed = (asyncio.Event() for _ in range(4))

    async def acquire_async(*args: object) -> int:
        acquiring.set()
        await acquired.wait()
        return 0

    async def invalidate_async() -> None:
        closing.set()
        await closed.wait()

    connection.scalar.side_effect = acquire_async
    connection.invalidate.side_effect = invalidate_async
    task = asyncio.create_task(try_upload_lease_async(memory=memory, upload_id=uuid4()))
    try:
        await asyncio.wait_for(acquiring.wait(), TEST_WATCHDOG_SECONDS)
        task.cancel()
        acquired.set()
        await asyncio.wait_for(closing.wait(), TEST_WATCHDOG_SECONDS)
        task.cancel()
    finally:
        acquired.set()
        closed.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, TEST_WATCHDOG_SECONDS)
    connection.invalidate.assert_awaited_once()
    connection.close.assert_awaited_once()
