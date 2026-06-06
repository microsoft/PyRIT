# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack-related request and response models.

All interactions in the UI are modeled as "attacks" - including manual conversations.
This is the attack-centric API design where every user interaction targets a model.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import ChatMessageRole, PromptResponseError


class Score(BaseModel):
    """A score associated with a message piece."""

    score_id: str = Field(..., description="Unique score identifier")
    scorer_type: str = Field(..., description="Type of scorer (e.g., 'bias', 'toxicity')")
    score_type: str = Field(..., description="Score type: 'true_false', 'float_scale', or 'unknown'")
    score_value: str = Field(
        ..., description="Score value ('true'/'false' for true_false, '0.0'-'1.0' for float_scale)"
    )
    score_category: list[str] | None = Field(None, description="Harm categories (e.g., ['hate', 'violence'])")
    score_rationale: str | None = Field(None, description="Explanation for the score")
    scored_at: datetime = Field(..., description="When the score was generated")


class MessagePiece(BaseModel):
    """
    A piece of a message (text, image, audio, etc.).

    Supports multimodal content with original/converted values and embedded scores.
    Media content is base64-encoded since frontend can't access server file paths.
    """

    piece_id: str = Field(..., description="Unique piece identifier")
    original_value_data_type: str = Field(
        default="text", description="Data type of the original value: 'text', 'image', 'audio', etc."
    )
    converted_value_data_type: str = Field(
        default="text", description="Data type of the converted value: 'text', 'image', 'audio', etc."
    )
    original_value: str | None = Field(default=None, description="Original value before conversion")
    original_value_mime_type: str | None = Field(default=None, description="MIME type of original value")
    converted_value: str = Field(..., description="Converted value (text or base64 for media)")
    converted_value_mime_type: str | None = Field(default=None, description="MIME type of converted value")
    scores: list[Score] = Field(default_factory=list, description="Scores embedded in this piece")
    response_error: PromptResponseError = Field(
        default="none", description="Error status: none, processing, blocked, empty, unknown"
    )
    response_error_description: str | None = Field(
        default=None, description="Description of the error if response_error is not 'none'"
    )
    original_filename: str | None = Field(
        default=None, description="Original filename extracted from file path or blob URL"
    )
    converted_filename: str | None = Field(
        default=None, description="Converted filename extracted from file path or blob URL"
    )
    prompt_metadata: dict[str, Any] | None = Field(
        default=None, description="Metadata associated with the piece (e.g., video_id for remix mode)"
    )


class Message(BaseModel):
    """A message within a conversation."""

    turn_number: int = Field(..., description="Turn number in the conversation (1-indexed)")
    role: ChatMessageRole = Field(..., description="Message role")
    pieces: list[MessagePiece] = Field(..., description="Message pieces (multimodal support)")
    created_at: datetime = Field(..., description="Message creation timestamp")


# ============================================================================
# Attack Summary (List View)
# ============================================================================


class TargetInfo(BaseModel):
    """Target information extracted from the stored TargetIdentifier."""

    target_type: str = Field(..., description="Target class name (e.g., 'OpenAIChatTarget')")
    endpoint: str | None = Field(None, description="Target endpoint URL")
    model_name: str | None = Field(None, description="Model or deployment name")


class RetryEventResponse(BaseModel):
    """A single retry attempt captured during execution."""

    timestamp: datetime = Field(..., description="When the retry occurred")
    attempt_number: int = Field(..., ge=1, description="Tenacity attempt number (1-based)")
    function_name: str = Field(..., description="The retried function name")
    exception_type: str = Field("", description="Exception class name")
    exception_message: str = Field("", description="Exception message")
    component_role: str = Field("", description="Component role from ExecutionContext")
    component_name: str | None = Field(None, description="Component class name")
    endpoint: str | None = Field(None, description="Target endpoint URL")
    elapsed_seconds: float = Field(0.0, ge=0, description="Time since first attempt in seconds")


class AttackSummary(BaseModel):
    """Summary view of an attack (for list views, omits full message content)."""

    attack_result_id: str = Field(..., description="Database-assigned unique ID for this AttackResult")
    conversation_id: str = Field(..., description="Primary conversation of this attack result")
    attack_type: str = Field("", description="Attack class name (e.g., 'CrescendoAttack', 'ManualAttack')")
    attack_specific_params: dict[str, Any] | None = Field(None, description="Additional attack-specific parameters")
    target: TargetInfo | None = Field(None, description="Target information from the stored identifier")
    converters: list[str] = Field(
        default_factory=list, description="Request converter class names applied in this attack"
    )
    objective: str = Field("", description="Natural-language description of the attacker's objective")
    outcome: Literal["undetermined", "success", "failure", "error"] | None = Field(
        None, description="Attack outcome (null if not yet determined)"
    )
    outcome_reason: str | None = Field(None, description="Reason for the outcome")
    last_response: str | None = Field(None, description="Model response from the final turn")
    last_message_preview: str | None = Field(None, description="Preview of the last message (truncated to ~100 chars)")
    score_value: str | None = Field(None, description="Score value from the objective scorer")
    executed_turns: int = Field(0, ge=0, description="Number of turns executed")
    execution_time_ms: int = Field(0, ge=0, description="Execution time in milliseconds")
    message_count: int = Field(0, description="Total number of messages in the attack")
    related_conversation_ids: list[str] = Field(
        default_factory=list, description="IDs of related conversations within this attack"
    )
    labels: dict[str, str] = Field(default_factory=dict, description="User-defined labels for filtering")
    created_at: datetime = Field(..., description="Attack creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # Error information
    error_message: str | None = Field(None, description="Error message if the attack failed with an exception")
    error_type: str | None = Field(None, description="Exception class name (e.g., 'RateLimitError')")
    error_traceback: str | None = Field(None, description="Formatted traceback string")

    # Retry information
    total_retries: int = Field(0, ge=0, description="Total number of retries during this attack")
    retry_events: list[RetryEventResponse] | None = Field(
        None, description="Detailed retry events (omitted in list views unless requested)"
    )


# ============================================================================
# Conversation Messages Response
# ============================================================================


class ConversationMessagesResponse(BaseModel):
    """Response containing all messages for a conversation."""

    conversation_id: str = Field(..., description="Conversation identifier")
    messages: list[Message] = Field(default_factory=list, description="All messages in order")


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
