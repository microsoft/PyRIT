# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    ARRAY,
    INTEGER,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    TypeDecorator,
    Unicode,
)
from sqlalchemy.dialects.sqlite import CHAR
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import Uuid

import pyrit
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY,
    AtomicAttackEvaluationIdentifier,
    AttackOutcome,
    AttackResult,
    ChatMessageRole,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    MessagePiece,
    PromptDataType,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunState,
    Score,
    ScorerEvaluationIdentifier,
    Seed,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    SeedType,
)
from pyrit.models.scenario_result import ScenarioRunState

logger = logging.getLogger(__name__)

# Default pyrit_version for database records created before version tracking was added
LEGACY_PYRIT_VERSION = "<0.10.0"

# Maximum length for string values in ComponentIdentifier.model_dump() when storing to the database.
# Longer values are truncated with a "..." suffix.
MAX_IDENTIFIER_VALUE_LENGTH: int = 80


def _dump_identifier(identifier: ComponentIdentifier | None) -> dict[str, Any] | None:
    """
    Serialize a ``ComponentIdentifier`` to a dict for JSON storage, truncating long values.

    Args:
        identifier (ComponentIdentifier | None): The identifier to serialize, or None.

    Returns:
        dict[str, Any] | None: The serialized identifier, or None if ``identifier`` is falsy.
    """
    if not identifier:
        return None
    return identifier.model_dump(context={"max_value_length": MAX_IDENTIFIER_VALUE_LENGTH})


def _dump_identifiers(identifiers: list[ComponentIdentifier]) -> list[dict[str, Any]]:
    """
    Serialize a list of ``ComponentIdentifier`` objects for JSON storage.

    Args:
        identifiers (list[ComponentIdentifier]): The identifiers to serialize.

    Returns:
        list[dict[str, Any]]: The serialized identifiers in order.
    """
    return [
        identifier.model_dump(context={"max_value_length": MAX_IDENTIFIER_VALUE_LENGTH}) for identifier in identifiers
    ]


def _load_identifier(stored: dict[str, Any] | None, *, pyrit_version: str | None = None) -> ComponentIdentifier | None:
    """
    Reconstruct a ``ComponentIdentifier`` from its stored dict representation.

    Args:
        stored (dict[str, Any] | None): The stored identifier dict, or None.
        pyrit_version (str | None): If provided, injected as the identifier's ``pyrit_version``
            so the reconstructed object reflects the version that created the row.

    Returns:
        ComponentIdentifier | None: The reconstructed identifier, or None if ``stored`` is falsy.
    """
    if not stored:
        return None
    if pyrit_version is not None:
        stored = {**stored, "pyrit_version": pyrit_version}
    return ComponentIdentifier.model_validate(stored)


def _load_identifiers(
    stored: Sequence[dict[str, Any] | None] | None, *, pyrit_version: str | None = None
) -> list[ComponentIdentifier] | None:
    """
    Reconstruct a list of ``ComponentIdentifier`` objects from their stored representation.

    Args:
        stored (Sequence[dict[str, Any] | None] | None): The stored identifier dicts, or None.
        pyrit_version (str | None): If provided, injected as each identifier's ``pyrit_version``.

    Returns:
        list[ComponentIdentifier] | None: The reconstructed identifiers, or None if
            ``stored`` is falsy.
    """
    if not stored:
        return None
    return [identifier for item in stored if (identifier := _load_identifier(item, pyrit_version=pyrit_version))]


