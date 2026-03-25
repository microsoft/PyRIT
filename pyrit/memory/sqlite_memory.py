# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import uuid
from collections.abc import MutableSequence, Sequence
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TypeVar, Union

from sqlalchemy import and_, create_engine, func, or_, text
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, sessionmaker
from sqlalchemy.orm.session import Session
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
    ):
        """
        Initialize the SQLiteMemory instance.

        Args:
            db_path (Optional[Union[Path, str]]): Path to the SQLite database file.
                Defaults to "pyrit.db".
            verbose (bool): Whether to enable verbose logging.
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
        self._create_tables_if_not_exist()

    def _init_storage_io(self) -> None:
        # Handles disk-based storage for SQLite local memory.
        self.results_storage_io = DiskStorageIO()

    def _create_engine(self, *, has_echo: bool) -> Engine:
        """
        Create the SQLAlchemy engine for SQLite.

        Creates an engine bound to the specified database file. The `has_echo` parameter
        controls the verbosity of SQL execution logging.

        Args:
            has_echo (bool): Flag to enable detailed SQL execution logging.

        Returns:
            Engine: The SQLAlchemy engine bound to the SQLite database.

        Raises:
            SQLAlchemyError: If there's an issue creating the engine.
        """
        try:
            # Create the SQLAlchemy engine.
            engine = create_engine(f"sqlite:///{self.db_path}", echo=has_echo)
            logger.info(f"Engine created successfully for database: {self.db_path}")
            return engine
        except SQLAlchemyError as e:
            logger.exception(f"Error creating the engine for the database: {e}")
            raise

    def _create_tables_if_not_exist(self) -> None:
        """
        Create all tables defined in the Base metadata, if they don't already exist in the database.

        Raises:
            Exception: If there's an issue creating the tables in the database.
        """
        try:
            # Using the 'checkfirst=True' parameter to avoid attempting to recreate existing tables
            Base.metadata.create_all(self.engine, checkfirst=True)
        except Exception as e:
            logger.exception(f"Error during table creation: {e}")
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

    def _get_message_pieces_attack_conditions(self, *, attack_id: str) -> Any:
        """
        Generate SQLAlchemy filter conditions for filtering by attack ID.

        Returns:
            Any: A SQLAlchemy text condition with bound parameters.
        """
        return text("JSON_EXTRACT(attack_identifier, '$.hash') = :attack_id").bindparams(attack_id=str(attack_id))

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

    def reset_database(self) -> None:
        """
        Drop and recreates all tables in the database.
        """
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)

    def dispose_engine(self) -> None:
        """
        Dispose the engine and close all connections.
        """
        if self.engine:
            self.engine.dispose()
            logger.info("Engine disposed and all connections closed.")

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

        # Export to JSON manually since the exporter expects objects but we have dicts
        with open(file_path, "w") as f:
            json.dump(merged_data, f, indent=4)
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

    def _get_attack_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        SQLite implementation for filtering AttackResults by labels.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy subquery for filtering by labels.
        """
        from sqlalchemy import and_, exists, func

        from pyrit.memory.memory_models import AttackResultEntry, PromptMemoryEntry

        labels_subquery = exists().where(
            and_(
                PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                PromptMemoryEntry.labels.isnot(None),
                and_(
                    *[func.json_extract(PromptMemoryEntry.labels, f"$.{key}") == value for key, value in labels.items()]
                ),
            )
        )
        return labels_subquery  # noqa: RET504

    def _get_attack_result_attack_class_condition(self, *, attack_class: str) -> Any:
        """
        SQLite implementation for filtering AttackResults by attack class.
        Uses json_extract() on the atomic_attack_identifier JSON column.

        Returns:
            Any: A SQLAlchemy condition for filtering by attack class.
        """
        return (
            func.json_extract(AttackResultEntry.atomic_attack_identifier, "$.children.attack.class_name")
            == attack_class
        )

    def _get_attack_result_converter_classes_condition(self, *, converter_classes: Sequence[str]) -> Any:
        """
        SQLite implementation for filtering AttackResults by converter classes.

        Uses json_extract() on the atomic_attack_identifier JSON column.

        When converter_classes is empty, matches attacks with no converters
        (children.attack.children.request_converters is absent or null in the JSON).
        When non-empty, uses json_each() to check all specified classes are present
        (AND logic, case-insensitive).

        Returns:
            Any: A SQLAlchemy condition for filtering by converter classes.
        """
        if len(converter_classes) == 0:
            # Explicitly "no converters": match attacks where the converter list
            # is absent, null, or empty in the stored JSON.
            converter_json = func.json_extract(
                AttackResultEntry.atomic_attack_identifier,
                "$.children.attack.children.request_converters",
            )
            return or_(
                AttackResultEntry.atomic_attack_identifier.is_(None),
                converter_json.is_(None),
                converter_json == "[]",
            )

        conditions = []
        for i, cls in enumerate(converter_classes):
            param_name = f"conv_cls_{i}"
            conditions.append(
                text(
                    f"""EXISTS(SELECT 1 FROM json_each(
                        json_extract("AttackResultEntries".atomic_attack_identifier,
                            '$.children.attack.children.request_converters'))
                        WHERE LOWER(json_extract(value, '$.class_name')) = :{param_name})"""
                ).bindparams(**{param_name: cls.lower()})
            )
        return and_(*conditions)

    def get_unique_attack_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique class_name values from
        the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique attack class name strings.
        """
        with closing(self.get_session()) as session:
            class_name_expr = func.json_extract(
                AttackResultEntry.atomic_attack_identifier, "$.children.attack.class_name"
            )
            rows = session.query(class_name_expr).filter(class_name_expr.isnot(None)).distinct().all()
        return sorted(row[0] for row in rows)

    def get_unique_converter_class_names(self) -> list[str]:
        """
        SQLite implementation: extract unique converter class_name values
        from the children.attack.children.request_converters array in the
        atomic_attack_identifier JSON column.

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
                            '$.children.attack.children.request_converters')
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

    def _get_scenario_result_target_endpoint_condition(self, *, endpoint: str) -> Any:
        """
        SQLite implementation for filtering ScenarioResults by target endpoint.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy subquery for filtering by target endpoint.
        """
        return func.lower(func.json_extract(ScenarioResultEntry.objective_target_identifier, "$.endpoint")).like(
            f"%{endpoint.lower()}%"
        )

    def _get_scenario_result_target_model_condition(self, *, model_name: str) -> Any:
        """
        SQLite implementation for filtering ScenarioResults by target model name.
        Uses json_extract() function specific to SQLite.

        Returns:
            Any: A SQLAlchemy subquery for filtering by target model name.
        """
        return func.lower(func.json_extract(ScenarioResultEntry.objective_target_identifier, "$.model_name")).like(
            f"%{model_name.lower()}%"
        )
