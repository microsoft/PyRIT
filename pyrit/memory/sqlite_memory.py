# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import uuid
from collections.abc import MutableSequence, Sequence
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional, TypeVar, Union, cast

from sqlalchemy import and_, create_engine, func, or_, text
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, joinedload, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.expression import TextClause

from pyrit.common.path import DB_DATA_PATH
from pyrit.common.singleton import Singleton
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import (
    AttackResultEntry,
    Base,
    EmbeddingDataEntry,
    PromptMemoryEntry,
    ScenarioResultEntry,
)
from pyrit.models import ConversationStats, DiskStorageIO, MessagePiece

logger = logging.getLogger(__name__)

Model = TypeVar("Model")


class _ExportableConversationPiece:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class SQLiteMemory(MemoryInterface, metaclass=Singleton):
    """
    A memory interface that uses SQLite as the backend database.

    This class provides functionality to insert, query, and manage conversation data
    using SQLite. It supports both file-based and in-memory databases.

    Note: this is replacing the old DuckDB implementation.
    """

    DEFAULT_DB_FILE_NAME = "pyrit.db"

    def __init__(
        self,
        *,
        db_path: Optional[Union[Path, str]] = None,
        verbose: bool = False,
        skip_schema_migration: bool = False,
    ):
        """
        Initialize the SQLiteMemory instance.

        Args:
            db_path (Optional[Union[Path, str]]): Path to the SQLite database file.
                Defaults to "pyrit.db".
            verbose (bool): Whether to enable verbose logging.
                Defaults to False.
            skip_schema_migration (bool): Whether to skip schema migration.
                Defaults to False.
        """
        super().__init__()

        if db_path == ":memory:":
            self.db_path: Union[Path, str] = ":memory:"
        else:
            self.db_path = Path(db_path or Path(DB_DATA_PATH, self.DEFAULT_DB_FILE_NAME)).resolve()
        self.results_path = str(DB_DATA_PATH)

        self.engine = self._create_engine(has_echo=verbose)
        self.SessionFactory = sessionmaker(bind=self.engine)
        if not skip_schema_migration:
            self._run_schema_migration()

    def _init_storage_io(self) -> None:
        # Handles disk-based storage for SQLite local memory.
        self.results_storage_io = DiskStorageIO()

    def _create_engine(self, *, has_echo: bool) -> Engine:
        """
        Create the SQLAlchemy engine for SQLite.

        Creates an engine bound to the specified database file. The `has_echo` parameter
        controls the verbosity of SQL execution logging.

        For in-memory databases (``db_path=":memory:"``), a ``StaticPool`` is used so
        that a single shared connection backs all threads.  SQLAlchemy's default pool
        for ``:memory:`` is ``SingletonThreadPool``, which gives each thread its own
        connection — and therefore its own *separate* in-memory database.  That causes
        tables created on one thread (e.g. a background initialisation thread) to be
        invisible from another thread (e.g. the main thread), resulting in
        "no such table" errors.

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

            engine = create_engine(f"sqlite:///{self.db_path}", echo=has_echo, **extra_kwargs)
            logger.info(f"Engine created successfully for database: {self.db_path}")
            return engine
        except SQLAlchemyError as e:
            logger.exception(f"Error creating the engine for the database: {e}")
            raise

    def get_all_embeddings(self) -> Sequence[EmbeddingDataEntry]:
        """
        Fetch all entries from the specified table and returns them as model instances.

        Returns:
            Sequence[EmbeddingDataEntry]: A sequence of EmbeddingDataEntry instances representing all stored embeddings.
        """
        result: Sequence[EmbeddingDataEntry] = self._query_entries(EmbeddingDataEntry)
        return result

    def _get_message_pieces_memory_label_conditions(self, *, memory_labels: dict[str, str]) -> list[TextClause]:
        """
        Generate SQLAlchemy filter conditions for filtering conversation pieces by memory labels.
        For SQLite, we use JSON_EXTRACT function to handle JSON fields.

        Returns:
            list: A list of SQLAlchemy conditions.
        """
        # For SQLite, we use JSON_EXTRACT with text() and bindparams similar to Azure SQL approach
        json_conditions = " AND ".join([f"JSON_EXTRACT(labels, '$.{key}') = :{key}" for key in memory_labels])

        # Create SQL condition using SQLAlchemy's text() with bindparams
        # for safe parameter passing, preventing SQL injection
        condition = text(json_conditions).bindparams(**{key: str(value) for key, value in memory_labels.items()})
        return [condition]

    def _get_message_pieces_prompt_metadata_conditions(
        self, *, prompt_metadata: dict[str, Union[str, int]]
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
        condition = text(json_conditions).bindparams(**{key: str(value) for key, value in prompt_metadata.items()})
        return [condition]

    def _get_seed_metadata_conditions(self, *, metadata: dict[str, Union[str, int]]) -> Any:
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
            partial_match (bool): Whether to perform a substring match.
            case_sensitive (bool): Whether the match should be case-sensitive. Defaults to False.

        Returns:
            Any: A SQLAlchemy condition for the backend-specific JSON query.
        """
        raw = func.json_extract(json_column, property_path)
        if case_sensitive:
            extracted_value, target = raw, value
        else:
            extracted_value, target = func.lower(raw), value.lower()

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
            array_element_path (Optional[str]): An optional JSON path applied to each array item before matching.
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
        value_expression = f"LOWER(json_extract(value, :{sp_param}))" if array_element_path else "LOWER(value)"

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

    def add_message_pieces_to_memory(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Insert a list of message pieces into the memory storage.
        """
        self._insert_entries(entries=[PromptMemoryEntry(entry=piece) for piece in message_pieces])

    def _add_embeddings_to_memory(self, *, embedding_data: Sequence[EmbeddingDataEntry]) -> None:
        """
        Insert embedding data into memory storage.
        """
        self._insert_entries(entries=embedding_data)

    def get_all_table_models(self) -> list[type[Base]]:
        """
        Return a list of all table models used in the database by inspecting the Base registry.

        Returns:
            list[Base]: A list of SQLAlchemy model classes.
        """
        # The '__subclasses__()' method returns a list of all subclasses of Base, which includes table models
        return Base.__subclasses__()

    def _query_entries(
        self,
        model_class: type[Model],
        *,
        conditions: Optional[Any] = None,
        distinct: bool = False,
        join_scores: bool = False,
    ) -> MutableSequence[Model]:
        """
        Fetch data from the specified table model with optional conditions.

        Args:
            model_class: The SQLAlchemy model class corresponding to the table you want to query.
            conditions: SQLAlchemy filter conditions (Optional).
            distinct: Flag to return distinct rows (default is False).
            join_scores: Flag to join the scores table (default is False).

        Returns:
            List of model instances representing the rows fetched from the table.

        Raises:
            SQLAlchemyError: If there's an issue fetching data from the table.
        """
        with closing(self.get_session()) as session:
            try:
                query = session.query(model_class)
                if join_scores and model_class == PromptMemoryEntry:
                    query = query.options(joinedload(PromptMemoryEntry.scores))
                elif model_class == AttackResultEntry:
                    query = query.options(
                        joinedload(AttackResultEntry.last_response).joinedload(PromptMemoryEntry.scores),
                        joinedload(AttackResultEntry.last_score),
                    )
                if conditions is not None:
                    query = query.filter(conditions)
                if distinct:
                    return query.distinct().all()
                return query.all()
            except SQLAlchemyError as e:
                logger.exception(f"Error fetching data from table {model_class.__tablename__}: {e}")  # type: ignore[attr-defined]
                raise

    def _insert_entry(self, entry: Base) -> None:
        """
        Insert an entry into the Table.

        Args:
            entry: An instance of a SQLAlchemy model to be inserted into the database.

        Raises:
            SQLAlchemyError: If there's an issue inserting the entry into the table.
        """
        with closing(self.get_session()) as session:
            try:
                session.add(entry)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting entry into the table: {e}")
                raise

    def _insert_entries(self, *, entries: Sequence[Base]) -> None:
        """
        Insert multiple entries into the database.

        Raises:
            SQLAlchemyError: If there's an issue inserting the entries into the table.
        """
        with closing(self.get_session()) as session:
            try:
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting multiple entries into the table: {e}")
                raise

    def _update_entries(self, *, entries: MutableSequence[Base], update_fields: dict[str, Any]) -> bool:
        """
        Update the given entries with the specified field values.

        Args:
            entries (Sequence[Base]): A list of SQLAlchemy model instances to be updated.
            update_fields (dict): A dictionary of field names and their new values.

        Returns:
            bool: True if the update was successful, False otherwise.

        Raises:
            ValueError: If update_fields is empty.
            SQLAlchemyError: If there's an issue updating the entries.
        """
        if not update_fields:
            raise ValueError("update_fields must be provided to update prompt entries.")
        with closing(self.get_session()) as session:
            try:
                for entry in entries:
                    # Load a fresh copy by primary key so we only touch the
                    # requested fields.  Using merge() would copy ALL
                    # attributes from the (potentially stale) detached object
                    # and silently overwrite concurrent updates to columns
                    # that are NOT in update_fields.
                    entry_in_session = session.get(type(entry), entry.id)  # type: ignore[attr-defined]
                    if entry_in_session is None:
                        entry_in_session = session.merge(entry)
                    for field, value in update_fields.items():
                        if field in vars(entry_in_session):
                            setattr(entry_in_session, field, value)
                        else:
                            session.rollback()
                            raise ValueError(
                                f"Field '{field}' does not exist in the table '{entry_in_session.__tablename__}'. "
                                f"Rolling back changes..."
                            )
                session.commit()
                return True
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error updating entries: {e}")
                raise

    def get_session(self) -> Session:
        """
        Provide a SQLAlchemy session for transactional operations.

        Returns:
            Session: A SQLAlchemy session bound to the engine.
        """
        return self.SessionFactory()

    def dispose_engine(self) -> None:
        """
        Dispose the engine and close all connections.
        """
        if self.engine:
            self.engine.dispose()
            # During interpreter shutdown, logging handler streams may already be closed,
            # causing the framework to print "Logging error" to stderr (GH-1520).
            # Temporarily suppress logging errors for this teardown message.
            previous_raise = logging.raiseExceptions
            logging.raiseExceptions = False
            try:
                logger.info("Engine disposed and all connections closed.")
            finally:
                logging.raiseExceptions = previous_raise

    def export_conversations(
        self,
        *,
        attack_id: Optional[str | uuid.UUID] = None,
        conversation_id: Optional[str | uuid.UUID] = None,
        prompt_ids: Optional[Sequence[str] | Sequence[uuid.UUID]] = None,
        labels: Optional[dict[str, str]] = None,
        sent_after: Optional[datetime] = None,
        sent_before: Optional[datetime] = None,
        original_values: Optional[Sequence[str]] = None,
        converted_values: Optional[Sequence[str]] = None,
        data_type: Optional[str] = None,
        not_data_type: Optional[str] = None,
        converted_value_sha256: Optional[Sequence[str]] = None,
        file_path: Optional[Path] = None,
        export_type: str = "json",
    ) -> Path:
        """
        Export conversations and their associated scores from the database to a specified file.

        Returns:
            Path: The path to the exported file.

        Raises:
            ValueError: If the specified export format is not supported.
        """
        # Import here to avoid circular import issues
        from pyrit.memory.memory_exporter import MemoryExporter

        if not self.exporter:
            self.exporter = MemoryExporter()

        # Get message pieces using the parent class method with appropriate filters
        message_pieces = self.get_message_pieces(
            attack_id=attack_id,
            conversation_id=conversation_id,
            prompt_ids=prompt_ids,
            labels=labels,
            sent_after=sent_after,
            sent_before=sent_before,
            original_values=original_values,
            converted_values=converted_values,
            data_type=data_type,
            not_data_type=not_data_type,
            converted_value_sha256=converted_value_sha256,
        )

        # Create the filename if not provided
        if not file_path:
            if attack_id:
                file_name = f"{attack_id}.{export_type}"
            elif conversation_id:
                file_name = f"{conversation_id}.{export_type}"
            else:
                file_name = f"all_conversations.{export_type}"
            file_path = Path(DB_DATA_PATH, file_name)

        # Get scores for the message pieces
        if message_pieces:
            message_piece_ids = [str(piece.id) for piece in message_pieces]
            scores = self.get_prompt_scores(prompt_ids=message_piece_ids)
        else:
            scores = []

        # Merge conversations and scores - create the data structure manually
        merged_data = []
        for piece in message_pieces:
            piece_data = piece.to_dict()
            # Find associated scores
            piece_scores = [score for score in scores if score.message_piece_id == piece.id]
            piece_data["scores"] = [score.to_dict() for score in piece_scores]
            merged_data.append(piece_data)

        if not merged_data:
            if export_type == "json":
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(merged_data, f, indent=4)
            elif export_type in self.exporter.export_strategies:
                file_path.write_text("", encoding="utf-8")
            else:
                raise ValueError(f"Unsupported export format: {export_type}")
            return file_path

        exportable_pieces = [_ExportableConversationPiece(data=piece_data) for piece_data in merged_data]
        self.exporter.export_data(
            cast("list[MessagePiece]", exportable_pieces), file_path=file_path, export_type=export_type
        )
        return file_path

    def print_schema(self) -> None:
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

    def export_all_tables(self, *, export_type: str = "json") -> None:
        """
        Export all table data using the specified exporter.

        Iterate over all tables, retrieves their data, and exports each to a file named after the table.

        Args:
            export_type (str): The format to export the data in (defaults to "json").
        """
        table_models = self.get_all_table_models()

        for model in table_models:
            data = self._query_entries(model)
            table_name = model.__tablename__
            file_extension = f".{export_type}"
            file_path = DB_DATA_PATH / f"{table_name}{file_extension}"
            # Convert to list for exporter compatibility
            self.exporter.export_data(list(data), file_path=file_path, export_type=export_type)  # type: ignore[arg-type]

    def _get_attack_result_harm_category_condition(self, *, targeted_harm_categories: Sequence[str]) -> Any:
        """
        SQLite implementation for filtering AttackResults by targeted harm categories.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy subquery for filtering by targeted harm categories.
        """
        from sqlalchemy import and_, exists, func

        from pyrit.memory.memory_models import AttackResultEntry, PromptMemoryEntry

        targeted_harm_categories_subquery = exists().where(
            and_(
                PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                # Exclude empty strings, None, and empty lists
                PromptMemoryEntry.targeted_harm_categories.isnot(None),
                PromptMemoryEntry.targeted_harm_categories != "",
                PromptMemoryEntry.targeted_harm_categories != "[]",
                and_(
                    *[
                        func.json_extract(PromptMemoryEntry.targeted_harm_categories, "$").like(f'%"{category}"%')
                        for category in targeted_harm_categories
                    ]
                ),
            )
        )
        return targeted_harm_categories_subquery  # noqa: RET504

    def _get_attack_result_label_condition(self, *, labels: dict[str, str | Sequence[str]]) -> Any:
        """
        SQLite implementation for filtering AttackResults by labels.
        Uses json_extract() function specific to SQLite.

        Keys are AND-combined. For each key, a string value is an equality match;
        a sequence value is an OR-within-key match (any listed value matches).
        Empty sequences are no-ops (no constraint on that key).

        Returns:
            Any: A SQLAlchemy subquery for filtering by labels.
        """
        from sqlalchemy import and_, exists, func

        from pyrit.memory.memory_models import AttackResultEntry, PromptMemoryEntry

        per_key_conditions = []
        for key, raw_value in labels.items():
            values = [raw_value] if isinstance(raw_value, str) else list(raw_value)
            if not values:
                continue
            col = func.json_extract(PromptMemoryEntry.labels, f"$.{key}")
            per_key_conditions.append(col.in_(values))

        return exists().where(
            and_(
                PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                PromptMemoryEntry.labels.isnot(None),
                and_(*per_key_conditions),
            )
        )

    def get_unique_attack_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique class_name values from
        the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique attack class name strings.
        """
        with closing(self.get_session()) as session:
            class_name_expr = func.json_extract(
                AttackResultEntry.atomic_attack_identifier,
                "$.children.attack_technique.children.attack.class_name",
            )
            rows = session.query(class_name_expr).filter(class_name_expr.isnot(None)).distinct().all()
        return sorted(row[0] for row in rows)

    def get_unique_converter_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique converter class_name values
        from the children.attack_technique.children.attack.children.request_converters
        array in the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique converter class name strings.
        """
        with closing(self.get_session()) as session:
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

    def get_conversation_stats(self, *, conversation_ids: Sequence[str]) -> dict[str, ConversationStats]:
        """
        SQLite implementation: lightweight aggregate stats per conversation.

        Executes a single SQL query that returns message count (distinct
        sequences), a truncated last-message preview, the first non-empty
        labels dict, and the earliest timestamp for each conversation_id.

        Args:
            conversation_ids: The conversation IDs to query.

        Returns:
            Mapping from conversation_id to ConversationStats.
        """
        if not conversation_ids:
            return {}

        placeholders = ", ".join(f":cid{i}" for i in range(len(conversation_ids)))
        params = {f"cid{i}": cid for i, cid in enumerate(conversation_ids)}

        max_len = ConversationStats.PREVIEW_MAX_LEN
        sql = text(
            f"""
            SELECT
                pme.conversation_id,
                COUNT(DISTINCT pme.sequence) AS msg_count,
                (
                    SELECT SUBSTR(p2.converted_value, 1, {max_len + 3})
                    FROM "PromptMemoryEntries" p2
                    WHERE p2.conversation_id = pme.conversation_id
                    ORDER BY p2.sequence DESC, p2.id DESC
                    LIMIT 1
                ) AS last_preview,
                (
                    SELECT p3.labels
                    FROM "PromptMemoryEntries" p3
                    WHERE p3.conversation_id = pme.conversation_id
                      AND p3.labels IS NOT NULL
                      AND p3.labels != '{{}}'
                      AND p3.labels != 'null'
                    ORDER BY p3.sequence ASC, p3.id ASC
                    LIMIT 1
                ) AS first_labels,
                MIN(pme.timestamp) AS created_at
            FROM "PromptMemoryEntries" pme
            WHERE pme.conversation_id IN ({placeholders})
            GROUP BY pme.conversation_id
            """
        )

        with closing(self.get_session()) as session:
            rows = session.execute(sql, params).fetchall()

        result: dict[str, ConversationStats] = {}
        for row in rows:
            conv_id, msg_count, last_preview, raw_labels, raw_created_at = row

            preview = None
            if last_preview:
                preview = last_preview[:max_len] + "..." if len(last_preview) > max_len else last_preview

            labels: dict[str, str] = {}
            if raw_labels and raw_labels not in ("null", "{}"):
                with suppress(ValueError, TypeError):
                    labels = json.loads(raw_labels)

            created_at = None
            if raw_created_at is not None:
                if isinstance(raw_created_at, str):
                    created_at = datetime.fromisoformat(raw_created_at)
                else:
                    created_at = raw_created_at

            result[conv_id] = ConversationStats(
                message_count=msg_count,
                last_message_preview=preview,
                labels=labels,
                created_at=created_at,
            )

        return result

    def _get_scenario_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        SQLite implementation for filtering ScenarioResults by labels.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy exists subquery condition.
        """
        return and_(
            *[func.json_extract(ScenarioResultEntry.labels, f"$.{key}") == value for key, value in labels.items()]
        )
