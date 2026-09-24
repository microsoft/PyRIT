# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import asyncio
import inspect
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.credentials import AccessToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from pyrit.auth.azure_auth import AsyncAzureAuth
from pyrit.common.async_compatibility import run_legacy_sync_async
from pyrit.memory import AzureSQLMemory, MemoryInterface, SQLiteMemory
from pyrit.models import Conversation, Message, MessagePiece, SeedPrompt


def test_public_memory_io_has_explicit_async_counterparts() -> None:
    configuration_methods = {"enable_embedding", "disable_embedding", "cleanup"}
    for name, method in inspect.getmembers(MemoryInterface, inspect.isfunction):
        if name.startswith("_") or name in configuration_methods or inspect.iscoroutinefunction(method):
            continue
        assert inspect.iscoroutinefunction(getattr(MemoryInterface, name + "_async", None)), name


@pytest.mark.parametrize(
    "name",
    [
        name
        for name, method in inspect.getmembers(MemoryInterface, inspect.isfunction)
        if not name.startswith("_")
        and not inspect.iscoroutinefunction(method)
        and inspect.iscoroutinefunction(getattr(MemoryInterface, name + "_async", None))
    ],
)
def test_public_sync_memory_method_is_deprecated(name: str, sqlite_instance: SQLiteMemory) -> None:
    method = getattr(sqlite_instance, name)
    required = {
        parameter.name: None
        for parameter in inspect.signature(method).parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    with patch("pyrit.memory.memory_interface.print_deprecation_message", side_effect=DeprecationWarning) as warning:
        with pytest.raises(DeprecationWarning):
            method(**required)
    assert warning.call_args.kwargs["new_item"] == f"MemoryInterface.{name}_async"


def test_library_does_not_reference_sync_memory_outside_compatibility() -> None:
    root = Path(__file__).resolve().parents[3] / "pyrit"
    sync_names = {
        name
        for name, method in inspect.getmembers(MemoryInterface, inspect.isfunction)
        if not name.startswith("_")
        and not inspect.iscoroutinefunction(method)
        and hasattr(MemoryInterface, name + "_async")
    }
    violations = []
    for path in root.rglob("*.py"):
        if "memory" in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in sync_names:
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents.get(owner)
            if isinstance(owner, ast.FunctionDef):
                container = parents.get(owner)
                if isinstance(container, (ast.ClassDef, ast.Module)) and any(
                    isinstance(method, ast.AsyncFunctionDef) and method.name == owner.name + "_async"
                    for method in container.body
                ):
                    continue
            violations.append(f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}")
    assert not violations, "\n".join(violations)


async def test_async_memory_uses_async_driver(sqlite_instance: SQLiteMemory) -> None:
    with patch.object(sqlite_instance, "SessionFactory", side_effect=AssertionError("Sync session used")):
        await sqlite_instance.add_conversation_to_memory_async(conversation=Conversation(conversation_id="async"))
        await sqlite_instance.add_message_to_memory_async(
            request=Message(message_pieces=[MessagePiece(role="user", original_value="hello", conversation_id="async")])
        )
        messages = await sqlite_instance.get_conversation_messages_async(conversation_id="async")
    assert messages[0].message_pieces[0].original_value == "hello"


async def test_async_memory_shares_legacy_database(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_conversation_to_memory_async(conversation=Conversation(conversation_id="shared"))
    with pytest.warns(DeprecationWarning, match="add_message_to_memory"):
        sqlite_instance.add_message_to_memory(
            request=Message(
                message_pieces=[MessagePiece(role="user", original_value="legacy", conversation_id="shared")]
            )
        )
    messages = await sqlite_instance.get_conversation_messages_async(conversation_id="shared")
    assert messages[0].message_pieces[0].original_value == "legacy"
    with pytest.warns(DeprecationWarning, match="get_conversation_messages"):
        assert sqlite_instance.get_conversation_messages(conversation_id="shared") == messages


async def test_async_seed_operations(sqlite_instance: SQLiteMemory) -> None:
    seed = SeedPrompt(value="hello", dataset_name="async", added_by="unit-test")
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed])
    assert len(await sqlite_instance.get_seeds_async(dataset_name="async")) == 1
    replacement = SeedPrompt(value="replacement", dataset_name="async", added_by="unit-test")
    assert await sqlite_instance.replace_seeds_for_dataset_async(dataset_name="async", seeds=[replacement]) == 1
    assert (await sqlite_instance.get_seeds_async(dataset_name="async"))[0].value == "replacement"


