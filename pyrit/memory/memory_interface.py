# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import atexit
import logging
import re
import uuid
import weakref
from collections.abc import MutableSequence, Sequence
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional, TypeVar, Union

from sqlalchemy import MetaData, and_, not_, or_
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import InstrumentedAttribute

if TYPE_CHECKING:
    from pyrit.memory.memory_embedding import MemoryEmbedding

from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.path import DB_DATA_PATH
from pyrit.identifiers.identifier_filters import IdentifierFilter, IdentifierType
from pyrit.memory.memory_exporter import MemoryExporter
from pyrit.memory.memory_models import (
    AttackResultEntry,
    Base,
    EmbeddingDataEntry,
    PromptMemoryEntry,
    ScenarioResultEntry,
    ScoreEntry,
    SeedEntry,
)
from pyrit.models import (
    AttackResult,
    ConversationStats,
    DataTypeSerializer,
    Message,
    MessagePiece,
    ScenarioResult,
    Score,
    Seed,
    SeedDataset,
    SeedGroup,
    SeedType,
    StorageIO,
    data_serializer_factory,
    group_conversation_message_pieces_by_sequence,
    sort_message_pieces,
)

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)


Model = TypeVar("Model")

# Label keys are interpolated into backend-specific JSON path expressions
# (e.g. ``$.key``) in the per-backend label-filter helpers. We restrict keys
# to a conservative allowlist so a crafted key cannot break out of the JSON
# path literal and inject SQL. Values are always passed as bound parameters
# and do not need this restriction.
_LABEL_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")


