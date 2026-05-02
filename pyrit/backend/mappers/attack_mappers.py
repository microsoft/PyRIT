# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack mappers – domain ↔ DTO translation for attack-related models.

Most functions are pure (no database or service calls).  The exceptions are
``pyrit_messages_to_dto_async`` which signs Azure Blob Storage URLs and
constructs local media endpoint URLs for media content.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, cast
from urllib.parse import quote, urlparse

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContainerSasPermissions, generate_container_sas
from azure.storage.blob.aio import BlobServiceClient

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AttackSummary,
    Message,
    MessagePiece,
    MessagePieceRequest,
    Score,
    TargetInfo,
)
from pyrit.common.deprecation import print_deprecation_message
from pyrit.models import AttackResult, ChatMessageRole, PromptDataType
from pyrit.models import Message as PyritMessage
from pyrit.models import MessagePiece as PyritMessagePiece
from pyrit.models import Score as PyritScore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.models.conversation_stats import ConversationStats

# ============================================================================
# Domain → DTO  (for API responses)
# ============================================================================

# Media data types whose values are file paths (local or Azure Blob URLs)
_MEDIA_PATH_TYPES = frozenset({"image_path", "audio_path", "video_path", "binary_path"})

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


def _resolve_media_url(*, value: Optional[str], data_type: str) -> Optional[str]:
    """
    For media path types, convert a local file path to a ``/api/media`` URL.

    Non-media types and Azure Blob URLs are returned as-is (blob URLs are
    signed later in ``pyrit_messages_to_dto_async``).

    Args:
        value: The stored value (file path, blob URL, data URI, or text).
        data_type: The prompt data type (e.g. ``image_path``, ``text``).

    Returns:
        The value unchanged for non-media types, a ``/api/media?path=...``
        URL for local file paths, or the original value for blob URLs / data URIs.
    """
    if not value or data_type not in _MEDIA_PATH_TYPES:
        return value
    # Already a URL or data URI — pass through
    if value.startswith(("http://", "https://", "data:")):
        return value
    # Local file path — construct a media endpoint URL
    if os.path.isfile(value):
        return f"/api/media?path={quote(str(value))}"
    return value


def attack_result_to_summary(
    ar: AttackResult,
    *,
    stats: ConversationStats,
) -> AttackSummary:
    """
    Build an AttackSummary DTO from an AttackResult.

    Args:
        ar: The domain AttackResult.
        stats: Pre-aggregated conversation stats (from ``get_conversation_stats``).

    Returns:
        AttackSummary DTO ready for the API response.
    """
    message_count = stats.message_count
    last_preview = stats.last_message_preview

    # Merge attack-result labels with conversation-level labels.
    # Conversation labels take precedence on key collision.
    labels = dict(ar.labels) if ar.labels else {}
    labels.update(stats.labels or {})
    # Resolution order for created_at: explicit metadata override, then the
    # persisted AttackResult.timestamp, and finally datetime.now() as a
    # last-resort fallback for never-persisted results.
    created_str = ar.metadata.get("created_at")
    updated_str = ar.metadata.get("updated_at")
    if created_str:
        created_at = datetime.fromisoformat(created_str)
    elif ar.timestamp is not None:
        created_at = ar.timestamp
    else:
        created_at = datetime.now(timezone.utc)
    updated_at = datetime.fromisoformat(updated_str) if updated_str else created_at

    aid = ar.get_attack_strategy_identifier()

    # Extract only frontend-relevant fields from ComponentIdentifier
    target_id = aid.get_child("objective_target") if aid else None
    converter_ids = aid.get_child_list("request_converters") if aid else []

    target_info = (
        TargetInfo(
            target_type=target_id.class_name,
            endpoint=target_id.params.get("endpoint") or None,
            model_name=target_id.params.get("model_name") or None,
        )
        if target_id
        else None
    )

    return AttackSummary(
        attack_result_id=ar.attack_result_id,
        conversation_id=ar.conversation_id,
        attack_type=aid.class_name if aid else "Unknown",
        attack_specific_params=(aid.params or None) if aid else None,
        target=target_info,
        converters=[c.class_name for c in converter_ids] if converter_ids else [],
        outcome=ar.outcome.value,
        last_message_preview=last_preview,
        message_count=message_count,
        related_conversation_ids=[ref.conversation_id for ref in ar.related_conversations],
        labels=labels,
        created_at=created_at,
        updated_at=updated_at,
    )


def pyrit_scores_to_dto(scores: list[PyritScore]) -> list[Score]:
    """
    Translate PyRIT score objects to backend Score DTOs.

    Returns:
        List of Score DTOs for the API.
    """
    return [
        Score(
            score_id=str(score.id),
            scorer_type=score.scorer_class_identifier.class_name,
            score_type=score.score_type,
            score_value=score.score_value,
            score_category=score.score_category,
            score_rationale=score.score_rationale,
            scored_at=score.timestamp,
        )
        for score in scores
    ]


def _infer_mime_type(*, value: Optional[str], data_type: PromptDataType) -> Optional[str]:
    """
    Infer MIME type from a value and its data type.

    For non-text data types, attempts to guess the MIME type from the value
    treated as a file path (using the file extension).  Returns ``None`` for
    text content or when the type cannot be determined.

    Args:
        value: The value (typically a file path for media content).
        data_type: The prompt data type (e.g., 'text', 'image', 'audio').

    Returns:
        MIME type string (e.g., 'image/png') or None.
    """
    if not value or data_type == "text":
        return None
    mime_type, _ = mimetypes.guess_type(value)
    return mime_type


