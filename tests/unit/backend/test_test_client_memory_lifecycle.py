# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from anyio import from_thread
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.pool import ConnectionPoolEntry

from pyrit.memory import SQLiteMemory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")


@pytest.fixture
async def worker_executor_async() -> AsyncGenerator[ThreadPoolExecutor, None]:
    """Exercise request cleanup with a fixture-owned default executor."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        asyncio.get_running_loop().set_default_executor(executor)
        yield executor


@pytest.mark.usefixtures("worker_executor_async")
@pytest.mark.parametrize("raise_server_exceptions", [False, True])
async def test_contextless_client_disposes_memory_on_request_loop_async(
    *, sqlite_instance: SQLiteMemory, raise_server_exceptions: bool
) -> None:
    app = FastAPI()
    request_loops: list[asyncio.AbstractEventLoop] = []
    drivers: list[aiosqlite.Connection] = []
    engines: list[AsyncEngine] = []

    @app.get("/query")
    async def query_async(fail: bool = False) -> dict[str, int]:
        request_loops.append(asyncio.get_running_loop())
        engine = sqlite_instance._get_async_engine()
        engines.append(engine)

        def record_connection(*, dbapi_connection: object, connection_record: ConnectionPoolEntry) -> None:
            driver = connection_record.driver_connection
            assert isinstance(driver, aiosqlite.Connection)
            drivers.append(driver)

        event.listen(engine.sync_engine, "connect", record_connection, named=True)
        async with await sqlite_instance.get_session_async() as session:
            result = (await session.execute(text("SELECT 1"))).scalar_one()
        if fail:
            raise RuntimeError("request failed after database access")
        return {"result": result}

    # Keep a pool on the pytest loop to prove portal cleanup leaves other owners alone.
    async with await sqlite_instance.get_session_async() as session:
        await session.execute(text("SELECT 1"))
    owner_loop = asyncio.get_running_loop()
    owner_engine = sqlite_instance._async_engines[owner_loop]
    client = TestClient(app, raise_server_exceptions=raise_server_exceptions)
    for index in range(12):
        fail = index % 2 == 1
        if fail and raise_server_exceptions:
            with pytest.raises(RuntimeError, match="request failed after database access"):
                await asyncio.to_thread(client.get, "/query", params={"fail": "true"})
        else:
            response = await asyncio.to_thread(client.get, "/query", params={"fail": str(fail).lower()})
            assert response.status_code == (500 if fail else 200)
            if not fail:
                assert response.json() == {"result": 1}
        assert sqlite_instance._async_engines == {owner_loop: owner_engine}
        assert request_loops[-1].is_closed()
        with pytest.raises(ValueError, match="no active connection"):
            await drivers[-1].execute("SELECT 1")
    assert len(set(request_loops)) == len(engines) == len(drivers) == 12


def test_context_managed_client_keeps_memory_until_lifespan_shutdown(
    sqlite_instance: SQLiteMemory,
) -> None:
    @asynccontextmanager
    async def lifespan_async(app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            await sqlite_instance.dispose_loop_resources_async()

    app = FastAPI(lifespan=lifespan_async)
    engines: list[AsyncEngine] = []

    @app.get("/query")
    async def query_async() -> dict[str, int]:
        async with await sqlite_instance.get_session_async() as session:
            result = (await session.execute(text("SELECT 1"))).scalar_one()
        engines.append(sqlite_instance._get_async_engine())
        return {"result": result}

    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/query").json() == {"result": 1}
            assert list(sqlite_instance._async_engines.values()) == [engines[0]]
    assert len(engines) == 3
    assert all(engine is engines[0] for engine in engines)
    assert not sqlite_instance._async_engines


def test_portal_memory_disposal_failure_is_not_suppressed(sqlite_instance: SQLiteMemory) -> None:
    with (
        patch.object(
            sqlite_instance,
            "dispose_loop_resources_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("memory disposal failed"),
        ) as dispose,
        pytest.raises(RuntimeError, match="memory disposal failed"),
    ):
        with from_thread.start_blocking_portal():
            pass
    dispose.assert_awaited_once()
