# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import atexit
import logging
import re
import uuid
import weakref
from collections.abc import Iterator, MutableSequence, Sequence
from contextlib import closing
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar

from sqlalchemy import MetaData, and_, not_, or_, select
from sqlalchemy.engine.base import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import InstrumentedAttribute, flag_modified
from sqlalchemy.orm.session import Session

if TYPE_CHECKING:
    from pyrit.memory.memory_embedding import MemoryEmbedding

from pyrit.memory.memory_models import (
    AtomicAttackIdentifierEntry,
    AttackIdentifierEntry,
    AttackResultEntry,
    AttackTechniqueIdentifierEntry,
    Base,
    ComponentIdentifierEntry,
    ConversationEntry,
    ConverterIdentifierEntry,
    EmbeddingDataEntry,
    PromptConverterIdentifierEntry,
    PromptMemoryEntry,
    ScenarioIdentifierEntry,
    ScenarioResultEntry,
    ScoreEntry,
    ScorerIdentifierEntry,
    SeedEntry,
    SeedIdentifierEntry,
    TargetIdentifierEntry,
)
from pyrit.memory.storage import (
    DataTypeSerializer,
    StorageIO,
    data_serializer_factory,
    set_seed_sha256_async,
)
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    Conversation,
    ConversationRetry,
    ConversationRetryReason,
    ConversationStats,
    ConverterIdentifier,
    IdentifierFilter,
    IdentifierType,
    Message,
    MessagePiece,
    ScenarioIdentifier,
    ScenarioResult,
    Score,
    ScorerIdentifier,
    Seed,
    SeedDataset,
    SeedGroup,
    SeedIdentifier,
    SeedType,
    TargetIdentifier,
    group_conversation_message_pieces_by_sequence,
    sort_message_pieces,
)

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)


