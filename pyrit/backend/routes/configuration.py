# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend configuration file API routes."""

import os

from azure.core.exceptions import ResourceNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request, status

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.configuration import (
    ConfigurationFileContent,
    EnvironmentFileContent,
    EnvironmentFileListResponse,
    UpdateConfigurationFileRequest,
    UpdateEnvironmentFileRequest,
)
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.environment_file_service import EnvironmentFileService


def _require_admin(request: Request) -> None:
    """Require an administrator when authentication is enabled."""
    user = getattr(request.state, "user", None)
    if user is None:
        return
    if not isinstance(user, AuthenticatedUser) or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required",
        )


router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(_require_admin)])


def _get_configuration_file_service(request: Request) -> ConfigurationFileService:
    """
    Get the configuration file service associated with backend startup.

    Returns:
        ConfigurationFileService: The active configuration file service.
    """
    service = getattr(request.app.state, "configuration_file_service", None)
    if isinstance(service, ConfigurationFileService):
        return service
    return ConfigurationFileService(config_file_value=os.getenv("PYRIT_CONFIG_FILE"))


def _get_environment_file_service(request: Request) -> EnvironmentFileService:
    """
    Get the environment file service created during backend startup.

    Returns:
        EnvironmentFileService: The active environment file service.

    Raises:
        HTTPException: If backend startup did not initialize the service.
    """
    service = getattr(request.app.state, "environment_file_service", None)
    if not isinstance(service, EnvironmentFileService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Environment file configuration is unavailable",
        )
    return service


@router.get(
    "",
    response_model=ConfigurationFileContent,
    responses={404: {"model": ProblemDetail, "description": "Configuration file not found"}},
)
async def get_configuration_file(  # pyrit-async-suffix-exempt
    request: Request,
) -> ConfigurationFileContent:
    """
    Read the backend configuration file or blob.

    Returns:
        ConfigurationFileContent: The raw YAML configuration contents.
    """
    service = _get_configuration_file_service(request)
    try:
        content = await service.read_async()
    except (FileNotFoundError, ResourceNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configuration file not found") from exc
    return ConfigurationFileContent(content=content, source=service.source)


@router.put(
    "",
    response_model=ConfigurationFileContent,
    responses={404: {"model": ProblemDetail, "description": "Configuration file not found"}},
)
async def update_configuration_file(  # pyrit-async-suffix-exempt
    body: UpdateConfigurationFileRequest,
    request: Request,
) -> ConfigurationFileContent:
    """
    Replace the backend configuration file or blob contents.

    Changes take effect the next time the backend starts.

    Returns:
        ConfigurationFileContent: The persisted YAML configuration contents.
    """
    service = _get_configuration_file_service(request)
    try:
        await service.read_async()
        await service.update_async(body.content)
    except (FileNotFoundError, ResourceNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Configuration file not found") from exc
    return ConfigurationFileContent(content=body.content, source=service.source)


@router.get("/env-files", response_model=EnvironmentFileListResponse)
async def list_environment_files(  # pyrit-async-suffix-exempt
    request: Request,
) -> EnvironmentFileListResponse:
    """
    Read environment files selected by the active backend configuration.

    Returns:
        EnvironmentFileListResponse: Environment files in effective load order.
    """
    items = await _get_environment_file_service(request).list_async()
    return EnvironmentFileListResponse(items=items)


@router.get(
    "/env-files/{file_id}",
    response_model=EnvironmentFileContent,
    responses={404: {"model": ProblemDetail, "description": "Environment file not found"}},
)
async def get_environment_file(  # pyrit-async-suffix-exempt
    file_id: str,
    request: Request,
) -> EnvironmentFileContent:
    """
    Read one environment source selected by backend configuration.

    Returns:
        EnvironmentFileContent: The selected environment source and its contents.
    """
    try:
        return await _get_environment_file_service(request).read_async(file_id=file_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment file not found") from exc


@router.put(
    "/env-files/{file_id}",
    response_model=EnvironmentFileContent,
    responses={404: {"model": ProblemDetail, "description": "Environment file not found"}},
)
async def update_environment_file(  # pyrit-async-suffix-exempt
    file_id: str,
    body: UpdateEnvironmentFileRequest,
    request: Request,
) -> EnvironmentFileContent:
    """
    Replace one environment file selected by backend configuration.

    Changes take effect the next time the backend starts.

    Returns:
        EnvironmentFileContent: The persisted environment file.
    """
    try:
        return await _get_environment_file_service(request).update_async(file_id=file_id, content=body.content)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
