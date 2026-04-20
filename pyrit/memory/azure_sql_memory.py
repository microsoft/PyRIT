# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import struct
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, MutableSequence, Optional, Sequence, TypeVar, Union

from azure.core.credentials import AccessToken
from sqlalchemy import and_, create_engine, event, exists, text
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import TextClause

from pyrit.auth.azure_auth import AzureAuth
from pyrit.common import default_values
from pyrit.common.singleton import Singleton
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import (
    AttackResultEntry,
    Base,
    EmbeddingDataEntry,
    PromptMemoryEntry,
)
from pyrit.models import (
    AzureBlobStorageIO,
    MessagePiece,
)

logger = logging.getLogger(__name__)

Model = TypeVar("Model")


class AzureSQLMemory(MemoryInterface, metaclass=Singleton):
    """
    A class to manage conversation memory using Azure SQL Server as the backend database. It leverages SQLAlchemy Base
    models for creating tables and provides CRUD operations to interact with the tables.

    This class encapsulates the setup of the database connection, table creation based on SQLAlchemy models,
    and session management to perform database operations.
    """

    # Azure SQL configuration
    SQL_COPT_SS_ACCESS_TOKEN = 1256  # Connection option for access tokens, as defined in msodbcsql.h
    TOKEN_URL = "https://database.windows.net/.default"  # The token URL for any Azure SQL database
    AZURE_SQL_DB_CONNECTION_STRING = "AZURE_SQL_DB_CONNECTION_STRING"

    # Azure Storage Account Container datasets and results environment variables
    AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL: str = "AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL"
    AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN: str = "AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN"

    def __init__(
        self,
        *,
        connection_string: Optional[str] = None,
        results_container_url: Optional[str] = None,
        results_sas_token: Optional[str] = None,
        verbose: bool = False,
    ):
        """
        Initialize an Azure SQL Memory backend.

        Args:
            connection_string (Optional[str]): The connection string for the Azure Sql Database. If not provided,
                it falls back to the 'AZURE_SQL_DB_CONNECTION_STRING' environment variable.
            results_container_url (Optional[str]): The URL to an Azure Storage Container. If not provided,
                it falls back to the 'AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL' environment variable.
            results_sas_token (Optional[str]): The Shared Access Signature (SAS) token for the storage container.
                If not provided, falls back to the 'AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN' environment variable.
            verbose (bool): Whether to enable verbose logging for the database engine. Defaults to False.
        """
        self._connection_string = default_values.get_required_value(
            env_var_name=self.AZURE_SQL_DB_CONNECTION_STRING, passed_value=connection_string
        )

        self._results_container_url: str = default_values.get_required_value(
            env_var_name=self.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL, passed_value=results_container_url
        )

        self._results_container_sas_token: Optional[str] = self._resolve_sas_token(
            self.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN, results_sas_token
        )

        self._auth_token: Optional[AccessToken] = None
        self._auth_token_expiry: Optional[int] = None

        self.results_path = self._results_container_url

        self.engine = self._create_engine(has_echo=verbose)

        # Generate the initial auth token
        self._create_auth_token()
        # Enable token-based authorization
        self._enable_azure_authorization()

        self.SessionFactory = sessionmaker(bind=self.engine)
        self._create_tables_if_not_exist()

        super(AzureSQLMemory, self).__init__()

    @staticmethod
    def _resolve_sas_token(env_var_name: str, passed_value: Optional[str] = None) -> Optional[str]:
        """
        Resolve the SAS token value, allowing a fallback to None for delegation SAS.

        Args:
            env_var_name (str): The environment variable name to look up.
            passed_value (Optional[str]): A passed-in value for the SAS token.

        Returns:
            Optional[str]: Resolved SAS token or None if not provided.
        """
        try:
            return default_values.get_required_value(env_var_name=env_var_name, passed_value=passed_value)  # type: ignore[no-any-return]
        except ValueError:
            return None

    def _init_storage_io(self) -> None:
        # Handle for Azure Blob Storage when using Azure SQL memory.
        self.results_storage_io = AzureBlobStorageIO(
            container_url=self._results_container_url, sas_token=self._results_container_sas_token
        )

    def _create_auth_token(self) -> None:
        """
        Create an Azure Entra ID access token.
        Stores the token and its expiry time.
        """
        azure_auth = AzureAuth(token_scope=self.TOKEN_URL)
        self._auth_token = azure_auth.access_token
        self._auth_token_expiry = azure_auth.access_token.expires_on

    def _refresh_token_if_needed(self) -> None:
        """
        Refresh the access token if it is close to expiry (within 5 minutes).
        """
        if datetime.now(timezone.utc) >= datetime.fromtimestamp(self._auth_token_expiry, tz=timezone.utc) - timedelta(
            minutes=5
        ):
            logger.info("Refreshing Microsoft Entra ID access token...")
            self._create_auth_token()

    def _create_engine(self, *, has_echo: bool) -> Engine:
        """
        Create the SQLAlchemy engine for Azure SQL Server.

        Creates an engine bound to the specified server and database. The `has_echo` parameter
        controls the verbosity of SQL execution logging.

        Args:
            has_echo (bool): Flag to enable detailed SQL execution logging.

        Returns:
            Engine: SQLAlchemy engine bound to the AZURE SQL Database.

        Raises:
            SQLAlchemyError: If the engine creation fails.
        """
        try:
            # Create the SQLAlchemy engine.
            # Use pool_pre_ping (health check) to gracefully handle server-closed connections
            # by testing and replacing stale connections.
            # Set pool_recycle to 1800 seconds to prevent connections from being closed due to server timeout.

            engine = create_engine(self._connection_string, pool_recycle=1800, pool_pre_ping=True, echo=has_echo)
            logger.info(f"Engine created successfully for database: {engine.name}")
            return engine
        except SQLAlchemyError as e:
            logger.exception(f"Error creating the engine for the database: {e}")
            raise

    def _enable_azure_authorization(self) -> None:
        """
        Enable Azure token-based authorization for SQL connections.

        The following is necessary because of how SQLAlchemy and PyODBC handle connection creation. In PyODBC, the
        token is passed outside the connection string in the `connect()` method. Since SQLAlchemy lazy-loads
        its connections, we need to set this as a separate argument to the `connect()` method. In SQLALchemy
        we do this by hooking into the `do_connect` event, which is fired when a connection is created.

        For further details, see:
        * <https://docs.sqlalchemy.org/en/20/dialects/mssql.html#connecting-to-databases-with-access-tokens>
        * <https://learn.microsoft.com/en-us/azure/azure-sql/database/azure-sql-python-quickstart
        """

        @event.listens_for(self.engine, "do_connect")
        def provide_token(_dialect: Any, _conn_rec: Any, cargs: list[Any], cparams: dict[str, Any]) -> None:
            # Refresh token if it's close to expiry
            self._refresh_token_if_needed()

            # remove the "Trusted_Connection" parameter that SQLAlchemy adds
            cargs[0] = cargs[0].replace(";Trusted_Connection=Yes", "")

            # encode the token
            azure_token = self._auth_token.token
            azure_token_bytes = azure_token.encode("utf-16-le")
            packed_azure_token = struct.pack(f"<I{len(azure_token_bytes)}s", len(azure_token_bytes), azure_token_bytes)

            # add the encoded token
            cparams["attrs_before"] = {self.SQL_COPT_SS_ACCESS_TOKEN: packed_azure_token}

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

    def _add_embeddings_to_memory(self, *, embedding_data: Sequence[EmbeddingDataEntry]) -> None:
        """
        Insert embedding data into memory storage.
        """
        self._insert_entries(entries=embedding_data)

    def _get_message_pieces_memory_label_conditions(self, *, memory_labels: dict[str, str]) -> list[TextClause]:
        """
        Generate SQL conditions for filtering message pieces by memory labels.

        Uses JSON_VALUE() function specific to SQL Azure to query label fields in JSON format.

        Args:
            memory_labels (dict[str, str]): Dictionary of label key-value pairs to filter by.

        Returns:
            list: List containing a single SQLAlchemy text condition with bound parameters.
        """
        json_validation = "ISJSON(labels) = 1"
        json_conditions = " AND ".join([f"JSON_VALUE(labels, '$.{key}') = :{key}" for key in memory_labels])
        # Combine both conditions
        conditions = f"{json_validation} AND {json_conditions}"

        # Create SQL condition using SQLAlchemy's text() with bindparams
        # for safe parameter passing, preventing SQL injection
        condition = text(conditions).bindparams(**{key: str(value) for key, value in memory_labels.items()})
        return [condition]

    def _get_message_pieces_attack_conditions(self, *, attack_id: str) -> Any:
        """
        Generate SQL condition for filtering message pieces by attack ID.

        Uses JSON_VALUE() function specific to SQL Azure to query the attack identifier.

        Args:
            attack_id (str): The attack identifier to filter by.

        Returns:
            Any: SQLAlchemy text condition with bound parameter.
        """
        return text("ISJSON(attack_identifier) = 1 AND JSON_VALUE(attack_identifier, '$.id') = :json_id").bindparams(
            json_id=str(attack_id)
        )

    def _get_metadata_conditions(self, *, prompt_metadata: dict[str, Union[str, int]]) -> list[TextClause]:
        """
        Generate SQL conditions for filtering by prompt metadata.

        Uses JSON_VALUE() function specific to SQL Azure to query metadata fields in JSON format.

        Args:
            prompt_metadata (dict[str, Union[str, int]]): Dictionary of metadata key-value pairs to filter by.

        Returns:
            list: List containing a single SQLAlchemy text condition with bound parameters.
        """
        json_validation = "ISJSON(prompt_metadata) = 1"
        json_conditions = " AND ".join([f"JSON_VALUE(prompt_metadata, '$.{key}') = :{key}" for key in prompt_metadata])
        # Combine both conditions
        conditions = f"{json_validation} AND {json_conditions}"

        # Create SQL condition using SQLAlchemy's text() with bindparams
        # for safe parameter passing, preventing SQL injection
        # Note: JSON_VALUE always returns nvarchar in SQL Server, so we must convert all values to strings
        # to avoid type conversion errors when comparing mixed types (e.g., int and string)
        condition = text(conditions).bindparams(**{key: str(value) for key, value in prompt_metadata.items()})
        return [condition]

    def _get_message_pieces_prompt_metadata_conditions(
        self, *, prompt_metadata: dict[str, Union[str, int]]
    ) -> list[TextClause]:
        """
        Generate SQL conditions for filtering message pieces by prompt metadata.

        This is a convenience wrapper around _get_metadata_conditions.

        Args:
            prompt_metadata (dict[str, Union[str, int]]): Dictionary of metadata key-value pairs to filter by.

        Returns:
            list: List containing SQLAlchemy text conditions with bound parameters.
        """
        return self._get_metadata_conditions(prompt_metadata=prompt_metadata)

    def _get_seed_metadata_conditions(self, *, metadata: dict[str, Union[str, int]]) -> TextClause:
        """
        Generate SQL condition for filtering seed prompts by metadata.

        This is a convenience wrapper around _get_metadata_conditions that returns
        the first (and only) condition.

        Args:
            metadata (dict[str, Union[str, int]]): Dictionary of metadata key-value pairs to filter by.

        Returns:
            Any: SQLAlchemy text condition with bound parameters.
        """
        return self._get_metadata_conditions(prompt_metadata=metadata)[0]

    def _get_attack_result_harm_category_condition(self, *, targeted_harm_categories: Sequence[str]) -> Any:
        """
        Get the SQL Azure implementation for filtering AttackResults by targeted harm categories.

        Uses JSON_QUERY() function specific to SQL Azure to check if categories exist in the JSON array.

        Args:
            targeted_harm_categories (Sequence[str]): List of harm category strings to filter by.

        Returns:
            Any: SQLAlchemy exists subquery condition with bound parameters.
        """
        # For SQL Azure, we need to use JSON_QUERY to check if a value exists in a JSON array
        # OPENJSON can parse the array and we check if the category exists
        # Using parameterized queries for safety
        harm_conditions = []
        bindparams_dict = {}
        for i, category in enumerate(targeted_harm_categories):
            param_name = f"harm_cat_{i}"
            # Check if the JSON array contains the category value
            harm_conditions.append(
                f"EXISTS(SELECT 1 FROM OPENJSON(targeted_harm_categories) WHERE value = :{param_name})"
            )
            bindparams_dict[param_name] = category

        combined_conditions = " AND ".join(harm_conditions)

        targeted_harm_categories_subquery = exists().where(
            and_(
                PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                PromptMemoryEntry.targeted_harm_categories.isnot(None),
                PromptMemoryEntry.targeted_harm_categories != "",
                PromptMemoryEntry.targeted_harm_categories != "[]",
                text(f"ISJSON(targeted_harm_categories) = 1 AND {combined_conditions}").bindparams(**bindparams_dict),
            )
        )
        return targeted_harm_categories_subquery

    def _get_attack_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        Get the SQL Azure implementation for filtering AttackResults by labels.

        Uses JSON_VALUE() function specific to SQL Azure with parameterized queries.

        Args:
            labels (dict[str, str]): Dictionary of label key-value pairs to filter by.

        Returns:
            Any: SQLAlchemy exists subquery condition with bound parameters.
        """
        # Build JSON conditions for all labels with parameterized queries
        label_conditions = []
        bindparams_dict = {}
        for key, value in labels.items():
            param_name = f"label_{key}"
            label_conditions.append(f"JSON_VALUE(labels, '$.{key}') = :{param_name}")
            bindparams_dict[param_name] = str(value)

        combined_conditions = " AND ".join(label_conditions)

        labels_subquery = exists().where(
            and_(
                PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                PromptMemoryEntry.labels.isnot(None),
                text(f"ISJSON(labels) = 1 AND {combined_conditions}").bindparams(**bindparams_dict),
            )
        )
        return labels_subquery

    def _get_scenario_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        Get the SQL Azure implementation for filtering ScenarioResults by labels.

        Uses JSON_VALUE() function specific to SQL Azure.

        Args:
            labels (dict[str, str]): Dictionary of label key-value pairs to filter by.

        Returns:
            Any: SQLAlchemy combined condition with bound parameters.
        """
        # Return combined conditions for all labels
        conditions = []
        for key, value in labels.items():
            condition = text(f"ISJSON(labels) = 1 AND JSON_VALUE(labels, '$.{key}') = :{key}").bindparams(
                **{key: str(value)}
            )
            conditions.append(condition)
        return and_(*conditions)

    def _get_scenario_result_target_endpoint_condition(self, *, endpoint: str) -> TextClause:
        """
        Get the SQL Azure implementation for filtering ScenarioResults by target endpoint.

        Uses JSON_VALUE() function specific to SQL Azure.

        Args:
            endpoint (str): The endpoint URL substring to filter by (case-insensitive).

        Returns:
            Any: SQLAlchemy text condition with bound parameter.
        """
        return text(
            """ISJSON(objective_target_identifier) = 1
            AND LOWER(JSON_VALUE(objective_target_identifier, '$.endpoint')) LIKE :endpoint"""
        ).bindparams(endpoint=f"%{endpoint.lower()}%")

    def _get_scenario_result_target_model_condition(self, *, model_name: str) -> TextClause:
        """
        Get the SQL Azure implementation for filtering ScenarioResults by target model name.

        Uses JSON_VALUE() function specific to SQL Azure.

        Args:
            model_name (str): The model name substring to filter by (case-insensitive).

        Returns:
            Any: SQLAlchemy text condition with bound parameter.
        """
        return text(
            """ISJSON(objective_target_identifier) = 1
            AND LOWER(JSON_VALUE(objective_target_identifier, '$.model_name')) LIKE :model_name"""
        ).bindparams(model_name=f"%{model_name.lower()}%")

    def add_message_pieces_to_memory(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Insert a list of message pieces into the memory storage.

        """
        self._insert_entries(entries=[PromptMemoryEntry(entry=piece) for piece in message_pieces])

    def dispose_engine(self) -> None:
        """
        Dispose the engine and clean up resources.
        """
        if self.engine:
            self.engine.dispose()
            logger.info("Engine disposed successfully.")

    def get_all_embeddings(self) -> Sequence[EmbeddingDataEntry]:
        """
        Fetch all entries from the specified table and returns them as model instances.

        Returns:
            Sequence[EmbeddingDataEntry]: A sequence of EmbeddingDataEntry instances representing all stored embeddings.
        """
        result: Sequence[EmbeddingDataEntry] = self._query_entries(EmbeddingDataEntry)
        return result

    def _insert_entry(self, entry: Base) -> None:
        """
        Insert an entry into the Table.

        Args:
            entry: An instance of a SQLAlchemy model to be added to the Table.

        Raises:
            SQLAlchemyError: If the insertion fails.
        """
        with closing(self.get_session()) as session:
            try:
                session.add(entry)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting entry into the table: {e}")
                raise

    # The following methods are not part of MemoryInterface, but seem
    # common between SQLAlchemy-based implementations, regardless of engine.
    # Perhaps we should find a way to refactor
    def _insert_entries(self, *, entries: Sequence[Base]) -> None:
        """
        Insert multiple entries into the database.

        Args:
            entries (Sequence[Base]): A sequence of SQLAlchemy model instances to insert.

        Raises:
            SQLAlchemyError: If the insertion fails.
        """
        with closing(self.get_session()) as session:
            try:
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting multiple entries into the table: {e}")
                raise

    def get_session(self) -> Session:
        """
        Provide a session for database operations.

        Returns:
            Session: A new SQLAlchemy session bound to the configured engine.
        """
        return self.SessionFactory()

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
            model_class: The SQLAlchemy model class to query.
            conditions: SQLAlchemy filter conditions (Optional).
            distinct: Flag to return distinct rows (defaults to False).
            join_scores: Flag to join the scores table with entries (defaults to False).

        Returns:
            List of model instances representing the rows fetched from the table.

        Raises:
            SQLAlchemyError: If the query fails.
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
                logger.exception(f"Error fetching data from table {model_class.__tablename__}: {e}")  # type: ignore
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
            ValueError: If 'update_fields' is empty.
            SQLAlchemyError: If the update fails.
        """
        if not update_fields:
            raise ValueError("update_fields must be provided to update prompt entries.")
        with closing(self.get_session()) as session:
            try:
                for entry in entries:
                    # Ensure the entry is attached to the session. If it's detached, merge it.
                    if not session.is_modified(entry):
                        entry_in_session = session.merge(entry)
                    else:
                        entry_in_session = entry
                    for field, value in update_fields.items():
                        if field in vars(entry_in_session):
                            setattr(entry_in_session, field, value)
                        else:
                            session.rollback()
                            raise ValueError(
                                f"Field '{field}' does not exist in the table \
                                            '{entry_in_session.__tablename__}'. Rolling back changes..."
                            )
                session.commit()
                return True
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error updating entries: {e}")
                raise

    def reset_database(self) -> None:
        """Drop and recreate existing tables."""
        # Drop all existing tables
        Base.metadata.drop_all(self.engine)
        # Recreate the tables
        Base.metadata.create_all(self.engine, checkfirst=True)
