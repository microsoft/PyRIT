# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack mappers – domain ↔ DTO translation for attack-related models.

Most functions are pure (no database or service calls).  The exceptions are
``pyrit_messages_to_dto_async`` which signs Azure Blob Storage URLs and
constructs local media endpoint URLs for media content.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlparse

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContainerSasPermissions, generate_container_sas
from azure.storage.blob.aio import BlobServiceClient

from pyrit.backend.mappers._preview import format_last_message_preview
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AttackSummary,
    MessagePieceRequest,
    MessagePieceView,
    MessageView,
    ScoreView,
)
from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    AttackResult,
    ChatMessageRole,
    Message,
    MessagePiece,
    PromptDataType,
    Score,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.models.conversation_stats import ConversationStats

# ============================================================================
# Domain → DTO  (for API responses)
# ============================================================================

# ---------------------------------------------------------------------------
# Azure Blob SAS token cache
# ---------------------------------------------------------------------------
# Container URL -> (sas_token_query_string, expiry_epoch)
_sas_token_cache: dict[str, tuple[str, float]] = {}
_SAS_CACHE_BUFFER_SECONDS = 300  # refresh 5 min before token expiry


def _is_azure_blob_url(value: str) -> bool:
    """Return True if *value* looks like an Azure Blob Storage URL."""
    parsed = urlparse(value)
    # Azure Blob Storage enforces HTTPS; rejecting HTTP also limits SSRF surface.
    if parsed.scheme != "https":
        return False
    host = parsed.netloc.split(":")[0]  # strip port
    return host.endswith(".blob.core.windows.net") and bool(host.split(".")[0])


async def _get_sas_for_container_async(*, container_url: str) -> str:
    """
    Return a read-only SAS query string for *container_url*, generating and
    caching one when necessary.

    The SAS token is cached per container URL and refreshed 5 minutes
    before expiry to avoid serving expired tokens.

    Args:
        container_url: The full URL of the Azure Blob Storage container
                       (e.g. ``https://account.blob.core.windows.net/container``).

    Returns:
        A SAS query string (without the leading ``?``).
    """
    now = time.time()
    cached = _sas_token_cache.get(container_url)
    if cached and cached[1] > now:
        return cached[0]

    parsed = urlparse(container_url)
    account_url = f"{parsed.scheme}://{parsed.netloc}"
    container_name = parsed.path.strip("/")
    storage_account_name = parsed.netloc.split(".")[0]

    start_time = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    expiry_time = start_time + timedelta(hours=1)

    credential = DefaultAzureCredential()
    try:
        async with BlobServiceClient(account_url=account_url, credential=credential) as bsc:
            delegation_key = await bsc.get_user_delegation_key(
                key_start_time=start_time,
                key_expiry_time=expiry_time,
            )
            sas_token: str = generate_container_sas(
                account_name=storage_account_name,
                container_name=container_name,
                user_delegation_key=delegation_key,
                permission=ContainerSasPermissions(read=True),
                expiry=expiry_time,
                start=start_time,
            )
    finally:
        await credential.close()

    _sas_token_cache[container_url] = (sas_token, expiry_time.timestamp() - _SAS_CACHE_BUFFER_SECONDS)
    return sas_token


async def _sign_blob_url_async(*, blob_url: str) -> str:
    """
    Append a read-only SAS token to an Azure Blob Storage URL.

    Non-blob URLs (local paths, data URIs, etc.) are returned unchanged.

    Args:
        blob_url: The raw Azure Blob Storage URL.

    Returns:
        The URL with an appended SAS query string, or the original value for
        non-blob URLs.
    """
    if not _is_azure_blob_url(blob_url):
        return blob_url

    parsed = urlparse(blob_url)

    # Strip any existing query string (e.g. expired SAS) so we always re-sign
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Extract container name from path: /container/path/to/blob
    parts = parsed.path.strip("/").split("/", 1)
    container_name = parts[0]
    if not container_name:
        return blob_url
    container_url = f"{parsed.scheme}://{parsed.netloc}/{container_name}"

    try:
        sas = await _get_sas_for_container_async(container_url=container_url)
        return f"{base_url}?{sas}"
    except Exception:
        logger.warning("Failed to generate SAS token for %s; returning unsigned URL", blob_url, exc_info=True)
        return blob_url


def _resolve_media_url(*, value: str | None, data_type: str) -> str | None:
    """
    Resolve a media value to a client-fetchable URL.

    Returns ``None`` for non-media data types or empty values — there's no URL
    to expose for plain text. For media values:

    - Local file paths -> ``/api/media?path=...``
    - data URIs and http(s) URLs -> passed through as-is (blob URLs are
      signed later in ``pyrit_messages_to_dto_async``)
    - Anything else (e.g. nonexistent paths) -> passed through unchanged

    Args:
        value: The stored value (file path, blob URL, data URI, or text).
        data_type: The prompt data type (e.g. ``image_path``, ``text``).

    Returns:
        A client-fetchable URL for media, or ``None`` for text / empty values.
    """
    if not value or data_type not in MEDIA_PATH_DATA_TYPES:
        return None
    # Already a URL or data URI — pass through
    if value.startswith(("http://", "https://", "data:")):
        return value
    # Local file path — construct a media endpoint URL
    if Path(value).is_file():
        return f"/api/media?path={quote(str(value))}"
    return value


