# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack-related request and response models.

All interactions in the UI are modeled as "attacks" - including manual conversations.
This is the attack-centric API design where every user interaction targets a model.
"""

from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, computed_field, field_serializer

from pyrit.backend.models._media import build_filename, infer_mime_type
from pyrit.backend.models.common import PaginationInfo
from pyrit.models import (
    AttackResult,
    ChatMessageRole,
    ConversationReference,
    Message,
    MessagePiece,
    Score,
)


class TargetInfo(BaseModel):
    """Target information extracted from the stored attack-strategy identifier."""

    target_type: str = Field(..., description="Target class name (e.g., 'OpenAIChatTarget')")
    endpoint: str | None = Field(None, description="Target endpoint URL")
    model_name: str | None = Field(None, description="Model or deployment name")


class ScoreView(Score):
    """
    API view of a ``pyrit.models.Score``.

    Exposes every canonical score field and adds a flattened ``scorer_type`` so
    clients don't have to dig into ``scorer_class_identifier``.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scorer_type(self) -> str:
        """The scorer class name, or ``"Unknown"`` when unavailable."""
        identifier = self.scorer_class_identifier
        if identifier and identifier.class_name:
            return identifier.class_name
        return "Unknown"

    @classmethod
    def from_domain(cls, score: Score) -> "ScoreView":
        """
        Build a ``ScoreView`` from a domain ``Score`` without re-validating.

        Uses ``model_construct`` to bypass the domain validators (the score is
        already valid) and copies fields by reference to preserve UUIDs,
        datetimes, and identifier objects.

        Returns:
            A ``ScoreView`` mirroring the domain score's fields.
        """
        return cls.model_construct(**{name: getattr(score, name) for name in Score.model_fields})


class MessagePieceView(MessagePiece):
    """
    API view of a ``pyrit.models.MessagePiece``.

    Inherits the canonical piece fields unchanged: ``original_value`` /
    ``converted_value`` carry the raw stored content the server holds (text, a
    local file path, a blob URL, or a data URI — whatever the database has).

    Adds presentation-only fields the client needs:

    - ``original_value_url`` / ``converted_value_url`` — client-fetchable URLs
      populated by the mapper for media pieces (``/api/media?path=...`` for
      local files; SAS-signed URLs for Azure Blob; pass-through for data URIs
      and existing http(s) URLs). ``None`` for plain text and empty values.
    - ``*_mime_type`` / ``*_filename`` — MIME types and download filenames
      derived from the raw values at map time.

    ``response_error_description`` is an optional error detail that defaults to
    ``None``; the canonical piece carries no separate description.
    """

    scores: list[ScoreView] = Field(default_factory=list)
    original_value_url: str | None = Field(
        default=None,
        description=(
            "Client-fetchable URL for the original media value (e.g. "
            "/api/media?path=... or a SAS-signed blob URL). None for text pieces."
        ),
    )
    converted_value_url: str | None = Field(
        default=None,
        description=(
            "Client-fetchable URL for the converted media value (e.g. "
            "/api/media?path=... or a SAS-signed blob URL). None for text pieces."
        ),
    )
    original_value_mime_type: str | None = Field(default=None, description="MIME type of the original value")
    converted_value_mime_type: str | None = Field(default=None, description="MIME type of the converted value")
    original_filename: str | None = Field(default=None, description="Download filename for the original value")
    converted_filename: str | None = Field(default=None, description="Download filename for the converted value")
    response_error_description: str | None = Field(
        default=None, description="Description of the error if response_error is not 'none'"
    )

    @classmethod
    def from_domain(
        cls,
        piece: MessagePiece,
        *,
        scores: list[Score] | None = None,
        original_value_url: str | None = None,
        converted_value_url: str | None = None,
    ) -> "MessagePieceView":
        """
        Build a ``MessagePieceView`` from a domain piece without re-validating.

        The canonical piece fields (``original_value``, ``converted_value``,
        sha256s, role, ids, etc.) are copied through unchanged. The optional
        kwargs are purely additive: ``scores`` is fetched separately from memory
        (``MessagePiece`` no longer carries scores) and the ``*_value_url`` fields
        give the client fetchable media URLs.

        Args:
            piece: The domain message piece.
            scores: Domain scores attached to this piece, fetched from memory.
            original_value_url: Client-fetchable URL for ``piece.original_value``
                when it's media; ``None`` for text.
            converted_value_url: Client-fetchable URL for ``piece.converted_value``
                when it's media; ``None`` for text.

        Returns:
            A ``MessagePieceView`` with derived MIME types, filenames, and views.
        """
        data = {name: getattr(piece, name) for name in MessagePiece.model_fields}
        orig_dtype = piece.original_value_data_type or "text"
        conv_dtype = piece.converted_value_data_type or "text"
        data.update(
            scores=[ScoreView.from_domain(score) for score in (scores or [])],
            original_value_url=original_value_url,
            converted_value_url=converted_value_url,
            original_value_mime_type=infer_mime_type(value=piece.original_value, data_type=orig_dtype),
            converted_value_mime_type=infer_mime_type(value=piece.converted_value, data_type=conv_dtype),
            original_filename=build_filename(
                data_type=orig_dtype, sha256=piece.original_value_sha256, value=piece.original_value
            ),
            converted_filename=build_filename(
                data_type=conv_dtype, sha256=piece.converted_value_sha256, value=piece.converted_value
            ),
        )
        return cls.model_construct(**data)