class MemoryInterface(abc.ABC):
    """
    Abstract interface for conversation memory storage systems.

    This interface defines the contract for storing and retrieving chat messages
    and conversation history. Implementations can use different storage backends
    such as files, databases, or cloud storage services.
    """

    # Maximum number of bind variables per SQL statement.
    # Conservative default based on SQLite's limit of 999. Subclasses can override
    # for backends with higher limits (e.g., Azure SQL supports 2100).
    _MAX_BIND_VARS: int = 500

    memory_embedding: "MemoryEmbedding | None" = None
    results_storage_io: StorageIO | None = None
    results_path: str | None = None
    engine: Engine | None = None

    @staticmethod
    def _uid() -> str:
        """Return a short unique suffix for bind-param deduplication."""
        return uuid.uuid4().hex[:8]

    def __init__(self, embedding_model: Optional[Any] = None) -> None:
        """
        Initialize the MemoryInterface.

        Args:
            embedding_model: If set, this includes embeddings in the memory entries
                which are extremely useful for comparing chat messages and similarities,
                but also includes overhead.
        """
        self.memory_embedding = embedding_model
        # Initialize the MemoryExporter instance
        self.exporter = MemoryExporter()
        self._init_storage_io()

        # Ensure cleanup at process exit
        self.cleanup()

    def enable_embedding(self, embedding_model: Optional[Any] = None) -> None:
        """
        Enable embedding functionality for the memory interface.

        Args:
            embedding_model: Optional embedding model to use. If not provided,
                attempts to create a default embedding model from environment variables.

        Raises:
            ValueError: If no embedding model is provided and required environment
            variables are not set.
        """
        from pyrit.memory.memory_embedding import default_memory_embedding_factory

        self.memory_embedding = default_memory_embedding_factory(embedding_model=embedding_model)

    def disable_embedding(self) -> None:
        """
        Disable embedding functionality for the memory interface.

        Sets the memory_embedding attribute to None, disabling any embedding operations.
        """
        self.memory_embedding = None

    def _build_identifier_filter_conditions(
        self,
        *,
        identifier_filters: Sequence[IdentifierFilter],
        identifier_column_map: dict[IdentifierType, Any],
        caller: str,
    ) -> list[Any]:
        """
        Build SQLAlchemy conditions from a sequence of IdentifierFilters.

        Args:
            identifier_filters (Sequence[IdentifierFilter]): The filters to convert to conditions.
            identifier_column_map (dict[IdentifierType, Any]): Mapping from IdentifierType to the
                JSON-backed SQLAlchemy column that should be queried for that type.
            caller (str): Name of the calling method, used in error messages.

        Returns:
            list[Any]: A list of SQLAlchemy conditions.

        Raises:
            ValueError: If a filter uses an IdentifierType not in identifier_column_map.
        """
        conditions: list[Any] = []
        for identifier_filter in identifier_filters:
            column = identifier_column_map.get(identifier_filter.identifier_type)
            if column is None:
                supported = ", ".join(t.name for t in identifier_column_map)
                raise ValueError(
                    f"{caller} does not support identifier type "
                    f"{identifier_filter.identifier_type!r}. Supported: {supported}"
                )
            conditions.append(
                self._get_condition_json_match(
                    json_column=column,
                    property_path=identifier_filter.property_path,
                    array_element_path=identifier_filter.array_element_path,
                    value=identifier_filter.value,
                    partial_match=identifier_filter.partial_match,
                    case_sensitive=identifier_filter.case_sensitive,
                )
            )
        return conditions

    def _get_condition_json_match(
        self,
        *,
        json_column: InstrumentedAttribute[Any],
        property_path: str,
        array_element_path: str | None = None,
        value: str,
        partial_match: bool = False,
        case_sensitive: bool = False,
    ) -> Any:
        """
        Return a database-specific condition for matching a value at a given path within a JSON object
        or within items of a JSON array if array_element_path is provided.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed model field to query.
            property_path (str): The JSON path for the property to match.
            array_element_path (str | None): An optional JSON path that indicates property at property_path is an array
                and the condition should resolve if any element in that array matches the value.
                Cannot be used with partial_match.
            value (str): The string value that must match the extracted JSON property value.
            partial_match (bool): Whether to perform a substring match.
            case_sensitive (bool): Whether the match should be case-sensitive. Defaults to False.

        Returns:
            Any: A SQLAlchemy condition for the backend-specific JSON query.

        Raises:
            ValueError: If array_element_path is provided together with partial_match or case_sensitive
        """
        if array_element_path and (partial_match or case_sensitive):
            raise ValueError("Cannot use array_element_path with partial_match or case_sensitive")
        if partial_match and case_sensitive:
            raise ValueError("case_sensitive is not reliably supported with partial_match across all backends")
        if array_element_path:
            return self._get_condition_json_array_match(
                json_column=json_column,
                property_path=property_path,
                array_element_path=array_element_path,
                array_to_match=[value],
            )
        return self._get_condition_json_property_match(
            json_column=json_column,
            property_path=property_path,
            value=value,
            partial_match=partial_match,
            case_sensitive=case_sensitive,
        )

    @abc.abstractmethod
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
        Return a database-specific condition for matching a value at a given path within a JSON object.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed model field to query.
            property_path (str): The JSON path for the property to match.
            value (str): The string value that must match the extracted JSON property value.
            partial_match (bool): Whether to perform a substring match.
            case_sensitive (bool): Whether the match should be case-sensitive. Defaults to False.

        Returns:
            Any: A SQLAlchemy condition for the backend-specific JSON query.
        """

    @abc.abstractmethod
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
        Return a database-specific condition for matching an array at a given path within a JSON object.

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
                ``"any"`` requires at least one listed value to be present. Ignored when
                ``array_to_match`` has fewer than 2 entries or is empty.

        Returns:
            Any: A database-specific SQLAlchemy condition.
        """

    @abc.abstractmethod
    def get_all_embeddings(self) -> Sequence[EmbeddingDataEntry]:
        """
        Load all EmbeddingData from the memory storage handler.
        """

    @abc.abstractmethod
    def _init_storage_io(self) -> None:
        """
        Initialize the storage IO handler results_storage_io.
        """

    @abc.abstractmethod
    def _get_message_pieces_memory_label_conditions(self, *, memory_labels: dict[str, str]) -> list[Any]:
        """
        Return a list of conditions for filtering memory entries based on memory labels.

        Args:
            memory_labels (dict[str, str]): A free-form dictionary for tagging prompts with custom labels.
                These labels can be used to track all prompts sent as part of an operation, score prompts based on
                the operation ID (op_id), and tag each prompt with the relevant Responsible AI (RAI) harm category.
                Users can define any key-value pairs according to their needs.

        Returns:
            list: A list of conditions for filtering memory entries based on memory labels.
        """

    @abc.abstractmethod
    def _get_message_pieces_prompt_metadata_conditions(
        self, *, prompt_metadata: dict[str, Union[str, int]]
    ) -> list[Any]:
        """
        Return a list of conditions for filtering memory entries based on prompt metadata.

        Args:
            prompt_metadata (dict[str, str | int]): A free-form dictionary for tagging prompts with custom metadata.
                This includes information that is useful for the specific target you're probing, such as encoding data.

        Returns:
            list: A list of conditions for filtering memory entries based on prompt metadata.
        """

    @abc.abstractmethod
    def _get_seed_metadata_conditions(self, *, metadata: dict[str, Union[str, int]]) -> Any:
        """
        Return a condition for filtering seed prompt entries based on prompt metadata.

        Args:
            metadata (dict[str, str | int]): A free-form dictionary for tagging prompts with custom metadata.
                This includes information that is useful for the specific target you're probing, such as encoding data.

        Returns:
            Any: A SQLAlchemy condition for filtering memory entries based on prompt metadata.
        """

    @abc.abstractmethod
    def add_message_pieces_to_memory(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Insert a list of message pieces into the memory storage.
        """

    @abc.abstractmethod
    def _add_embeddings_to_memory(self, *, embedding_data: Sequence[EmbeddingDataEntry]) -> None:
        """
        Insert embedding data into memory storage.
        """

    @abc.abstractmethod
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
            distinct: Whether to return distinct rows only. Defaults to False.
            join_scores: Whether to join the scores table. Defaults to False.

        Returns:
            List of model instances representing the rows fetched from the table.
        """

    def _execute_batched_query(
        self,
        model_class: type[Model],
        *,
        batch_column: InstrumentedAttribute[Any],
        batch_values: Sequence[Any],
        other_conditions: list[Any] | None = None,
        distinct: bool = False,
        join_scores: bool = False,
        batch_size: int | None = None,
    ) -> MutableSequence[Model]:
        """
        Execute queries in batches to avoid exceeding database bind variable limits.

        SQLite and other databases have per-statement parameter limits. This method
        executes separate queries for each batch of values and merges the results.

        Args:
            model_class: The SQLAlchemy model class to query.
            batch_column: The column to batch the IN condition on.
            batch_values: The values to filter by (will be batched).
            other_conditions: Additional SQLAlchemy conditions to include in each query.
            distinct: Whether to return distinct rows only.
            join_scores: Whether to join the scores table.
            batch_size: Override for the number of values per batch.
                Defaults to ``_MAX_BIND_VARS`` when not specified.

        Returns:
            MutableSequence[Model]: Merged and deduplicated results from all batched queries.
        """
        if other_conditions is None:
            other_conditions = []

        effective_size = batch_size if batch_size is not None else self._MAX_BIND_VARS

        # If values fit in one batch, execute a single query
        if len(batch_values) <= effective_size:
            conditions = other_conditions + [batch_column.in_(batch_values)]
            return self._query_entries(
                model_class,
                conditions=and_(*conditions) if conditions else None,
                distinct=distinct,
                join_scores=join_scores,
            )

        # Execute multiple separate queries and merge results
        all_results: MutableSequence[Model] = []
        seen_ids: set[str] = set()

        for i in range(0, len(batch_values), effective_size):
            batch = batch_values[i : i + effective_size]
            conditions = other_conditions + [batch_column.in_(batch)]

            results = self._query_entries(
                model_class,
                conditions=and_(*conditions) if conditions else None,
                distinct=distinct,
                join_scores=join_scores,
            )

            # Deduplicate by primary key (id)
            for result in results:
                result_id = getattr(result, "id", None)
                if result_id is not None:
                    id_str = str(result_id)
                    if id_str not in seen_ids:
                        seen_ids.add(id_str)
                        all_results.append(result)
                else:
                    all_results.append(result)

        return all_results

    def _query_with_list_params(
        self,
        model_class: type[Model],
        *,
        conditions: list[Any],
        list_params: list[tuple[InstrumentedAttribute[Any], Sequence[Any], str]],
        join_scores: bool = False,
    ) -> MutableSequence[Model]:
        """
        Execute a query with list-based IN filters, batching when lists exceed bind limits.

        Splits list parameters into "small" (fit in one query) and "large" (need batching).
        Small params are added to the SQL conditions directly. The first large param is
        batched via ``_execute_batched_query``; any remaining large params are filtered
        in Python after fetching.

        The effective batch size is reduced to account for bind variables contributed by
        small params, preventing cumulative overflow of the per-statement limit.

        Args:
            model_class: The SQLAlchemy model class to query.
            conditions: Base conditions (scalar filters) to include in every query.
            list_params: List of (column, values, attr_name) tuples for IN-clause filters.
            join_scores: Whether to join the scores table.

        Returns:
            MutableSequence[Model]: Query results with all filters applied.
        """
        if not list_params:
            return self._query_entries(
                model_class,
                conditions=and_(*conditions) if conditions else None,
                join_scores=join_scores,
            )

        large_params = [(col, vals, name) for col, vals, name in list_params if len(vals) > self._MAX_BIND_VARS]
        small_params = [(col, vals, name) for col, vals, name in list_params if len(vals) <= self._MAX_BIND_VARS]

        # If cumulative small params exceed the limit, promote the largest ones to large
        small_params.sort(key=lambda x: len(x[1]))
        while sum(len(v) for _, v, _ in small_params) > self._MAX_BIND_VARS and small_params:
            large_params.append(small_params.pop())

        small_param_binds = sum(len(vals) for _, vals, _ in small_params)
        for col, vals, _ in small_params:
            conditions.append(col.in_(vals))

        if not large_params:
            return self._query_entries(
                model_class,
                conditions=and_(*conditions) if conditions else None,
                join_scores=join_scores,
            )

        batch_col, batch_vals, _ = large_params[0]
        other_large_params = large_params[1:]

        # Reduce batch size to account for bind variables already used by small params
        effective_batch_size = max(1, self._MAX_BIND_VARS - small_param_binds)

        results = self._execute_batched_query(
            model_class,
            batch_column=batch_col,
            batch_values=batch_vals,
            other_conditions=conditions,
            join_scores=join_scores,
            batch_size=effective_batch_size,
        )

        for _col, vals, attr_name in other_large_params:
            vals_set = set(vals)
            results = [e for e in results if getattr(e, attr_name, None) in vals_set]

        return results

    @abc.abstractmethod
    def _insert_entry(self, entry: Base) -> None:
        """
        Insert an entry into the Table.

        Args:
            entry: An instance of a SQLAlchemy model to be added to the Table.
        """

    @abc.abstractmethod
    def _insert_entries(self, *, entries: Sequence[Base]) -> None:
        """Insert multiple entries into the database."""

    @abc.abstractmethod
    def get_session(self) -> Any:
        """
        Provide a SQLAlchemy session for transactional operations.

        Returns:
            Session: A SQLAlchemy session bound to the engine.
        """

    def _update_entry(self, entry: Base) -> None:
        """
        Update an existing entry in the Table using merge.

        This method uses SQLAlchemy's merge operation which will:
        - Update the existing record if the primary key matches
        - Insert a new record if the primary key doesn't exist

        Args:
            entry: An instance of a SQLAlchemy model to be updated in the Table.

        Raises:
            SQLAlchemyError: If there's an error during the database operation.
        """
        with closing(self.get_session()) as session:
            try:
                session.merge(entry)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error updating entry in the table: {e}")
                raise

    @abc.abstractmethod
    def _update_entries(self, *, entries: MutableSequence[Base], update_fields: dict[str, Any]) -> bool:
        """
        Update the given entries with the specified field values.

        Args:
            entries (Sequence[Base]): A list of SQLAlchemy model instances to be updated.
            update_fields (dict): A dictionary of field names and their new values.
        """

    @abc.abstractmethod
    def _get_attack_result_harm_category_condition(self, *, targeted_harm_categories: Sequence[str]) -> Any:
        """
        Return a database-specific condition for filtering AttackResults by targeted harm categories
        in the associated PromptMemoryEntry records.

        Args:
            targeted_harm_categories: List of harm categories that must ALL be present.

        Returns:
            Database-specific SQLAlchemy condition.
        """

    @abc.abstractmethod
    def _get_attack_result_label_condition(self, *, labels: dict[str, str | Sequence[str]]) -> Any:
        """
        Return a database-specific condition for filtering AttackResults by labels.

        Matches if the labels are present on **either** an associated
        PromptMemoryEntry (via conversation_id) **or** directly on the
        AttackResultEntry itself.

        Semantics: entries are AND-combined across label names; within a single
        entry, a string value is an equality match and a sequence value is an
        OR match over the listed values. An empty sequence is a no-op for that
        label. See ``get_attack_results`` for examples.

        Args:
            labels: Label-name-to-value(s) map.

        Returns:
            Database-specific SQLAlchemy condition.
        """

    @abc.abstractmethod
    def get_unique_attack_class_names(self) -> list[str]:
        """
        Return sorted unique attack class names from all stored attack results.

        Extracts class_name from the attack_identifier JSON column via a
        database-level DISTINCT query.

        Returns:
            Sorted list of unique attack class name strings.
        """

    @abc.abstractmethod
    def get_unique_converter_class_names(self) -> list[str]:
        """
        Return sorted unique converter class names used across all attack results.

        Extracts class_name values from the request_converter_identifiers array
        within the attack_identifier JSON column via a database-level query.

        Returns:
            Sorted list of unique converter class name strings.
        """

    @abc.abstractmethod
    def get_conversation_stats(self, *, conversation_ids: Sequence[str]) -> dict[str, "ConversationStats"]:
        """
        Return lightweight aggregate statistics for one or more conversations.

        Computes per-conversation message count (distinct sequence numbers),
        a truncated last-message preview, the first non-empty labels dict,
        and the earliest message timestamp using efficient SQL aggregation
        instead of loading full pieces.

        Args:
            conversation_ids: The conversation IDs to query.

        Returns:
            Mapping from conversation_id to ConversationStats.
            Conversations with no pieces are omitted from the result.
        """

    @abc.abstractmethod
    def _get_scenario_result_label_condition(self, *, labels: dict[str, str]) -> Any:
        """
        Return a database-specific condition for filtering ScenarioResults by labels.

        Args:
            labels: Dictionary of labels that must ALL be present.

        Returns:
            Database-specific SQLAlchemy condition.
        """

    def add_scores_to_memory(self, *, scores: Sequence[Score]) -> None:
        """
        Insert a list of scores into the memory storage.
        """
        for score in scores:
            if score.message_piece_id:
                message_piece_id = score.message_piece_id
                pieces = self.get_message_pieces(prompt_ids=[str(message_piece_id)])
                if not pieces:
                    logger.error(f"MessagePiece with ID {message_piece_id} not found in memory.")
                    continue
                # auto-link score to the original prompt id if the prompt is a duplicate
                if pieces[0].original_prompt_id != pieces[0].id:
                    score.message_piece_id = pieces[0].original_prompt_id
        self._insert_entries(entries=[ScoreEntry(entry=score) for score in scores])

    def get_scores(
        self,
        *,
        score_ids: Optional[Sequence[str]] = None,
        score_type: Optional[str] = None,
        score_category: Optional[str] = None,
        sent_after: Optional[datetime] = None,
        sent_before: Optional[datetime] = None,
        identifier_filters: Optional[Sequence[IdentifierFilter]] = None,
    ) -> Sequence[Score]:
        """
        Retrieve a list of Score objects based on the specified filters.

        Args:
            score_ids (Optional[Sequence[str]]): A list of score IDs to filter by.
            score_type (Optional[str]): The type of the score to filter by.
            score_category (Optional[str]): The category of the score to filter by.
            sent_after (Optional[datetime]): Filter for scores sent after this datetime.
            sent_before (Optional[datetime]): Filter for scores sent before this datetime.
            identifier_filters (Optional[Sequence[IdentifierFilter]]): A sequence of IdentifierFilter objects that
                allows filtering by various scorer identifier JSON properties. Defaults to None.

        Returns:
            Sequence[Score]: A list of Score objects that match the specified filters.
        """
        if score_ids is not None and len(score_ids) == 0:
            return []

        conditions: list[Any] = []

        if score_type:
            conditions.append(ScoreEntry.score_type == score_type)
        if score_category:
            conditions.append(ScoreEntry.score_category == score_category)
        if sent_after:
            conditions.append(ScoreEntry.timestamp >= sent_after)
        if sent_before:
            conditions.append(ScoreEntry.timestamp <= sent_before)
        if identifier_filters:
            conditions.extend(
                self._build_identifier_filter_conditions(
                    identifier_filters=identifier_filters,
                    identifier_column_map={IdentifierType.SCORER: ScoreEntry.scorer_class_identifier},
                    caller="get_scores",
                )
            )

        # Handle score_ids with batched queries if needed
        if score_ids:
            entries = self._execute_batched_query(
                ScoreEntry,
                batch_column=ScoreEntry.id,
                batch_values=list(score_ids),
                other_conditions=conditions,
            )
            return [entry.get_score() for entry in entries]

        # No score_ids specified - use regular query
        if not conditions:
            return []

        score_entries: Sequence[ScoreEntry] = self._query_entries(ScoreEntry, conditions=and_(*conditions))
        return [entry.get_score() for entry in score_entries]

    def get_prompt_scores(
        self,
        *,
        attack_id: Optional[str | uuid.UUID] = None,
        role: Optional[str] = None,
        conversation_id: Optional[str | uuid.UUID] = None,
        prompt_ids: Optional[Sequence[str | uuid.UUID]] = None,
        labels: Optional[dict[str, str]] = None,
        prompt_metadata: Optional[dict[str, Union[str, int]]] = None,
        sent_after: Optional[datetime] = None,
        sent_before: Optional[datetime] = None,
        original_values: Optional[Sequence[str]] = None,
        converted_values: Optional[Sequence[str]] = None,
        data_type: Optional[str] = None,
        not_data_type: Optional[str] = None,
        converted_value_sha256: Optional[Sequence[str]] = None,
    ) -> Sequence[Score]:
        """
        Retrieve scores attached to message pieces based on the specified filters.

        Args:
            attack_id (Optional[str | uuid.UUID], optional): The ID of the attack. Defaults to None.
            role (Optional[str], optional): The role of the prompt. Defaults to None.
            conversation_id (Optional[str | uuid.UUID], optional): The ID of the conversation. Defaults to None.
            prompt_ids (Optional[Sequence[str] | Sequence[uuid.UUID]], optional): A list of prompt IDs.
                Defaults to None.
            labels (Optional[dict[str, str]], optional): A dictionary of labels. Defaults to None.
            prompt_metadata (Optional[dict[str, Union[str, int]]], optional): The metadata associated with the prompt.
                Defaults to None.
            sent_after (Optional[datetime], optional): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (Optional[datetime], optional): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Optional[Sequence[str]], optional): A list of original values. Defaults to None.
            converted_values (Optional[Sequence[str]], optional): A list of converted values. Defaults to None.
            data_type (Optional[str], optional): The data type to filter by. Defaults to None.
            not_data_type (Optional[str], optional): The data type to exclude. Defaults to None.
            converted_value_sha256 (Optional[Sequence[str]], optional): A list of SHA256 hashes of converted values.
                Defaults to None.

        Returns:
            Sequence[Score]: A list of scores extracted from the message pieces.
        """
        message_pieces = self.get_message_pieces(
            attack_id=attack_id,
            role=role,
            conversation_id=conversation_id,
            prompt_ids=prompt_ids,
            labels=labels,
            prompt_metadata=prompt_metadata,
            sent_after=sent_after,
            sent_before=sent_before,
            original_values=original_values,
            converted_values=converted_values,
            data_type=data_type,
            not_data_type=not_data_type,
            converted_value_sha256=converted_value_sha256,
        )

        # Deduplicate message pieces by original_prompt_id to avoid duplicate scores
        # since duplicated pieces share scores with their originals
        seen_original_ids = set()
        unique_pieces = []
        for piece in message_pieces:
            if piece.original_prompt_id not in seen_original_ids:
                seen_original_ids.add(piece.original_prompt_id)
                unique_pieces.append(piece)

        scores = []
        for piece in unique_pieces:
            if piece.scores:
                scores.extend(piece.scores)

        return list(scores)

    def get_conversation(self, *, conversation_id: str) -> MutableSequence[Message]:
        """
        Retrieve a list of Message objects that have the specified conversation ID.

        Args:
            conversation_id (str): The conversation ID to match.

        Returns:
            MutableSequence[Message]: A list of chat memory entries with the specified conversation ID.
        """
        message_pieces = self.get_message_pieces(conversation_id=conversation_id)
        return group_conversation_message_pieces_by_sequence(message_pieces=message_pieces)

    def get_request_from_response(self, *, response: Message) -> Message:
        """
        Retrieve the request that produced the given response.

        Args:
            response (Message): The response message object to match.

        Returns:
            Message: The corresponding message object.

        Raises:
            ValueError: If the response is not from an assistant role or has no preceding request.
        """
        if response.api_role != "assistant":
            raise ValueError("The provided request is not a response (role must be 'assistant').")
        if response.sequence < 1:
            raise ValueError("The provided request does not have a preceding request (sequence < 1).")

        conversation = self.get_conversation(conversation_id=response.conversation_id)
        return conversation[response.sequence - 1]

    def get_message_pieces(
        self,
        *,
        attack_id: Optional[str | uuid.UUID] = None,
        role: Optional[str] = None,
        conversation_id: Optional[str | uuid.UUID] = None,
        prompt_ids: Optional[Sequence[str | uuid.UUID]] = None,
        labels: Optional[dict[str, str]] = None,
        prompt_metadata: Optional[dict[str, Union[str, int]]] = None,
        sent_after: Optional[datetime] = None,
        sent_before: Optional[datetime] = None,
        original_values: Optional[Sequence[str]] = None,
        converted_values: Optional[Sequence[str]] = None,
        data_type: Optional[str] = None,
        not_data_type: Optional[str] = None,
        converted_value_sha256: Optional[Sequence[str]] = None,
        identifier_filters: Optional[Sequence[IdentifierFilter]] = None,
    ) -> Sequence[MessagePiece]:
        """
        Retrieve a list of MessagePiece objects based on the specified filters.

        Args:
            attack_id (Optional[str | uuid.UUID], optional): The ID of the attack. Defaults to None.
            role (Optional[str], optional): The role of the prompt. Defaults to None.
            conversation_id (Optional[str | uuid.UUID], optional): The ID of the conversation. Defaults to None.
            prompt_ids (Optional[Sequence[str] | Sequence[uuid.UUID]], optional): A list of prompt IDs.
                Defaults to None.
            labels (Optional[dict[str, str]], optional): A dictionary of labels. Defaults to None.
            prompt_metadata (Optional[dict[str, Union[str, int]]], optional): The metadata associated with the prompt.
                Defaults to None.
            sent_after (Optional[datetime], optional): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (Optional[datetime], optional): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Optional[Sequence[str]], optional): A list of original values. Defaults to None.
            converted_values (Optional[Sequence[str]], optional): A list of converted values. Defaults to None.
            data_type (Optional[str], optional): The data type to filter by. Defaults to None.
            not_data_type (Optional[str], optional): The data type to exclude. Defaults to None.
            converted_value_sha256 (Optional[Sequence[str]], optional): A list of SHA256 hashes of converted values.
                Defaults to None.
            identifier_filters (Optional[Sequence[IdentifierFilter]], optional):
                A sequence of IdentifierFilter objects that
                allow filtering by various identifier JSON properties. Defaults to None.

        Returns:
            Sequence[MessagePiece]: A list of MessagePiece objects that match the specified filters.

        Raises:
            Exception: If there is an error retrieving the prompts,
                an exception is logged and an empty list is returned.
        """
        try:
            conditions: list[Any] = []
            if attack_id:
                conditions.append(
                    self._get_condition_json_property_match(
                        json_column=PromptMemoryEntry.attack_identifier,
                        property_path="$.hash",
                        value=str(attack_id),
                    )
                )
            if role:
                conditions.append(PromptMemoryEntry.role == role)
            if conversation_id:
                conditions.append(PromptMemoryEntry.conversation_id == str(conversation_id))
            if labels:
                conditions.extend(self._get_message_pieces_memory_label_conditions(memory_labels=labels))
            if prompt_metadata:
                conditions.extend(self._get_message_pieces_prompt_metadata_conditions(prompt_metadata=prompt_metadata))
            if sent_after:
                conditions.append(PromptMemoryEntry.timestamp >= sent_after)
            if sent_before:
                conditions.append(PromptMemoryEntry.timestamp <= sent_before)
            if data_type:
                conditions.append(PromptMemoryEntry.converted_value_data_type == data_type)
            if not_data_type:
                conditions.append(PromptMemoryEntry.converted_value_data_type != not_data_type)
            if identifier_filters:
                conditions.extend(
                    self._build_identifier_filter_conditions(
                        identifier_filters=identifier_filters,
                        identifier_column_map={
                            IdentifierType.ATTACK: PromptMemoryEntry.attack_identifier,
                            IdentifierType.TARGET: PromptMemoryEntry.prompt_target_identifier,
                            IdentifierType.CONVERTER: PromptMemoryEntry.converter_identifiers,
                        },
                        caller="get_message_pieces",
                    )
                )

            # Identify list parameters that may need batching
            list_params: list[tuple[InstrumentedAttribute[Any], Sequence[Any], str]] = []
            if prompt_ids:
                list_params.append((PromptMemoryEntry.id, [str(pi) for pi in prompt_ids], "id"))
            if original_values:
                list_params.append((PromptMemoryEntry.original_value, list(original_values), "original_value"))
            if converted_values:
                list_params.append((PromptMemoryEntry.converted_value, list(converted_values), "converted_value"))
            if converted_value_sha256:
                list_params.append(
                    (PromptMemoryEntry.converted_value_sha256, list(converted_value_sha256), "converted_value_sha256")
                )

            memory_entries = self._query_with_list_params(
                PromptMemoryEntry,
                conditions=conditions,
                list_params=list_params,
                join_scores=True,
            )
            message_pieces = [memory_entry.get_message_piece() for memory_entry in memory_entries]
            return sort_message_pieces(message_pieces=message_pieces)
        except Exception as e:
            logger.exception(f"Failed to retrieve prompts with error {e}")
            raise

    def duplicate_messages(self, *, messages: Sequence[Message]) -> tuple[str, Sequence[MessagePiece]]:
        """
        Duplicate messages with a new conversation ID.

        Each duplicated piece gets a fresh ``id`` and ``timestamp`` while
        preserving ``original_prompt_id`` for tracking lineage.

        Args:
            messages: The messages to duplicate.

        Returns:
            Tuple of (new_conversation_id, duplicated_message_pieces).
        """
        new_conversation_id = str(uuid.uuid4())

        all_pieces: list[MessagePiece] = []
        for message in messages:
            duplicated_message = message.duplicate_message()

            for piece in duplicated_message.message_pieces:
                piece.conversation_id = new_conversation_id

            all_pieces.extend(duplicated_message.message_pieces)

        return new_conversation_id, all_pieces

    def duplicate_conversation(self, *, conversation_id: str) -> str:
        """
        Duplicate a conversation for reuse.

        This can be useful when an attack strategy requires branching out from a particular point in the conversation.
        One cannot continue both branches with the same conversation ID since that would corrupt
        the memory. Instead, one needs to duplicate the conversation and continue with the new conversation ID.

        Args:
            conversation_id (str): The conversation ID with existing conversations.

        Returns:
            The uuid for the new conversation.
        """
        messages = self.get_conversation(conversation_id=conversation_id)
        new_conversation_id, all_pieces = self.duplicate_messages(messages=messages)
        self.add_message_pieces_to_memory(message_pieces=all_pieces)
        return new_conversation_id

    def duplicate_conversation_excluding_last_turn(self, *, conversation_id: str) -> str:
        """
        Duplicate a conversation, excluding the last turn. In this case, last turn is defined as before the last
        user request (e.g. if there is half a turn, it just removes that half).

        This can be useful when an attack strategy requires back tracking the last prompt/response pair.

        Args:
            conversation_id (str): The conversation ID with existing conversations.

        Returns:
            The uuid for the new conversation.
        """
        messages = self.get_conversation(conversation_id=conversation_id)

        # remove the final turn from the conversation
        if len(messages) == 0:
            return str(uuid.uuid4())

        last_message = messages[-1]

        length_of_sequence_to_remove = 0

        length_of_sequence_to_remove = 1 if last_message.api_role == "system" or last_message.api_role == "user" else 2

        messages_to_duplicate = [
            message for message in messages if message.sequence <= last_message.sequence - length_of_sequence_to_remove
        ]

        new_conversation_id, all_pieces = self.duplicate_messages(messages=messages_to_duplicate)
        self.add_message_pieces_to_memory(message_pieces=all_pieces)

        return new_conversation_id

    def add_message_to_memory(self, *, request: Message) -> None:
        """
        Insert a list of message pieces into the memory storage.

        Automatically updates the sequence to be the next number in the conversation.
        If necessary, generates embedding data for applicable entries

        Args:
            request (MessagePiece): The message piece to add to the memory.
        """
        request.validate()

        embedding_entries = []
        message_pieces = request.message_pieces

        self._update_sequence(message_pieces=message_pieces)

        self.add_message_pieces_to_memory(message_pieces=message_pieces)

        if self.memory_embedding:
            for piece in message_pieces:
                embedding_entry = self.memory_embedding.generate_embedding_memory_data(message_piece=piece)
                embedding_entries.append(embedding_entry)

            self._add_embeddings_to_memory(embedding_data=embedding_entries)

    def _update_sequence(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Update the sequence number of the message pieces in the conversation.

        Args:
            message_pieces (Sequence[MessagePiece]): The list of message pieces to update.
        """
        prev_conversations = self.get_message_pieces(conversation_id=message_pieces[0].conversation_id)

        sequence = 0

        if len(prev_conversations) > 0:
            sequence = max(prev_conversations, key=lambda item: item.sequence).sequence + 1

        for piece in message_pieces:
            piece.sequence = sequence

    def update_prompt_entries_by_conversation_id(self, *, conversation_id: str, update_fields: dict[str, Any]) -> bool:
        """
        Update prompt entries for a given conversation ID with the specified field values.

        Args:
            conversation_id (str): The conversation ID of the entries to be updated.
            update_fields (dict): A dictionary of field names and their new values (ex. {"labels": {"test": "value"}})

        Returns:
            bool: True if the update was successful, False otherwise.

        Raises:
            ValueError: If update_fields is empty or not provided.
        """
        if not update_fields:
            raise ValueError("update_fields must be provided to update prompt entries.")
        # Fetch the relevant entries using query_entries
        entries_to_update: MutableSequence[Base] = self._query_entries(
            PromptMemoryEntry, conditions=PromptMemoryEntry.conversation_id == conversation_id
        )
        # Check if there are entries to update
        if not entries_to_update:
            logger.info(f"No entries found with conversation_id {conversation_id} to update.")
            return False

        # Use the utility function to update the entries
        success = self._update_entries(entries=entries_to_update, update_fields=update_fields)

        if success:
            logger.info(f"Updated {len(entries_to_update)} entries with conversation_id {conversation_id}.")
        else:
            logger.error(f"Failed to update entries with conversation_id {conversation_id}.")
        return success

    def update_labels_by_conversation_id(self, *, conversation_id: str, labels: dict[str, Any]) -> bool:
        """
        Update the labels of prompt entries in memory for a given conversation ID.

        Args:
            conversation_id (str): The conversation ID of the entries to be updated.
            labels (dict): New dictionary of labels.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        return self.update_prompt_entries_by_conversation_id(
            conversation_id=conversation_id, update_fields={"labels": labels}
        )

    def update_prompt_metadata_by_conversation_id(
        self, *, conversation_id: str, prompt_metadata: dict[str, Union[str, int]]
    ) -> bool:
        """
        Update the metadata of prompt entries in memory for a given conversation ID.

        Args:
            conversation_id (str): The conversation ID of the entries to be updated.
            prompt_metadata (dict[str, str | int]): New metadata.

        Returns:
            bool: True if the update was successful, False otherwise.
        """
        return self.update_prompt_entries_by_conversation_id(
            conversation_id=conversation_id, update_fields={"prompt_metadata": prompt_metadata}
        )

    def _run_schema_migration(self) -> None:
        """
        Run schema migrations to ensure the database schema is up to date.

        Raises:
            ValueError: If an invalid schema handling option is provided.
            RuntimeError: If the engine is not initialized when required.
            Exception: If there is an error during schema validation or migration.
        """
        from pyrit.memory.migration import check_schema_migrations, run_schema_migrations

        logger.info("Running schema migration.")
        if self.engine is None:
            raise RuntimeError("Engine must be initialized to run schema migrations.")
        run_schema_migrations(engine=self.engine)
        check_schema_migrations(engine=self.engine)

    def reset_database(self) -> None:
        """
        Drop and recreate all tables in the database.

        Raises:
            RuntimeError: If the engine is not initialized.
        """
        from pyrit.memory.migration import reset_database

        if self.engine is None:
            raise RuntimeError("Engine is not initialized")
        reset_database(engine=self.engine)

    @abc.abstractmethod
    def dispose_engine(self) -> None:
        """
        Dispose the engine and clean up resources.
        """

    def cleanup(self) -> None:
        """
        Ensure cleanup on process exit.
        """
        # Ensure cleanup at process exit
        atexit.register(self.dispose_engine)

        # Ensure cleanup happens even if the object is garbage collected before process exits
        weakref.finalize(self, self.dispose_engine)

    def get_seeds(
        self,
        *,
        value: Optional[str] = None,
        value_sha256: Optional[Sequence[str]] = None,
        dataset_name: Optional[str] = None,
        dataset_name_pattern: Optional[str] = None,
        data_types: Optional[Sequence[str]] = None,
        harm_categories: Optional[Sequence[str]] = None,
        added_by: Optional[str] = None,
        authors: Optional[Sequence[str]] = None,
        groups: Optional[Sequence[str]] = None,
        source: Optional[str] = None,
        seed_type: Optional[SeedType] = None,
        parameters: Optional[Sequence[str]] = None,
        metadata: Optional[dict[str, Union[str, int]]] = None,
        prompt_group_ids: Optional[Sequence[uuid.UUID]] = None,
    ) -> Sequence[Seed]:
        """
        Retrieve a list of seed prompts based on the specified filters.

        Args:
            value (str): The value to match by substring. If None, all values are returned.
            value_sha256 (str): The SHA256 hash of the value to match. If None, all values are returned.
            dataset_name (str): The dataset name to match exactly. If None, all dataset names are considered.
            dataset_name_pattern (str): A pattern to match dataset names using SQL LIKE syntax.
                Supports wildcards: % (any characters) and _ (single character).
                Examples: "harm%" matches names starting with "harm", "%test%" matches names containing "test".
                If both dataset_name and dataset_name_pattern are provided, dataset_name takes precedence.
            data_types (Optional[Sequence[str], Optional): List of data types to filter seed prompts by
                (e.g., text, image_path).
            harm_categories (Sequence[str]): A list of harm categories to filter by. If None,
            all harm categories are considered.
                Specifying multiple harm categories returns only prompts that are marked with all harm categories.
            added_by (str): The user who added the prompts.
            authors (Sequence[str]): A list of authors to filter by.
                Note that this filters by substring, so a query for "Adam Jones" may not return results if the record
                is "A. Jones", "Jones, Adam", etc. If None, all authors are considered.
            groups (Sequence[str]): A list of groups to filter by. If None, all groups are considered.
            source (str): The source to filter by. If None, all sources are considered.
            seed_type (SeedType): The type of seed to filter by ("prompt", "objective", or
                "simulated_conversation").
            parameters (Sequence[str]): A list of parameters to filter by. Specifying parameters effectively returns
                prompt templates instead of prompts.
            metadata (dict[str, str | int]): A free-form dictionary for tagging prompts with custom metadata.
            prompt_group_ids (Sequence[uuid.UUID]): A list of prompt group IDs to filter by.

        Returns:
            Sequence[SeedPrompt]: A list of prompts matching the criteria.
        """
        conditions = []

        # Apply filters for non-list fields
        if value:
            conditions.append(SeedEntry.value.contains(value))
        if value_sha256:
            conditions.append(SeedEntry.value_sha256.in_(value_sha256))
        if dataset_name:
            conditions.append(SeedEntry.dataset_name == dataset_name)
        elif dataset_name_pattern:
            conditions.append(SeedEntry.dataset_name.like(dataset_name_pattern))
        if prompt_group_ids:
            conditions.append(SeedEntry.prompt_group_id.in_(prompt_group_ids))
        if data_types:
            data_type_conditions = SeedEntry.data_type.in_(data_types)
            conditions.append(data_type_conditions)
        if added_by:
            conditions.append(SeedEntry.added_by == added_by)
        if source:
            conditions.append(SeedEntry.source == source)

        # Handle seed_type filtering
        if seed_type == "objective":
            conditions.append(SeedEntry.seed_type == "objective")
        elif seed_type is not None:
            conditions.append(SeedEntry.seed_type == seed_type)

        self._add_list_conditions(field=SeedEntry.harm_categories, values=harm_categories, conditions=conditions)
        self._add_list_conditions(field=SeedEntry.authors, values=authors, conditions=conditions)
        self._add_list_conditions(field=SeedEntry.groups, values=groups, conditions=conditions)

        if parameters:
            self._add_list_conditions(field=SeedEntry.parameters, values=parameters, conditions=conditions)

        if metadata:
            conditions.append(self._get_seed_metadata_conditions(metadata=metadata))

        try:
            memory_entries: Sequence[SeedEntry] = self._query_entries(
                SeedEntry,
                conditions=and_(*conditions) if conditions else None,
            )
            return [memory_entry.get_seed() for memory_entry in memory_entries]
        except Exception as e:
            logger.exception(f"Failed to retrieve prompts with dataset name {dataset_name} with error {e}")
            raise

    def _add_list_conditions(
        self, field: InstrumentedAttribute[Any], conditions: list[Any], values: Optional[Sequence[str]] = None
    ) -> None:
        if values:
            conditions.extend(field.contains(value) for value in values)

    async def _serialize_seed_value(self, prompt: Seed) -> str:
        """
        Serialize the value of a seed prompt based on its data type.

        Args:
            prompt (Seed): The seed prompt to serialize. Must have a valid `data_type`.

        Returns:
            str: The serialized value for the prompt.

        Raises:
            ValueError: If the `data_type` of the prompt is unsupported.
        """
        extension = DataTypeSerializer.get_extension(prompt.value)
        if extension:
            extension = extension.lstrip(".")
        serializer = data_serializer_factory(
            category="seed-prompt-entries", data_type=prompt.data_type, value=prompt.value, extension=extension
        )
        serialized_prompt_value = None
        if prompt.data_type == "image_path":
            # Read the image
            original_img_bytes = await serializer.read_data_base64()
            # Save the image
            await serializer.save_b64_image(original_img_bytes)
            serialized_prompt_value = str(serializer.value)
        elif prompt.data_type in ["audio_path", "video_path"]:
            audio_bytes = await serializer.read_data()
            await serializer.save_data(data=audio_bytes)
            serialized_prompt_value = str(serializer.value)
        return serialized_prompt_value or ""

    async def add_seeds_to_memory_async(self, *, seeds: Sequence[Seed], added_by: Optional[str] = None) -> None:
        """
        Insert a list of seeds into the memory storage.

        Args:
            seeds (Sequence[Seed]): A list of seeds to insert.
            added_by (str): The user who added the seeds.

        Raises:
            ValueError: If the 'added_by' attribute is not set for each prompt.
        """
        entries: MutableSequence[SeedEntry] = []
        current_time = datetime.now(tz=timezone.utc)
        for prompt in seeds:
            if added_by:
                prompt.added_by = added_by
            if not prompt.added_by:
                raise ValueError(
                    """The 'added_by' attribute must be set for each prompt.
                    Set it explicitly or pass a value to the 'added_by' parameter."""
                )
            if prompt.date_added is None:
                prompt.date_added = current_time

            # Only SeedPrompt has set_encoding_metadata for audio/video/image files
            if hasattr(prompt, "set_encoding_metadata"):
                prompt.set_encoding_metadata()  # type: ignore[ty:call-non-callable]

            # Handle serialization for image, audio & video SeedPrompts
            if prompt.data_type in ["image_path", "audio_path", "video_path"]:
                serialized_prompt_value = await self._serialize_seed_value(prompt=prompt)
                prompt.value = serialized_prompt_value

            await prompt.set_sha256_value_async()

            if prompt.value_sha256 and not self.get_seeds(
                value_sha256=[prompt.value_sha256], dataset_name=prompt.dataset_name
            ):
                entries.append(SeedEntry(entry=prompt))

        self._insert_entries(entries=entries)

    async def add_seed_datasets_to_memory_async(self, *, datasets: Sequence[SeedDataset], added_by: str) -> None:
        """
        Insert a list of seed datasets into the memory storage.

        Args:
            datasets (Sequence[SeedDataset]): A list of seed datasets to insert.
            added_by (str): The user who added the datasets.
        """
        for dataset in datasets:
            await self.add_seeds_to_memory_async(seeds=dataset.seeds, added_by=added_by)

    def get_seed_dataset_names(self) -> Sequence[str]:
        """
        Return a list of all seed dataset names in the memory storage.

        Returns:
            Sequence[str]: A list of unique dataset names.
        """
        try:
            entries: Sequence[SeedEntry] = self._query_entries(
                SeedEntry,
                conditions=and_(SeedEntry.dataset_name.isnot(None), SeedEntry.dataset_name != ""),
                distinct=True,
            )
            # Extract unique dataset names from the entries
            dataset_names = set()
            for entry in entries:
                if entry.dataset_name:
                    dataset_names.add(entry.dataset_name)
            return list(dataset_names)
        except Exception as e:
            logger.exception(f"Failed to retrieve dataset names with error {e}")
            raise

    async def add_seed_groups_to_memory_async(
        self, *, prompt_groups: Sequence[SeedGroup], added_by: Optional[str] = None
    ) -> None:
        """
        Insert a list of seed groups into the memory storage.

        Args:
            prompt_groups (Sequence[SeedGroup]): A list of prompt groups to insert.
            added_by (str): The user who added the prompt groups.

        Raises:
            ValueError: If a seed group does not have at least one seed.
            ValueError: If seed group IDs are inconsistent within the same seed group.
        """
        if not prompt_groups:
            raise ValueError("At least one prompt group must be provided.")
        # Validates the prompt group IDs and sets them if possible before leveraging
        # the add_seeds_to_memory_async method.
        all_seeds: MutableSequence[Seed] = []
        for prompt_group in prompt_groups:
            if not prompt_group.seeds:
                raise ValueError("Seed group must have at least one seed.")
            # Determine the prompt group ID.
            # It should either be set uniformly or generated if not set.
            # Inconsistent prompt group IDs will raise an error.
            group_id_set = {seed.prompt_group_id for seed in prompt_group.seeds}
            if len(group_id_set) > 1:
                raise ValueError(
                    f"""Inconsistent 'prompt_group_id' attribute between members of the
                    same seed group. Found {group_id_set}"""
                )
            prompt_group_id = group_id_set.pop() or uuid.uuid4()
            for seed in prompt_group.seeds:
                seed.prompt_group_id = prompt_group_id

            all_seeds.extend(prompt_group.seeds)
        await self.add_seeds_to_memory_async(seeds=all_seeds, added_by=added_by)

    def get_seed_groups(
        self,
        *,
        value: Optional[str] = None,
        value_sha256: Optional[Sequence[str]] = None,
        dataset_name: Optional[str] = None,
        dataset_name_pattern: Optional[str] = None,
        data_types: Optional[Sequence[str]] = None,
        harm_categories: Optional[Sequence[str]] = None,
        added_by: Optional[str] = None,
        authors: Optional[Sequence[str]] = None,
        groups: Optional[Sequence[str]] = None,
        source: Optional[str] = None,
        seed_type: Optional[SeedType] = None,
        parameters: Optional[Sequence[str]] = None,
        metadata: Optional[dict[str, Union[str, int]]] = None,
        prompt_group_ids: Optional[Sequence[uuid.UUID]] = None,
        group_length: Optional[Sequence[int]] = None,
    ) -> Sequence[SeedGroup]:
        """
        Retrieve groups of seed prompts based on the provided filtering criteria.

        Args:
            value (Optional[str], Optional): The value to match by substring.
            value_sha256 (Optional[Sequence[str]], Optional): SHA256 hash of value to filter seed groups by.
            dataset_name (Optional[str], Optional): Name of the dataset to match exactly.
            dataset_name_pattern (Optional[str], Optional): A pattern to match dataset names using SQL LIKE syntax.
                Supports wildcards: % (any characters) and _ (single character).
                Examples: "harm%" matches names starting with "harm", "%test%" matches names containing "test".
                If both dataset_name and dataset_name_pattern are provided, dataset_name takes precedence.
            data_types (Optional[Sequence[str]], Optional): List of data types to filter seed prompts by
            (e.g., text, image_path).
            harm_categories (Optional[Sequence[str]], Optional): List of harm categories to filter seed prompts by.
            added_by (Optional[str], Optional): The user who added the seed groups to filter by.
            authors (Optional[Sequence[str]], Optional): List of authors to filter seed groups by.
            groups (Optional[Sequence[str]], Optional): List of groups to filter seed groups by.
            source (Optional[str], Optional): The source from which the seed prompts originated.
            seed_type (Optional[SeedType], Optional): The type of seed to filter by ("prompt", "objective", or
                "simulated_conversation").
            parameters (Optional[Sequence[str]], Optional): List of parameters to filter by.
            metadata (Optional[dict[str, Union[str, int]]], Optional): A free-form dictionary for tagging
                prompts with custom metadata.
            prompt_group_ids (Optional[Sequence[uuid.UUID]], Optional): List of prompt group IDs to filter by.
            group_length (Optional[Sequence[int]], Optional): The number of seeds in the group to filter by.

        Returns:
            Sequence[SeedGroup]: A list of `SeedGroup` objects that match the filtering criteria.
        """
        seeds = self.get_seeds(
            value=value,
            value_sha256=value_sha256,
            dataset_name=dataset_name,
            dataset_name_pattern=dataset_name_pattern,
            data_types=data_types,
            harm_categories=harm_categories,
            added_by=added_by,
            authors=authors,
            groups=groups,
            source=source,
            seed_type=seed_type,
            parameters=parameters,
            metadata=metadata,
            prompt_group_ids=prompt_group_ids,
        )

        # If we have filtered seeds, we want to get all seeds in the same group
        # This allows us to filter by one modality (e.g. audio) and get the whole group (e.g. audio + text)
        if seeds:
            related_prompt_group_ids = {seed.prompt_group_id for seed in seeds if seed.prompt_group_id}
            if related_prompt_group_ids:
                seeds = self.get_seeds(prompt_group_ids=list(related_prompt_group_ids))

        # Deduplicate seeds to ensure we don't have duplicate prompts in the groups
        if seeds:
            seeds = list({seed.id: seed for seed in seeds}.values())

        seed_groups = SeedDataset.group_seed_prompts_by_prompt_group_id(seeds)

        if group_length:
            seed_groups = [group for group in seed_groups if len(group.seeds) in group_length]

        return seed_groups

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
        Export conversation data with the given inputs to a specified file.
            Defaults to all conversations if no filters are provided.

        Args:
            attack_id (Optional[str | uuid.UUID], optional): The ID of the attack. Defaults to None.
            conversation_id (Optional[str | uuid.UUID], optional): The ID of the conversation. Defaults to None.
            prompt_ids (Optional[Sequence[str] | Sequence[uuid.UUID]], optional): A list of prompt IDs.
                Defaults to None.
            labels (Optional[dict[str, str]], optional): A dictionary of labels. Defaults to None.
            sent_after (Optional[datetime], optional): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (Optional[datetime], optional): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Optional[Sequence[str]], optional): A list of original values. Defaults to None.
            converted_values (Optional[Sequence[str]], optional): A list of converted values. Defaults to None.
            data_type (Optional[str], optional): The data type to filter by. Defaults to None.
            not_data_type (Optional[str], optional): The data type to exclude. Defaults to None.
            converted_value_sha256 (Optional[Sequence[str]], optional): A list of SHA256 hashes of converted values.
                Defaults to None.
            file_path (Optional[Path], optional): The path to the file where the data will be exported.
                Defaults to None.
            export_type (str, optional): The format of the export. Defaults to "json".

        Returns:
            Path: The path to the exported file.
        """
        data = self.get_message_pieces(
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

        # If file_path is not provided, construct a default using the exporter's results_path
        if not file_path:
            file_name = f"exported_conversations_on_{datetime.now(tz=timezone.utc).strftime('%Y_%m_%d')}.{export_type}"
            file_path = DB_DATA_PATH / file_name

        self.exporter.export_data(list(data), file_path=file_path, export_type=export_type)

        return file_path

    def add_attack_results_to_memory(self, *, attack_results: Sequence[AttackResult]) -> None:
        """
        Insert a list of attack results into the memory storage.
        The database model automatically calculates objective_sha256 for consistency.

        Raises:
            SQLAlchemyError: If the database transaction fails.
        """
        entries = [AttackResultEntry(entry=attack_result) for attack_result in attack_results]
        with closing(self.get_session()) as session:
            try:
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise

    def update_attack_result(self, *, conversation_id: str, update_fields: dict[str, Any]) -> bool:
        """
        Update specific fields of an existing AttackResultEntry identified by conversation_id.

        This method queries for the raw database entry by conversation_id and updates
        the specified fields in place, avoiding the creation of duplicate rows.

        Args:
            conversation_id (str): The conversation ID of the attack result to update.
            update_fields (dict[str, Any]): A dictionary of column names to new values.
                Valid fields include 'adversarial_chat_conversation_ids',
                'pruned_conversation_ids', 'outcome', 'attack_metadata', etc.

        Returns:
            bool: True if the update was successful, False if the entry was not found.

        Raises:
            ValueError: If update_fields is empty.
        """
        if not update_fields:
            raise ValueError("update_fields must not be empty")

        entries: MutableSequence[AttackResultEntry] = self._query_entries(
            AttackResultEntry,
            conditions=AttackResultEntry.conversation_id == conversation_id,
        )
        if not entries:
            return False

        # When duplicate rows exist for the same conversation_id (legacy bug),
        # pick the newest entry — it has the most up-to-date data.
        target_entry = max(entries, key=lambda e: e.timestamp)
        self._update_entries(entries=[target_entry], update_fields=update_fields)
        return True

    def update_attack_result_by_id(self, *, attack_result_id: str, update_fields: dict[str, Any]) -> bool:
        """
        Update specific fields of an existing AttackResultEntry identified by its primary key.

        Args:
            attack_result_id: The UUID primary key of the AttackResultEntry.
            update_fields: Column names to new values.

        Returns:
            True if the update was successful, False if the entry was not found.
        """
        try:
            attack_result_uuid = uuid.UUID(attack_result_id)
        except (ValueError, TypeError):
            logger.warning(
                "Invalid attack_result_id '%s' passed to update_attack_result_by_id",
                attack_result_id,
            )
            return False

        entries: MutableSequence[AttackResultEntry] = self._query_entries(
            AttackResultEntry,
            conditions=AttackResultEntry.id == attack_result_uuid,
        )
        if not entries:
            return False
        self._update_entries(entries=[entries[0]], update_fields=update_fields)
        return True

    def get_attack_results(
        self,
        *,
        attack_result_ids: Optional[Sequence[str]] = None,
        conversation_id: Optional[str] = None,
        objective: Optional[str] = None,
        objective_sha256: Optional[Sequence[str]] = None,
        outcome: Optional[str] = None,
        attack_class: Optional[str] = None,
        attack_classes: Optional[Sequence[str]] = None,
        converter_classes: Optional[Sequence[str]] = None,
        converter_classes_match: Literal["all", "any"] = "all",
        has_converters: Optional[bool] = None,
        targeted_harm_categories: Optional[Sequence[str]] = None,
        labels: Optional[dict[str, str | Sequence[str]]] = None,
        identifier_filters: Optional[Sequence[IdentifierFilter]] = None,
    ) -> Sequence[AttackResult]:
        """
        Retrieve a list of AttackResult objects based on the specified filters.

        Args:
            attack_result_ids (Optional[Sequence[str]], optional): A list of attack result IDs. Defaults to None.
            conversation_id (Optional[str], optional): The conversation ID to filter by. Defaults to None.
            objective (Optional[str], optional): The objective to filter by (substring match). Defaults to None.
            objective_sha256 (Optional[Sequence[str]], optional): A list of objective SHA256 hashes to filter by.
                Defaults to None.
            outcome (Optional[str], optional): The outcome to filter by (success, failure, undetermined).
                Defaults to None.
            attack_class (Optional[str], optional): Deprecated. Filter by a single exact attack
                class_name in attack_identifier. Equivalent to passing ``attack_classes=[attack_class]``.
                Cannot be combined with ``attack_classes``. Defaults to None.
            attack_classes (Optional[Sequence[str]], optional): Filter by exact attack class_name in
                attack_identifier. Returns attacks matching ANY of the listed class names (OR logic,
                case-sensitive). An empty sequence applies no filter. Defaults to None.
            converter_classes (Optional[Sequence[str]], optional): Filter by converter class names.
                Combination semantics for multiple entries are controlled by ``converter_classes_match``.
                An empty sequence filters to attacks that used no converters; ``None`` applies no
                filter. To filter by presence/absence of any converter explicitly, use the
                ``has_converters`` parameter instead. Defaults to None.
            converter_classes_match (Literal["all", "any"]): How to combine multiple entries in
                ``converter_classes``. ``"all"`` (default) matches attacks that used every listed
                converter (AND, case-insensitive). ``"any"`` matches attacks that used at least one
                listed converter (OR, case-insensitive). Ignored when ``converter_classes`` has
                fewer than 2 entries or is empty.
            has_converters (Optional[bool], optional): Filter by converter presence.
                ``True`` returns only attacks that used at least one converter. ``False`` returns
                only attacks that used no converters. ``None`` applies no filter. Defaults to None.
            targeted_harm_categories (Optional[Sequence[str]], optional):
                A list of targeted harm categories to filter results by.
                These targeted harm categories are associated with the prompts themselves,
                meaning they are harm(s) we're trying to elicit with the prompt,
                not necessarily one(s) that were found in the response.
                By providing a list, this means ALL categories in the list must be present.
                Defaults to None.
            labels (Optional[dict[str, str | Sequence[str]]], optional): Filter results
                by attack labels. Entries are AND-combined across label names; within a
                single entry, a string value is an equality match and a sequence value is
                an OR match over the listed values. An empty sequence applies no filter
                for that label. Example: ``{"operator": "roakey", "operation":
                ["roakey_op_a", "roakey_op_b"]}`` matches attacks where ``operator ==
                "roakey"`` AND (``operation == "roakey_op_a"`` OR ``operation ==
                "roakey_op_b"``). Defaults to None.
            identifier_filters (Optional[Sequence[IdentifierFilter]], optional):
                A sequence of IdentifierFilter objects that allows filtering by various attack identifier
                JSON properties. Defaults to None.

        Returns:
            Sequence[AttackResult]: A list of AttackResult objects that match the specified filters.

        Raises:
            ValueError: If both ``attack_class`` (deprecated) and ``attack_classes`` are provided.
        """
        # Handle empty list cases
        if attack_result_ids is not None and len(attack_result_ids) == 0:
            return []
        if objective_sha256 is not None and len(objective_sha256) == 0:
            return []

        if attack_class is not None and attack_classes is not None:
            raise ValueError(
                "Pass either `attack_class` (deprecated, singular) or `attack_classes` (plural), not both."
            )
        if attack_class is not None and attack_classes is None:
            print_deprecation_message(
                old_item="get_attack_results(attack_class=...)",
                new_item="get_attack_results(attack_classes=...)",
                removed_in="0.15.0",
            )
            attack_classes = [attack_class]

        # Build non-list conditions
        conditions: list[ColumnElement[bool]] = []
        if conversation_id:
            conditions.append(AttackResultEntry.conversation_id == conversation_id)
        if objective:
            conditions.append(AttackResultEntry.objective.contains(objective))
        if outcome:
            conditions.append(AttackResultEntry.outcome == outcome)

        if attack_classes:
            # Case-insensitive to mirror converter_classes; forgives casing drift in
            # REST/CLI callers. PyRIT class names are PascalCase with no case-variant
            # collisions so this never changes match results for well-formed inputs.
            conditions.append(
                or_(
                    *[
                        self._get_condition_json_property_match(
                            json_column=AttackResultEntry.atomic_attack_identifier,
                            property_path="$.children.attack_technique.children.attack.class_name",
                            value=ac,
                        )
                        for ac in attack_classes
                    ]
                )
            )

        if converter_classes is not None:
            # Non-empty sequence: filter to attacks that used ALL (or ANY, depending on
            # converter_classes_match) of the listed converters.
            # Empty sequence: filter to attacks that used NO converters.
            # None: no filter.
            conditions.append(
                self._get_condition_json_array_match(
                    json_column=AttackResultEntry.atomic_attack_identifier,
                    property_path="$.children.attack_technique.children.attack.children.request_converters",
                    array_element_path="$.class_name",
                    array_to_match=converter_classes,
                    match_mode=converter_classes_match,
                )
            )

        # Skip when has_converters=True and converter_classes is already non-empty:
        # the "ALL listed converters" constraint strictly implies "at least one
        # converter", so adding this predicate is redundant work.
        if has_converters is not None and not (has_converters is True and converter_classes):
            # Reuse the array-empty match (array_to_match=[]) as the "no converters"
            # condition; invert it for "has at least one converter".
            empty_condition = self._get_condition_json_array_match(
                json_column=AttackResultEntry.atomic_attack_identifier,
                property_path="$.children.attack_technique.children.attack.children.request_converters",
                array_element_path="$.class_name",
                array_to_match=[],
            )
            conditions.append(not_(empty_condition) if has_converters else empty_condition)

        if targeted_harm_categories:
            print_deprecation_message(
                old_item="get_attack_results(targeted_harm_categories=...)",
                new_item="get_attack_results(labels={'harm_category': [...]})",
                removed_in="0.15.0",
            )
            conditions.append(
                self._get_attack_result_harm_category_condition(targeted_harm_categories=targeted_harm_categories)
            )
        if labels:
            # Strip keys whose value is an empty sequence — an empty sequence means
            # "no OR-candidates", and per the docstring applies no filter for that
            # key. Without this, the per-backend helpers would still emit a base
            # EXISTS(... labels IS NOT NULL) predicate that is strictly more
            # restrictive than "no filter".
            effective_labels = {k: v for k, v in labels.items() if not (isinstance(v, (list, tuple)) and len(v) == 0)}
            # Validate label keys against an allowlist: backend helpers
            # interpolate keys into JSON path expressions (e.g. ``$.key``),
            # so a key with quotes or SQL punctuation could otherwise break
            # out and inject SQL.
            invalid_keys = [k for k in effective_labels if not _LABEL_KEY_PATTERN.match(k)]
            if invalid_keys:
                raise ValueError(
                    f"Invalid label key(s) {invalid_keys!r}: keys must match {_LABEL_KEY_PATTERN.pattern}."
                )
            if effective_labels:
                # Use database-specific JSON query method
                conditions.append(self._get_attack_result_label_condition(labels=effective_labels))

        if identifier_filters:
            conditions.extend(
                self._build_identifier_filter_conditions(
                    identifier_filters=identifier_filters,
                    identifier_column_map={IdentifierType.ATTACK: AttackResultEntry.atomic_attack_identifier},
                    caller="get_attack_results",
                )
            )

        try:
            list_params: list[tuple[InstrumentedAttribute[Any], Sequence[Any], str]] = []
            if attack_result_ids:
                list_params.append((AttackResultEntry.id, list(attack_result_ids), "id"))
            if objective_sha256:
                list_params.append((AttackResultEntry.objective_sha256, list(objective_sha256), "objective_sha256"))

            entries = self._query_with_list_params(
                AttackResultEntry,
                conditions=conditions,
                list_params=list_params,
            )
            return self._dedup_attack_entries(entries)
        except Exception as e:
            logger.exception(f"Failed to retrieve attack results with error {e}")
            raise

    @staticmethod
    def _dedup_attack_entries(entries: Sequence[AttackResultEntry]) -> list[AttackResult]:
        """
        Deduplicate AttackResultEntry rows by conversation_id and convert to AttackResult.

        When duplicate rows exist (legacy bug), keeps only the newest entry per conversation_id.

        Returns:
            list[AttackResult]: Deduplicated attack results.
        """
        seen: dict[str, AttackResultEntry] = {}
        for entry in entries:
            prev = seen.get(entry.conversation_id)
            if prev is None or entry.timestamp > prev.timestamp:
                seen[entry.conversation_id] = entry
        return [entry.get_attack_result() for entry in seen.values()]

    def get_unique_attack_labels(self) -> dict[str, list[str]]:
        """
        Return all unique label key-value pairs across attack results.

        Labels may live on ``PromptMemoryEntry.labels`` (joined via
        conversation_id) **or** directly on ``AttackResultEntry.labels``.
        Both sources are queried (OR logic, mirroring the label filter
        behaviour in ``get_attack_results``), and unique key-value pairs
        are aggregated in Python.

        Returns:
            dict[str, list[str]]: Mapping of label keys to sorted lists of
            unique values.
        """
        label_values: dict[str, set[str]] = {}

        with closing(self.get_session()) as session:
            # Labels from PromptMemoryEntry linked to an attack
            pme_rows = (
                session.query(PromptMemoryEntry.labels)
                .join(
                    AttackResultEntry,
                    PromptMemoryEntry.conversation_id == AttackResultEntry.conversation_id,
                )
                .filter(PromptMemoryEntry.labels.isnot(None))
                .distinct()
                .all()
            )

            # Labels directly on AttackResultEntry
            are_rows = (
                session.query(AttackResultEntry.labels).filter(AttackResultEntry.labels.isnot(None)).distinct().all()
            )

        for (labels,) in (*pme_rows, *are_rows):
            if not isinstance(labels, dict):
                continue
            for key, value in labels.items():
                if isinstance(value, str):
                    if key not in label_values:
                        label_values[key] = set()
                    label_values[key].add(value)

        return {key: sorted(values) for key, values in sorted(label_values.items())}

    def add_scenario_results_to_memory(self, *, scenario_results: Sequence[ScenarioResult]) -> None:
        """
        Insert a list of scenario results into the memory storage.

        Args:
            scenario_results: Sequence of ScenarioResult objects to store in the database.
        """
        self._insert_entries(
            entries=[ScenarioResultEntry(entry=scenario_result) for scenario_result in scenario_results]
        )

    def add_attack_results_to_scenario(
        self,
        *,
        scenario_result_id: str,
        atomic_attack_name: str,
        attack_results: Sequence[AttackResult],
    ) -> bool:
        """
        Add attack results to an existing scenario result in memory.

        This method efficiently updates a scenario result by appending new attack results
        to a specific atomic attack name without requiring a full retrieve-modify-save cycle.

        Args:
            scenario_result_id (str): The ID of the scenario result to update.
            atomic_attack_name (str): The name of the atomic attack to add results for.
            attack_results (Sequence[AttackResult]): The attack results to add.

        Returns:
            bool: True if the update was successful, False otherwise.

        Example:
            >>> memory.add_attack_results_to_scenario(
            ...     scenario_result_id="123e4567-e89b-12d3-a456-426614174000",
            ...     atomic_attack_name="base64_attack",
            ...     attack_results=[result1, result2]
            ... )
        """
        try:
            # Retrieve current scenario result
            scenario_results = self.get_scenario_results(scenario_result_ids=[scenario_result_id])

            if not scenario_results:
                logger.error(f"Scenario result with ID {scenario_result_id} not found in memory")
                return False

            scenario_result = scenario_results[0]

            # Update attack results for this atomic attack name
            if atomic_attack_name not in scenario_result.attack_results:
                scenario_result.attack_results[atomic_attack_name] = []

            scenario_result.attack_results[atomic_attack_name].extend(list(attack_results))

            # Save updated result back to memory using update
            entry = ScenarioResultEntry(entry=scenario_result)
            self._update_entry(entry)

            logger.info(
                f"Added {len(attack_results)} attack results to scenario {scenario_result_id} "
                f"for atomic attack '{atomic_attack_name}'"
            )
            return True

        except Exception as e:
            logger.exception(f"Failed to add attack results to scenario {scenario_result_id}: {str(e)}")
            raise

    def update_scenario_run_state(self, *, scenario_result_id: str, scenario_run_state: str) -> bool:
        """
        Update the run state of an existing scenario result.

        Args:
            scenario_result_id (str): The ID of the scenario result to update.
            scenario_run_state (str): The new state for the scenario
                (e.g., "CREATED", "IN_PROGRESS", "COMPLETED", "FAILED").

        Returns:
            bool: True if the update was successful, False otherwise.

        Example:
            >>> memory.update_scenario_run_state(
            ...     scenario_result_id="123e4567-e89b-12d3-a456-426614174000",
            ...     scenario_run_state="COMPLETED"
            ... )
        """
        try:
            # Retrieve current scenario result
            scenario_results = self.get_scenario_results(scenario_result_ids=[scenario_result_id])

            if not scenario_results:
                logger.error(f"Scenario result with ID {scenario_result_id} not found in memory")
                return False

            scenario_result = scenario_results[0]

            # Update the scenario run state
            scenario_result.scenario_run_state = scenario_run_state  # type: ignore[ty:invalid-assignment]

            # Save updated result back to memory using update
            entry = ScenarioResultEntry(entry=scenario_result)
            self._update_entry(entry)

            logger.info(f"Updated scenario {scenario_result_id} state to '{scenario_run_state}'")
            return True

        except Exception as e:
            logger.exception(
                f"Failed to update scenario {scenario_result_id} state to '{scenario_run_state}': {str(e)}"
            )
            raise

    def get_scenario_results(
        self,
        *,
        scenario_result_ids: Optional[Sequence[str]] = None,
        scenario_name: Optional[str] = None,
        scenario_version: Optional[int] = None,
        pyrit_version: Optional[str] = None,
        added_after: Optional[datetime] = None,
        added_before: Optional[datetime] = None,
        labels: Optional[dict[str, str]] = None,
        objective_target_endpoint: Optional[str] = None,
        objective_target_model_name: Optional[str] = None,
        identifier_filters: Optional[Sequence[IdentifierFilter]] = None,
    ) -> Sequence[ScenarioResult]:
        """
        Retrieve a list of ScenarioResult objects based on the specified filters.

        Args:
            scenario_result_ids (Optional[Sequence[str]], optional): A list of scenario result IDs.
                Defaults to None.
            scenario_name (Optional[str], optional): The scenario name to filter by (substring match).
                Defaults to None.
            scenario_version (Optional[int], optional): The scenario version to filter by. Defaults to None.
            pyrit_version (Optional[str], optional): The PyRIT version to filter by. Defaults to None.
            added_after (Optional[datetime], optional): Filter for scenarios completed after this datetime.
                Defaults to None.
            added_before (Optional[datetime], optional): Filter for scenarios completed before this datetime.
                Defaults to None.
            labels (Optional[dict[str, str]], optional): A dictionary of memory labels to filter by.
                Defaults to None.
            objective_target_endpoint (Optional[str], optional): Filter for scenarios where the
                objective_target_identifier has an endpoint attribute containing this value (case-insensitive).
                Defaults to None.
            objective_target_model_name (Optional[str], optional): Filter for scenarios where the
                objective_target_identifier has a model_name attribute containing this value (case-insensitive).
                Defaults to None.
            identifier_filters (Optional[Sequence[IdentifierFilter]], optional):
                A sequence of IdentifierFilter objects that allows filtering by identifier JSON properties.
                Defaults to None.

        Returns:
            Sequence[ScenarioResult]: A list of ScenarioResult objects that match the specified filters.
        """
        if scenario_result_ids is not None and len(scenario_result_ids) == 0:
            return []

        conditions: list[ColumnElement[bool]] = []

        if scenario_name:
            normalized_name = ScenarioResult.normalize_scenario_name(scenario_name)
            conditions.append(ScenarioResultEntry.scenario_name.contains(normalized_name))

        if scenario_version is not None:
            conditions.append(ScenarioResultEntry.scenario_version == scenario_version)

        if pyrit_version:
            conditions.append(ScenarioResultEntry.pyrit_version == pyrit_version)

        if added_after:
            conditions.append(ScenarioResultEntry.completion_time >= added_after)

        if added_before:
            conditions.append(ScenarioResultEntry.completion_time <= added_before)

        if labels:
            conditions.append(self._get_scenario_result_label_condition(labels=labels))

        if objective_target_endpoint:
            conditions.append(
                self._get_condition_json_property_match(
                    json_column=ScenarioResultEntry.objective_target_identifier,
                    property_path="$.endpoint",
                    value=objective_target_endpoint,
                    partial_match=True,
                )
            )

        if objective_target_model_name:
            conditions.append(
                self._get_condition_json_property_match(
                    json_column=ScenarioResultEntry.objective_target_identifier,
                    property_path="$.model_name",
                    value=objective_target_model_name,
                    partial_match=True,
                )
            )

        if identifier_filters:
            conditions.extend(
                self._build_identifier_filter_conditions(
                    identifier_filters=identifier_filters,
                    identifier_column_map={
                        IdentifierType.SCORER: ScenarioResultEntry.objective_scorer_identifier,
                        IdentifierType.TARGET: ScenarioResultEntry.objective_target_identifier,
                    },
                    caller="get_scenario_results",
                )
            )

        try:
            # Handle scenario_result_ids with batched queries if needed
            if scenario_result_ids:
                entries = self._execute_batched_query(
                    ScenarioResultEntry,
                    batch_column=ScenarioResultEntry.id,
                    batch_values=list(scenario_result_ids),
                    other_conditions=conditions,
                )
            else:
                entries = self._query_entries(ScenarioResultEntry, conditions=and_(*conditions) if conditions else None)

            # Convert entries to ScenarioResults and populate attack_results efficiently
            scenario_results = []
            for entry in entries:
                scenario_result = entry.get_scenario_result()

                # Get conversation IDs grouped by attack name
                conversation_ids_by_attack = entry.get_conversation_ids_by_attack_name()

                # Collect all conversation IDs to query in a single batch
                all_conversation_ids = []
                for conv_ids in conversation_ids_by_attack.values():
                    all_conversation_ids.extend(conv_ids)

                # Query all AttackResults using batched queries if needed
                if all_conversation_ids:
                    attack_entries = self._execute_batched_query(
                        AttackResultEntry,
                        batch_column=AttackResultEntry.conversation_id,
                        batch_values=all_conversation_ids,
                    )

                    # Build a dict for quick lookup
                    attack_results_dict = {entry.conversation_id: entry.get_attack_result() for entry in attack_entries}

                    # Populate attack_results by attack name, preserving order
                    scenario_result.attack_results = {}
                    for attack_name, conv_ids in conversation_ids_by_attack.items():
                        scenario_result.attack_results[attack_name] = [
                            attack_results_dict[conv_id] for conv_id in conv_ids if conv_id in attack_results_dict
                        ]

                scenario_results.append(scenario_result)

            return scenario_results
        except Exception as e:
            logger.exception(f"Failed to retrieve scenario results with error {e}")
            raise

    def print_schema(self) -> None:
        """
        Print the schema of all tables in the database.

        Raises:
            RuntimeError: If the engine is not initialized.
        """
        metadata = MetaData()
        if self.engine is None:
            raise RuntimeError("Engine is not initialized")
        metadata.reflect(bind=self.engine)

        for table_name in metadata.tables:
            table = metadata.tables[table_name]
            print(f"Schema for {table_name}:")
            for column in table.columns:
                print(f"  Column {column.name} ({column.type})")
