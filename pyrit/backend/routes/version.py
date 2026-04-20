# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API routes for version information."""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import pyrit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/version", tags=["version"])


class VersionResponse(BaseModel):
    """Version information response model."""

    version: str
    source: Optional[str] = None
    commit: Optional[str] = None
    modified: Optional[bool] = None
    display: str


@router.get("", response_model=VersionResponse)
async def get_version_async() -> VersionResponse:
    """
    Get version information for the PyRIT installation.

    Returns version from pyrit.__version__ and additional build info
    if running in Docker (from /app/build_info.json).

    Returns:
        VersionResponse: Version information including build metadata.
    """
    version = pyrit.__version__
    display = version
    source = None
    commit = None
    modified = None

    # Try to load build info from Docker
    build_info_path = Path("/app/build_info.json")
    if build_info_path.exists():
        try:
            with open(build_info_path, "r") as f:
                build_info = json.load(f)
                source = build_info.get("source")
                commit = build_info.get("commit")
                modified = build_info.get("modified")
                display = build_info.get("display", version)
        except Exception as e:
            logger.warning(f"Failed to load build info: {e}")

    return VersionResponse(version=version, source=source, commit=commit, modified=modified, display=display)
