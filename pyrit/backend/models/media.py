# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Requests and responses for staged media uploads."""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import IdentifierStr


class MediaUploadRequest(BaseModel):
    """Upload bytes without creating an attack or sending a message."""

    data_type: Literal["image_path", "audio_path", "video_path", "binary_path"]
    content: str = Field(description="Raw base64-encoded file bytes.")
    mime_type: IdentifierStr = "application/octet-stream"


class MediaUploadResponse(BaseModel):
    """Expiring upload reference that a later message can reuse."""

    value: str