class CustomUUID(TypeDecorator[uuid.UUID]):
    """
    A custom UUID type that works consistently across different database backends.
    For SQLite, stores UUIDs as strings and converts them back to UUID objects.
    For other databases, uses the native UUID type.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        """
        Load the dialect-specific implementation for UUID handling.

        Args:
            dialect: The database dialect being used.

        Returns:
            The appropriate type descriptor for the given dialect.
        """
        if dialect.name == "sqlite":
            return dialect.type_descriptor(CHAR(36))
        return dialect.type_descriptor(Uuid())

    def process_bind_param(self, value: uuid.UUID | None, dialect: Any) -> str | None:
        """
        Process a parameter value before binding it to a database statement.

        Args:
            value: The value to be processed (UUID or None).
            dialect: The database dialect being used.

        Returns:
            str or None: The string representation of the UUID or None if value is None.
        """
        return str(value) if value else None

    def process_result_value(self, value: uuid.UUID | str | None, dialect: Any) -> uuid.UUID | None:
        """
        Process a result value after it has been retrieved from the database.

        Args:
            value: The value to be processed (UUID or None).
            dialect: The database dialect being used.

        Returns:
            UUID or None: The UUID object or None if value is None.
        """
        if value is None:
            return None
        if dialect.name == "sqlite":
            return uuid.UUID(value) if isinstance(value, str) else value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


class UTCDateTime(TypeDecorator[datetime]):
    """
    A DateTime type that returns timezone-aware UTC datetimes.

    Databases such as SQLite store datetimes without timezone information and return naive
    ``datetime`` objects. This decorator attaches UTC tzinfo on read so callers always receive
    aware datetimes, removing the need to normalize at every read site.
    """

    impl = DateTime
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        """
        Attach UTC tzinfo to a naive datetime read from the database.

        Args:
            value (datetime | None): The value retrieved from the database.
            dialect (Any): The database dialect being used.

        Returns:
            datetime | None: The value with UTC tzinfo if it was naive, otherwise unchanged.
        """
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    """
    Base class for all database models.
    """


class PromptMemoryEntry(Base):
    """
    Represents the prompt data.

    Because of the nature of database and sql alchemy, type ignores are abundant :)

    Parameters:
        __tablename__ (str): The name of the database table.
        __table_args__ (dict): Additional arguments for the database table.
        id (Uuid): The unique identifier for the memory entry.
        role (PromptType): system, assistant, user
        conversation_id (str): The identifier for the conversation which is associated with a single target.
        sequence (int): The order of the conversation within a conversation_id.
            Can be the same number for multi-part requests or multi-part responses.
        timestamp (DateTime): The timestamp of the memory entry.
        labels (dict[str, str]): The labels associated with the memory entry. Several can be standardized.
        prompt_metadata (JSON): The metadata associated with the prompt. This can be specific to any scenarios.
            Because memory is how components talk with each other, this can be component specific.
            e.g. the URI from a file uploaded to a blob store, or a document type you want to upload.
        converters (list[PromptConverter]): The converters for the prompt.
        prompt_target (PromptTarget): The target for the prompt.
        attack_identifier (dict[str, str]): The attack identifier for the prompt.
        original_value_data_type (PromptDataType): The data type of the original prompt (text, image)
        original_value (str): The text of the original prompt. If prompt is an image, it's a link.
        original_value_sha256 (str): The SHA256 hash of the original prompt data.
        converted_value_data_type (PromptDataType): The data type of the converted prompt (text, image)
        converted_value (str): The text of the converted prompt. If prompt is an image, it's a link.
        converted_value_sha256 (str): The SHA256 hash of the original prompt data.
        idx_conversation_id (Index): The index for the conversation ID.
        original_prompt_id (UUID): The original prompt id. It is equal to id unless it is a duplicate.
        scores (list[ScoreEntry]): The list of scores associated with the prompt.

    Methods:
        __str__(): Returns a string representation of the memory entry.
    """

    __tablename__ = "PromptMemoryEntries"
    __table_args__ = {"extend_existing": True}
    id = mapped_column(CustomUUID, nullable=False, primary_key=True)
    role: Mapped[Literal["system", "user", "assistant", "simulated_assistant", "tool", "developer"]] = mapped_column(
        String, nullable=False
    )
    conversation_id = mapped_column(String, nullable=False)
    sequence = mapped_column(INTEGER, nullable=False)
    timestamp = mapped_column(UTCDateTime, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSON)
    prompt_metadata: Mapped[dict[str, str | int]] = mapped_column(JSON)
    converter_identifiers: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)
    prompt_target_identifier: Mapped[dict[str, str]] = mapped_column(JSON)
    attack_identifier: Mapped[dict[str, str]] = mapped_column(JSON)
    response_error: Mapped[Literal["blocked", "none", "processing", "unknown"]] = mapped_column(String, nullable=True)

    original_value_data_type: Mapped[PromptDataType] = mapped_column(String, nullable=False)
    original_value = mapped_column(Unicode, nullable=False)
    original_value_sha256 = mapped_column(String)

    converted_value_data_type: Mapped[PromptDataType] = mapped_column(String, nullable=False)
    converted_value = mapped_column(Unicode)
    converted_value_sha256 = mapped_column(String)

    idx_conversation_id = Index("idx_conversation_id", "conversation_id")

    original_prompt_id = mapped_column(CustomUUID, nullable=False)

    # Version of PyRIT used when this entry was created
    # Nullable for backwards compatibility with existing databases
    pyrit_version = mapped_column(String, nullable=True)

    scores: Mapped[list["ScoreEntry"]] = relationship(
        "ScoreEntry",
        primaryjoin="ScoreEntry.prompt_request_response_id == PromptMemoryEntry.original_prompt_id",
        back_populates="prompt_request_piece",
        foreign_keys="ScoreEntry.prompt_request_response_id",
    )

    def __init__(self, *, entry: MessagePiece) -> None:
        """
        Initialize a PromptMemoryEntry from a MessagePiece.

        Args:
            entry (MessagePiece): The message piece to convert into a database entry.
        """
        self.id = entry.id
        self.role = entry.role
        self.conversation_id = entry.conversation_id
        self.sequence = entry.sequence
        self.timestamp = entry.timestamp
        self.labels = entry.labels
        self.prompt_metadata = entry.prompt_metadata
        self.converter_identifiers = _dump_identifiers(entry.converter_identifiers)  # type: ignore[ty:invalid-assignment]
        self.prompt_target_identifier = _dump_identifier(entry.prompt_target_identifier) or {}
        self.attack_identifier = _dump_identifier(entry.attack_identifier) or {}

        self.original_value = entry.original_value
        self.original_value_data_type = entry.original_value_data_type
        self.original_value_sha256 = entry.original_value_sha256

        self.converted_value = entry.converted_value
        self.converted_value_data_type = entry.converted_value_data_type
        self.converted_value_sha256 = entry.converted_value_sha256

        self.response_error = entry.response_error  # type: ignore[ty:invalid-assignment]

        self.original_prompt_id = entry.original_prompt_id
        self.pyrit_version = pyrit.__version__

    def get_message_piece(self) -> MessagePiece:
        """
        Convert this database entry back into a MessagePiece object.

        Returns:
            MessagePiece: The reconstructed message piece with all its data.
        """
        # Reconstruct ComponentIdentifiers with the stored pyrit_version
        stored_version = self.pyrit_version or LEGACY_PYRIT_VERSION
        converter_ids = _load_identifiers(self.converter_identifiers, pyrit_version=stored_version)
        target_id = _load_identifier(self.prompt_target_identifier, pyrit_version=stored_version)
        attack_id = _load_identifier(self.attack_identifier, pyrit_version=stored_version)

        message_piece = MessagePiece(
            role=self.role,
            original_value=self.original_value,
            original_value_sha256=self.original_value_sha256,
            converted_value=self.converted_value,
            converted_value_sha256=self.converted_value_sha256,
            id=self.id,
            conversation_id=self.conversation_id,
            sequence=self.sequence,
            prompt_metadata=self.prompt_metadata,
            converter_identifiers=[c for c in (converter_ids or []) if c is not None],
            prompt_target_identifier=target_id,
            attack_identifier=attack_id,
            original_value_data_type=self.original_value_data_type,
            converted_value_data_type=self.converted_value_data_type,
            response_error=self.response_error or "none",
            original_prompt_id=self.original_prompt_id,
            timestamp=self.timestamp,
        )
        # Assign deprecated ``labels`` container post-construction so the DB-load
        # path does not trip the ``MessagePiece`` deprecation-kwarg validator.
        # ``validate_assignment=False`` on the model makes this assignment bypass
        # the model_validator entirely.
        message_piece.labels = self.labels or {}
        return message_piece

    def __str__(self) -> str:
        """
        Return a string representation of the memory entry.

        Returns:
            str: Formatted string representation of the memory entry.
        """
        if self.prompt_target_identifier:
            # prompt_target_identifier is stored as dict in the database
            class_name = self.prompt_target_identifier.get("class_name") or self.prompt_target_identifier.get(
                "__type__", "Unknown"
            )
            return f"{class_name}: {self.role}: {self.converted_value}"
        return f": {self.role}: {self.converted_value}"


class EmbeddingDataEntry(Base):
    """
    Represents the embedding data associated with conversation entries in the database.
    Each embedding is linked to a specific conversation entry via an id.

    Parameters:
        id (Uuid): The primary key, which is a foreign key referencing the UUID in the PromptMemoryEntries table.
        embedding (ARRAY(Float)): An array of floats representing the embedding vector.
        embedding_type_name (String): The name or type of the embedding, indicating the model or method used.
    """

    __tablename__ = "EmbeddingData"
    # Allows table redefinition if already defined.
    __table_args__ = {"extend_existing": True}
    id = mapped_column(Uuid(as_uuid=True), ForeignKey(f"{PromptMemoryEntry.__tablename__}.id"), primary_key=True)
    # Use ARRAY for PostgreSQL, JSON for SQLite and MSSQL (SQL Server/Azure SQL)
    embedding = mapped_column(ARRAY(Float).with_variant(JSON, "sqlite").with_variant(JSON, "mssql"))
    embedding_type_name = mapped_column(String)

    def __str__(self) -> str:
        """
        Return a string representation of the embedding data entry (its ID).

        Returns:
            str: The stringified ID of the entry.
        """
        return f"{self.id}"


class ScoreEntry(Base):
    """
    Represents the Score Memory Entry.

    """

    __tablename__ = "ScoreEntries"
    __table_args__ = {"extend_existing": True}

    id = mapped_column(CustomUUID, nullable=False, primary_key=True)
    score_value = mapped_column(String, nullable=False)
    score_value_description = mapped_column(String, nullable=True)
    score_type: Mapped[Literal["true_false", "float_scale", "unknown"]] = mapped_column(String, nullable=False)
    score_category: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    score_rationale = mapped_column(String, nullable=True)
    score_metadata: Mapped[dict[str, str | int | float]] = mapped_column(JSON)
    scorer_class_identifier: Mapped[dict[str, Any]] = mapped_column(JSON)
    prompt_request_response_id = mapped_column(CustomUUID, ForeignKey(f"{PromptMemoryEntry.__tablename__}.id"))
    timestamp = mapped_column(UTCDateTime, nullable=False)
    task = mapped_column(String, nullable=True)  # Deprecated: Use objective instead
    objective = mapped_column(String, nullable=True)
    # Version of PyRIT used when this score was created
    # Nullable for backwards compatibility with existing databases
    pyrit_version = mapped_column(String, nullable=True)
    prompt_request_piece: Mapped["PromptMemoryEntry"] = relationship("PromptMemoryEntry", back_populates="scores")

    def __init__(self, *, entry: Score) -> None:
        """
        Initialize a ScoreEntry from a Score object.

        Args:
            entry (Score): The score object to convert into a database entry.
        """
        self.id = entry.id
        self.score_value = entry.score_value
        self.score_value_description = entry.score_value_description
        self.score_type = entry.score_type
        self.score_category = entry.score_category
        self.score_rationale = entry.score_rationale
        self.score_metadata = entry.score_metadata or {}
        normalized_scorer = entry.scorer_class_identifier
        # Ensure eval_hash is set before truncation so it survives the DB round-trip
        if normalized_scorer is not None and normalized_scorer.eval_hash is None:
            normalized_scorer = normalized_scorer.with_eval_hash(
                ScorerEvaluationIdentifier(normalized_scorer).eval_hash
            )
        self.scorer_class_identifier = _dump_identifier(normalized_scorer) or {}
        self.prompt_request_response_id = entry.message_piece_id if entry.message_piece_id else None
        self.timestamp = entry.timestamp
        # Store in both columns for backward compatibility
        # New code should only read from objective
        self.task = entry.objective
        self.objective = entry.objective
        self.pyrit_version = pyrit.__version__

    def get_score(self) -> Score:
        """
        Convert this database entry back into a Score object.

        Returns:
            Score: The reconstructed score object with all its data.
        """
        # Convert dict back to ComponentIdentifier with the stored pyrit_version
        stored_version = self.pyrit_version or LEGACY_PYRIT_VERSION
        scorer_identifier = _load_identifier(self.scorer_class_identifier, pyrit_version=stored_version)
        return Score(
            id=self.id,
            score_value=self.score_value,
            score_value_description=self.score_value_description,
            score_type=self.score_type,
            score_category=self.score_category,
            score_rationale=self.score_rationale,
            score_metadata=self.score_metadata,
            scorer_class_identifier=scorer_identifier,
            message_piece_id=self.prompt_request_response_id,
            timestamp=self.timestamp,
            objective=self.objective,
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Convert this database entry to a dictionary.

        Returns:
            dict: The dictionary representation of the score entry.
        """
        return {
            "id": str(self.id),
            "score_value": self.score_value,
            "score_value_description": self.score_value_description,
            "score_type": self.score_type,
            "score_category": self.score_category,
            "score_rationale": self.score_rationale,
            "score_metadata": self.score_metadata,
            "scorer_class_identifier": self.scorer_class_identifier,
            "prompt_request_response_id": str(self.prompt_request_response_id),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "objective": self.objective,
        }