Model = TypeVar("Model")
IdentifierModel = TypeVar("IdentifierModel", bound=ComponentIdentifier)


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

    # Label keys are interpolated into backend-specific JSON path expressions
    # (e.g. ``$.key``) in the per-backend label-filter helpers. We restrict keys
    # to a conservative allowlist so a crafted key cannot break out of the JSON
    # path literal and inject SQL. Values are always passed as bound parameters
    # and do not need this restriction.
    _LABEL_KEY_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.\-]+$")

    memory_embedding: "MemoryEmbedding | None" = None
    results_storage_io: StorageIO | None = None
    results_path: str | None = None
    engine: Engine | None = None

    @staticmethod
    def _uid() -> str:
        """Return a short unique suffix for bind-param deduplication."""
        return uuid.uuid4().hex[:8]

    def __init__(self, embedding_model: Any | None = None) -> None:
        """
        Initialize the MemoryInterface.

        Args:
            embedding_model: If set, this includes embeddings in the memory entries
                which are extremely useful for comparing chat messages and similarities,
                but also includes overhead.
        """
        self.memory_embedding = embedding_model
        self._init_storage_io()

        # Ensure cleanup at process exit
        self.cleanup()

    def enable_embedding(self, embedding_model: Any | None = None) -> None:
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
            partial_match (bool): Whether to perform a substring match. Defaults to False.
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

        Concrete subclasses translate this contract into their SQL dialect (e.g. SQLite's
        ``json_extract``, Azure SQL's ``JSON_VALUE`` + ``ISJSON``). Implementations must honor
        ``partial_match`` and ``case_sensitive`` identically so callers can rely on consistent
        matching semantics across backends.

        Args:
            json_column (InstrumentedAttribute[Any]): The JSON-backed model field to query.
            property_path (str): The JSON path for the property to match.
            value (str): The string value that must match the extracted JSON property value.
            partial_match (bool): Whether to perform a substring match. Defaults to False.
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

        Concrete subclasses translate this contract into their SQL dialect (e.g. SQLite's
        ``json_each`` + ``json_extract``, Azure SQL's ``OPENJSON`` + ``JSON_QUERY``).
        Implementations must honor ``match_mode`` and the empty-``array_to_match`` "absence"
        semantics identically so callers can rely on consistent matching across backends.

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
                ``"any"`` requires at least one listed value to be present. Ignored when
                ``array_to_match`` has fewer than 2 entries or is empty.

        Returns:
            Any: A database-specific SQLAlchemy condition.
        """

    def get_all_embeddings(self) -> Sequence[EmbeddingDataEntry]:
        """
        Load all EmbeddingData from the memory storage handler.

        Returns:
            Sequence[EmbeddingDataEntry]: All stored embedding entries.
        """
        result: Sequence[EmbeddingDataEntry] = self._query_entries(EmbeddingDataEntry)
        return result

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
    def _get_message_pieces_prompt_metadata_conditions(self, *, prompt_metadata: dict[str, str | int]) -> list[Any]:
        """
        Return a list of conditions for filtering memory entries based on prompt metadata.

        Args:
            prompt_metadata (dict[str, str | int]): A free-form dictionary for tagging prompts with custom metadata.
                This includes information that is useful for the specific target you're probing, such as encoding data.

        Returns:
            list: A list of conditions for filtering memory entries based on prompt metadata.
        """

    @abc.abstractmethod
    def _get_seed_metadata_conditions(self, *, metadata: dict[str, str | int]) -> Any:
        """
        Return a condition for filtering seed prompt entries based on prompt metadata.

        Args:
            metadata (dict[str, str | int]): A free-form dictionary for tagging prompts with custom metadata.
                This includes information that is useful for the specific target you're probing, such as encoding data.

        Returns:
            Any: A SQLAlchemy condition for filtering memory entries based on prompt metadata.
        """

    def add_conversation_to_memory(self, *, conversation: Conversation) -> None:
        """
        Register a conversation in memory, recording its conversation-scoped metadata.

        A conversation is a first-class entity held with a single target. Build a
        ``Conversation`` when it is created and call this once (before, or independently
        of, adding its messages) to record the target it is held with. Message writes
        (``add_message_to_memory`` / ``add_message_pieces_to_memory``) deliberately do
        not take a target, so that conversation ownership is expressed in a single place
        rather than threaded through every write.

        Registration is idempotent only for an identical conversation: re-registering the
        same ``conversation_id`` with the same target is a no-op (so repeated per-turn
        registration is safe). Re-registering an existing ``conversation_id`` with a
        different target is a conflict and raises ``ValueError`` -- a conversation is held
        with exactly one target and is never re-targeted.

        Args:
            conversation (Conversation): The conversation metadata to record, carrying the
                ``conversation_id`` and the target it is held with (if known).

        Raises:
            ValueError: If ``conversation_id`` is empty, or if a conversation with the same
                id already exists with a different target.
        """
        self._insert_conversation(conversation=conversation)

    def add_message_pieces_to_memory(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Insert a list of message pieces into the memory storage.

        Pieces flagged via ``MessagePiece.not_in_memory = True`` are silently filtered
        out so callers don't need to track persistence policy themselves. Every
        remaining piece must carry a non-empty ``conversation_id`` (the memory layer
        never invents one -- see ``_validate_persistable_conversation_ids``).

        Conversation-scoped metadata (the target a conversation is held with) is not
        recorded here; register it once via ``add_conversation_to_memory`` when the
        conversation is created.

        This is a template method: subclasses implement only the backend-specific
        ``_add_message_pieces_to_memory`` and inherit the filtering and validation
        steps so no subclass can forget to run them.

        Args:
            message_pieces (Sequence[MessagePiece]): The pieces to persist.
        """
        pieces_to_insert = [piece for piece in message_pieces if not piece.not_in_memory]
        if not pieces_to_insert:
            return
        self._validate_persistable_conversation_ids(message_pieces=pieces_to_insert)
        self._add_message_pieces_to_memory(message_pieces=pieces_to_insert)

    def _add_message_pieces_to_memory(self, *, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Persist already-validated message pieces to the backing store.

        Called by ``add_message_pieces_to_memory`` after ``not_in_memory`` pieces are
        filtered out and conversation_ids are validated.

        Args:
            message_pieces (Sequence[MessagePiece]): Persistable pieces (none flagged
                ``not_in_memory``), each carrying a non-empty ``conversation_id``.

        Raises:
            SQLAlchemyError: If the message pieces or converter identifiers cannot be persisted.
        """
        entries = [PromptMemoryEntry(entry=piece) for piece in message_pieces]
        with closing(self.get_session()) as session:
            try:
                for piece, entry in zip(message_pieces, entries, strict=True):
                    for position, identifier in enumerate(piece.converter_identifiers):
                        converter_identifier = ConverterIdentifier.from_component_identifier(identifier)
                        self._persist_identifier(session=session, identifier=converter_identifier)
                        entry.converter_identifier_links.append(
                            PromptConverterIdentifierEntry(
                                position=position,
                                converter_identifier_hash=converter_identifier.hash,
                            )
                        )
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting prompt memory entries: {e}")
                raise

    @staticmethod
    def _validate_persistable_conversation_ids(*, message_pieces: Sequence[MessagePiece]) -> None:
        """
        Ensure every persistable piece carries a usable ``conversation_id``.

        A conversation is its own entity, so the caller that starts it owns the id; the
        memory layer never generates one. Any piece reaching persistence without a
        non-empty, non-blank ``conversation_id`` is a programming error and raises loudly
        rather than being silently assigned a throwaway conversation.

        Args:
            message_pieces (Sequence[MessagePiece]): Pieces about to be persisted
                (``not_in_memory`` pieces should already be filtered out).

        Raises:
            ValueError: If any piece has a ``None``, empty, or whitespace-only
                ``conversation_id``.
        """
        for piece in message_pieces:
            if piece.conversation_id is None or not piece.conversation_id.strip():
                raise ValueError(
                    f"MessagePiece {piece.id} has no conversation_id. A conversation_id must be set by "
                    "the caller before a piece is persisted; the memory layer does not generate one."
                )

    def _insert_conversation(self, *, conversation: Conversation) -> None:
        """
        Insert the ``Conversations`` row for a conversation, never updating an existing one.

        A conversation is held with exactly one target, so this is insert-only with
        idempotent-on-identical semantics: if no row exists it is inserted; if a row
        already exists with the same target it is left untouched; if a row exists with a
        different target it is a conflict and raises.

        Args:
            conversation (Conversation): The conversation metadata to record.

        Raises:
            ValueError: If ``conversation.conversation_id`` is empty, or if a conversation
                with the same id already exists with a different target.
            SQLAlchemyError: If the insert fails.
        """
        if not conversation.conversation_id:
            raise ValueError("Cannot register a conversation without a conversation_id.")
        entry = ConversationEntry(conversation=conversation)
        with closing(self.get_session()) as session:
            try:
                existing = session.get(ConversationEntry, conversation.conversation_id)
                if existing is None:
                    if conversation.target_identifier is not None:
                        self._persist_target_identifier(
                            session=session,
                            target_identifier=TargetIdentifier.from_component_identifier(
                                conversation.target_identifier
                            ),
                        )
                    session.add(entry)
                elif (
                    entry.target_identifier is not None
                    and existing.target_identifier is not None
                    and existing.target_identifier != entry.target_identifier
                ):
                    raise ValueError(
                        f"Conversation {conversation.conversation_id} is already registered with a different "
                        f"target ({existing.target_identifier!r}); a conversation is held with exactly one "
                        f"target and cannot be re-registered with {entry.target_identifier!r}."
                    )
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error registering conversation {conversation.conversation_id}: {e}")
                raise

    def add_conversation_retry(self, *, conversation_id: str, sequence: int, reason: ConversationRetryReason) -> None:
        """
        Append a retry record to the conversation-scoped metadata for ``conversation_id``.

        Records that a turn had to be retried (e.g. because its response failed JSON
        validation and was rolled back out of memory). The conversation's ``Conversations``
        row is updated in place; if no row exists yet it is created. This is distinct from
        the insert-only ``_insert_conversation``.

        Args:
            conversation_id (str): The conversation whose turn was retried.
            sequence (int): The sequence the retried turn's request occupies.
            reason (ConversationRetryReason): Why the turn was retried.

        Raises:
            SQLAlchemyError: If the database update fails; the transaction is rolled back first.
        """
        record = ConversationRetry(sequence=sequence, reason=reason).model_dump(mode="json")
        with closing(self.get_session()) as session:
            try:
                entry = session.get(ConversationEntry, str(conversation_id))
                if entry is None:
                    entry = ConversationEntry(conversation=Conversation(conversation_id=str(conversation_id)))
                    session.add(entry)
                entry.retries = [*(entry.retries or []), record]
                flag_modified(entry, "retries")
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error recording retry for conversation {conversation_id}: {e}")
                raise

    def delete_conversation_pieces_after_sequence(self, *, conversation_id: str, sequence: int) -> int:
        """
        Delete all message pieces in a conversation whose sequence is greater than ``sequence``.

        Rolls a conversation back to a baseline so a failed turn can be resent on a clean
        history. Dependent ``EmbeddingData`` rows for the deleted pieces are removed first to
        avoid orphaned foreign keys. Pieces at or below ``sequence`` (e.g. the system prompt
        and any prior good turns) are left intact.

        Args:
            conversation_id (str): The conversation to roll back.
            sequence (int): The baseline sequence; pieces with a greater sequence are deleted.

        Returns:
            int: The number of ``PromptMemoryEntries`` deleted.

        Raises:
            SQLAlchemyError: If the deletion fails; the transaction is rolled back first.
        """
        with closing(self.get_session()) as session:
            try:
                pieces = (
                    session.query(PromptMemoryEntry)
                    .filter(
                        PromptMemoryEntry.conversation_id == str(conversation_id),
                        PromptMemoryEntry.sequence > sequence,
                    )
                    .all()
                )
                if not pieces:
                    return 0
                piece_ids = [piece.id for piece in pieces]
                session.query(EmbeddingDataEntry).filter(EmbeddingDataEntry.id.in_(piece_ids)).delete(
                    synchronize_session=False
                )
                for piece in pieces:
                    session.delete(piece)
                session.commit()
                return len(pieces)
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error deleting conversation pieces for {conversation_id}: {e}")
                raise

    @classmethod
    def _persist_target_identifier(cls, *, session: Any, target_identifier: TargetIdentifier) -> None:
        """
        Persist ``target_identifier`` and its inner targets as content-addressed rows.

        Dependencies are persisted before the target row, whose ordered child edges
        reference those rows by content hash. Identifier rows are immutable and keyed
        by their content hash, so an identical target reused across many conversations
        maps to a single row.

        If the row already exists it was fully persisted before (children and edges
        included, since rows are immutable), so this returns early. Otherwise the row and
        its child edges are inserted inside a savepoint. If an ``IntegrityError`` occurs,
        it is treated as a concurrent duplicate only when a fresh lookup confirms that
        the identifier hash now exists; all other integrity failures are re-raised.

        Args:
            session (Any): The active SQLAlchemy session (the caller's transaction).
            target_identifier (TargetIdentifier): The target identifier to persist.
        """
        cls._persist_identifier(session=session, identifier=target_identifier)

    @classmethod
    def _persist_identifier(cls, *, session: Any, identifier: ComponentIdentifier) -> None:
        entry_type = cls._get_identifier_entry_type(identifier)
        if session.get(entry_type, identifier.hash) is not None:
            return

        for dependency in cls._iter_identifier_dependencies(identifier):
            cls._persist_identifier(session=session, identifier=dependency)

        try:
            with session.begin_nested():
                session.add(entry_type.from_domain_model(identifier))
                session.flush()
        except IntegrityError:
            with session.no_autoflush:
                existing_entry = session.get(entry_type, identifier.hash, populate_existing=True)
            if existing_entry is None:
                raise

    @staticmethod
    def _get_identifier_entry_type(identifier: ComponentIdentifier) -> type[ComponentIdentifierEntry[Any]]:
        if isinstance(identifier, AtomicAttackIdentifier):
            return AtomicAttackIdentifierEntry
        if isinstance(identifier, AttackTechniqueIdentifier):
            return AttackTechniqueIdentifierEntry
        if isinstance(identifier, AttackIdentifier):
            return AttackIdentifierEntry
        if isinstance(identifier, SeedIdentifier):
            return SeedIdentifierEntry
        if isinstance(identifier, TargetIdentifier):
            return TargetIdentifierEntry
        if isinstance(identifier, ConverterIdentifier):
            return ConverterIdentifierEntry
        if isinstance(identifier, ScorerIdentifier):
            return ScorerIdentifierEntry
        if isinstance(identifier, ScenarioIdentifier):
            return ScenarioIdentifierEntry
        raise TypeError(f"Identifier type {type(identifier).__name__} does not have a persistence entry.")

    @staticmethod
    def _iter_identifier_dependencies(identifier: ComponentIdentifier) -> Iterator[ComponentIdentifier]:
        for field_name in identifier.promoted_child_field_names():
            child = getattr(identifier, field_name)
            if isinstance(child, ComponentIdentifier):
                yield child
            elif isinstance(child, list):
                yield from (item for item in child if isinstance(item, ComponentIdentifier))

    def _get_identifiers(
        self,
        *,
        identifier_type: type[IdentifierModel],
        entry_type: type[ComponentIdentifierEntry[Any]],
        identifier_hashes: Sequence[str] | None,
        filters: dict[str, Any],
    ) -> Sequence[IdentifierModel]:
        if identifier_hashes is not None and not identifier_hashes:
            return []

        list_filters = {name: value for name, value in filters.items() if isinstance(value, list)}
        conditions = [
            getattr(entry_type, name) == value
            for name, value in filters.items()
            if value is not None and name not in list_filters
        ]
        if identifier_hashes is not None:
            entries = self._execute_batched_query(
                entry_type,
                batch_column=entry_type.hash,
                batch_values=identifier_hashes,
                other_conditions=conditions,
                order_by=entry_type.hash,
            )
        else:
            entries = self._query_entries(
                entry_type,
                conditions=and_(*conditions) if conditions else None,
                order_by=entry_type.hash,
            )

        entries = [
            entry
            for entry in entries
            if all(
                getattr(entry, name) is not None and sorted(getattr(entry, name)) == sorted(value)
                for name, value in list_filters.items()
            )
        ]
        identifiers: list[IdentifierModel] = []
        seen_hashes: set[str] = set()
        for entry in sorted(entries, key=lambda item: item.hash):
            if entry.hash in seen_hashes:
                continue
            if entry.identifier_json is None:
                raise ValueError(f"Identifier row {entry.hash} in {entry_type.__tablename__} has no identifier JSON.")
            identifier = identifier_type.model_validate(entry.identifier_json)
            if identifier.hash != entry.hash:
                raise ValueError(
                    f"Identifier row {entry.hash} in {entry_type.__tablename__} does not match its stored JSON hash."
                )
            identifiers.append(identifier)
            seen_hashes.add(entry.hash)
        return identifiers

    def get_target_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        endpoint: str | None = None,
        model_name: str | None = None,
        underlying_model_name: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_requests_per_minute: int | None = None,
        supported_auth_modes: Sequence[str] | None = None,
    ) -> Sequence[TargetIdentifier]:
        """
        Retrieve target identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            endpoint (str | None): Target endpoint to match.
            model_name (str | None): Target model name to match.
            underlying_model_name (str | None): Underlying model name to match.
            temperature (float | None): Temperature to match.
            top_p (float | None): Top-p value to match.
            max_requests_per_minute (int | None): Request limit to match.
            supported_auth_modes (Sequence[str] | None): Authentication modes to match exactly, in any order.

        Returns:
            Sequence[TargetIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=TargetIdentifier,
            entry_type=TargetIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "endpoint": endpoint,
                "model_name": model_name,
                "underlying_model_name": underlying_model_name,
                "temperature": temperature,
                "top_p": top_p,
                "max_requests_per_minute": max_requests_per_minute,
                "supported_auth_modes": list(supported_auth_modes) if supported_auth_modes is not None else None,
            },
        )

    def get_converter_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        supported_input_types: Sequence[str] | None = None,
        supported_output_types: Sequence[str] | None = None,
        converter_target_hash: str | None = None,
        sub_converter_hash: str | None = None,
    ) -> Sequence[ConverterIdentifier]:
        """
        Retrieve converter identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            supported_input_types (Sequence[str] | None): Input types to match exactly, in any order.
            supported_output_types (Sequence[str] | None): Output types to match exactly, in any order.
            converter_target_hash (str | None): Converter target hash to match.
            sub_converter_hash (str | None): Nested converter hash to match.

        Returns:
            Sequence[ConverterIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=ConverterIdentifier,
            entry_type=ConverterIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "supported_input_types": (list(supported_input_types) if supported_input_types is not None else None),
                "supported_output_types": (
                    list(supported_output_types) if supported_output_types is not None else None
                ),
                "converter_target_hash": converter_target_hash,
                "sub_converter_hash": sub_converter_hash,
            },
        )

    def get_scorer_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        scorer_type: str | None = None,
        score_aggregator: str | None = None,
        prompt_target_hash: str | None = None,
    ) -> Sequence[ScorerIdentifier]:
        """
        Retrieve scorer identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            scorer_type (str | None): Scorer type to match.
            score_aggregator (str | None): Score aggregator to match.
            prompt_target_hash (str | None): Scorer target hash to match.

        Returns:
            Sequence[ScorerIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=ScorerIdentifier,
            entry_type=ScorerIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "scorer_type": scorer_type,
                "score_aggregator": score_aggregator,
                "prompt_target_hash": prompt_target_hash,
            },
        )

    def get_scenario_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        version: int | None = None,
        techniques: Sequence[str] | None = None,
        datasets: Sequence[str] | None = None,
        objective_target_hash: str | None = None,
        objective_scorer_hash: str | None = None,
    ) -> Sequence[ScenarioIdentifier]:
        """
        Retrieve scenario identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            version (int | None): Scenario definition version to match.
            techniques (Sequence[str] | None): Technique names to match exactly, in any order.
            datasets (Sequence[str] | None): Dataset names to match exactly, in any order.
            objective_target_hash (str | None): Objective target hash to match.
            objective_scorer_hash (str | None): Objective scorer hash to match.

        Returns:
            Sequence[ScenarioIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=ScenarioIdentifier,
            entry_type=ScenarioIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "version": version,
                "techniques": list(techniques) if techniques is not None else None,
                "datasets": list(datasets) if datasets is not None else None,
                "objective_target_hash": objective_target_hash,
                "objective_scorer_hash": objective_scorer_hash,
            },
        )

    def get_seed_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        value: str | None = None,
        value_sha256: str | None = None,
        data_type: str | None = None,
        dataset_name: str | None = None,
        is_general_technique: bool | None = None,
    ) -> Sequence[SeedIdentifier]:
        """
        Retrieve seed identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            value (str | None): Seed value to match.
            value_sha256 (str | None): Seed value hash to match.
            data_type (str | None): Seed data type to match.
            dataset_name (str | None): Dataset name to match.
            is_general_technique (bool | None): General-technique flag to match.

        Returns:
            Sequence[SeedIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=SeedIdentifier,
            entry_type=SeedIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "value": value,
                "value_sha256": value_sha256,
                "data_type": data_type,
                "dataset_name": dataset_name,
                "is_general_technique": is_general_technique,
            },
        )

    def get_attack_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        adversarial_system_prompt: str | None = None,
        adversarial_seed_prompt: str | None = None,
        objective_target_hash: str | None = None,
        adversarial_chat_hash: str | None = None,
        objective_scorer_hash: str | None = None,
    ) -> Sequence[AttackIdentifier]:
        """
        Retrieve attack identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            adversarial_system_prompt (str | None): Adversarial system prompt to match.
            adversarial_seed_prompt (str | None): Adversarial seed prompt to match.
            objective_target_hash (str | None): Objective target hash to match.
            adversarial_chat_hash (str | None): Adversarial chat target hash to match.
            objective_scorer_hash (str | None): Objective scorer hash to match.

        Returns:
            Sequence[AttackIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=AttackIdentifier,
            entry_type=AttackIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "adversarial_system_prompt": adversarial_system_prompt,
                "adversarial_seed_prompt": adversarial_seed_prompt,
                "objective_target_hash": objective_target_hash,
                "adversarial_chat_hash": adversarial_chat_hash,
                "objective_scorer_hash": objective_scorer_hash,
            },
        )

    def get_attack_technique_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        attack_identifier_hash: str | None = None,
    ) -> Sequence[AttackTechniqueIdentifier]:
        """
        Retrieve attack technique identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            attack_identifier_hash (str | None): Attack identifier hash to match.

        Returns:
            Sequence[AttackTechniqueIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=AttackTechniqueIdentifier,
            entry_type=AttackTechniqueIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "attack_identifier_hash": attack_identifier_hash,
            },
        )

    def get_atomic_attack_identifiers(
        self,
        *,
        identifier_hashes: Sequence[str] | None = None,
        class_name: str | None = None,
        attack_technique_identifier_hash: str | None = None,
    ) -> Sequence[AtomicAttackIdentifier]:
        """
        Retrieve atomic attack identifiers using exact normalized-column filters.

        Args:
            identifier_hashes (Sequence[str] | None): Content hashes to include.
            class_name (str | None): Component class name to match.
            attack_technique_identifier_hash (str | None): Attack technique hash to match.

        Returns:
            Sequence[AtomicAttackIdentifier]: Matching identifiers ordered by content hash.
        """
        return self._get_identifiers(
            identifier_type=AtomicAttackIdentifier,
            entry_type=AtomicAttackIdentifierEntry,
            identifier_hashes=identifier_hashes,
            filters={
                "class_name": class_name,
                "attack_technique_identifier_hash": attack_technique_identifier_hash,
            },
        )

    def _add_embeddings_to_memory(self, *, embedding_data: Sequence[EmbeddingDataEntry]) -> None:
        """
        Insert embedding data into memory storage.
        """
        self._insert_entries(entries=embedding_data)

    def _query_entries(
        self,
        model_class: type[Model],
        *,
        conditions: Any | None = None,
        distinct: bool = False,
        join_scores: bool = False,
        order_by: Any | None = None,
        limit: int | None = None,
    ) -> MutableSequence[Model]:
        """
        Fetch data from the specified table model with optional conditions.

        Args:
            model_class: The SQLAlchemy model class corresponding to the table you want to query.
            conditions: SQLAlchemy filter conditions (Optional).
            distinct: Whether to return distinct rows only. Defaults to False.
            join_scores: Whether to join the scores table. Defaults to False.
            order_by: SQLAlchemy order_by clause (Optional).
            limit (int | None): Maximum number of rows to return. Defaults to None (no limit).

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
                if order_by is not None:
                    query = query.order_by(order_by)
                if distinct:
                    query = query.distinct()
                if limit is not None:
                    query = query.limit(limit)
                return query.all()
            except SQLAlchemyError as e:
                logger.exception(f"Error fetching data from table {model_class.__tablename__}: {e}")  # type: ignore[ty:unresolved-attribute]
                raise

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
        order_by: Any | None = None,
        limit: int | None = None,
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
            order_by: SQLAlchemy order_by clause (Optional).
            limit (int | None): Maximum number of rows to return. Defaults to None (no limit).

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
                order_by=order_by,
                limit=limit,
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
                order_by=order_by,
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

    @abc.abstractmethod
    def get_session(self) -> Session:
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

    def _update_entries(self, *, entries: MutableSequence[Base], update_fields: dict[str, Any]) -> bool:
        """
        Update the given entries with the specified field values.

        Args:
            entries (Sequence[Base]): A list of SQLAlchemy model instances to be updated.
            update_fields (dict): A dictionary of field names and their new values.

        Returns:
            bool: True if the update was successful.

        Raises:
            ValueError: If update_fields is empty or contains an unknown field.
            SQLAlchemyError: If the update fails.
        """
        if not update_fields:
            raise ValueError("update_fields must be provided to update prompt entries.")
        with closing(self.get_session()) as session:
            try:
                for entry in entries:
                    entry_in_session = session.get(type(entry), entry.id)  # type: ignore[ty:unresolved-attribute]
                    if entry_in_session is None:
                        entry_in_session = session.merge(entry)
                    for field, value in update_fields.items():
                        if field not in vars(entry_in_session):
                            session.rollback()
                            raise ValueError(
                                f"Field '{field}' does not exist in the table '{entry_in_session.__tablename__}'. "
                                "Rolling back changes..."
                            )
                        setattr(entry_in_session, field, value)
                session.commit()
                return True
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error updating entries: {e}")
                raise

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

        Extracts class_name from the atomic_attack_identifier JSON column via a
        database-level DISTINCT query.

        Returns:
            Sorted list of unique attack class name strings.
        """

    @abc.abstractmethod
    def get_unique_converter_class_names(self) -> list[str]:
        """
        Return sorted unique converter class names used across all attack results.

        Extracts class_name values from the nested request_converters array
        within the atomic_attack_identifier JSON column via a database-level query.

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

        Callers that produce scores for pieces flagged via
        ``MessagePiece.not_in_memory = True`` should null out
        ``message_piece_id`` on those scores before calling this method so the
        score itself can still be persisted without a dangling piece linkage.
        Persisting the score even without a piece is intentional: aggregate
        analytics (e.g. refusal rate over a batch) still want the score row
        even when the scored content was never a real conversation turn.

        Raises:
            SQLAlchemyError: If the score or identifier rows cannot be persisted.
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
                    score.message_piece_id = pieces[0].original_prompt_id  # type: ignore[ty:invalid-assignment]
        entries = [ScoreEntry(entry=score) for score in scores]
        with closing(self.get_session()) as session:
            try:
                for entry in entries:
                    if entry.scorer_class_identifier:
                        self._persist_scorer_identifier(
                            session=session,
                            scorer_identifier=ScorerIdentifier.model_validate(entry.scorer_class_identifier),
                        )
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error inserting scores: {e}")
                raise

    @classmethod
    def _persist_scorer_identifier(cls, *, session: Any, scorer_identifier: ScorerIdentifier) -> None:
        """Persist a complete scorer graph and its target dependencies."""
        cls._persist_identifier(session=session, identifier=scorer_identifier)

    def get_scores(
        self,
        *,
        score_ids: Sequence[str] | None = None,
        score_type: str | None = None,
        score_category: str | None = None,
        sent_after: datetime | None = None,
        sent_before: datetime | None = None,
        identifier_filters: Sequence[IdentifierFilter] | None = None,
    ) -> Sequence[Score]:
        """
        Retrieve a list of Score objects based on the specified filters.

        Args:
            score_ids (Sequence[str] | None): A list of score IDs to filter by.
            score_type (str | None): The type of the score to filter by.
            score_category (str | None): The category of the score to filter by.
            sent_after (datetime | None): Filter for scores sent after this datetime.
            sent_before (datetime | None): Filter for scores sent before this datetime.
            identifier_filters (Sequence[IdentifierFilter] | None): A sequence of IdentifierFilter objects that
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
        role: str | None = None,
        conversation_id: str | uuid.UUID | None = None,
        prompt_ids: Sequence[str | uuid.UUID] | None = None,
        labels: dict[str, str] | None = None,
        prompt_metadata: dict[str, str | int] | None = None,
        sent_after: datetime | None = None,
        sent_before: datetime | None = None,
        original_values: Sequence[str] | None = None,
        converted_values: Sequence[str] | None = None,
        data_type: str | None = None,
        not_data_type: str | None = None,
        converted_value_sha256: Sequence[str] | None = None,
    ) -> Sequence[Score]:
        """
        Retrieve scores attached to message pieces based on the specified filters.

        Args:
            role (str | None, optional): The role of the prompt. Defaults to None.
            conversation_id (str | uuid.UUID | None, optional): The ID of the conversation. Defaults to None.
            prompt_ids (Sequence[str] | Sequence[uuid.UUID] | None, optional): A list of prompt IDs.
                Defaults to None.
            labels (dict[str, str] | None, optional): A dictionary of labels. Defaults to None.
            prompt_metadata (dict[str, str | int] | None, optional): The metadata associated with the prompt.
                Defaults to None.
            sent_after (datetime | None, optional): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (datetime | None, optional): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Sequence[str] | None, optional): A list of original values. Defaults to None.
            converted_values (Sequence[str] | None, optional): A list of converted values. Defaults to None.
            data_type (str | None, optional): The data type to filter by. Defaults to None.
            not_data_type (str | None, optional): The data type to exclude. Defaults to None.
            converted_value_sha256 (Sequence[str] | None, optional): A list of SHA256 hashes of converted values.
                Defaults to None.

        Returns:
            Sequence[Score]: A list of scores extracted from the message pieces.
        """
        message_pieces = self.get_message_pieces(
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

        # Deduplicate by original_prompt_id since duplicated pieces share scores
        # with their originals.
        original_ids = {piece.original_prompt_id for piece in message_pieces if piece.original_prompt_id is not None}
        if not original_ids:
            return []

        score_entries = self._execute_batched_query(
            ScoreEntry,
            batch_column=ScoreEntry.prompt_request_response_id,
            batch_values=list(original_ids),
            other_conditions=[],
        )
        return [entry.get_score() for entry in score_entries]

    def get_conversation_messages(self, *, conversation_id: str) -> MutableSequence[Message]:
        """
        Retrieve a list of Message objects that have the specified conversation ID.

        Args:
            conversation_id (str): The conversation ID to match.

        Returns:
            MutableSequence[Message]: A list of chat memory entries with the specified conversation ID.

        Raises:
            ValueError: If conversation_id is empty or None. A falsy id would cause the underlying
                get_message_pieces filter to be skipped, silently returning pieces from every
                conversation in memory.
        """
        if not conversation_id:
            raise ValueError("get_conversation_messages requires a non-empty conversation_id")
        message_pieces = self.get_message_pieces(conversation_id=conversation_id)
        return group_conversation_message_pieces_by_sequence(message_pieces=message_pieces)

    def _get_conversation(self, *, conversation_id: str) -> Conversation | None:
        """
        Return the conversation-scoped metadata stored for ``conversation_id``.

        Args:
            conversation_id (str): The conversation to look up.

        Returns:
            Conversation | None: The conversation metadata (including the target
                identifier), or ``None`` if no row exists for the conversation.
        """
        # NOTE: The leading underscore is retained to distinguish this conversation-entity
        # accessor from the messages-returning helpers (``get_conversation_messages``).
        entries = self._query_entries(
            ConversationEntry,
            conditions=ConversationEntry.conversation_id == str(conversation_id),
        )
        if not entries:
            return None
        return entries[0].get_conversation()

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

        conversation = self.get_conversation_messages(conversation_id=response.conversation_id)
        return conversation[response.sequence - 1]

    def _build_message_piece_identifier_conditions(
        self, *, identifier_filters: Sequence[IdentifierFilter]
    ) -> list[Any]:
        """
        Build ``get_message_pieces`` conditions for identifier filters.

        ``CONVERTER`` identifiers remain on the piece. ``TARGET`` identifiers moved to
        the ``Conversations`` table, so target filters are applied via a subquery on
        ``ConversationEntry`` correlated by ``conversation_id``. ``ATTACK`` identifiers
        are no longer stamped on pieces (use ``get_attack_results`` instead) and are
        rejected by ``_build_identifier_filter_conditions``.

        Args:
            identifier_filters (Sequence[IdentifierFilter]): The filters to convert.

        Returns:
            list[Any]: SQLAlchemy conditions for the message-piece query.
        """
        conditions: list[Any] = []
        piece_filters = [f for f in identifier_filters if f.identifier_type != IdentifierType.TARGET]
        target_filters = [f for f in identifier_filters if f.identifier_type == IdentifierType.TARGET]

        if piece_filters:
            conditions.extend(
                self._build_identifier_filter_conditions(
                    identifier_filters=piece_filters,
                    identifier_column_map={
                        IdentifierType.CONVERTER: PromptMemoryEntry.converter_identifiers,
                    },
                    caller="get_message_pieces",
                )
            )
        if target_filters:
            target_conditions = self._build_identifier_filter_conditions(
                identifier_filters=target_filters,
                identifier_column_map={
                    IdentifierType.TARGET: ConversationEntry.target_identifier,
                },
                caller="get_message_pieces",
            )
            conditions.append(
                PromptMemoryEntry.conversation_id.in_(
                    select(ConversationEntry.conversation_id).where(and_(*target_conditions))
                )
            )
        return conditions

    def get_message_pieces(
        self,
        *,
        role: str | None = None,
        conversation_id: str | uuid.UUID | None = None,
        prompt_ids: Sequence[str | uuid.UUID] | None = None,
        labels: dict[str, str] | None = None,
        prompt_metadata: dict[str, str | int] | None = None,
        sent_after: datetime | None = None,
        sent_before: datetime | None = None,
        original_values: Sequence[str] | None = None,
        converted_values: Sequence[str] | None = None,
        data_type: str | None = None,
        not_data_type: str | None = None,
        converted_value_sha256: Sequence[str] | None = None,
        identifier_filters: Sequence[IdentifierFilter] | None = None,
    ) -> Sequence[MessagePiece]:
        """
        Retrieve a list of MessagePiece objects based on the specified filters.

        Args:
            role (str | None, optional): The role of the prompt. Defaults to None.
            conversation_id (str | uuid.UUID | None, optional): The ID of the conversation. Defaults to None.
            prompt_ids (Sequence[str] | Sequence[uuid.UUID] | None, optional): A list of prompt IDs.
                Defaults to None.
            labels (dict[str, str] | None, optional): A dictionary of labels. Defaults to None.
            prompt_metadata (dict[str, str | int] | None, optional): The metadata associated with the prompt.
                Defaults to None.
            sent_after (datetime | None, optional): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (datetime | None, optional): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Sequence[str] | None, optional): A list of original values. Defaults to None.
            converted_values (Sequence[str] | None, optional): A list of converted values. Defaults to None.
            data_type (str | None, optional): The data type to filter by. Defaults to None.
            not_data_type (str | None, optional): The data type to exclude. Defaults to None.
            converted_value_sha256 (Sequence[str] | None, optional): A list of SHA256 hashes of converted values.
                Defaults to None.
            identifier_filters (Sequence[IdentifierFilter] | None, optional):
                A sequence of IdentifierFilter objects that
                allow filtering by various identifier JSON properties. Defaults to None.

        Returns:
            Sequence[MessagePiece]: A list of MessagePiece objects that match the specified filters.

        Raises:
            Exception: If there is an error retrieving the prompts,
                an exception is logged and an empty list is returned.
        """
        if prompt_ids is not None and len(prompt_ids) == 0:
            return []

        try:
            conditions: list[Any] = []
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
                    self._build_message_piece_identifier_conditions(identifier_filters=identifier_filters)
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
            duplicated_message = message.duplicate()

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
        messages = self.get_conversation_messages(conversation_id=conversation_id)
        source_metadata = self._get_conversation(conversation_id=conversation_id)
        source_target = source_metadata.target_identifier if source_metadata else None
        new_conversation_id, all_pieces = self.duplicate_messages(messages=messages)
        if all_pieces:
            self.add_conversation_to_memory(
                conversation=Conversation(conversation_id=new_conversation_id, target_identifier=source_target)
            )
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
        messages = self.get_conversation_messages(conversation_id=conversation_id)

        # remove the final turn from the conversation
        if len(messages) == 0:
            return str(uuid.uuid4())

        last_message = messages[-1]

        length_of_sequence_to_remove = 0

        length_of_sequence_to_remove = 1 if last_message.api_role == "system" or last_message.api_role == "user" else 2

        messages_to_duplicate = [
            message for message in messages if message.sequence <= last_message.sequence - length_of_sequence_to_remove
        ]

        source_metadata = self._get_conversation(conversation_id=conversation_id)
        source_target = source_metadata.target_identifier if source_metadata else None
        new_conversation_id, all_pieces = self.duplicate_messages(messages=messages_to_duplicate)
        if all_pieces:
            self.add_conversation_to_memory(
                conversation=Conversation(conversation_id=new_conversation_id, target_identifier=source_target)
            )
            self.add_message_pieces_to_memory(message_pieces=all_pieces)

        return new_conversation_id

    def add_message_to_memory(self, *, request: Message) -> None:
        """
        Insert a list of message pieces into the memory storage.

        Automatically updates the sequence to be the next number in the conversation.
        If necessary, generates embedding data for applicable entries

        Args:
            request (Message): The message to add to the memory.
        """
        request.validate()

        embedding_entries = []
        message_pieces = request.message_pieces

        pieces_to_persist = [piece for piece in message_pieces if not piece.not_in_memory]
        if not pieces_to_persist:
            return

        self._update_sequence(message_pieces=message_pieces)

        # conversation_id validation happens in add_message_pieces_to_memory, the shared choke point.
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

    def update_prompt_metadata_by_conversation_id(
        self, *, conversation_id: str, prompt_metadata: dict[str, str | int]
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

    def _run_schema_migration(self, *, silent: bool = False) -> None:
        """
        Run schema migrations to ensure the database schema is up to date.

        Args:
            silent (bool): If True, suppresses Alembic console output. Defaults to False.

        Raises:
            ValueError: If an invalid schema handling option is provided.
            RuntimeError: If the engine is not initialized when required.
            Exception: If there is an error during schema validation or migration.
        """
        from pyrit.memory.migration import check_schema_migrations, run_schema_migrations

        logger.info("Running schema migration.")
        if self.engine is None:
            raise RuntimeError("Engine must be initialized to run schema migrations.")
        run_schema_migrations(engine=self.engine, silent=silent)
        check_schema_migrations(engine=self.engine, silent=silent)

    def _check_schema_migration(self, *, silent: bool = False) -> None:
        """
        Verify that the current database schema matches the models without modifying the database.

        Args:
            silent (bool): If True, suppresses Alembic console output. Defaults to False.

        Raises:
            RuntimeError: If the engine is not initialized.
            AutogenerateDiffsDetected: If the schema does not match the models.
        """
        from pyrit.memory.migration import check_schema_migrations

        logger.info("Checking schema migration compatibility.")
        if self.engine is None:
            raise RuntimeError("Engine must be initialized to check schema migrations.")
        check_schema_migrations(engine=self.engine, silent=silent)

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

    def dispose_engine(self) -> None:
        """
        Dispose the engine and clean up resources.
        """
        if self.engine:
            self.engine.dispose()
            previous_raise = logging.raiseExceptions
            logging.raiseExceptions = False
            try:
                logger.info("Engine disposed successfully.")
            finally:
                logging.raiseExceptions = previous_raise

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
        value: str | None = None,
        value_sha256: Sequence[str] | None = None,
        dataset_name: str | None = None,
        dataset_name_pattern: str | None = None,
        data_types: Sequence[str] | None = None,
        harm_categories: Sequence[str] | None = None,
        added_by: str | None = None,
        authors: Sequence[str] | None = None,
        groups: Sequence[str] | None = None,
        source: str | None = None,
        seed_type: SeedType | None = None,
        parameters: Sequence[str] | None = None,
        metadata: dict[str, str | int] | None = None,
        prompt_group_ids: Sequence[uuid.UUID] | None = None,
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
            data_types (Sequence[str] | None): List of data types to filter seed prompts by
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
        self, field: InstrumentedAttribute[Any], conditions: list[Any], values: Sequence[str] | None = None
    ) -> None:
        if values:
            conditions.extend(field.contains(value) for value in values)

    async def _serialize_seed_value_async(self, prompt: Seed) -> str:
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
            original_img_bytes = await serializer.read_data_base64_async()
            # Save the image
            await serializer.save_b64_image_async(original_img_bytes)
            serialized_prompt_value = str(serializer.value)
        elif prompt.data_type in ["audio_path", "video_path"]:
            audio_bytes = await serializer.read_data_async()
            await serializer.save_data_async(data=audio_bytes)
            serialized_prompt_value = str(serializer.value)
        return serialized_prompt_value or ""

    async def _prepare_seed_for_storage_async(
        self, *, prompt: Seed, added_by: str | None, current_time: datetime
    ) -> None:
        """
        Prepare a seed in place for persistence.

        Sets provenance and timestamp, serializes any media value to storage, and computes the
        SHA256 used for identity and deduplication. Performs no database writes, so it is safe to
        call before opening a transaction.

        Args:
            prompt (Seed): The seed to prepare; it is mutated in place.
            added_by (str | None): The user to attribute the seed to; overrides an existing value.
            current_time (datetime): The timestamp to apply when the seed has no ``date_added``.

        Raises:
            ValueError: If ``added_by`` is not set on the seed and none is provided.
        """
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
            prompt.value = await self._serialize_seed_value_async(prompt=prompt)

        await set_seed_sha256_async(prompt)

    async def add_seeds_to_memory_async(self, *, seeds: Sequence[Seed], added_by: str | None = None) -> None:
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
            await self._prepare_seed_for_storage_async(prompt=prompt, added_by=added_by, current_time=current_time)

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

    async def replace_seeds_for_dataset_async(
        self, *, dataset_name: str, seeds: Sequence[Seed], added_by: str | None = None
    ) -> int:
        """
        Atomically replace all stored seeds for a dataset with a new set.

        Every existing ``SeedPromptEntries`` row for ``dataset_name`` is deleted and the provided
        seeds are inserted in a single transaction and commit; if the insert fails the delete is
        rolled back with it, so the previously stored seeds are preserved. Seeds are prepared
        (media serialized, SHA256 computed) before the transaction opens. Deduplication is
        intentionally skipped: this is a full replace, so the provided seeds are stored as given.

        The isolation guarantee is the database transaction boundary: a reader that queries after
        the commit sees the complete new set. This holds on the file-backed SQLite and Azure SQL
        backends, where each session has its own connection. The in-memory SQLite backend shares a
        single connection across all sessions, so it does not isolate concurrent sessions from one
        another; callers that need to read a dataset while it is being replaced should use a
        file-backed or Azure SQL backend. ``RefreshDatasets`` replaces datasets sequentially, so it
        does not rely on cross-session isolation.

        ``SeedPromptEntries`` has no dependent foreign keys, so no related rows are removed first.
        Deleting media-backed seeds (``image_path``, ``audio_path``, ``video_path``) removes only the
        database rows; any serialized media files they reference are left on disk. This matches every
        other seed-delete path and results in disk bloat, not data loss.

        Args:
            dataset_name (str): The name of the dataset whose seeds should be replaced.
            seeds (Sequence[Seed]): The new seeds to store for the dataset; must be non-empty and
                every seed's ``dataset_name`` must equal ``dataset_name``.
            added_by (str | None): The user to attribute the new seeds to.

        Returns:
            int: The number of ``SeedPromptEntries`` deleted before the new seeds were inserted.

        Raises:
            ValueError: If ``dataset_name`` is empty, ``seeds`` is empty, or any seed's
                ``dataset_name`` does not match ``dataset_name``.
            SQLAlchemyError: If the replacement fails; the transaction is rolled back first.
        """
        if not dataset_name:
            raise ValueError("dataset_name must be a non-empty string.")
        if not seeds:
            raise ValueError("seeds must be non-empty; refusing to replace a dataset with nothing.")
        mismatched = sorted(
            {seed.dataset_name for seed in seeds if seed.dataset_name != dataset_name},
            key=lambda name: (name is None, name or ""),
        )
        if mismatched:
            raise ValueError(
                f"All seeds must belong to dataset '{dataset_name}', but got mismatched "
                f"dataset_name(s): {mismatched}. Refusing to delete '{dataset_name}' and insert "
                "seeds tagged for another dataset."
            )

        current_time = datetime.now(tz=timezone.utc)
        entries: list[SeedEntry] = []
        for prompt in seeds:
            await self._prepare_seed_for_storage_async(prompt=prompt, added_by=added_by, current_time=current_time)
            entries.append(SeedEntry(entry=prompt))

        with closing(self.get_session()) as session:
            try:
                deleted = (
                    session.query(SeedEntry)
                    .filter(SeedEntry.dataset_name == dataset_name)
                    .delete(synchronize_session=False)
                )
                session.add_all(entries)
                session.commit()
                return deleted
            except SQLAlchemyError as e:
                session.rollback()
                logger.exception(f"Error replacing seeds for dataset {dataset_name}: {e}")
                raise

    async def add_seed_groups_to_memory_async(
        self, *, prompt_groups: Sequence[SeedGroup], added_by: str | None = None
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
        value: str | None = None,
        value_sha256: Sequence[str] | None = None,
        dataset_name: str | None = None,
        dataset_name_pattern: str | None = None,
        data_types: Sequence[str] | None = None,
        harm_categories: Sequence[str] | None = None,
        added_by: str | None = None,
        authors: Sequence[str] | None = None,
        groups: Sequence[str] | None = None,
        source: str | None = None,
        seed_type: SeedType | None = None,
        parameters: Sequence[str] | None = None,
        metadata: dict[str, str | int] | None = None,
        prompt_group_ids: Sequence[uuid.UUID] | None = None,
        group_length: Sequence[int] | None = None,
    ) -> Sequence[SeedGroup]:
        """
        Retrieve groups of seed prompts based on the provided filtering criteria.

        Args:
            value (str | None, Optional): The value to match by substring.
            value_sha256 (Sequence[str] | None, Optional): SHA256 hash of value to filter seed groups by.
            dataset_name (str | None, Optional): Name of the dataset to match exactly.
            dataset_name_pattern (str | None, Optional): A pattern to match dataset names using SQL LIKE syntax.
                Supports wildcards: % (any characters) and _ (single character).
                Examples: "harm%" matches names starting with "harm", "%test%" matches names containing "test".
                If both dataset_name and dataset_name_pattern are provided, dataset_name takes precedence.
            data_types (Sequence[str] | None, Optional): List of data types to filter seed prompts by
            (e.g., text, image_path).
            harm_categories (Sequence[str] | None, Optional): List of harm categories to filter seed prompts by.
            added_by (str | None, Optional): The user who added the seed groups to filter by.
            authors (Sequence[str] | None, Optional): List of authors to filter seed groups by.
            groups (Sequence[str] | None, Optional): List of groups to filter seed groups by.
            source (str | None, Optional): The source from which the seed prompts originated.
            seed_type (SeedType | None, Optional): The type of seed to filter by ("prompt", "objective", or
                "simulated_conversation").
            parameters (Sequence[str] | None, Optional): List of parameters to filter by.
            metadata (dict[str, str | int] | None, Optional): A free-form dictionary for tagging
                prompts with custom metadata.
            prompt_group_ids (Sequence[uuid.UUID] | None, Optional): List of prompt group IDs to filter by.
            group_length (Sequence[int] | None, Optional): The number of seeds in the group to filter by.

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
                for attack_result in attack_results:
                    if attack_result.atomic_attack_identifier is not None:
                        self._persist_identifier(
                            session=session,
                            identifier=AtomicAttackIdentifier.from_component_identifier(
                                attack_result.atomic_attack_identifier
                            ),
                        )
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
        attack_result_ids: Sequence[str] | None = None,
        conversation_id: str | None = None,
        objective: str | None = None,
        objective_sha256: Sequence[str] | None = None,
        outcome: str | None = None,
        attack_classes: Sequence[str] | None = None,
        atomic_attack_eval_hashes: Sequence[str] | None = None,
        converter_classes: Sequence[str] | None = None,
        converter_classes_match: Literal["all", "any"] = "all",
        has_converters: bool | None = None,
        labels: dict[str, str | Sequence[str]] | None = None,
        targeted_harm_categories: Sequence[str] | None = None,
        identifier_filters: Sequence[IdentifierFilter] | None = None,
        scenario_result_id: str | None = None,
    ) -> Sequence[AttackResult]:
        """
        Retrieve a list of AttackResult objects based on the specified filters.

        Args:
            attack_result_ids (Sequence[str] | None, optional): A list of attack result IDs. Defaults to None.
            conversation_id (str | None, optional): The conversation ID to filter by. Defaults to None.
            objective (str | None, optional): The objective to filter by (substring match). Defaults to None.
            objective_sha256 (Sequence[str] | None, optional): A list of objective SHA256 hashes to filter by.
                Defaults to None.
            outcome (str | None, optional): The outcome to filter by (success, failure, undetermined).
                Defaults to None.
            attack_classes (Sequence[str] | None, optional): Filter by exact attack class_name in
                atomic_attack_identifier. Returns attacks matching ANY of the listed class names
                (OR logic, case-sensitive). An empty sequence applies no filter. Defaults to None.
            atomic_attack_eval_hashes (Sequence[str] | None, optional): Filter by behavioral
                equivalence hash on ``atomic_attack_identifier.eval_hash`` (auto-stamped on persistence
                by ``AtomicAttackEvaluationIdentifier``). Returns results matching ANY of the listed
                hashes (OR logic, case-sensitive). Designed for ASR aggregation by technique
                configuration. An empty sequence applies no filter. Defaults to None.
            converter_classes (Sequence[str] | None, optional): Filter by converter class names.
                Combination semantics for multiple entries are controlled by ``converter_classes_match``.
                An empty sequence filters to attacks that used no converters; ``None`` applies no
                filter. To filter by presence/absence of any converter explicitly, use the
                ``has_converters`` parameter instead. Defaults to None.
            converter_classes_match (Literal["all", "any"]): How to combine multiple entries in
                ``converter_classes``. ``"all"`` (default) matches attacks that used every listed
                converter (AND, case-insensitive). ``"any"`` matches attacks that used at least one
                listed converter (OR, case-insensitive). Ignored when ``converter_classes`` has
                fewer than 2 entries or is empty.
            has_converters (bool | None, optional): Filter by converter presence.
                ``True`` returns only attacks that used at least one converter. ``False`` returns
                only attacks that used no converters. ``None`` applies no filter. Defaults to None.
            labels (dict[str, str | Sequence[str]] | None, optional): Filter results
                by attack labels. Entries are AND-combined across label names; within a
                single entry, a string value is an equality match and a sequence value is
                an OR match over the listed values. An empty sequence applies no filter
                for that label. Example: ``{"operator": "roakey", "operation":
                ["roakey_op_a", "roakey_op_b"]}`` matches attacks where ``operator ==
                "roakey"`` AND (``operation == "roakey_op_a"`` OR ``operation ==
                "roakey_op_b"``). Defaults to None.
            targeted_harm_categories (Sequence[str] | None, optional): Filter results by the
                harm categories targeted by the attack (stored on
                ``AttackResultEntry.targeted_harm_categories``, auto-populated from the
                attack's SeedGroup). Returns attacks targeting ANY of the listed categories
                (OR logic, case-insensitive). An empty sequence applies no filter. Defaults
                to None.
            identifier_filters (Sequence[IdentifierFilter] | None, optional):
                A sequence of IdentifierFilter objects that allows filtering by various attack identifier
                JSON properties. Defaults to None.
            scenario_result_id (str | None, optional): Filter to attack results linked to a
                specific scenario via the ``AttackResultEntry.attribution_parent_id`` foreign key.
                Combined with ``outcome=AttackOutcome.ERROR`` this is the replacement for the
                removed per-scenario error_attack_result_ids manifest. Defaults to None.

        Returns:
            Sequence[AttackResult]: A list of AttackResult objects that match the specified filters.

        Raises:
            ValueError: If any label key contains characters outside the allowlist
                ``[A-Za-z0-9_.-]+``.
        """
        # Handle empty list cases
        if attack_result_ids is not None and len(attack_result_ids) == 0:
            return []
        if objective_sha256 is not None and len(objective_sha256) == 0:
            return []

        # Build non-list conditions
        conditions: list[ColumnElement[bool]] = []
        if conversation_id:
            conditions.append(AttackResultEntry.conversation_id == conversation_id)
        if objective:
            conditions.append(AttackResultEntry.objective.contains(objective))
        if outcome:
            conditions.append(AttackResultEntry.outcome == outcome)
        if scenario_result_id:
            conditions.append(AttackResultEntry.attribution_parent_id == uuid.UUID(scenario_result_id))

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

        if atomic_attack_eval_hashes:
            # Single JSON path query on the auto-stamped eval_hash. OR-combined across
            # supplied hashes so callers can fetch history for multiple technique
            # configurations in one round trip.
            conditions.append(
                or_(
                    *[
                        self._get_condition_json_property_match(
                            json_column=AttackResultEntry.atomic_attack_identifier,
                            property_path="$.eval_hash",
                            value=h,
                            case_sensitive=True,
                        )
                        for h in atomic_attack_eval_hashes
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
            invalid_keys = [k for k in effective_labels if not self._LABEL_KEY_PATTERN.match(k)]
            if invalid_keys:
                raise ValueError(
                    f"Invalid label key(s) {invalid_keys!r}: keys must match {self._LABEL_KEY_PATTERN.pattern}."
                )
            if effective_labels:
                # Use database-specific JSON query method
                conditions.append(self._get_attack_result_label_condition(labels=effective_labels))

        if targeted_harm_categories:
            # Match attacks whose targeted_harm_categories array contains ANY of the
            # requested categories.
            conditions.append(
                self._get_condition_json_array_match(
                    json_column=AttackResultEntry.targeted_harm_categories,
                    property_path="$",
                    array_to_match=list(targeted_harm_categories),
                    match_mode="any",
                )
            )

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

        Returns:
            dict[str, list[str]]: Mapping of label keys to sorted lists of
            unique values.
        """
        label_values: dict[str, set[str]] = {}

        with closing(self.get_session()) as session:
            are_rows = (
                session.query(AttackResultEntry.labels).filter(AttackResultEntry.labels.isnot(None)).distinct().all()
            )

        for (labels,) in are_rows:
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

        Raises:
            SQLAlchemyError: If a scenario result or identifier graph cannot be persisted.
        """
        entries = [ScenarioResultEntry(entry=scenario_result) for scenario_result in scenario_results]
        with closing(self.get_session()) as session:
            try:
                for scenario_result in scenario_results:
                    self._persist_scenario_identifier(
                        session=session,
                        scenario_identifier=scenario_result.scenario_identifier,
                    )
                session.add_all(entries)
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                raise

    @classmethod
    def _persist_scenario_identifier(cls, *, session: Any, scenario_identifier: ScenarioIdentifier) -> None:
        """Persist a scenario identifier and its target and scorer dependencies."""
        cls._persist_identifier(session=session, identifier=scenario_identifier)

    def update_scenario_run_state(
        self,
        *,
        scenario_result_id: str,
        scenario_run_state: str,
        error_message: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """
        Update the run state of an existing scenario result.

        Performs a targeted UPDATE of only the state/error columns instead of
        rebuilding the entire ``ScenarioResultEntry`` row.

        Args:
            scenario_result_id (str): The ID of the scenario result to update.
            scenario_run_state (str): The new state for the scenario
                (e.g., "CREATED", "IN_PROGRESS", "COMPLETED", "FAILED").
            error_message (str | None): Optional scenario-level error message.
            error_type (str | None): Optional exception class name.

        Raises:
            ValueError: If the scenario result is not found.
        """
        with closing(self.get_session()) as session:
            entry = session.query(ScenarioResultEntry).filter_by(id=scenario_result_id).first()

            if not entry:
                raise ValueError(f"Scenario result with ID {scenario_result_id} not found in memory")

            entry.scenario_run_state = scenario_run_state
            if error_message is not None:
                entry.error_message = error_message
            if error_type is not None:
                entry.error_type = error_type

            session.commit()

        logger.info(f"Updated scenario {scenario_result_id} state to '{scenario_run_state}'")

    def update_scenario_metadata(
        self,
        *,
        scenario_result_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """
        Replace the ``scenario_metadata`` JSON blob on an existing scenario result.

        Used by the scenario layer to persist first-run state (e.g.
        ``objective_hashes``) that resume needs to replay. Performs a
        targeted UPDATE so it doesn't clobber other columns.

        Args:
            scenario_result_id (str): The ID of the scenario result to update.
            metadata (dict[str, Any]): The full metadata dict to store. Pass the
                merged dict, not just the new keys — this writes the whole value.

        Raises:
            ValueError: If the scenario result is not found.
        """
        with closing(self.get_session()) as session:
            entry = session.query(ScenarioResultEntry).filter_by(id=scenario_result_id).first()
            if not entry:
                raise ValueError(f"Scenario result with ID {scenario_result_id} not found in memory")
            entry.scenario_metadata = metadata if metadata else None
            session.commit()

    def get_scenario_results(
        self,
        *,
        scenario_result_ids: Sequence[str] | None = None,
        scenario_name: str | None = None,
        scenario_version: int | None = None,
        pyrit_version: str | None = None,
        added_after: datetime | None = None,
        added_before: datetime | None = None,
        labels: dict[str, str] | None = None,
        objective_target_endpoint: str | None = None,
        objective_target_model_name: str | None = None,
        identifier_filters: Sequence[IdentifierFilter] | None = None,
        limit: int | None = None,
    ) -> Sequence[ScenarioResult]:
        """
        Retrieve a list of ScenarioResult objects based on the specified filters.

        Results are always ordered by completion_time descending (most recent first).

        Args:
            scenario_result_ids (Sequence[str] | None, optional): A list of scenario result IDs.
                Defaults to None.
            scenario_name (str | None, optional): The scenario name to filter by (substring match).
                Defaults to None.
            scenario_version (int | None, optional): The scenario version to filter by. Defaults to None.
            pyrit_version (str | None, optional): The PyRIT version to filter by. Defaults to None.
            added_after (datetime | None, optional): Filter for scenarios completed after this datetime.
                Defaults to None.
            added_before (datetime | None, optional): Filter for scenarios completed before this datetime.
                Defaults to None.
            labels (dict[str, str] | None, optional): A dictionary of memory labels to filter by.
                Defaults to None.
            objective_target_endpoint (str | None, optional): Filter for scenarios where the
                objective_target_identifier has an endpoint attribute containing this value (case-insensitive).
                Defaults to None.
            objective_target_model_name (str | None, optional): Filter for scenarios where the
                objective_target_identifier has a model_name attribute containing this value (case-insensitive).
                Defaults to None.
            identifier_filters (Sequence[IdentifierFilter] | None, optional):
                A sequence of IdentifierFilter objects that allows filtering by identifier JSON properties.
                Defaults to None.
            limit (int | None): Maximum number of results to return. Defaults to None (no limit).

        Returns:
            Sequence[ScenarioResult]: A list of ScenarioResult objects that match the specified filters,
                ordered by completion_time descending.
        """
        if scenario_result_ids is not None and len(scenario_result_ids) == 0:
            return []

        conditions = self._build_scenario_result_query_conditions(
            scenario_name=scenario_name,
            scenario_version=scenario_version,
            pyrit_version=pyrit_version,
            added_after=added_after,
            added_before=added_before,
            labels=labels,
            objective_target_endpoint=objective_target_endpoint,
            objective_target_model_name=objective_target_model_name,
            identifier_filters=identifier_filters,
        )

        try:
            entries = self._query_scenario_result_entries(
                scenario_result_ids=scenario_result_ids,
                conditions=conditions,
                limit=limit,
            )

            attack_results_by_scenario = self._get_attack_results_by_scenario(entries=entries)

            scenario_results: list[ScenarioResult] = []
            for entry in entries:
                scenario_result = entry.get_scenario_result()
                scenario_result.attack_results = attack_results_by_scenario.get(entry.id, {})
                scenario_results.append(scenario_result)

            return scenario_results
        except Exception as e:
            logger.exception(f"Failed to retrieve scenario results with error {e}")
            raise

    def _build_scenario_result_query_conditions(
        self,
        *,
        scenario_name: str | None,
        scenario_version: int | None,
        pyrit_version: str | None,
        added_after: datetime | None,
        added_before: datetime | None,
        labels: dict[str, str] | None,
        objective_target_endpoint: str | None,
        objective_target_model_name: str | None,
        identifier_filters: Sequence[IdentifierFilter] | None,
    ) -> "list[ColumnElement[bool]]":
        """
        Build the WHERE conditions for ``get_scenario_results``.

        Returns:
            list[ColumnElement[bool]]: SQLAlchemy WHERE clauses derived from the supplied filters.
        """
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

        return conditions

    def _query_scenario_result_entries(
        self,
        *,
        scenario_result_ids: Sequence[str] | None,
        conditions: "list[ColumnElement[bool]]",
        limit: int | None,
    ) -> Sequence[ScenarioResultEntry]:
        """
        Run the (possibly batched) ScenarioResultEntry query.

        Returns:
            Sequence[ScenarioResultEntry]: The matching rows ordered by completion_time descending.
        """
        order_by_clause = ScenarioResultEntry.completion_time.desc()

        if scenario_result_ids:
            return self._execute_batched_query(
                ScenarioResultEntry,
                batch_column=ScenarioResultEntry.id,
                batch_values=list(scenario_result_ids),
                other_conditions=conditions,
                order_by=order_by_clause,
                limit=limit,
            )

        return self._query_entries(
            ScenarioResultEntry,
            conditions=and_(*conditions) if conditions else None,
            order_by=order_by_clause,
            limit=limit,
        )

    def _get_attack_results_by_scenario(
        self,
        *,
        entries: Sequence[ScenarioResultEntry],
    ) -> dict[uuid.UUID, dict[str, list[AttackResult]]]:
        """
        Fetch every ``AttackResult`` linked to the given scenarios via the
        ``AttackResultEntry.attribution_parent_id`` foreign key in a single
        batched query, then group by scenario + ``parent_collection`` (which
        the scenario layer uses for the atomic attack name) and sort each
        group by ``AttackResultEntry.timestamp``.

        Foreign-key linkage is the sole source of truth — set at write-time by
        the attack persistence path when an ``AttackResultAttribution`` is on
        the context. Rows without a valid ``attribution_data`` payload are
        skipped (and logged) rather than guessed at.

        Returns:
            dict[uuid.UUID, dict[str, list[AttackResult]]]: Mapping of
            ``scenario_result_id`` → ``atomic_attack_name`` → ordered list of
            ``AttackResult`` objects. Scenarios with no linked rows map to ``{}``.
        """
        if not entries:
            return {}

        scenario_ids = [entry.id for entry in entries]
        attack_rows = self._execute_batched_query(
            AttackResultEntry,
            batch_column=AttackResultEntry.attribution_parent_id,
            batch_values=scenario_ids,
        )

        grouped: dict[uuid.UUID, dict[str, list[tuple[datetime, AttackResult]]]] = {entry.id: {} for entry in entries}

        for row in attack_rows:
            scenario_id = row.attribution_parent_id
            if scenario_id is None or scenario_id not in grouped:
                continue

            data = row.attribution_data or {}
            name = data.get("parent_collection")
            if not name:
                logger.debug(
                    f"Skipping AttackResultEntry {row.id} during scenario load: "
                    "attribution_data missing parent_collection"
                )
                continue

            sort_key = row.timestamp or datetime.min.replace(tzinfo=timezone.utc)
            grouped[scenario_id].setdefault(name, []).append((sort_key, row.get_attack_result()))

        return {
            scenario_id: {
                name: [ar for _, ar in sorted(bucket, key=lambda kv: kv[0])] for name, bucket in name_buckets.items()
            }
            for scenario_id, name_buckets in grouped.items()
        }

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
