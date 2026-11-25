# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""API routes for version information."""

import logging
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

import pyrit
from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/version", tags=["version"])


class BuildInfo(BaseModel):
    """Docker build-info file; every key may be absent and extra keys are ignored."""

    model_config = ConfigDict(strict=True)

    source: str | None = None
    commit: str | None = None
    modified: bool | None = None
    display: str | None = None


def _load_build_info(build_info_path: Path) -> BuildInfo:
    """
    Load Docker build metadata from disk.

    Args:
        build_info_path: Path to the build metadata JSON file.

    Returns:
        BuildInfo: Parsed build metadata; absent keys are None.

    Raises:
        pydantic.ValidationError: If the file is not a JSON object or a known key has the wrong type.
    """
    with open(build_info_path) as file:
        return BuildInfo.model_validate_json(file.read())


def read_build_info(build_info_path: Path = Path("/app/build_info.json")) -> BuildInfo:
    """
    Read Docker build metadata once at startup, so version requests never touch the disk or a thread pool.

    Args:
        build_info_path (Path): Path to the build metadata JSON file. Defaults to the Docker image location.

    Returns:
        BuildInfo: Parsed build metadata, or an empty ``BuildInfo`` when the file is absent or invalid.
    """
    if not build_info_path.exists():
        return BuildInfo()
    try:
        return _load_build_info(build_info_path)
    except Exception as e:
        logger.warning(f"Failed to load build info: {e}")
        return BuildInfo()


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

    Returns version from pyrit.__version__ and the Docker build info that the app
    lifespan read from /app/build_info.json at startup.

    Returns:
        VersionResponse: Version information including build metadata.
    """
    version = pyrit.__version__
    # The app lifespan publishes a BuildInfo; it is unset before startup.
    build_info = cast("BuildInfo | None", getattr(request.app.state, "build_info", None)) or BuildInfo()

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
        source=build_info.source,
        commit=build_info.commit,
        modified=build_info.modified,
        display=build_info.display or version,
        database_info=database_info,
        default_labels=default_labels,
        default_operation=default_operation,
    )
