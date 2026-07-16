# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import struct
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import and_, create_engine, event, exists, text
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import InstrumentedAttribute, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import ColumnElement, TextClause

from pyrit.auth.azure_auth import AzureAuth
from pyrit.common import default_values
from pyrit.common.singleton import Singleton
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import (
    AttackResultEntry,
    PromptMemoryEntry,
)
from pyrit.memory.storage import AzureBlobStorageIO
from pyrit.models import ConversationStats

if TYPE_CHECKING:
    from azure.core.credentials import AccessToken

logger = logging.getLogger(__name__)


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

    # Azure SQL supports up to 2100 parameters per statement
    _MAX_BIND_VARS: int = 2000

    # Azure Storage Account Container datasets and results environment variables
    AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL: str = "AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL"
    AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN: str = "AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN"

    # Optional environment variable for production connection string to prevent accidental schema migrations on prod
    AZURE_SQL_DB_CONNECTION_STRING_PROD: str = "AZURE_SQL_DB_CONNECTION_STRING_PROD"

    def __init__(
        self,
        *,
        connection_string: str | None = None,
        results_container_url: str | None = None,
        results_sas_token: str | None = None,
        verbose: bool = False,
        skip_schema_migration: bool = False,
        silent: bool = False,
    ) -> None:
        """
        Initialize an Azure SQL Memory backend.

        Args:
            connection_string (str | None): The connection string for the Azure Sql Database. If not provided,
                it falls back to the 'AZURE_SQL_DB_CONNECTION_STRING' environment variable.
            results_container_url (str | None): The URL to an Azure Storage Container. If not provided,
                it falls back to the 'AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL' environment variable.
            results_sas_token (str | None): The Shared Access Signature (SAS) token for the storage container.
                If not provided, falls back to the 'AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN' environment variable.
            verbose (bool): Whether to enable verbose logging for the database engine. Defaults to False.
            skip_schema_migration (bool): Whether to skip schema migration. Defaults to False.
            silent (bool): If True, suppresses schema migration console output. Defaults to False.
        """
        self._connection_string = default_values.get_required_value(
            env_var_name=self.AZURE_SQL_DB_CONNECTION_STRING, passed_value=connection_string
        )

        self._results_container_url: str = default_values.get_required_value(
            env_var_name=self.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL, passed_value=results_container_url
        )

        self._results_container_sas_token: str | None = self._resolve_sas_token(
            self.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN, results_sas_token
        )

        self._auth_token: AccessToken | None = None
        self._auth_token_expiry: int | None = None

        self.results_path = self._results_container_url

        self.engine = self._create_engine(has_echo=verbose)

        # Generate the initial auth token
        self._create_auth_token()
        # Enable token-based authorization
        self._enable_azure_authorization()

        self.SessionFactory = sessionmaker(bind=self.engine)

        prod_connection_string = default_values.get_non_required_value(
            env_var_name=self.AZURE_SQL_DB_CONNECTION_STRING_PROD
        )

        is_prod = bool(prod_connection_string) and self._connection_string == prod_connection_string
        should_migrate = not is_prod and not skip_schema_migration

        if should_migrate:
            # Non-production: run schema migration (upgrade + check).
            self._run_schema_migration(silent=silent)
        else:
            # Production or skip_schema_migration=True: verify schema compatibility
            # without modifying the database. Logs a warning on mismatch but does not
            # block startup, so developers on newer code can still query data.
            from alembic.util.exc import AutogenerateDiffsDetected, CommandError

            try:
                self._check_schema_migration(silent=silent)
            except (AutogenerateDiffsDetected, CommandError) as e:
                logger.warning(
                    "Schema mismatch detected. "
                    "Your code models differ from the database schema. "
                    "This may cause errors if your code references columns or tables that don't exist. "
                    f"Schema was NOT modified. Details: {e}"
                )

        super().__init__()

    @staticmethod
    def _resolve_sas_token(env_var_name: str, passed_value: str | None = None) -> str | None:
        """
        Resolve the SAS token value, allowing a fallback to None for delegation SAS.

        Args:
            env_var_name (str): The environment variable name to look up.
            passed_value (str | None): A passed-in value for the SAS token.

        Returns:
            str | None: Resolved SAS token or None if not provided.
        """
        try:
            return default_values.get_required_value(env_var_name=env_var_name, passed_value=passed_value)
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

        Raises:
            RuntimeError: If auth token expiry was not initialized.
        """
        if self._auth_token_expiry is None:
            raise RuntimeError("Auth token expiry not initialized; call _create_auth_token() first")
        if datetime.now(timezone.utc) >= datetime.fromtimestamp(
            float(self._auth_token_expiry), tz=timezone.utc
        ) - timedelta(minutes=5):
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
            if self._auth_token is None:
                raise RuntimeError("Azure auth token is not initialized")
            azure_token = self._auth_token.token
            azure_token_bytes = azure_token.encode("utf-16-le")
            packed_azure_token = struct.pack(f"<I{len(azure_token_bytes)}s", len(azure_token_bytes), azure_token_bytes)

            # add the encoded token
            cparams["attrs_before"] = {self.SQL_COPT_SS_ACCESS_TOKEN: packed_azure_token}

    def _get_message_pieces_memory_label_conditions(self, *, memory_labels: dict[str, str]) -> list[Any]:
        """
        Generate SQL conditions for filtering message pieces by memory labels.

        Uses JSON_VALUE() function specific to SQL Azure to query label fields in JSON format.

        Matches labels on an AttackResultEntry that shares the same conversation_id.

        Args:
            memory_labels (dict[str, str]): Dictionary of label key-value pairs to filter by.

        Returns:
            list: List containing a single SQLAlchemy OR condition with bound parameters.
        """
        are_label_parts: list[str] = []
        are_bindparams: dict[str, str] = {}

        for key, value in memory_labels.items():
            are_param = f"are_ml_{key}"
            are_label_parts.append(f"JSON_VALUE(\"AttackResultEntries\".labels, '$.{key}') = :{are_param}")
            are_bindparams[are_param] = str(value)

        combined_are = " AND ".join(are_label_parts)
        are_match = exists().where(
            and_(
                AttackResultEntry.conversation_id == PromptMemoryEntry.conversation_id,
                AttackResultEntry.labels.isnot(None),
                cast(
                    "ColumnElement[bool]",
                    text(f'ISJSON("AttackResultEntries".labels) = 1 AND {combined_are}').bindparams(**are_bindparams),
                ),
            )
        )

        return [are_match]

    def _get_metadata_conditions(self, *, prompt_metadata: dict[str, str | int]) -> list[TextClause]:
        """
        Generate SQL conditions for filtering by prompt metadata.

        Uses JSON_VALUE() function specific to SQL Azure to query metadata fields in JSON format.

        Args:
            prompt_metadata (dict[str, str | int]): Dictionary of metadata key-value pairs to filter by.

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
        self, *, prompt_metadata: dict[str, str | int]
    ) -> list[TextClause]:
        """
        Generate SQL conditions for filtering message pieces by prompt metadata.

        This is a convenience wrapper around _get_metadata_conditions.

        Args:
            prompt_metadata (dict[str, str | int]): Dictionary of metadata key-value pairs to filter by.

        Returns:
            list: List containing SQLAlchemy text conditions with bound parameters.
        """
        return self._get_metadata_conditions(prompt_metadata=prompt_metadata)

    def _get_seed_metadata_conditions(self, *, metadata: dict[str, str | int]) -> TextClause:
        """
        Generate SQL condition for filtering seed prompts by metadata.

        This is a convenience wrapper around _get_metadata_conditions that returns
        the first (and only) condition.

        Args:
            metadata (dict[str, str | int]): Dictionary of metadata key-value pairs to filter by.

        Returns:
            Any: SQLAlchemy text condition with bound parameters.
        """
        return self._get_metadata_conditions(prompt_metadata=metadata)[0]

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
        Return an Azure SQL DB condition for matching a value at a given path within a JSON object.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed model field to query.
            property_path (str): The JSON path for the property to match.
            value (str): The string value that must match the extracted JSON property value.
            partial_match (bool): Whether to perform a substring match. Defaults to False.
            case_sensitive (bool): Whether the match should be case-sensitive. Defaults to False.

        Returns:
            Any: A SQLAlchemy condition for the backend-specific JSON query.
        """
        uid = self._uid()
        table_name = json_column.class_.__tablename__
        column_name = json_column.key
        pp_param = f"pp_{uid}"
        mv_param = f"mv_{uid}"
        operator = "LIKE" if partial_match else "="
        target = value if case_sensitive else value.lower()
        if partial_match:
            escaped = target.replace("%", "\\%").replace("_", "\\_")
            target = f"%{escaped}%"

        json_value_expr = f'JSON_VALUE("{table_name}".{column_name}, :{pp_param})'
        if not case_sensitive:
            json_value_expr = f"LOWER({json_value_expr})"

        escape_clause = " ESCAPE '\\'" if partial_match else ""
        return text(
            f"""ISJSON("{table_name}".{column_name}) = 1
                AND {json_value_expr} {operator} :{mv_param}{escape_clause}"""
        ).bindparams(
            **{
                pp_param: property_path,
                mv_param: target,
            }
        )

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
        Return an Azure SQL DB condition for matching an array at a given path within a JSON object.

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
        uid = self._uid()
        table_name = json_column.class_.__tablename__
        column_name = json_column.key
        pp_param = f"pp_{uid}"
        sp_param = f"sp_{uid}"

        if len(array_to_match) == 0:
            return text(
                f"""("{table_name}".{column_name} IS NULL
                OR JSON_QUERY("{table_name}".{column_name}, :{pp_param}) IS NULL
                OR JSON_QUERY("{table_name}".{column_name}, :{pp_param}) = '[]')"""
            ).bindparams(**{pp_param: property_path})

        value_expression = f"LOWER(JSON_VALUE(value, :{sp_param}))" if array_element_path else "LOWER(value)"

        conditions = []
        bindparams_dict: dict[str, str] = {pp_param: property_path}
        if array_element_path:
            bindparams_dict[sp_param] = array_element_path

        for index, match_value in enumerate(array_to_match):
            mv_param = f"mv_{uid}_{index}"
            conditions.append(
                f"""EXISTS(SELECT 1 FROM OPENJSON(JSON_QUERY("{table_name}".{column_name},
                    :{pp_param}))
                    WHERE {value_expression} = :{mv_param})"""
            )
            bindparams_dict[mv_param] = match_value.lower()

        joiner = " OR " if match_mode == "any" else " AND "
        combined = joiner.join(conditions)
        return text(f"""ISJSON("{table_name}".{column_name}) = 1 AND ({combined})""").bindparams(**bindparams_dict)

    def _get_attack_result_label_condition(self, *, labels: dict[str, str | Sequence[str]]) -> Any:
        """
        Azure SQL implementation for filtering AttackResults by labels.

        Matches labels directly on the AttackResultEntry.

        Uses JSON_VALUE() with parameterized IN clauses. See
        ``MemoryInterface._get_attack_result_label_condition`` for semantics.

        Returns:
            Any: SQLAlchemy condition with bound parameters.
        """
        are_label_conditions: list[str] = []
        are_bindparams: dict[str, str] = {}

        for key, raw_value in labels.items():
            values = [raw_value] if isinstance(raw_value, str) else list(raw_value)
            if not values:
                continue
            are_placeholders = []
            for idx, v in enumerate(values):
                are_param = f"are_label_{key}_{idx}"
                are_placeholders.append(f":{are_param}")
                are_bindparams[are_param] = str(v)
            are_in = ", ".join(are_placeholders)
            are_label_conditions.append(f"JSON_VALUE(\"AttackResultEntries\".labels, '$.{key}') IN ({are_in})")

        are_parts: list[Any] = [AttackResultEntry.labels.isnot(None)]
        if are_label_conditions:
            combined_are = " AND ".join(are_label_conditions)
            are_parts.append(
                cast(
                    "ColumnElement[bool]",
                    text(f'ISJSON("AttackResultEntries".labels) = 1 AND {combined_are}').bindparams(**are_bindparams),
                )
            )
        return and_(*are_parts)

    def get_unique_attack_class_names(self) -> list[str]:
        """
        Azure SQL implementation: extract unique class_name values from
        the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique attack class name strings.
        """
        with closing(self.get_session()) as session:
            rows = session.execute(
                text(
                    """SELECT DISTINCT JSON_VALUE(atomic_attack_identifier,
                        '$.children.attack_technique.children.attack.class_name') AS cls
                    FROM "AttackResultEntries"
                    WHERE ISJSON(atomic_attack_identifier) = 1
                    AND JSON_VALUE(atomic_attack_identifier,
                        '$.children.attack_technique.children.attack.class_name') IS NOT NULL"""
                )
            ).fetchall()
        return sorted(row[0] for row in rows)

    def get_unique_converter_class_names(self) -> list[str]:
        """
        Azure SQL implementation: extract unique converter class_name values
        from the children.attack_technique.children.attack.children.request_converters array
        in the atomic_attack_identifier JSON column.

        Returns:
            Sorted list of unique converter class name strings.
        """
        with closing(self.get_session()) as session:
            rows = session.execute(
                text(
                    """SELECT DISTINCT JSON_VALUE(c.value, '$.class_name') AS cls
                    FROM "AttackResultEntries"
                    CROSS APPLY OPENJSON(JSON_QUERY(atomic_attack_identifier,
                        '$.children.attack_technique.children.attack.children.request_converters')) AS c
                    WHERE ISJSON(atomic_attack_identifier) = 1
                    AND JSON_VALUE(c.value, '$.class_name') IS NOT NULL"""
                )
            ).fetchall()
        return sorted(row[0] for row in rows)

    def get_conversation_stats(self, *, conversation_ids: Sequence[str]) -> dict[str, ConversationStats]:
        """
        Azure SQL implementation: lightweight aggregate stats per conversation.

        Executes a single SQL query that returns message count (distinct
        sequences), a truncated last-message preview, and the earliest
        timestamp for each conversation_id.

        Args:
            conversation_ids (Sequence[str]): The conversation IDs to query.

        Returns:
            Mapping from conversation_id to ConversationStats.
        """
        if not conversation_ids:
            return {}

        placeholders = ", ".join(f":cid{i}" for i in range(len(conversation_ids)))
        params = {f"cid{i}": cid for i, cid in enumerate(conversation_ids)}

        sql = text(
            f"""
            SELECT
                pme.conversation_id,
                COUNT(DISTINCT pme.sequence) AS msg_count,
                (
                    SELECT TOP 1 LEFT(p2.converted_value, {ConversationStats.PREVIEW_FETCH_MAX_LEN})
                    FROM "PromptMemoryEntries" p2
                    WHERE p2.conversation_id = pme.conversation_id
                    ORDER BY p2.sequence DESC, p2.id DESC
                ) AS last_preview,
                (
                    SELECT TOP 1 p2b.converted_value_data_type
                    FROM "PromptMemoryEntries" p2b
                    WHERE p2b.conversation_id = pme.conversation_id
                    ORDER BY p2b.sequence DESC, p2b.id DESC
                ) AS last_data_type,
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

    def get_session(self) -> Session:
        """
        Provide a session for database operations.

        Returns:
            Session: A new SQLAlchemy session bound to the configured engine.
        """
        return self.SessionFactory()
