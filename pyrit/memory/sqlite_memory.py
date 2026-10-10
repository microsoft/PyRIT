# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import threading
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection
from sqlite3 import Cursor as SQLiteCursor
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, case, create_engine, event, exists, func, or_, select, text
from sqlalchemy.engine import AdaptedConnection, ExceptionContext
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import InstrumentedAttribute, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.expression import TextClause
from sqlalchemy.util.concurrency import greenlet_spawn

from pyrit.common.path import DB_DATA_PATH
from pyrit.common.singleton import Singleton
from pyrit.memory.analytics_sql import UnicodeLower
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import (
    AttackResultEntry,
    Base,
    PromptMemoryEntry,
    ScenarioResultEntry,
)
from pyrit.memory.memory_session import MemorySession
from pyrit.memory.storage import DiskStorageIO
from pyrit.models import ConversationStats

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)
_sqlite_session_cleanup: ContextVar[bool] = ContextVar("sqlite_session_cleanup", default=False)


class _CursorClosingSQLiteConnection(SQLiteConnection):
    """A native SQLite connection that finalizes live cursors before disconnecting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cursors: weakref.WeakSet[SQLiteCursor] = weakref.WeakSet()

    def cursor(self, *args: Any, **kwargs: Any) -> SQLiteCursor:
        return self._track_cursor(super().cursor(*args, **kwargs))

    def execute(self, *args: Any, **kwargs: Any) -> SQLiteCursor:
        return self._track_cursor(super().execute(*args, **kwargs))

    def executemany(self, *args: Any, **kwargs: Any) -> SQLiteCursor:
        return self._track_cursor(super().executemany(*args, **kwargs))

    def executescript(self, *args: Any, **kwargs: Any) -> SQLiteCursor:
        return self._track_cursor(super().executescript(*args, **kwargs))

    def close(self) -> None:
        for cursor in tuple(self._cursors):
            cursor.close()
        self._cursors.clear()
        super().close()

    def _track_cursor(self, cursor: SQLiteCursor) -> SQLiteCursor:
        self._cursors.add(cursor)
        return cursor


async def _finish_sqlite_cleanup_async(cleanup: Awaitable[None]) -> asyncio.CancelledError | None:
    """
    Drain SQLite cleanup and retain cancellation received while waiting.

    Returns:
        asyncio.CancelledError | None: The first cancellation received during cleanup.
    """
    task = asyncio.ensure_future(cleanup)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except Exception:
            break
    try:
        task.result()
    except Exception as error:
        if cancellation is not None:
            raise cancellation from error
        raise
    return cancellation


def _cleanup_interrupted_sqlite_connection(context: ExceptionContext) -> None:
    execution_context, connection = context.execution_context, context.connection
    cancelled = isinstance(context.original_exception, asyncio.CancelledError)
    if connection is None or not (cancelled or _sqlite_session_cleanup.get()):
        return
    # The public interface has empty slots, but implementations expose these writable flags.
    context.is_disconnect = True  # type: ignore[ty:missing-slot]
    context.invalidate_pool_on_disconnect = False  # type: ignore[ty:missing-slot]
    dbapi_connection = connection.connection.dbapi_connection
    if not isinstance(dbapi_connection, AdaptedConnection):
        raise TypeError("Async SQLite memory requires an adapted driver connection.")
    cursor = execution_context.cursor if execution_context is not None else None

    def close_and_invalidate() -> None:
        # SQLAlchemy skips cursor cleanup on cancellation. SQLite keeps an active
        # statement's transaction lock even after its connection is closed.
        if cursor is not None:
            cursor.close()
        connection.invalidate(context.original_exception)

    try:
        dbapi_connection.run_async(lambda _: _finish_sqlite_cleanup_async(greenlet_spawn(close_and_invalidate)))
    except (asyncio.CancelledError, Exception) as error:
        if cancelled:
            cause = (
                error.__cause__ if isinstance(error, asyncio.CancelledError) and error.__cause__ is not None else error
            )
            raise context.original_exception from cause
        raise


class _SQLiteAsyncSession(AsyncSession):
    def __init__(self, *, engine: AsyncEngine, release: Callable[[], None] | None = None) -> None:
        super().__init__(bind=engine, sync_session_class=MemorySession)
        self._release: Callable[[], None] | None = release

    async def close(self) -> None:  # pyrit-async-suffix-exempt
        try:
            cancellation = await _finish_sqlite_cleanup_async(self._close_session_async())
        finally:
            if self._release is not None:
                release, self._release = self._release, None
                release()
        if cancellation is not None:
            raise cancellation

    async def __aexit__(
        self,
        type_: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.close()
        except asyncio.CancelledError as error:
            if isinstance(value, asyncio.CancelledError):
                cause = error.__cause__ if error.__cause__ is not None else value.__cause__
                raise value from cause
            raise
        except Exception as error:
            if isinstance(value, asyncio.CancelledError):
                raise value from error
            raise

    async def _close_session_async(self) -> None:
        # A failed rollback must discard its connection before the ORM drops
        # the transaction reference and before exclusive access is released.
        token = _sqlite_session_cleanup.set(True)
        try:
            await super().close()
        finally:
            _sqlite_session_cleanup.reset(token)


class SQLiteMemory(MemoryInterface, metaclass=Singleton):
    """
    A memory interface that uses SQLite as the backend database.

    This class provides functionality to insert, query, and manage conversation data
    using SQLite. It supports both file-based and in-memory databases.

    Cancellation finalizes active cursors and closes interrupted connections before
    returning to the caller. Session cleanup also finishes under repeated cancellation.
    Failed disconnects and session rollbacks preserve the original cancellation
    and expose cleanup failures as its cause.

    Note: this is replacing the old DuckDB implementation.
    """

    DEFAULT_DB_FILE_NAME = "pyrit.db"

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        verbose: bool = False,
        skip_schema_migration: bool = False,
        silent: bool = False,
        _defer_initialization: bool = False,
    ) -> None:
        """
        Initialize the SQLiteMemory instance.

        Args:
            db_path (Path | str | None): Path to the SQLite database file.
                Defaults to "pyrit.db".
            verbose (bool): Whether to enable verbose logging.
                Defaults to False.
            skip_schema_migration (bool): Whether to skip schema migration.
                Defaults to False.
            silent (bool): If True, suppresses schema migration console output.
                Defaults to False.
        """
        super().__init__()

        if db_path == ":memory:":
            self.db_path: Path | str = ":memory:"
        else:
            self.db_path = Path(db_path or Path(DB_DATA_PATH, self.DEFAULT_DB_FILE_NAME)).resolve()
        self.results_path = str(DB_DATA_PATH)
        self._memory_uri = f"file:pyrit-{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
        self._skip_schema_migration = skip_schema_migration
        self._silent = silent
        self._keepalive: Connection | None = None
        self._verbose = verbose

        # Shared-cache SQLite does not wait on table locks. Serialize whole transactions
        # across sync callers and all event loops, not just within each connection pool.
        self._connection_lock = threading.RLock() if self.db_path == ":memory:" else None
        self._transaction_lock = threading.Lock() if self.db_path == ":memory:" else None
        self._sync_session_depth = 0
        self._sync_session_thread: int | None = None

        self.engine = self._create_engine(has_echo=verbose)
        self.SessionFactory = sessionmaker(bind=self.engine, class_=MemorySession)
        if not _defer_initialization:
            self._initialize_schema()
            self._initialized = True

    def _initialize_schema(self) -> None:
        if self.engine is None:
            raise RuntimeError("Engine is not initialized.")
        if self.db_path == ":memory:" and self._keepalive is None:
            self._keepalive = self.engine.connect()
        if not self._skip_schema_migration:
            self._run_schema_migration(silent=self._silent)

    def _create_async_engine(self) -> AsyncEngine:
        database = self._memory_uri if self.db_path == ":memory:" else str(self.db_path)
        kwargs: dict[str, Any] = {"connect_args": {"factory": _CursorClosingSQLiteConnection}}
        if self.db_path == ":memory:":
            kwargs["poolclass"] = StaticPool
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}", echo=self._verbose, **kwargs)
        self._register_analytics_lower(engine=engine.sync_engine)
        event.listen(engine.sync_engine, "handle_error", _cleanup_interrupted_sqlite_connection)
        return engine

    @staticmethod
    def _unicode_lower(value: str | int | float | bytes | None) -> str | None:
        """
        Lowercase SQLite text with Unicode rules, retaining NULL semantics.

        Returns:
            str | None: The folded value, or SQL NULL.
        """
        return str(value).lower() if value is not None else None

    @staticmethod
    def _register_analytics_lower(*, engine: Engine) -> None:
        """Install the Unicode lowercase function for analytics and JSON filters on every pooled SQLite connection."""

        @event.listens_for(engine, "connect")
        def register(dbapi_connection: Any, connection_record: Any) -> None:
            dbapi_connection.create_function(
                UnicodeLower.SQLITE_FUNCTION_NAME, 1, SQLiteMemory._unicode_lower, deterministic=True
            )

    async def get_session_async(self) -> AsyncSession:
        """
        Create a session with cancellation-safe SQLite cleanup.

        In-memory sessions also have exclusive access to the shared database.

        Returns:
            AsyncSession: A session that finishes cleanup before releasing exclusive access.

        Raises:
            NotImplementedError: If a custom sync session hook has not been migrated.
            RuntimeError: If this thread already holds a synchronous session.
        """
        if self._uses_legacy_session_override():
            raise NotImplementedError("Override get_session_async when customizing the legacy get_session hook.")
        if self._sync_session_thread == threading.get_ident():
            raise RuntimeError("Close the synchronous memory session before opening an async session on this thread.")
        connection_lock = self._transaction_lock
        if connection_lock is None:
            return _SQLiteAsyncSession(engine=self._get_async_engine())
        while not connection_lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        try:
            return _SQLiteAsyncSession(engine=self._get_async_engine(), release=connection_lock.release)
        except BaseException:
            connection_lock.release()
            raise

    def _uses_legacy_session_override(self) -> bool:
        return super()._uses_legacy_session_override() or (
            type(self).get_session is not MemoryInterface.get_session
            and type(self).get_session_async is SQLiteMemory.get_session_async
        )

    def _dispose_sync_engine(self) -> None:
        if self._keepalive is not None:
            self._keepalive.close()
            self._keepalive = None
        super()._dispose_sync_engine()

    def _init_storage_io(self) -> None:
        # Handles disk-based storage for SQLite local memory.
        self.results_storage_io = DiskStorageIO()

    def _create_engine(self, *, has_echo: bool) -> Engine:
        """
        Create the SQLAlchemy engine for SQLite.

        Creates an engine bound to the specified database file. The `has_echo` parameter
        controls the verbosity of SQL execution logging.

        For in-memory databases, the sync pool and each async pool connect to an
        instance-specific named database. A keepalive connection preserves its
        contents while async pools are closed between event loops.

        Args:
            has_echo (bool): Flag to enable detailed SQL execution logging.

        Returns:
            Engine: The SQLAlchemy engine bound to the SQLite database.

        Raises:
            SQLAlchemyError: If there's an issue creating the engine.
        """
        try:
            extra_kwargs: dict[str, Any] = {}

            if self.db_path == ":memory:":
                # Use StaticPool so every checkout returns the same underlying
                # DBAPI connection, keeping all threads on a single in-memory
                # database.  ``check_same_thread=False`` is required because
                # the connection will be shared across threads.
                extra_kwargs["poolclass"] = StaticPool
                extra_kwargs["connect_args"] = {"check_same_thread": False}

            database = self._memory_uri if self.db_path == ":memory:" else str(self.db_path)
            engine = create_engine(f"sqlite:///{database}", echo=has_echo, **extra_kwargs)
            self._register_analytics_lower(engine=engine)
            logger.info(f"Engine created successfully for database: {self.db_path}")
            return engine
        except SQLAlchemyError as e:
            logger.exception(f"Error creating the engine for the database: {e}")
            raise

    def _get_message_pieces_memory_label_conditions(self, *, memory_labels: dict[str, str]) -> list[Any]:
        """
        Generate SQLAlchemy filter conditions for filtering conversation pieces by memory labels.
        For SQLite, we use JSON_EXTRACT function to handle JSON fields.

        Matches if labels are on the PromptMemoryEntry itself OR on any
        AttackResultEntry that shares the same conversation_id.

        Returns:
            list: A list of SQLAlchemy conditions.
        """
        per_key_are_conditions = []
        for key, value in memory_labels.items():
            are_col = func.json_extract(AttackResultEntry.labels, f"$.{key}")
            per_key_are_conditions.append(are_col == str(value))
        return [
            exists().where(
                and_(
                    AttackResultEntry.conversation_id == PromptMemoryEntry.conversation_id,
                    AttackResultEntry.labels.isnot(None),
                    *per_key_are_conditions,
                )
            )
        ]

    def _get_message_pieces_prompt_metadata_conditions(
        self, *, prompt_metadata: dict[str, str | int]
    ) -> list[TextClause]:
        """
        Generate SQLAlchemy filter conditions for filtering conversation pieces by prompt metadata.

        Returns:
            list: A list of SQLAlchemy conditions.
        """
        json_conditions = " AND ".join(
            [f"JSON_EXTRACT(prompt_metadata, '$.{key}') = :{key}" for key in prompt_metadata]
        )

        # Create SQL condition using SQLAlchemy's text() with bindparams
        # Note: We do NOT convert values to string here, to allow integer comparison in JSON
        condition = text(json_conditions).bindparams(**dict(prompt_metadata.items()))
        return [condition]

    def _get_seed_metadata_conditions(self, *, metadata: dict[str, str | int]) -> Any:
        """
        Generate SQLAlchemy filter conditions for filtering seed prompts by metadata.

        Returns:
            Any: A SQLAlchemy text condition with bound parameters.
        """
        json_conditions = " AND ".join([f"JSON_EXTRACT(prompt_metadata, '$.{key}') = :{key}" for key in metadata])

        # Create SQL condition using SQLAlchemy's text() with bindparams
        # Note: We do NOT convert values to string here, to allow integer comparison in JSON
        return text(json_conditions).bindparams(**dict(metadata.items()))

    def _get_condition_json_property_match(
        self,
        *,
        json_column: InstrumentedAttribute[Any],
        property_path: str,
        value: str,
        partial_match: bool = False,
        case_sensitive: bool = False,
    ) -> Any:
        """
        Return a SQLite DB condition for matching a value at a given path within a JSON object.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed model field to query.
            property_path (str): The JSON path for the property to match.
            value (str): The string value that must match the extracted JSON property value.
            partial_match (bool): Whether to perform a substring match. Defaults to False.
            case_sensitive (bool): Whether the match should be case-sensitive. Defaults to False.

        Returns:
            Any: A SQLAlchemy condition for the backend-specific JSON query.
        """
        raw = func.json_extract(json_column, property_path)
        if case_sensitive:
            extracted_value, target = raw, value
        else:
            extracted_value, target = UnicodeLower(raw), value.lower()

        if partial_match:
            escaped = target.replace("%", "\\%").replace("_", "\\_")
            return extracted_value.like(f"%{escaped}%", escape="\\")
        return extracted_value == target

    def _get_condition_json_array_match(
        self,
        *,
        json_column: InstrumentedAttribute[Any],
        property_path: str,
        array_element_path: str | None = None,
        array_to_match: Sequence[str],
        match_mode: Literal["all", "any"] = "all",
    ) -> Any:
        """
        Return a SQLite DB condition for matching an array at a given path within a JSON object.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed SQLAlchemy field to query.
            property_path (str): The JSON path for the target array.
            array_element_path (str | None): An optional JSON path applied to each array item before matching.
            array_to_match (Sequence[str]): The array that must match the extracted JSON array values.
                Combination semantics for multiple entries are controlled by ``match_mode``.
                If ``array_to_match`` is empty, the condition matches only if the target is also an
                empty array or None (overloaded "absence" semantics, regardless of ``match_mode``).
            match_mode (Literal["all", "any"]): How to combine multiple entries in ``array_to_match``.
                ``"all"`` (default) requires every listed value to be present in the JSON array.
                ``"any"`` requires at least one listed value to be present.

        Returns:
            Any: A database-specific SQLAlchemy condition.
        """
        array_expr = func.json_extract(json_column, property_path)
        if len(array_to_match) == 0:
            return or_(
                json_column.is_(None),
                array_expr.is_(None),
                array_expr == "[]",
            )

        uid = self._uid()
        table_name = json_column.class_.__tablename__
        column_name = json_column.key
        pp_param = f"property_path_{uid}"
        sp_param = f"array_element_path_{uid}"
        element = f"json_extract(value, :{sp_param})" if array_element_path else "value"
        # SQLite's LOWER folds ASCII only; match the Unicode folding applied to the values below.
        value_expression = f"{UnicodeLower.SQLITE_FUNCTION_NAME}({element})"

        conditions = []
        bindparams_dict: dict[str, str] = {pp_param: property_path}
        if array_element_path:
            bindparams_dict[sp_param] = array_element_path

        for index, match_value in enumerate(array_to_match):
            mv_param = f"mv_{uid}_{index}"
            conditions.append(
                f"""EXISTS(SELECT 1 FROM json_each(
                        json_extract("{table_name}".{column_name}, :{pp_param}))
                        WHERE {value_expression} = :{mv_param})"""
            )
            bindparams_dict[mv_param] = match_value.lower()

        joiner = " OR " if match_mode == "any" else " AND "
        combined = joiner.join(conditions)
        return text(f"({combined})").bindparams(**bindparams_dict)

    def get_all_table_models(self) -> list[type[Base]]:
        """
        Return a list of all table models used in the database by inspecting the Base registry.

        Returns:
            list[Base]: A list of SQLAlchemy model classes.
        """
        # The '__subclasses__()' method returns a list of all subclasses of Base, which includes table models
        return Base.__subclasses__()

    def _get_sync_session(self) -> Session:
        """
        Provide a SQLAlchemy session for transactional operations.

        For an in-memory database every session borrows the same DBAPI connection, so the
        session is handed out under a lock that is only released when it is closed. That keeps
        a whole transaction, not just a single statement, isolated from the other threads.

        Returns:
            Session: A SQLAlchemy session bound to the engine.

        Raises:
            RuntimeError: If acquiring a session would block an event loop.
        """
        session = self.SessionFactory()
        connection_lock = self._connection_lock
        if connection_lock is None:
            return session

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_event_loop = False
        else:
            on_event_loop = True

        if not connection_lock.acquire(blocking=not on_event_loop):
            raise RuntimeError("A synchronous memory session cannot wait on an event loop. Use the async API.")
        if self._sync_session_depth == 0:
            assert self._transaction_lock is not None
            if not self._transaction_lock.acquire(blocking=not on_event_loop):
                connection_lock.release()
                raise RuntimeError("A synchronous memory session cannot overlap an async session. Use the async API.")
        self._sync_session_depth += 1
        self._sync_session_thread = threading.get_ident()
        close_session = session.close
        released = False
        owner_thread = threading.get_ident()

        def release_once() -> None:
            # Also runs if the session is discarded without being closed, so one caller that
            # forgets cannot leave the lock held and stall every other thread forever.
            nonlocal released
            if released:
                return
            if threading.get_ident() != owner_thread:
                logger.warning("An in-memory session was discarded by a thread that did not open it.")
                return
            released = True
            self._sync_session_depth -= 1
            if self._sync_session_depth == 0:
                self._sync_session_thread = None
                assert self._transaction_lock is not None
                self._transaction_lock.release()
            try:
                connection_lock.release()
            except RuntimeError:
                logger.warning("An in-memory session was discarded by a thread that did not open it.")

        def close_and_release() -> None:
            try:
                close_session()
            finally:
                release_once()

        session.close = close_and_release  # type: ignore[ty:invalid-assignment]
        weakref.finalize(session, release_once)
        return session

    def _print_schema(self) -> None:
        """
        Print the schema of all tables in the SQLite database.
        """
        print("Database Schema:")
        print("================")
        for table_name, table in Base.metadata.tables.items():
            print(f"\nTable: {table_name}")
            print("-" * (len(table_name) + 7))  # +7 to align to be under header ("table: " is 7 chars)
            for column in table.columns:
                nullable = "NULL" if column.nullable else "NOT NULL"
                default = f" DEFAULT {column.default}" if column.default else ""
                print(f"  {column.name}: {column.type} {nullable}{default}")

    def _get_attack_result_label_condition(self, *, labels: dict[str, str | Sequence[str]]) -> Any:
        """
        SQLite implementation for filtering AttackResults by labels.
        Uses json_extract() function specific to SQLite.

        Matches labels directly on the AttackResultEntry.

        Keys are AND-combined. For each key, a string value is an equality match;
        a sequence value is an OR-within-key match (any listed value matches).
        Empty sequences are no-ops (no constraint on that key).

        Returns:
            Any: A SQLAlchemy condition for filtering by labels.
        """
        per_key_are_conditions = []
        for key, raw_value in labels.items():
            values = [raw_value] if isinstance(raw_value, str) else list(raw_value)
            if not values:
                continue
            are_col = func.json_extract(AttackResultEntry.labels, f"$.{key}")
            per_key_are_conditions.append(are_col.in_(values))

        return and_(
            AttackResultEntry.labels.isnot(None),
            *per_key_are_conditions,
        )

    def _execute_get_unique_attack_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique class_name values from
        the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique attack class name strings.
        """
        with closing(self._get_session()) as session:
            class_name_expr = func.json_extract(
                AttackResultEntry.atomic_attack_identifier,
                "$.children.attack_technique.children.attack.class_name",
            )
            rows = session.query(class_name_expr).filter(class_name_expr.isnot(None)).distinct().all()
        return sorted(row[0] for row in rows)

    def _execute_get_unique_converter_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique converter class_name values
        from the children.attack_technique.children.attack.children.request_converters
        array in the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique converter class name strings.
        """
        with closing(self._get_session()) as session:
            rows = session.execute(
                text(
                    """SELECT DISTINCT json_extract(j.value, '$.class_name') AS cls
                    FROM "AttackResultEntries",
                    json_each(
                        json_extract("AttackResultEntries".atomic_attack_identifier,
                            '$.children.attack_technique.children.attack.children.request_converters')
                    ) AS j
                    WHERE cls IS NOT NULL"""
                )
            ).fetchall()
        return sorted(row[0] for row in rows)

    def _execute_get_conversation_stats(self, *, conversation_ids: Sequence[str]) -> dict[str, ConversationStats]:
        """
        SQLite implementation: lightweight aggregate stats per conversation.

        Executes a single SQL query that returns message count (distinct
        sequences), a truncated last-message preview, and the earliest
        timestamp for each conversation_id.

        Args:
            conversation_ids: The conversation IDs to query.

        Returns:
            Mapping from conversation_id to ConversationStats.
        """
        if not conversation_ids:
            return {}

        placeholders = ", ".join(f":cid{i}" for i in range(len(conversation_ids)))
        params = {f"cid{i}": cid for i, cid in enumerate(conversation_ids)}

        sql = text(
            f"""
            WITH aggregate_rows AS (
                SELECT
                    conversation_id,
                    COUNT(DISTINCT sequence) AS msg_count,
                    MIN(timestamp) AS created_at
                FROM "PromptMemoryEntries"
                WHERE conversation_id IN ({placeholders})
                GROUP BY conversation_id
            )
            SELECT
                aggregate_rows.conversation_id,
                aggregate_rows.msg_count,
                SUBSTR(latest.converted_value, 1, {ConversationStats.PREVIEW_FETCH_MAX_LEN}) AS last_preview,
                latest.converted_value_data_type AS last_data_type,
                aggregate_rows.created_at
            FROM aggregate_rows
            LEFT JOIN "PromptMemoryEntries" latest
                ON latest.id = (
                    SELECT p2.id
                    FROM "PromptMemoryEntries" p2
                    WHERE p2.conversation_id = aggregate_rows.conversation_id
                    ORDER BY p2.sequence DESC, p2.id DESC
                    LIMIT 1
                )
            """
        )

        with closing(self._get_session()) as session:
            rows = session.execute(sql, params).fetchall()

        result: dict[str, ConversationStats] = {}
        for row in rows:
            conv_id, msg_count, last_preview, last_data_type, raw_created_at = row

            created_at = None
            if raw_created_at is not None:
                if isinstance(raw_created_at, str):
                    created_at = datetime.fromisoformat(raw_created_at)
                else:
                    created_at = raw_created_at

            result[conv_id] = ConversationStats(
                message_count=msg_count,
                last_message_preview=last_preview,
                last_message_data_type=last_data_type,
                created_at=created_at,
            )

        return result

    def _get_scenario_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        Filter ScenarioResults by legacy single-value labels.

        Returns:
            Any: SQLAlchemy condition for all supplied labels.
        """
        return and_(
            *(func.json_extract(ScenarioResultEntry.labels, f'$."{key}"') == value for key, value in labels.items())
        )

    def _get_scenario_result_labels_condition(self, *, labels: Mapping[str, str | Sequence[str]]) -> Any:
        """
        SQLite implementation for filtering ScenarioResults by multi-value labels.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy exists subquery condition.
        """
        conditions = []
        for key, raw_value in labels.items():
            values = [raw_value] if isinstance(raw_value, str) else list(raw_value)
            if values:
                conditions.append(func.json_extract(ScenarioResultEntry.labels, f'$."{key}"').in_(values))
        return and_(*conditions)

    def _get_scenario_registry_name_condition(self, *, scenario_names: Sequence[str]) -> Any:
        """
        Match requested scenario registry names inside the persisted run plan.

        Returns:
            Any: SQLite JSON condition for the requested names.
        """
        registry_name = func.json_extract(
            ScenarioResultEntry.scenario_metadata,
            "$.run_plan.scenario_registry_name",
        )
        return registry_name.in_(scenario_names)

    def _get_scenario_history_plan_expressions(self) -> tuple[Any, Any, Any]:
        """Return compact SQLite run-plan fields without objective-bearing seed groups."""
        seed_groups = case(
            (
                func.json_type(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.seed_groups",
                )
                == "array",
                func.json_extract(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.seed_groups",
                ),
            ),
            else_="[]",
        )
        seed_rows = func.json_each(
            seed_groups,
        ).table_valued("value", "type")
        seed_json = case((seed_rows.c.type == "object", seed_rows.c.value), else_="{}")
        compact_seed_map = (
            select(
                func.json_group_array(
                    func.json_object(
                        "id",
                        func.json_extract(seed_json, "$.id"),
                        "objective_sha256",
                        func.json_extract(seed_json, "$.objective_sha256"),
                    )
                )
            )
            .select_from(seed_rows)
            .scalar_subquery()
        )
        return (
            func.json_extract(
                ScenarioResultEntry.scenario_metadata,
                "$.run_plan.scenario_registry_name",
            ),
            func.json_extract(
                ScenarioResultEntry.scenario_metadata,
                "$.run_plan.atomic_groups",
            ),
            compact_seed_map,
        )

    def _get_scenario_started_at_expression(self) -> Any:
        """Return the persisted execution start without loading full scenario metadata."""
        return func.json_extract(ScenarioResultEntry.scenario_metadata, "$.started_at")

    def _get_scenario_attempt_unit_expressions(self) -> tuple[Any, Any, Any, Any]:
        """Return SQLite JSON expressions for persisted scenario attempt attribution."""
        atomic_name = func.coalesce(
            func.json_extract(AttackResultEntry.attribution_data, '$."parent_collection"'),
            "",
        )
        technique_hash = func.coalesce(
            func.json_extract(AttackResultEntry.attribution_data, '$."parent_eval_hash"'),
            "",
        )
        attributed_seed_group_id = func.nullif(
            func.json_extract(AttackResultEntry.attribution_data, '$."seed_group_id"'),
            "",
        )
        seeds = func.json_each(
            AttackResultEntry.atomic_attack_identifier,
            "$.children.seed_identifiers",
        ).table_valued("value", joins_implicitly=True)
        identifier_seed_key = (
            select(func.group_concat(func.json_extract(seeds.c.value, "$.hash"), ","))
            .select_from(seeds)
            .scalar_subquery()
        )
        return atomic_name, technique_hash, attributed_seed_group_id, identifier_seed_key

    def _get_scenario_plan_unit_subqueries(self, *, scenario_result_ids: Sequence[uuid.UUID]) -> tuple[Any, Any]:
        """Return SQLite run-plan expansions for planned units and planned seed groups."""
        atomic_groups = case(
            (
                func.json_type(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.atomic_groups",
                )
                == "array",
                func.json_extract(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.atomic_groups",
                ),
            ),
            else_="[]",
        )
        groups = func.json_each(
            atomic_groups,
        ).table_valued("key", "value", "type", joins_implicitly=True)
        group_json = case((groups.c.type == "object", groups.c.value), else_="{}")
        group_seeds = func.json_each(group_json, "$.seed_group_ids").table_valued("value", joins_implicitly=True)
        seed_groups = case(
            (
                func.json_type(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.seed_groups",
                )
                == "array",
                func.json_extract(
                    ScenarioResultEntry.scenario_metadata,
                    "$.run_plan.seed_groups",
                ),
            ),
            else_="[]",
        )
        seeds = func.json_each(
            seed_groups,
        ).table_valued("value", "type", joins_implicitly=True)
        seed_json = case((seeds.c.type == "object", seeds.c.value), else_="{}")
        planned_units = (
            select(
                ScenarioResultEntry.id.label("scenario_result_id"),
                groups.c.key.label("group_ordinal"),
                func.json_extract(group_json, "$.id").label("atomic_group_id"),
                func.json_extract(group_json, "$.atomic_attack_name").label("atomic_attack_name"),
                func.json_extract(group_json, "$.technique_eval_hash").label("technique_eval_hash"),
                group_seeds.c.value.label("seed_group_id"),
            )
            .select_from(ScenarioResultEntry, groups, group_seeds)
            .where(ScenarioResultEntry.id.in_(scenario_result_ids))
            .subquery("plan_units")
        )
        plan_seeds = (
            select(
                ScenarioResultEntry.id.label("scenario_result_id"),
                func.json_extract(seed_json, "$.id").label("seed_group_id"),
                func.json_extract(seed_json, "$.objective_sha256").label("objective_sha256"),
            )
            .select_from(ScenarioResultEntry, seeds)
            .where(ScenarioResultEntry.id.in_(scenario_result_ids))
            .subquery("plan_seeds")
        )
        return planned_units, plan_seeds