async def attack_result_to_summary_async(
    ar: AttackResult,
    *,
    stats: ConversationStats,
) -> AttackSummary:
    """
    Build an AttackSummary view from an AttackResult.

    Conversation-level stats (message count, preview, labels, timestamps) are
    injected here; every other field is inherited from the AttackResult. The
    summary's ``last_response`` media is resolved to a ``/api/media`` URL and
    Azure Blob URLs are SAS-signed so they're directly fetchable by the client.

    Args:
        ar: The domain AttackResult.
        stats: Pre-aggregated conversation stats (from ``get_conversation_stats``).

    Returns:
        AttackSummary view ready for the API response.
    """
    labels = dict(ar.labels) if ar.labels else {}
    labels.update(stats.labels or {})
    created_at, updated_at = _resolve_summary_timestamps(ar)

    data = {name: getattr(ar, name) for name in AttackResult.model_fields}
    data.update(
        last_response=await _summary_last_response_async(ar.last_response),
        last_score=ScoreView.from_domain(ar.last_score) if ar.last_score else None,
        labels=labels,
        message_count=stats.message_count,
        last_message_preview=format_last_message_preview(
            value=stats.last_message_preview,
            data_type=stats.last_message_data_type,
        ),
        created_at=created_at,
        updated_at=updated_at,
    )
    return AttackSummary.model_construct(**data)


def _resolve_summary_timestamps(ar: AttackResult) -> tuple[datetime, datetime]:
    """
    Resolve ``created_at`` / ``updated_at`` for a summary.

    Resolution order for ``created_at``: explicit metadata override, then the
    persisted ``AttackResult.timestamp``, and finally ``datetime.now`` as a
    last-resort fallback for never-persisted results.

    Returns:
        A ``(created_at, updated_at)`` tuple.
    """
    created_str = ar.metadata.get("created_at")
    updated_str = ar.metadata.get("updated_at")
    if created_str:
        created_at = datetime.fromisoformat(created_str)
    elif ar.timestamp is not None:
        created_at = ar.timestamp
    else:
        created_at = datetime.now(timezone.utc)
    updated_at = datetime.fromisoformat(updated_str) if updated_str else created_at
    return created_at, updated_at


async def _summary_last_response_async(piece: MessagePiece | None) -> MessagePieceView | None:
    """
    Build a ``MessagePieceView`` for a summary's last response with signed media URLs.

    Returns:
        A ``MessagePieceView`` for the piece, or ``None`` when no piece is given.
    """
    if piece is None:
        return None
    return MessagePieceView.from_domain(
        piece,
        original_value_url=await _resolve_and_sign_media_async(
            value=piece.original_value, data_type=piece.original_value_data_type or "text"
        ),
        converted_value_url=await _resolve_and_sign_media_async(
            value=piece.converted_value, data_type=piece.converted_value_data_type or "text"
        ),
    )


async def _resolve_and_sign_media_async(*, value: str | None, data_type: str) -> str | None:
    """
    Resolve a media value to a fetchable URL, signing Azure Blob URLs when present.

    Returns:
        The resolved (and signed, if a blob) URL, or ``None`` when *value* is empty.
    """
    resolved = _resolve_media_url(value=value, data_type=data_type)
    if resolved and _is_azure_blob_url(resolved):
        return await _sign_blob_url_async(blob_url=resolved)
    return resolved


def _score_lookup_key(*, piece: MessagePiece) -> str:
    """
    Compute the score-lookup key for a piece.

    Scores are linked to the piece's ``original_prompt_id`` (set by
    ``MemoryInterface.add_scores_to_memory``), which equals ``piece.id``
    for original pieces and points at the original for duplicates.

    Returns:
        Stringified piece identifier suitable for matching ``Score.message_piece_id``.
    """
    return str(piece.original_prompt_id or piece.id)


async def _fetch_scores_by_piece_async(
    *,
    pyrit_messages: list[Message],
) -> dict[str, list[Score]]:
    """
    Batch-fetch scores for every piece in ``pyrit_messages`` and group by piece id.

    Wrapped in ``asyncio.to_thread`` because ``get_prompt_scores`` is a blocking
    SQLAlchemy call and ``pyrit_messages_to_dto_async`` runs on the event loop.

    Returns:
        ``{piece_lookup_key: [Score, ...]}``. Missing keys map to an empty list.
    """
    score_lookup_ids = sorted({_score_lookup_key(piece=p) for msg in pyrit_messages for p in msg.message_pieces})
    if not score_lookup_ids:
        return {}

    memory = CentralMemory.get_memory_instance()
    fetched = await asyncio.to_thread(memory.get_prompt_scores, prompt_ids=score_lookup_ids)

    grouped: dict[str, list[Score]] = {}
    for score in fetched:
        grouped.setdefault(str(score.message_piece_id), []).append(score)
    return grouped


