# Memory

PyRIT's memory component allows users to track and manage a history of interactions throughout an attack scenario. This feature enables the storage, retrieval, and sharing of conversation entries, making it easier to maintain context and continuity in ongoing interactions.

To simplify memory interaction, the `pyrit.memory.CentralMemory` class automatically manages a shared memory instance across all components in a session. Memory must be set explicitly.

**Manual Memory Setting**:

At the beginning of each notebook, make sure to call:
```
# Import initialize_pyrit_async
# Import the specific constant for the MemoryDatabaseType, or provide the literal value
from pyrit.setup import initialize_pyrit_async, IN_MEMORY, SQLITE, AZURE_SQL

await initialize_pyrit_async(memory_db_type: MemoryDatabaseType, memory_instance_kwargs: Optional[Any])
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