def _build_filename(
    *,
    data_type: str,
    sha256: Optional[str],
    value: Optional[str],
) -> Optional[str]:
    """
    Build a human-readable download filename from the data type and hash.

    Produces names like ``image_a1b2c3d4e5f6.png`` or ``audio_e5f6g7h8i9j0.wav``.
    The hash is truncated to 12 characters for readability.

    Falls back to the file extension from *value* (path or URL) when the
    MIME type cannot be determined from the data type alone.

    Returns ``None`` for text-like types that don't need a download filename.

    Args:
        data_type: The prompt data type (e.g. ``image_path``, ``audio_path``).
        sha256: The SHA256 hash of the content, if available.
        value: The original value (path or URL) used to infer file extension.

    Returns:
        Optional[str]: A filename like ``image_a1b2c3d4e5f6.png``, or ``None`` for text-like types.
    """
    # Map data types to friendly prefixes
    prefix_map = {
        "image_path": "image",
        "audio_path": "audio",
        "video_path": "video",
        "binary_path": "file",
    }
    prefix = prefix_map.get(data_type)
    if not prefix:
        return None

    short_hash = sha256[:12] if sha256 else uuid.uuid4().hex[:12]

    # Derive extension from the value (file path or URL)
    ext = ""
    if value and not value.startswith("data:"):
        source = value
        if source.startswith("http"):
            source = urlparse(source).path
        ext = os.path.splitext(source)[1]  # e.g. ".png"

    if not ext:
        # Fallback: guess from mime type based on data type prefix
        default_ext = {"image": ".png", "audio": ".wav", "video": ".mp4", "file": ".bin"}
        ext = default_ext.get(prefix, ".bin")

    return f"{prefix}_{short_hash}{ext}"


async def pyrit_messages_to_dto_async(pyrit_messages: list[PyritMessage]) -> list[Message]:
    """
    Translate PyRIT messages to backend Message DTOs.

    Media file paths are converted to URLs the frontend can fetch directly:
    - Local files → ``/api/media?path=...`` (served by the media endpoint)
    - Azure Blob Storage files → signed URLs with SAS tokens

    Returns:
        List of Message DTOs for the API.
    """
    messages = []
    for msg in pyrit_messages:
        pieces = []
        for p in msg.message_pieces:
            orig_dtype = p.original_value_data_type or "text"
            conv_dtype = p.converted_value_data_type or "text"

            orig_val = _resolve_media_url(value=p.original_value, data_type=orig_dtype)
            conv_val = _resolve_media_url(value=p.converted_value or "", data_type=conv_dtype) or ""

            # Sign Azure Blob Storage URLs so the frontend can fetch them directly
            if orig_val and _is_azure_blob_url(orig_val):
                orig_val = await _sign_blob_url_async(blob_url=orig_val)
            if conv_val and _is_azure_blob_url(conv_val):
                conv_val = await _sign_blob_url_async(blob_url=conv_val)

            pieces.append(
                MessagePiece(
                    piece_id=str(p.id),
                    original_value_data_type=orig_dtype,
                    converted_value_data_type=conv_dtype,
                    original_value=orig_val,
                    original_value_mime_type=_infer_mime_type(value=p.original_value, data_type=orig_dtype),
                    converted_value=conv_val,
                    converted_value_mime_type=_infer_mime_type(value=p.converted_value, data_type=conv_dtype),
                    prompt_metadata=dict(p.prompt_metadata) if p.prompt_metadata else None,
                    scores=pyrit_scores_to_dto(p.scores) if p.scores else [],
                    response_error=p.response_error or "none",
                    original_filename=_build_filename(
                        data_type=orig_dtype,
                        sha256=p.original_value_sha256,
                        value=p.original_value,
                    ),
                    converted_filename=_build_filename(
                        data_type=conv_dtype,
                        sha256=p.converted_value_sha256,
                        value=p.converted_value,
                    ),
                )
            )

        first = msg.message_pieces[0] if msg.message_pieces else None
        messages.append(
            Message(
                turn_number=first.sequence if first else 0,
                role=first.get_role_for_storage() if first else "user",
                pieces=pieces,
                created_at=first.timestamp if first else datetime.now(timezone.utc),
            )
        )

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
    labels: Optional[dict[str, str]] = None,  # deprecated
) -> PyritMessagePiece:
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
        PyritMessagePiece domain object.
    """
    if labels is not None:
        print_deprecation_message(
            old_item="request_piece_to_pyrit_message_piece(..., labels=...)",
            new_item="request_piece_to_pyrit_message_piece(...)",
            removed_in="0.16.0",
        )
    metadata: Optional[dict[str, str | int]] = None
    if piece.prompt_metadata:
        metadata = dict(piece.prompt_metadata)
    elif piece.mime_type:
        metadata = {"mime_type": piece.mime_type}
    original_prompt_id = uuid.UUID(piece.original_prompt_id) if piece.original_prompt_id else None
    return PyritMessagePiece(
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
    labels: Optional[dict[str, str]] = None,  # deprecated
) -> PyritMessage:
    """
    Build a PyRIT Message from an AddMessageRequest DTO.

    Args:
        request: The inbound API request.
        conversation_id: The conversation/attack ID.
        sequence: The message sequence number.
        labels: Optional labels to attach to each piece.
            Deprecated: This parameter will be removed in a release 0.16.0.

    Returns:
        PyritMessage ready to send to the target.
    """
    if labels is not None:
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
    return PyritMessage(pieces)


# ============================================================================
# Private Helpers
# ============================================================================