async def pyrit_messages_to_dto_async(
    pyrit_messages: list[Message],
) -> list[MessageView]:
    """
    Translate PyRIT messages to backend MessageView responses.

    The raw stored ``original_value`` / ``converted_value`` are passed through
    unchanged. Media file paths are additionally resolved into client-fetchable
    URLs and exposed via ``original_value_url`` / ``converted_value_url``:

    - Local files -> ``/api/media?path=...`` (served by the media endpoint)
    - Azure Blob Storage files -> signed URLs with SAS tokens

    Scores are fetched from ``CentralMemory`` (``MessagePiece`` no longer carries
    them) via a single batched ``get_prompt_scores`` call and attached to their
    originating piece.

    Returns:
        List of MessageView responses for the API.
    """
    scores_by_piece = await _fetch_scores_by_piece_async(pyrit_messages=pyrit_messages)

    messages: list[MessageView] = []
    for msg in pyrit_messages:
        pieces: list[MessagePieceView] = []
        for p in msg.message_pieces:
            original_value_url = await _resolve_and_sign_media_async(
                value=p.original_value, data_type=p.original_value_data_type or "text"
            )
            converted_value_url = await _resolve_and_sign_media_async(
                value=p.converted_value, data_type=p.converted_value_data_type or "text"
            )
            piece_scores = scores_by_piece.get(_score_lookup_key(piece=p), [])
            pieces.append(
                MessagePieceView.from_domain(
                    p,
                    scores=piece_scores,
                    original_value_url=original_value_url,
                    converted_value_url=converted_value_url,
                )
            )
        messages.append(MessageView.model_construct(message_pieces=pieces))
    return messages


# ============================================================================
# DTO → Domain  (for inbound requests)
# ============================================================================


def request_piece_to_pyrit_message_piece(
    *,
    piece: MessagePieceRequest,
    role: ChatMessageRole,
    conversation_id: str,
    sequence: int,
    labels: dict[str, str] | None = None,  # deprecated
) -> MessagePiece:
    """
    Convert a single request piece DTO to a PyRIT MessagePiece domain object.

    Args:
        piece: The request piece (with data_type, original_value, converted_value).
        role: The message role.
        conversation_id: The conversation/attack ID.
        sequence: The message sequence number.
        labels: Optional labels to attach to the piece.
            Deprecated: This parameter will be removed in a release 0.16.0.

    Returns:
        MessagePiece domain object.
    """
    # Only a truthy value counts as "passed"; an empty/falsy ``labels`` (e.g. {}
    # forwarded on the happy path) is treated as not supplied to avoid a spurious
    # warning. Matches MessagePiece's deprecated-kwarg guard.
    if labels:
        print_deprecation_message(
            old_item="request_piece_to_pyrit_message_piece(..., labels=...)",
            new_item="request_piece_to_pyrit_message_piece(...)",
            removed_in="0.16.0",
        )
    metadata: dict[str, str | int] = {}
    if piece.prompt_metadata:
        metadata = dict(piece.prompt_metadata)
    elif piece.mime_type:
        metadata = {"mime_type": piece.mime_type}
    original_prompt_id = uuid.UUID(piece.original_prompt_id) if piece.original_prompt_id else None
    return MessagePiece(
        role=role,
        original_value=piece.original_value,
        original_value_data_type=cast("PromptDataType", piece.data_type),
        converted_value=piece.converted_value or piece.original_value,
        converted_value_data_type=cast("PromptDataType", piece.data_type),
        conversation_id=conversation_id,
        sequence=sequence,
        prompt_metadata=metadata,
        labels=labels or {},  # deprecated
        original_prompt_id=original_prompt_id,
    )


def request_to_pyrit_message(
    *,
    request: AddMessageRequest,
    conversation_id: str,
    sequence: int,
    labels: dict[str, str] | None = None,  # deprecated
) -> Message:
    """
    Build a PyRIT Message from an AddMessageRequest DTO.

    Args:
        request: The inbound API request.
        conversation_id: The conversation/attack ID.
        sequence: The message sequence number.
        labels: Optional labels to attach to each piece.
            Deprecated: This parameter will be removed in a release 0.16.0.

    Returns:
        Message ready to send to the target.
    """
    # Only a truthy value counts as "passed"; an empty/falsy ``labels`` (e.g. {}
    # forwarded on the happy path) is treated as not supplied to avoid a spurious
    # warning. Matches MessagePiece's deprecated-kwarg guard.
    if labels:
        print_deprecation_message(
            old_item="request_to_pyrit_message(..., labels=...)",
            new_item="request_to_pyrit_message(...)",
            removed_in="0.16.0",
        )
    pieces = [
        request_piece_to_pyrit_message_piece(
            piece=p,
            role=request.role,
            conversation_id=conversation_id,
            sequence=sequence,
            labels=labels,  # deprecated
        )
        for p in request.pieces
    ]
    return Message(message_pieces=pieces)


# ============================================================================
# Private Helpers
# ============================================================================