class MessageView(Message):
    """
    API view of a ``pyrit.models.Message``.

    Adds turn-level metadata (``turn_number``, ``role``, ``created_at``) derived
    from the first piece, and narrows ``message_pieces`` to ``MessagePieceView``.
    """

    message_pieces: list[MessagePieceView] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def turn_number(self) -> int:
        """The sequence of the first piece (the conversation turn)."""
        return self.message_pieces[0].sequence if self.message_pieces else 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def role(self) -> ChatMessageRole:
        """The role of the first piece."""
        return self.message_pieces[0].role if self.message_pieces else "user"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def created_at(self) -> datetime:
        """The timestamp of the first piece."""
        return self.message_pieces[0].timestamp if self.message_pieces else datetime.now(timezone.utc)


class AttackSummary(AttackResult):
    """
    API view of a ``pyrit.models.AttackResult``.

    Inherits every canonical attack-result field (including ``last_response``,
    ``last_score`` and ``retry_events``) and adds presentation data: computed
    projections of the strategy identifier plus mapper-populated conversation
    stats. ``last_response`` / ``last_score`` are narrowed to their view types so
    their presentation fields serialize.
    """

    last_response: MessagePieceView | None = None
    last_score: ScoreView | None = None

    # Mapper-populated presentation fields (need external stats / metadata).
    message_count: int = Field(default=0, description="Total number of messages in the attack")
    last_message_preview: str | None = Field(default=None, description="Preview of the last message")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Attack creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp"
    )

    @field_serializer("related_conversations")
    def _serialize_related_conversations(
        self,
        related_conversations: set[ConversationReference],
    ) -> list[dict[str, Any]]:
        """
        Serialize related conversations in a stable (sorted) order for deterministic output.

        Returns:
            A list of serialized conversation references ordered by ``conversation_id``.
        """
        ordered = sorted(related_conversations, key=lambda ref: ref.conversation_id)
        return [ref.model_dump() for ref in ordered]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def attack_type(self) -> str:
        """The attack strategy class name, or ``"Unknown"``."""
        identifier = self.get_attack_strategy_identifier()
        return identifier.class_name if identifier else "Unknown"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def attack_specific_params(self) -> dict[str, Any] | None:
        """The attack strategy params, or ``None``."""
        identifier = self.get_attack_strategy_identifier()
        return (identifier.params or None) if identifier else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target(self) -> TargetInfo | None:
        """The objective target info extracted from the identifier."""
        identifier = self.get_attack_strategy_identifier()
        target_id = identifier.get_child("objective_target") if identifier else None
        if not target_id:
            return None
        return TargetInfo(
            target_type=target_id.class_name,
            endpoint=cast("str | None", target_id.params.get("endpoint") or None),
            model_name=cast("str | None", target_id.params.get("model_name") or None),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def converters(self) -> list[str]:
        """The request-converter class names applied in this attack."""
        identifier = self.get_attack_strategy_identifier()
        converter_ids = identifier.get_child_list("request_converters") if identifier else []
        return [c.class_name for c in converter_ids]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def related_conversation_ids(self) -> list[str]:
        """The IDs of related conversations, sorted for stable output."""
        return sorted(ref.conversation_id for ref in self.related_conversations)


# ============================================================================
# Conversation Messages Response
# ============================================================================


class ConversationMessagesResponse(BaseModel):
    """Response containing all messages for a conversation."""

    conversation_id: str = Field(..., description="Conversation identifier")
    messages: list[MessageView] = Field(default_factory=list, description="All messages in order")


# ============================================================================
# Attack List Response (Paginated)
# ============================================================================


class AttackListResponse(BaseModel):
    """Paginated response for listing attacks."""

    items: list[AttackSummary] = Field(..., description="List of attack summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class AttackOptionsResponse(BaseModel):
    """Response containing unique attack type names used across attacks."""

    attack_types: list[str] = Field(..., description="Sorted list of unique attack type names found in attack results")


class ConverterOptionsResponse(BaseModel):
    """Response containing unique converter type names used across attacks."""

    converter_types: list[str] = Field(
        ..., description="Sorted list of unique converter type names found in attack results"
    )


# ============================================================================
# Message Input Models
# ============================================================================


class MessagePieceRequest(BaseModel):
    """A piece of content for a message."""

    data_type: str = Field(default="text", description="Data type: 'text', 'image', 'audio', etc.")
    original_value: str = Field(..., description="Original value (text or base64 for media)")
    converted_value: str | None = Field(None, description="Converted value. If provided, bypasses converters.")
    mime_type: str | None = Field(None, description="MIME type for media content")
    prompt_metadata: dict[str, Any] | None = Field(
        None,
        description="Metadata to attach to the piece (e.g., {'video_id': '...'} for remix mode).",
    )
    original_prompt_id: str | None = Field(
        None,
        description="ID of the source piece when prepending from an existing conversation. "
        "Preserves lineage so the new piece traces back to the original.",
    )


class PrependedMessageRequest(BaseModel):
    """A message to prepend to the attack (for system prompt/branching)."""

    role: ChatMessageRole = Field(..., description="Message role")
    pieces: list[MessagePieceRequest] = Field(..., description="Message pieces (supports multimodal)", max_length=50)


# ============================================================================
# Create Attack
# ============================================================================


class CreateAttackRequest(BaseModel):
    """
    Request to create a new attack.

    For branching from an existing conversation into a new attack, provide
    ``source_conversation_id`` and ``cutoff_index``.  The backend will
    duplicate messages up to and including the cutoff turn, preserving
    lineage via ``original_prompt_id``.  The new attack gets the labels
    supplied in ``labels`` (typically the current operator's labels).
    """

    name: str | None = Field(None, description="Attack name/label")
    target_registry_name: str = Field(..., description="Target registry name to attack")
    source_conversation_id: str | None = Field(
        None, description="Conversation to branch from (clone messages into the new attack)"
    )
    cutoff_index: int | None = Field(None, description="Include messages up to and including this turn index (0-based)")
    system_prompt: str | None = Field(
        None,
        description="System prompt lowered to a single system-role message at the front of the conversation. "
        "Composes with prepended_conversation (the system message is inserted first).",
    )
    prepended_conversation: list[PrependedMessageRequest] | None = Field(
        None, description="Messages to prepend (system prompts, branching context)", max_length=200
    )
    labels: dict[str, str] | None = Field(None, description="User-defined labels for filtering")


class CreateAttackResponse(BaseModel):
    """Response after creating an attack."""

    attack_result_id: str = Field(..., description="Database-assigned unique ID for the AttackResult")
    conversation_id: str = Field(..., description="Unique conversation identifier")
    created_at: datetime = Field(..., description="Attack creation timestamp")


# ============================================================================
# Update Attack
# ============================================================================


class UpdateAttackRequest(BaseModel):
    """Request to update an attack's outcome."""

    outcome: Literal["undetermined", "success", "failure", "error"] = Field(..., description="Updated attack outcome")


# ============================================================================
# Related Conversations
# ============================================================================


class ConversationSummary(BaseModel):
    """Summary of a conversation (message count, preview, timestamp)."""

    conversation_id: str = Field(..., description="Unique conversation identifier")
    message_count: int = Field(0, description="Number of messages in this conversation")
    last_message_preview: str | None = Field(None, description="Preview of the last message")
    created_at: datetime | None = Field(None, description="Timestamp of the first message")


class AttackConversationsResponse(BaseModel):
    """Response listing all conversations belonging to an attack."""

    attack_result_id: str = Field(..., description="The AttackResult ID")
    main_conversation_id: str = Field(..., description="The attack's primary conversation_id")
    conversations: list[ConversationSummary] = Field(
        default_factory=list, description="All conversations including main"
    )


class CreateConversationRequest(BaseModel):
    """
    Request to create a new conversation within an existing attack.

    For branching from an existing conversation, provide ``source_conversation_id``
    and ``cutoff_index``. The backend will duplicate messages up to and including
    the cutoff turn, preserving tracking relationships (original_prompt_id).
    """

    source_conversation_id: str | None = Field(None, description="Conversation to branch from")
    cutoff_index: int | None = Field(None, description="Include messages up to and including this turn index (0-based)")


class CreateConversationResponse(BaseModel):
    """Response after creating a new related conversation."""

    conversation_id: str = Field(..., description="New conversation identifier")
    created_at: datetime = Field(..., description="Conversation creation timestamp")


class UpdateMainConversationRequest(BaseModel):
    """Request to update the main conversation of an attack result."""

    conversation_id: str = Field(..., description="The conversation to promote to main")


class UpdateMainConversationResponse(BaseModel):
    """Response after updating the main conversation of an attack result."""

    attack_result_id: str = Field(..., description="The AttackResult whose main conversation was swapped")
    conversation_id: str = Field(..., description="The conversation that is now the main conversation")
    updated_at: datetime = Field(..., description="Timestamp when the main conversation was changed")


# ============================================================================
# Add Message
# ============================================================================


class AddMessageRequest(BaseModel):
    """
    Request to add a message to an attack.

    If send=True (default for user role), the message is sent to the target
    and we wait for a response. If send=False, the message is just stored
    in memory without sending (useful for system messages, context injection).
    """

    role: ChatMessageRole = Field(default="user", description="Message role")
    pieces: list[MessagePieceRequest] = Field(..., description="Message pieces", max_length=50)
    send: bool = Field(
        default=True,
        description="If True, send to target and wait for response. If False, just store in memory.",
    )
    target_registry_name: str | None = Field(
        None,
        description="Target registry name. Required when send=True so the backend knows which target to use.",
    )
    converter_ids: list[str] | None = Field(
        None, description="Converter instance IDs to apply (overrides attack-level)"
    )
    target_conversation_id: str = Field(
        ...,
        description="The conversation_id to store and send messages under. "
        "Usually the attack's main conversation, but can be a related conversation.",
    )
    labels: dict[str, str] | None = Field(
        None,
        description="Labels to attach to every message piece. "
        "Falls back to labels from existing pieces in the conversation.",
    )


class AddMessageResponse(BaseModel):
    """
    Response after adding a message.

    Returns the attack metadata and all messages. If send=True was used, the new
    assistant response will be in the messages list. Check response_error
    on the assistant's message pieces if the target returned an error.
    """

    attack: AttackSummary = Field(..., description="Updated attack metadata")
    messages: ConversationMessagesResponse = Field(..., description="All messages including new one(s)")