class ConversationMessageWithSimilarity(BaseModel):
    """
    Represents a conversation message with its similarity score.

    Attributes:
        role (str): The role of the message (e.g., "user", "assistant").
        content (str): The content of the message.
        metric (str): The metric used to calculate the similarity score.
        score (float): The similarity score (default is 0.0).
    """

    model_config = ConfigDict(extra="forbid")
    role: str
    content: str
    metric: str
    score: float = 0.0


class EmbeddingMessageWithSimilarity(BaseModel):
    """
    Represents an embedding message with its similarity score.

    Parameters:
        uuid (uuid.UUID): The UUID of the embedding message.
        metric (str): The metric used to calculate the similarity score.
        score (float): The similarity score (default is 0.0).
    """

    model_config = ConfigDict(extra="forbid")
    uuid: uuid.UUID
    metric: str
    score: float = 0.0


class SeedEntry(Base):
    """
    Represents the raw prompt or prompt template data as found in open datasets.

    Note: This is different from the PromptMemoryEntry which is the processed prompt data.
    SeedPrompt merely reflects basic prompts before plugging into attacks,
    running through models with corresponding attack strategies, and applying converters.
    PromptMemoryEntry captures the processed prompt data before and after the above steps.

    Parameters:
        __tablename__ (str): The name of the database table.
        __table_args__ (dict): Additional arguments for the database table.
        id (Uuid): The unique identifier for the memory entry.
        value (str): The value of the seed prompt.
        value_sha256 (str): The SHA256 hash of the value of the seed prompt data.
        data_type (PromptDataType): The data type of the seed prompt.
        dataset_name (str): The name of the dataset the seed prompt belongs to.
        harm_categories (list[str]): The harm categories associated with the seed prompt.
        description (str): The description of the seed prompt.
        authors (list[str]): The authors of the seed prompt.
        groups (list[str]): The groups involved in authoring the seed prompt (if any).
        source (str): The source of the seed prompt.
        date_added (DateTime): The date the seed prompt was added.
        added_by (str): The user who added the seed prompt.
        prompt_metadata (dict[str, str | int]): The metadata associated with the seed prompt. This includes
            information that is useful for the specific target you're probing, such as encoding data.
        parameters (list[str]): The parameters included in the value.
            Note that seed prompts do not have parameters, only prompt templates do.
            However, they are stored in the same table.
        prompt_group_id (uuid.UUID): The ID of a group the seed prompt may optionally belong to.
            Groups are used to organize prompts for multi-turn conversations or multi-modal prompts.
        sequence (int): The turn of the seed prompt in a group. When entire multi-turn conversations
            are stored, this is used to order the prompts.
        role (str): The role of the prompt (e.g., user, system, assistant).
        seed_type (SeedType): The type of seed - "prompt", "objective", or "simulated_conversation".

    Methods:
        __str__(): Returns a string representation of the memory entry.
    """

    __tablename__ = "SeedPromptEntries"
    __table_args__ = {"extend_existing": True}
    id = mapped_column(CustomUUID, nullable=False, primary_key=True)
    value = mapped_column(Unicode, nullable=False)
    value_sha256 = mapped_column(Unicode, nullable=True)
    data_type: Mapped[PromptDataType] = mapped_column(String, nullable=False)
    name = mapped_column(String, nullable=True)
    dataset_name = mapped_column(String, nullable=True)
    harm_categories: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    description = mapped_column(String, nullable=True)
    authors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    groups: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    source = mapped_column(String, nullable=True)
    date_added = mapped_column(UTCDateTime, nullable=False)
    added_by = mapped_column(String, nullable=False)
    prompt_metadata: Mapped[dict[str, str | int] | None] = mapped_column(JSON, nullable=True)
    parameters: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    prompt_group_id: Mapped[uuid.UUID | None] = mapped_column(CustomUUID, nullable=True)
    sequence: Mapped[int | None] = mapped_column(INTEGER, nullable=True)
    role: Mapped[ChatMessageRole | None] = mapped_column(String, nullable=True)
    seed_type: Mapped[SeedType] = mapped_column(String, nullable=False, default="prompt")

    def __init__(self, *, entry: Seed) -> None:
        """
        Initialize a SeedEntry from a Seed object.

        Args:
            entry (Seed): The seed object to convert into a database entry.
        """
        # Determine seed_type based on the Seed subclass
        if isinstance(entry, SeedObjective):
            seed_type: SeedType = "objective"
        elif isinstance(entry, SeedSimulatedConversation):
            seed_type = "simulated_conversation"
        else:
            seed_type = "prompt"

        self.id = entry.id
        self.value = entry.value
        self.value_sha256 = entry.value_sha256
        self.data_type = entry.data_type
        self.name = entry.name
        self.dataset_name = entry.dataset_name
        self.harm_categories = list(entry.harm_categories) if entry.harm_categories else None
        self.description = entry.description
        self.authors = list(entry.authors) if entry.authors else None
        self.groups = list(entry.groups) if entry.groups else None
        self.source = entry.source
        self.date_added = entry.date_added
        self.added_by = entry.added_by
        self.prompt_metadata = self._pack_seed_metadata(entry)
        self.prompt_group_id = entry.prompt_group_id
        self.seed_type = seed_type

        # SeedPrompt-specific fields
        if isinstance(entry, SeedPrompt):
            self.parameters = list(entry.parameters) if entry.parameters else None
            self.sequence = entry.sequence
            self.role = entry.role
        else:
            self.parameters = None
            self.sequence = None
            self.role = None

    @staticmethod
    def _pack_seed_metadata(entry: Seed) -> dict[str, str | int] | None:
        """
        Build the persisted ``prompt_metadata`` for ``entry``.

        Packs ``SeedPrompt.response_json_schema`` (when present) under the
        reserved ``SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY`` as a JSON-encoded
        string so the existing ``dict[str, str | int]`` column type stays
        honest. Always strips the reserved key from caller-supplied metadata
        first so a forged entry cannot smuggle in a fake schema.

        Args:
            entry (Seed): The seed to serialize.

        Returns:
            dict[str, str | int] | None: The metadata dict to persist (or
            ``None`` when the caller's metadata was ``None`` and no schema
            needed packing).

        Raises:
            TypeError: If ``entry.response_json_schema`` contains values that
                are not JSON-serializable. The re-raised error includes the
                seed's type name and ``name`` to make the bad seed easy to
                locate.
        """
        raw = entry.metadata
        schema = getattr(entry, "response_json_schema", None)

        if not raw and schema is None:
            return raw

        packed: dict[str, str | int] = dict(raw) if raw else {}
        # Defensive strip — the reserved key is owned by this class.
        packed.pop(SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY, None)
        if schema is not None:
            try:
                packed[SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY] = json.dumps(schema, sort_keys=True)
            except TypeError as exc:
                # json.dumps surfaces non-JSON-serializable members deep inside the
                # schema as a bare TypeError. Re-raise with context the caller can
                # actually act on (which seed, which class, which type).
                raise TypeError(
                    f"response_json_schema on {type(entry).__name__} "
                    f"(name={getattr(entry, 'name', None)!r}) is not JSON-serializable: {exc}. "
                    "Schemas must contain only JSON-native types (dict, list, str, int, float, bool, None)."
                ) from exc
        return packed

    @staticmethod
    def _unpack_seed_metadata(
        raw: dict[str, str | int] | None,
    ) -> tuple[dict[str, str | int] | None, dict[str, Any] | None]:
        """
        Unpack the reserved schema key from a persisted ``prompt_metadata`` dict.

        Args:
            raw (dict[str, str | int] | None): Metadata as stored in the
                database.

        Returns:
            tuple[dict[str, str | int] | None, dict[str, Any] | None]:
                ``(cleaned_metadata, decoded_response_json_schema)``. The
                cleaned dict never contains the reserved key, even when the
                encoded value was malformed.
        """
        if not raw:
            return raw, None
        cleaned = dict(raw)
        encoded = cleaned.pop(SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY, None)
        if not isinstance(encoded, str):
            return cleaned, None
        try:
            decoded = json.loads(encoded)
        except (json.JSONDecodeError, TypeError):
            # Corrupt entry — surface the cleaned metadata without a schema.
            decoded = None
        return cleaned, decoded

    def get_seed(self) -> Seed:
        """
        Convert this database entry back into a Seed object.

        Returns:
            Seed: The reconstructed seed object (SeedPrompt, SeedObjective, or SeedSimulatedConversation)
        """
        cleaned_metadata, decoded_schema = self._unpack_seed_metadata(self.prompt_metadata)
        if self.seed_type == "objective":
            return SeedObjective(
                id=self.id,
                value=self.value,
                value_sha256=self.value_sha256,
                name=self.name,
                dataset_name=self.dataset_name,
                harm_categories=self.harm_categories,
                description=self.description,
                authors=self.authors,
                groups=self.groups,
                source=self.source,
                date_added=self.date_added,
                added_by=self.added_by,
                metadata=cleaned_metadata,
                prompt_group_id=self.prompt_group_id,
            )
        if self.seed_type == "simulated_conversation":
            # Reconstruct SeedSimulatedConversation from JSON value
            config = json.loads(self.value)
            return SeedSimulatedConversation(
                id=self.id,
                value_sha256=self.value_sha256,
                name=self.name,
                dataset_name=self.dataset_name,
                harm_categories=self.harm_categories,
                description=self.description,
                authors=self.authors,
                groups=self.groups,
                source=self.source,
                date_added=self.date_added,
                added_by=self.added_by,
                metadata=cleaned_metadata,
                prompt_group_id=self.prompt_group_id,
                num_turns=config.get("num_turns", 3),
                sequence=config.get("sequence", 0),
                adversarial_chat_system_prompt_path=config.get("adversarial_chat_system_prompt_path"),
                simulated_target_system_prompt_path=config.get("simulated_target_system_prompt_path"),
                next_message_system_prompt_path=config.get("next_message_system_prompt_path"),
            )
        return SeedPrompt(
            id=self.id,
            value=self.value,
            value_sha256=self.value_sha256,
            data_type=self.data_type,
            name=self.name,
            dataset_name=self.dataset_name,
            harm_categories=self.harm_categories,
            description=self.description,
            authors=self.authors,
            groups=self.groups,
            source=self.source,
            date_added=self.date_added,
            added_by=self.added_by,
            metadata=cleaned_metadata,
            response_json_schema=decoded_schema,
            parameters=self.parameters,
            prompt_group_id=self.prompt_group_id,
            sequence=self.sequence or 0,
            role=self.role,
        )