async def test_async_database_wait_does_not_block_loop(sqlite_instance: SQLiteMemory) -> None:
    started = threading.Event()
    release = threading.Event()

    def wait_in_database() -> int:
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("Database wait was not released")
        return 1

    async with await sqlite_instance.get_session_async() as session:
        connection = await session.connection()
        await connection.run_sync(
            lambda sync_connection: sync_connection.connection.run_async(
                lambda driver: driver.create_function("wait_in_database", 0, wait_in_database)
            )
        )
        task = asyncio.create_task(session.execute(text("SELECT wait_in_database()")))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert not task.done()
            await asyncio.sleep(0)
        finally:
            release.set()
        assert (await task).scalar_one() == 1


async def test_azure_auth_wait_is_async_and_credential_is_closed() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    token = AccessToken("unit-test", 100)

    async def get_token_async(scope: str) -> AccessToken:
        assert scope == "scope"
        started.set()
        await release.wait()
        return token

    credential = MagicMock(get_token=AsyncMock(side_effect=get_token_async), close=AsyncMock())
    with patch("pyrit.auth.azure_auth.AsyncDefaultAzureCredential", return_value=credential):
        auth = AsyncAzureAuth("scope")
        task = asyncio.create_task(auth.get_access_token_async())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            assert await task is token
            await auth.close_async()
    credential.close.assert_awaited_once()


@pytest.mark.parametrize("failing_resource", ["engine", "credential"])
async def test_azure_disposal_failure_retains_loop_ownership(failing_resource: str) -> None:
    loop = asyncio.get_running_loop()
    memory = AzureSQLMemory.__new__(AzureSQLMemory)
    engine = MagicMock(dispose=AsyncMock())
    auth = MagicMock(spec=AsyncAzureAuth)
    memory._async_engines = {loop: engine}
    memory._async_auth = {loop: auth}
    close = engine.dispose if failing_resource == "engine" else auth.close_async
    close.side_effect = [RuntimeError("close failed"), None]

    with pytest.raises(RuntimeError, match="close failed"):
        await memory.dispose_loop_resources_async()
    assert memory._async_engines[loop] is engine

    await memory.dispose_loop_resources_async()
    assert memory._async_engines == {}
    assert memory._async_auth == {}


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_legacy_call_finishes_before_returning(cancellations: int) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def legacy_operation() -> None:
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("Legacy operation was not released")
        finished.set()

    task = asyncio.create_task(run_legacy_sync_async(legacy_operation))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()


async def test_async_sessions_are_task_local(sqlite_instance: SQLiteMemory) -> None:
    sessions = []

    def read() -> int:
        session = sqlite_instance._get_session()
        sessions.append(session)
        result = session.execute(text("SELECT 1")).scalar_one()
        assert isinstance(result, int)
        return result

    results = await asyncio.gather(*(sqlite_instance._run_database_operation_async(read) for _ in range(5)))
    assert results == [1] * 5
    assert len({id(session) for session in sessions}) == 5
    assert sqlite_instance._operation_session.get() is None


