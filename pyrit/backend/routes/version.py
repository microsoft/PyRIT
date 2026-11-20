# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API routes for version information."""

import asyncio
import json
import logging
from pathlib import Path
from typing import NotRequired, TypedDict, TypeVar, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel

import pyrit
from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/version", tags=["version"])

_T = TypeVar("_T")


class BuildInfo(TypedDict):
    """Optional Docker build-info keys; commit may also be explicitly null."""

    source: NotRequired[str]
    commit: NotRequired[str | None]
    modified: NotRequired[bool]
    display: NotRequired[str]


def _load_build_info(build_info_path: Path) -> BuildInfo:
    """
    Load Docker build metadata from disk.

    Args:
        build_info_path: Path to the build metadata JSON file.

    Returns:
        BuildInfo: Parsed build metadata; every key may be absent.

    Raises:
        ValueError: If the file is not a JSON object or a known key has the wrong type.
    """
    with open(build_info_path) as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{build_info_path} must contain a JSON object, got {type(data).__name__}")
    info: BuildInfo = {}
    if "source" in data:
        info["source"] = _build_info_value(data, key="source", expected=str)
    if "commit" in data:
        info["commit"] = None if data["commit"] is None else _build_info_value(data, key="commit", expected=str)
    if "modified" in data:
        info["modified"] = _build_info_value(data, key="modified", expected=bool)
    if "display" in data:
        info["display"] = _build_info_value(data, key="display", expected=str)
    return info


def _build_info_value(data: dict[str, object], *, key: str, expected: type[_T]) -> _T:
    """
    Return one build-info value after checking its type.

    Args:
        data: The parsed build-info object.
        key: The key to read.
        expected: The type the value must have.

    Returns:
        The value under ``key``.

    Raises:
        ValueError: If the value does not have the expected type.
    """
    value = data[key]
    if not isinstance(value, expected):
        raise ValueError(f"Build info '{key}' must be {expected.__name__}, got {type(value).__name__}")
    return value


class VersionResponse(BaseModel):
    """Version information response model."""

    version: str
    compatibility_id: str
    source: str | None = None
    commit: str | None = None
    modified: bool | None = None
    display: str
    database_info: str | None = None
    default_labels: dict[str, str] | None = None
    default_operation: str | None = None


@router.get("", response_model=VersionResponse)
async def get_version_async(request: Request) -> VersionResponse:
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
    if await asyncio.to_thread(build_info_path.exists):
        try:
            build_info = await asyncio.to_thread(_load_build_info, build_info_path)
            source = build_info.get("source")
            commit = build_info.get("commit")
            modified = build_info.get("modified")
            display = build_info.get("display", version)
        except Exception as e:
            logger.warning(f"Failed to load build info: {e}")

    # Detect current database backend
    database_info: str | None = None
    try:
        memory = CentralMemory.get_memory_instance()
        db_type = type(memory).__name__
        db_name = None
        if memory.engine is not None and memory.engine.url.database:
            db_name = memory.engine.url.database.split("?")[0]
        database_info = f"{db_type} ({db_name})" if db_name else f"{db_type} (None)"
    except Exception as e:
        logger.debug(f"Could not detect database info: {e}")

    # Read default labels from app state. RuntimeLifecycle publishes both from the validated
    # server config, as dict[str, str] and str | None, and they are unset before startup.
    default_labels = cast("dict[str, str] | None", getattr(request.app.state, "default_labels", None)) or None
    default_operation = cast("str | None", getattr(request.app.state, "default_operation", None))

    return VersionResponse(
        version=version,
        compatibility_id=request.app.state.compatibility_id,
        source=source,
        commit=commit,
        modified=modified,
        display=display,
        database_info=database_info,
        default_labels=default_labels,
        default_operation=default_operation,
    )
