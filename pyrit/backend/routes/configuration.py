# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend configuration file API routes."""

import logging
import os
from hashlib import sha256

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Request, status

from pyrit.backend.middleware.auth import AuthenticatedUser, require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.configuration import (
    ConfigurationFileContent,
    EnvironmentFileContent,
    EnvironmentFileListResponse,
    UpdateConfigurationFileRequest,
    UpdateEnvironmentFileRequest,
)
from pyrit.backend.services.configuration_file_service import (
    ConfigurationFileConflictError,
    ConfigurationFileService,
)
from pyrit.backend.services.environment_file_service import EnvironmentFileConflictError, EnvironmentFileService
from pyrit.exceptions import KeyVaultInitializationException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_admin)])


def _content_hash(content: str) -> str:
    """
    Hash content for audit correlation without logging its value.

    Returns:
        str: The SHA-256 content digest.
    """
    return sha256(content.encode("utf-8")).hexdigest()


def _audit_configuration_access(
    *,
    request: Request,
    action: str,
    source: str,
    outcome: str,
    version: str | None = None,
) -> None:
    """Write a secret-free audit record for a configuration operation."""
    user = getattr(request.state, "user", None)
    oid = user.oid if isinstance(user, AuthenticatedUser) else "local-development"
    user_name = user.name if isinstance(user, AuthenticatedUser) else "local-development"
    logger.info(
        "Configuration audit oid=%s user_name=%r action=%s source=%s outcome=%s version=%s",
        oid,
        user_name,
        action,
        source,
        outcome,
        version or "none",
    )


def _storage_unavailable() -> HTTPException:
    """
    Create a sanitized response for an unavailable remote configuration store.

    Returns:
        HTTPException: A service-unavailable response without SDK details.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Configuration storage is temporarily unavailable",
    )


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
        content, version = await service.read_with_version_async()
    except AzureError as exc:
        _audit_configuration_access(request=request, action="read-config", source=service.source, outcome="unavailable")
        raise _storage_unavailable() from exc
    _audit_configuration_access(
        request=request,
        action="read-config",
        source=service.source,
        outcome="success",
        version=version,
    )
    return ConfigurationFileContent(content=content, source=service.source, version=version)


@router.put(
    "",
    response_model=ConfigurationFileContent,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid configuration file"},
        409: {"model": ProblemDetail, "description": "Configuration file changed"},
    },
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
        version = await service.update_async(body.content, expected_version=body.version)
    except AzureError as exc:
        _audit_configuration_access(
            request=request,
            action="update-config",
            source=service.source,
            outcome="unavailable",
        )
        raise _storage_unavailable() from exc
    except ValueError as exc:
        _audit_configuration_access(request=request, action="update-config", source=service.source, outcome="invalid")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConfigurationFileConflictError as exc:
        _audit_configuration_access(
            request=request,
            action="update-config",
            source=service.source,
            outcome="conflict",
            version=body.version,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _audit_configuration_access(
        request=request,
        action="update-config",
        source=service.source,
        outcome="success",
        version=version,
    )
    return ConfigurationFileContent(content=body.content, source=service.source, version=version)


@router.get("/env-files", response_model=EnvironmentFileListResponse)
async def list_environment_files(  # pyrit-async-suffix-exempt
    request: Request,
) -> EnvironmentFileListResponse:
    """
    Read environment files selected by the active backend configuration.

    Returns:
        EnvironmentFileListResponse: Environment files in effective load order.
    """
    try:
        items = await _get_environment_file_service(request).list_async()
    except (AzureError, KeyVaultInitializationException) as exc:
        _audit_configuration_access(
            request=request,
            action="list-environment-sources",
            source="environment-sources",
            outcome="unavailable",
        )
        raise _storage_unavailable() from exc
    _audit_configuration_access(
        request=request,
        action="list-environment-sources",
        source="environment-sources",
        outcome="success",
    )
    return EnvironmentFileListResponse(items=items)


@router.get(
    "/env-files/{file_id}",
    response_model=EnvironmentFileContent,
    responses={
        404: {"model": ProblemDetail, "description": "Environment file not found"},
        409: {"model": ProblemDetail, "description": "Environment file changed"},
    },
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
        item = await _get_environment_file_service(request).read_async(file_id=file_id)
    except KeyError as exc:
        _audit_configuration_access(request=request, action="read-environment", source=file_id, outcome="not-found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment file not found") from exc
    except (AzureError, KeyVaultInitializationException) as exc:
        _audit_configuration_access(request=request, action="read-environment", source=file_id, outcome="unavailable")
        raise _storage_unavailable() from exc
    _audit_configuration_access(
        request=request,
        action="read-environment",
        source=item.path,
        outcome="success",
        version=item.version,
    )
    return item


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
        item = await _get_environment_file_service(request).update_async(
            file_id=file_id,
            content=body.content,
            expected_version=body.version,
        )
    except KeyError as exc:
        _audit_configuration_access(request=request, action="update-environment", source=file_id, outcome="not-found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment file not found") from exc
    except (AzureError, KeyVaultInitializationException) as exc:
        _audit_configuration_access(request=request, action="update-environment", source=file_id, outcome="unavailable")
        raise _storage_unavailable() from exc
    except ValueError as exc:
        _audit_configuration_access(
            request=request,
            action="update-environment",
            source=file_id,
            outcome="invalid",
            version=_content_hash(body.content),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except EnvironmentFileConflictError as exc:
        _audit_configuration_access(
            request=request,
            action="update-environment",
            source=file_id,
            outcome="conflict",
            version=body.version,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _audit_configuration_access(
        request=request,
        action="update-environment",
        source=item.path,
        outcome="success",
        version=item.version,
    )
    return item