class AttackResultEntry(Base):
    """
    Represents the attack result data in the database.

    Parameters:
        __tablename__ (str): The name of the database table.
        __table_args__ (dict): Additional arguments for the database table.
        id (Uuid): The unique identifier for the attack result entry.
        conversation_id (str): The unique identifier of the conversation that produced this result.
        objective (str): Natural-language description of the attacker's objective.
        atomic_attack_identifier (dict[str, Any] | None): Composite identifier of the attack
            (technique, seeds, etc.).
        objective_sha256 (str): The SHA256 hash of the objective.
        last_response_id (Uuid): Foreign key to the last response MessagePiece.
        last_score_id (Uuid): Foreign key to the last score ScoreEntry.
        executed_turns (int): Total number of turns that were executed.
        execution_time_ms (int): Total execution time of the attack in milliseconds.
        outcome (AttackOutcome): The outcome of the attack, indicating success, failure, or undetermined.
        outcome_reason (str): Optional reason for the outcome, providing additional context.
        attack_metadata (dict[str, Any]): Metadata can be included as key-value pairs to provide extra context.
        labels (dict[str, str]): Optional labels associated with the attack result entry.
        pruned_conversation_ids (list[str]): List of conversation IDs that were pruned from the attack.
        adversarial_chat_conversation_ids (list[str]): List of conversation IDs used for adversarial chat.
        timestamp (DateTime): The timestamp of the attack result entry.
        last_response (PromptMemoryEntry): Relationship to the last response prompt memory entry.
        last_score (ScoreEntry): Relationship to the last score entry.

    Methods:
        __str__(): Returns a string representation of the attack result entry.
    """

    __tablename__ = "AttackResultEntries"
    __table_args__ = {"extend_existing": True}
    id = mapped_column(CustomUUID, nullable=False, primary_key=True)
    conversation_id = mapped_column(String, nullable=False)
    objective = mapped_column(Unicode, nullable=False)
    atomic_attack_identifier: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    objective_sha256 = mapped_column(String, nullable=True)
    last_response_id: Mapped[uuid.UUID | None] = mapped_column(
        CustomUUID, ForeignKey(f"{PromptMemoryEntry.__tablename__}.id"), nullable=True
    )
    last_score_id: Mapped[uuid.UUID | None] = mapped_column(
        CustomUUID, ForeignKey(f"{ScoreEntry.__tablename__}.id"), nullable=True
    )
    executed_turns = mapped_column(INTEGER, nullable=False, default=0)
    execution_time_ms = mapped_column(INTEGER, nullable=False, default=0)
    outcome: Mapped[Literal["success", "failure", "error", "undetermined"]] = mapped_column(
        String, nullable=False, default="undetermined"
    )
    outcome_reason = mapped_column(String, nullable=True)
    attack_metadata: Mapped[dict[str, str | int | float | bool] | None] = mapped_column(JSON, nullable=True)
    labels: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    pruned_conversation_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    adversarial_chat_conversation_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    timestamp = mapped_column(UTCDateTime, nullable=False)
    # Version of PyRIT used when this attack result was created
    # Nullable for backwards compatibility with existing databases
    pyrit_version = mapped_column(String, nullable=True)

    # Error information (populated when attack fails with exception)
    error_message = mapped_column(Unicode, nullable=True)
    error_type = mapped_column(String, nullable=True)
    error_traceback = mapped_column(Unicode, nullable=True)

    # Retry events (JSON-serialized list of RetryEvent dicts)
    retry_events_json: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    total_retries = mapped_column(INTEGER, nullable=True, default=0)

    # Attribution / parent linkage (set when the AttackResult is produced
    # inside an orchestrator that supplies an AttackResultAttribution, e.g. a
    # Scenario). attribution_parent_id is an indexed foreign key so per-parent
    # hydration and resume queries are direct lookups (no JSON manifest
    # required, no orphaning if the orchestrator is interrupted mid-run).
    # attribution_data is a documented-fixed-schema JSON blob keyed by
    # parent_collection (str). When the AttackResult is created outside an
    # orchestrator both fields remain NULL.
    attribution_parent_id: Mapped[uuid.UUID | None] = mapped_column(
        CustomUUID, ForeignKey("ScenarioResultEntries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attribution_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    last_response: Mapped["PromptMemoryEntry | None"] = relationship(
        "PromptMemoryEntry",
        foreign_keys=[last_response_id],
    )
    last_score: Mapped["ScoreEntry | None"] = relationship(
        "ScoreEntry",
        foreign_keys=[last_score_id],
    )

    def __init__(self, *, entry: AttackResult) -> None:
        """
        Initialize an AttackResultEntry from an AttackResult object.

        Args:
            entry (AttackResult): The attack result object to convert into a database entry.
        """
        self.id = uuid.UUID(entry.attack_result_id)
        self.conversation_id = entry.conversation_id
        self.objective = entry.objective
        # Ensure eval_hash is set before truncation so it survives the DB round-trip
        if entry.atomic_attack_identifier and entry.atomic_attack_identifier.eval_hash is None:
            entry.atomic_attack_identifier = entry.atomic_attack_identifier.with_eval_hash(
                AtomicAttackEvaluationIdentifier(entry.atomic_attack_identifier).eval_hash
            )
        self.atomic_attack_identifier = _dump_identifier(entry.atomic_attack_identifier)
        self.objective_sha256 = to_sha256(entry.objective)

        # Use helper method for UUID conversions
        self.last_response_id = self._get_id_as_uuid(entry.last_response)
        self.last_score_id = self._get_id_as_uuid(entry.last_score)

        self.executed_turns = entry.executed_turns
        self.execution_time_ms = entry.execution_time_ms
        self.outcome = entry.outcome.value
        self.outcome_reason = entry.outcome_reason
        self.attack_metadata = self.filter_json_serializable_metadata(entry.metadata)
        self.labels = entry.labels or {}

        # Persist conversation references by type
        self.pruned_conversation_ids = [
            ref.conversation_id for ref in entry.get_conversations_by_type(ConversationType.PRUNED)
        ] or None

        self.adversarial_chat_conversation_ids = [
            ref.conversation_id for ref in entry.get_conversations_by_type(ConversationType.ADVERSARIAL)
        ] or None

        self.timestamp = entry.timestamp or datetime.now(tz=timezone.utc)
        self.pyrit_version = pyrit.__version__

        # Error information
        self.error_message = entry.error_message
        self.error_type = entry.error_type
        # Truncate traceback to 10KB to avoid excessive DB storage
        self.error_traceback = entry.error_traceback[:10240] if entry.error_traceback else None

        # Retry events
        self.retry_events_json = (
            json.dumps([evt.model_dump(mode="json") for evt in entry.retry_events]) if entry.retry_events else None
        )
        self.total_retries = entry.total_retries

        # Attribution / parent linkage (set by the attack persistence path when
        # an AttackResultAttribution is present on the AttackContext; otherwise None)
        self.attribution_parent_id = uuid.UUID(entry.attribution_parent_id) if entry.attribution_parent_id else None
        self.attribution_data = entry.attribution_data  # type: ignore[ty:invalid-assignment]

    @staticmethod
    def _get_id_as_uuid(obj: Any) -> uuid.UUID | None:
        """
        Safely extract and convert an object's id to UUID.

        Args:
            obj: Object that might have an id attribute

        Returns:
            UUID if successful, None otherwise
        """
        if obj and hasattr(obj, "id") and obj.id:
            try:
                return uuid.UUID(str(obj.id))
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def filter_json_serializable_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Filter a dictionary to only include JSON-serializable values.

        This function iterates through the metadata dictionary and keeps only
        values that can be serialized to JSON, discarding any non-serializable objects.

        Args:
            metadata: Dictionary with potentially non-serializable values

        Returns:
            Dictionary with only JSON-serializable values
        """
        if not metadata:
            return {}

        filtered_metadata = {}

        for key, value in metadata.items():
            try:
                json.dumps(value)
                filtered_metadata[key] = value
            except (TypeError, ValueError):
                pass

        return filtered_metadata

    def get_attack_result(self) -> AttackResult:
        """
        Convert this database entry back into an AttackResult object.

        Returns:
            AttackResult: The reconstructed attack result including related conversations and scores.
        """
        related_conversations: set[ConversationReference] = set()

        for cid in self.pruned_conversation_ids or []:
            related_conversations.add(
                ConversationReference(
                    conversation_id=cid,
                    conversation_type=ConversationType.PRUNED,
                    description="pruned conversation",
                )
            )

        for cid in self.adversarial_chat_conversation_ids or []:
            related_conversations.add(
                ConversationReference(
                    conversation_id=cid,
                    conversation_type=ConversationType.ADVERSARIAL,
                    description="adversarial chat conversation",
                )
            )

        atomic_id = _load_identifier(self.atomic_attack_identifier)

        # Deserialize retry events from JSON
        retry_events = []
        if self.retry_events_json:
            from pyrit.models.retry_event import RetryEvent

            retry_events = [RetryEvent.model_validate(evt_dict) for evt_dict in json.loads(self.retry_events_json)]

        return AttackResult(
            conversation_id=self.conversation_id,
            attack_result_id=str(self.id),
            objective=self.objective,
            atomic_attack_identifier=atomic_id,
            last_response=self.last_response.get_message_piece() if self.last_response else None,
            last_score=self.last_score.get_score() if self.last_score else None,
            executed_turns=self.executed_turns,
            execution_time_ms=self.execution_time_ms,
            outcome=AttackOutcome(self.outcome),
            outcome_reason=self.outcome_reason,
            related_conversations=related_conversations,
            metadata=self.attack_metadata or {},
            timestamp=self.timestamp or datetime.now(tz=timezone.utc),
            labels=self.labels or {},
            error_message=self.error_message,
            error_type=self.error_type,
            error_traceback=self.error_traceback,
            retry_events=retry_events,
            total_retries=self.total_retries or 0,
            attribution_parent_id=str(self.attribution_parent_id) if self.attribution_parent_id else None,
            attribution_data=self.attribution_data,
        )


class ScenarioResultEntry(Base):
    """
    Represents a scenario execution result in the database.

    This class stores the high-level metadata and results of a PyRIT scenario execution,
    including references to all attack results generated during the scenario run. The actual
    AttackResult objects are stored separately in AttackResultEntries and can be retrieved
    using the conversation IDs stored here.

    Attributes:
        __tablename__ (str): The name of the database table ("ScenarioResultEntries").
        __table_args__ (dict): Additional arguments for the database table.
        id (Uuid): Unique identifier for this scenario result entry.
        scenario_name (str): Name of the scenario that was executed.
        scenario_description (str): Optional detailed description of the scenario.
        scenario_version (int): Version number of the scenario definition (default: 1).
        pyrit_version (str): Version of PyRIT framework used during scenario execution.
        scenario_init_data (dict): Optional initialization parameters used to configure the scenario.
        objective_target_identifier (dict): Identifier for the target being evaluated in the scenario.
        objective_scorer_identifier (dict): Optional identifier for the scorer used to evaluate results.
        scenario_run_state (str): Current execution state of the scenario
            (one of CREATED, IN_PROGRESS, COMPLETED, FAILED, CANCELLED).
        attack_results_json (str): JSON-serialized dictionary mapping attack names to conversation IDs.
            Format: {"attack_name": ["conversation_id1", "conversation_id2", ...]}.
            The full AttackResult objects are stored in AttackResultEntries and can be queried by conversation_id.
        labels (dict): Optional key-value pairs for categorization and filtering.
        number_tries (int): Number of times run_async has been called on this scenario (incremented at each run).
        completion_time (DateTime): When the scenario execution completed.
        timestamp (DateTime): When this database entry was created.

    Methods:
        get_scenario_result(): Returns a ScenarioResult object with scenario metadata.
            Note: attack_results will be empty. Use memory_interface.get_scenario_results()
            to automatically populate AttackResults from the database.
        get_conversation_ids_by_attack_name(): Returns the mapping of attack names to conversation IDs.
        __str__(): Returns a human-readable string representation.
    """

    __tablename__ = "ScenarioResultEntries"
    __table_args__ = {"extend_existing": True}
    id = mapped_column(CustomUUID, nullable=False, primary_key=True)
    scenario_name = mapped_column(String, nullable=False)
    scenario_description = mapped_column(Unicode, nullable=True)
    scenario_version = mapped_column(INTEGER, nullable=False, default=1)
    pyrit_version = mapped_column(String, nullable=False)
    scenario_init_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    objective_target_identifier: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    objective_scorer_identifier: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scenario_run_state: Mapped[str] = mapped_column(String, nullable=False, default="CREATED")
    attack_results_json: Mapped[str] = mapped_column(Unicode, nullable=False)
    display_group_map_json: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    labels: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    number_tries: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0)
    completion_time = mapped_column(UTCDateTime, nullable=False)
    timestamp = mapped_column(UTCDateTime, nullable=False)

    # Scenario-level error info (persisted so it survives process restarts)
    error_message: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)

    # Free-form JSON metadata stamped by the scenario. Currently used to record
    # ``objective_hashes`` — the objective sha256 set chosen on the
    # first run, replayed on resume so a fresh ``random.sample`` can't
    # silently change which objectives the scenario operates on. Column is
    # named ``scenario_metadata`` because SQLAlchemy's ``DeclarativeBase``
    # reserves ``metadata`` as a class attribute on the model.
    scenario_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    def __init__(self, *, entry: ScenarioResult) -> None:
        """
        Initialize a ScenarioResultEntry from a ScenarioResult object.

        Args:
            entry (ScenarioResult): The scenario result object to convert into a database entry.
        """
        self.id = entry.id
        self.scenario_name = entry.scenario_identifier.name
        self.scenario_description = entry.scenario_identifier.description
        self.scenario_version = entry.scenario_identifier.version
        self.pyrit_version = entry.scenario_identifier.pyrit_version
        self.scenario_init_data = entry.scenario_identifier.init_data
        # Convert ComponentIdentifier to dict for JSON storage
        self.objective_target_identifier = _dump_identifier(entry.objective_target_identifier)  # type: ignore[ty:invalid-assignment]
        # Ensure eval_hash is set before truncation so it survives the DB round-trip.
        if entry.objective_scorer_identifier and entry.objective_scorer_identifier.eval_hash is None:
            entry.objective_scorer_identifier = entry.objective_scorer_identifier.with_eval_hash(
                ScorerEvaluationIdentifier(entry.objective_scorer_identifier).eval_hash
            )
        self.objective_scorer_identifier = _dump_identifier(entry.objective_scorer_identifier)
        self.scenario_run_state = entry.scenario_run_state.value
        self.labels = entry.labels
        self.number_tries = entry.number_tries
        self.completion_time = entry.completion_time

        # Serialize attack_results: dict[str, list[AttackResult]] -> dict[str, list[str]]
        # Store only conversation_ids - the full AttackResults can be queried from the database
        serialized_attack_results = {}
        for attack_name, results in entry.attack_results.items():
            serialized_attack_results[attack_name] = [result.conversation_id for result in results]
        self.attack_results_json = json.dumps(serialized_attack_results)

        # Serialize display_group_map if present
        self.display_group_map_json = json.dumps(entry.display_group_map) if entry.display_group_map else None

        self.error_message = entry.error_message
        self.error_type = entry.error_type
        self.scenario_metadata = entry.metadata if entry.metadata else None  # type: ignore[ty:invalid-assignment]

        self.timestamp = datetime.now(tz=timezone.utc)

    def get_scenario_result(self) -> ScenarioResult:
        """
        Convert the database entry back to a ScenarioResult object.

        Note: This returns a ScenarioResult with empty attack_results.
        Use memory_interface.get_scenario_results() to automatically populate
        the full AttackResults by querying the database.

        Returns:
            ScenarioResult object with scenario metadata but empty attack_results
        """
        # Recreate ScenarioIdentifier with the stored pyrit_version
        stored_version = self.pyrit_version or LEGACY_PYRIT_VERSION
        scenario_identifier = ScenarioIdentifier(
            name=self.scenario_name,
            description=self.scenario_description or "",
            scenario_version=self.scenario_version,
            init_data=self.scenario_init_data,
            pyrit_version=stored_version,
        )

        # Return empty attack_results - will be populated by memory_interface
        attack_results: dict[str, list[AttackResult]] = {}

        # Convert dict back to ComponentIdentifier with the stored pyrit_version
        scorer_identifier = _load_identifier(self.objective_scorer_identifier, pyrit_version=stored_version)

        # Convert dict back to ComponentIdentifier for reconstruction
        target_identifier = _load_identifier(self.objective_target_identifier)

        # Deserialize display_group_map if stored
        display_group_map: dict[str, str] | None = None
        if self.display_group_map_json:
            display_group_map = json.loads(self.display_group_map_json)

        return ScenarioResult(
            id=self.id,
            scenario_identifier=scenario_identifier,
            objective_target_identifier=target_identifier,
            attack_results=attack_results,
            objective_scorer_identifier=scorer_identifier,
            scenario_run_state=ScenarioRunState(self.scenario_run_state),
            labels=self.labels or {},
            creation_time=self.timestamp,
            number_tries=self.number_tries,
            completion_time=self.completion_time,
            display_group_map=display_group_map or {},
            error_message=self.error_message,
            error_type=self.error_type,
            metadata=dict(self.scenario_metadata) if self.scenario_metadata else {},
        )

    def get_conversation_ids_by_attack_name(self) -> dict[str, list[str]]:
        """
        Get the conversation IDs grouped by attack name.

        Returns:
            Dictionary mapping attack names to lists of conversation IDs
        """
        result: dict[str, list[str]] = json.loads(self.attack_results_json)
        return result

    def __str__(self) -> str:
        """
        Return a string representation of the scenario result entry.

        Returns:
            str: String representation of the scenario result entry
        """
        return f"ScenarioResultEntry: {self.scenario_name} (version {self.scenario_version})"
