# Memory

PyRIT's memory component allows users to track and manage a history of interactions throughout an attack scenario. This feature enables the storage, retrieval, and sharing of conversation entries, making it easier to maintain context and continuity in ongoing interactions.

To simplify memory interaction, the `pyrit.memory.CentralMemory` class automatically manages a shared memory instance across all components in a session. Memory must be set explicitly.

**Manual Memory Setting**:

At the beginning of each notebook, make sure to call:
```python
from pyrit.memory import CentralMemory
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)
memory = CentralMemory.get_memory_instance()
messages = await memory.get_conversation_messages_async(conversation_id="example")
```

The `MemoryDatabaseType` is a `Literal` with 3 options: IN_MEMORY, SQLITE, AZURE_SQL. (Read more below)
   - `initialize_pyrit_async` takes the `MemoryDatabaseType` and an argument list (`memory_instance_kwargs`), to initialize the shared memory instance.

##  Memory Database Type Options

**IN_MEMORY:** _In-Memory SQLite Database_
   - This option can be preferable if the user does not care about storing conversations or scores in memory beyond the current process. It is used as the default in most of the PyRIT notebooks.
   - **Note**: In in-memory mode, no data is persisted to disk, therefore, all data is lost when the process finishes

**SQLITE:** _Persistent SQLite Database_
   - Interactions will be stored in a persistent `SQLiteMemory` instance with a location on-disk. See notebook [here](./1_sqlite_memory.ipynb) for more details.

**AZURE_SQL:** _Azure SQL Database_
   - For examples on setting up `AzureSQLMemory`, please refer to the notebook [here](./7_azure_sql_memory_attacks.ipynb).
   - To configure AzureSQLMemory without an extra argument list, these keys should be in your `.env` file:
     - `AZURE_SQL_DB_CONNECTION_STRING`
     - `AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL`

## Async memory access

Use the `_async` methods for database reads, writes, and lifecycle operations.
Azure SQL uses SQLAlchemy `AsyncEngine` and `AsyncSession` with `aioodbc`.
SQLite uses the same session contract with `aiosqlite`. Both drivers use worker
threads internally so a database wait does not block the caller's event loop.
Async access improves responsiveness during concurrent work. It does not make
an individual query faster or remove the need for indexes, batching, and pagination.

Synchronous methods remain available until PyRIT 1.4.0 and emit deprecation
warnings. They use a separate synchronous engine; they do not run a nested event
loop. These methods can still block the caller. Constructors and pure
configuration methods remain synchronous. Use `initialize_pyrit_async` to await
database setup rather than initialize a database in a request handler.

Use an independent session for each task when direct SQL access is necessary:

```python
async with await memory.get_session_async() as session:
    # Await session.execute(), session.commit(), and other database operations.
    ...
```

Do not share a session across concurrent tasks. An operation owns its transaction
and releases its session when it finishes or fails. Cancellation before a commit
can roll back a write. Cancellation during a commit can have an uncertain
outcome; do not retry a write without checking its persisted state.

The application that owns memory must await `memory.dispose_engine_async()`
after its tasks finish and before its event loop stops. The backend does this
in its lifespan handler. If a temporary loop uses memory, it must first await
`memory.dispose_loop_resources_async()` on that loop. A notebook can keep its
memory open while its kernel is in use. Synchronous exit hooks do not close
async connections.

### Custom implementations

Add async overrides when you extend a migrated memory-dependent helper.
During the deprecation period, an inherited async method detects a custom
synchronous override, warns, and runs that override in a worker thread. If both
methods are overridden, the async override takes precedence. Cancellation waits
for a legacy worker to finish; it cannot stop synchronous code.

Legacy memory subclasses can keep their existing synchronous session and query
hooks. Their inherited async operations use a warned worker-thread fallback.
For native async access, implement `_create_async_engine`, `_initialize_schema`,
`_get_sync_session`, and the backend query hooks. Raw `get_session_async` requires
a native async engine. The built-in Azure SQL and SQLite drivers do not use the
legacy operation fallback.

### Azure SQL integration checks

The gated Azure SQL integration fixture requires
`PYRIT_TEST_AZURE_SQL_CONNECTION_STRING` for an isolated non-production database.
It rejects the configured production connection string. These tests can create
schema objects and write test data. Do not point them at a shared production
database. Keep the normal results-storage configuration available for the fixture.