async def test_cancelled_transaction_rolls_back_and_pool_remains_usable(sqlite_instance: SQLiteMemory) -> None:
    async with await sqlite_instance.get_session_async() as session:
        await session.execute(text("CREATE TABLE CancellationProbe (value INTEGER)"))
        await session.commit()
    inserted = asyncio.Event()

    async def write_async() -> None:
        async with await sqlite_instance.get_session_async() as session:
            await session.execute(text("INSERT INTO CancellationProbe VALUES (1)"))
            inserted.set()
            await asyncio.Event().wait()
            await session.commit()

    task = asyncio.create_task(write_async())
    await asyncio.wait_for(inserted.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with await sqlite_instance.get_session_async() as session:
        assert await session.scalar(text("SELECT COUNT(*) FROM CancellationProbe")) == 0
        await session.execute(text("INSERT INTO CancellationProbe VALUES (2)"))
        await session.commit()


async def test_temporary_loops_release_their_own_resources(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_conversation_to_memory_async(conversation=Conversation(conversation_id="loop-test"))
    owner = asyncio.get_running_loop()
    owner_engine = sqlite_instance._async_engines[owner]

    async def read_on_temporary_loop_async() -> None:
        try:
            assert await sqlite_instance.get_conversation_metadata_async(conversation_id="loop-test") is not None
            assert sqlite_instance._async_engines[asyncio.get_running_loop()] is not owner_engine
        finally:
            await sqlite_instance.dispose_loop_resources_async()

    for _ in range(2):
        await asyncio.to_thread(lambda: asyncio.run(read_on_temporary_loop_async()))
        assert list(sqlite_instance._async_engines) == [owner]


async def test_legacy_subclass_override_runs_off_loop() -> None:
    called_from = []

    class LegacyMemory(SQLiteMemory):
        def get_unique_attack_class_names(self) -> list[str]:
            called_from.append(threading.get_ident())
            return ["custom"]

    memory = LegacyMemory.__new__(LegacyMemory)
    with pytest.warns(DeprecationWarning, match="override"):
        assert await memory.get_unique_attack_class_names_async() == ["custom"]
    assert len(called_from) == 1
    assert called_from[0] != threading.get_ident()


async def test_legacy_session_override_is_not_bypassed(sqlite_instance: SQLiteMemory) -> None:
    threads = []

    class LegacyMemory(SQLiteMemory):
        def get_session(self) -> Session:
            threads.append(threading.get_ident())
            return self._get_sync_session()

    memory = LegacyMemory.__new__(LegacyMemory)
    memory.__dict__.update(sqlite_instance.__dict__)
    with pytest.warns(DeprecationWarning, match="synchronous backend"):
        assert await memory.get_conversation_messages_async(conversation_id="empty") == []
    assert threads and all(thread != threading.get_ident() for thread in threads)
    with pytest.raises(NotImplementedError, match="get_session_async"):
        await memory.get_session_async()


async def test_async_override_can_call_super_without_using_legacy_override(sqlite_instance: SQLiteMemory) -> None:
    class MigratedMemory(SQLiteMemory):
        def get_unique_attack_class_names(self) -> list[str]:
            raise AssertionError("Legacy override must not be called")

        async def get_unique_attack_class_names_async(self) -> list[str]:
            return await super().get_unique_attack_class_names_async()

    memory = MigratedMemory.__new__(MigratedMemory)
    memory.__dict__.update(sqlite_instance.__dict__)
    assert await memory.get_unique_attack_class_names_async() == []


async def test_azure_async_creator_preserves_options_and_refreshes_token() -> None:
    memory = AzureSQLMemory.__new__(AzureSQLMemory)
    memory._connection_string = (
        "mssql+pyodbc://@example.invalid/test?"
        "driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no"
    )
    memory._verbose = False
    memory._async_auth = {}
    memory._async_engines = {}
    credential = MagicMock()
    credential.get_access_token_async = AsyncMock(side_effect=[AccessToken("first", 10), AccessToken("second", 20)])
    credential.close_async = AsyncMock()
    with (
        patch("pyrit.memory.azure_sql_memory.AsyncAzureAuth", return_value=credential),
        patch("pyrit.memory.azure_sql_memory.create_async_engine", wraps=create_async_engine) as factory,
        patch("aioodbc.connect", new_callable=AsyncMock) as connect,
    ):
        engine = memory._get_async_engine()
        try:
            creator = factory.call_args.kwargs["async_creator"]
            await creator()
            await creator()
            options = connect.await_args_list[0].kwargs
            assert "Encrypt=yes" in options["dsn"]
            assert "TrustServerCertificate=no" in options["dsn"]
            assert "Trusted_Connection" not in options["dsn"]
            assert options["attrs_before"][1256][4:] == "first".encode("utf-16-le")
            assert connect.await_args_list[1].kwargs["attrs_before"][1256][4:] == "second".encode("utf-16-le")
            assert engine.url.drivername == "mssql+aioodbc"
            assert factory.call_args.kwargs["pool_pre_ping"] is True
            assert factory.call_args.kwargs["pool_recycle"] == 1800
        finally:
            await memory.dispose_loop_resources_async()
    credential.close_async.assert_awaited_once()
    assert memory._async_auth == {}
    assert memory._async_engines == {}
