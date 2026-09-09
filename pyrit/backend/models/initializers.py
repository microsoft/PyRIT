# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the initializer endpoints.

Canonical initializer catalog types (``RegisteredInitializer``) live in
``pyrit.models.catalog.initializer`` and should be imported from there directly.
Initializer parameters are described by the shared ``pyrit.models.Parameter``.
"""

from typing import Any

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import REGISTRY_NAME_PATTERN
from pyrit.models.catalog.initializer import RegisteredInitializer

__all__ = [
    "CustomInitializerListResponse",
    "CustomInitializerResponse",
    "ConfiguredInitializerSetting",
    "InitializerSettingsResponse",
    "ListRegisteredInitializersResponse",
    "RegisterInitializerRequest",
]


class ListRegisteredInitializersResponse(BaseModel):
    """Response for listing initializers."""

    items: list[RegisteredInitializer] = Field(..., description="List of initializer summaries")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class RegisterInitializerRequest(BaseModel):
    """Request body for registering a custom initializer by uploading script content."""

    name: str = Field(
        ...,
        pattern=REGISTRY_NAME_PATTERN,
        description="Registry name for the initializer (e.g., 'my_custom')",
    )
    script_content: str = Field(..., description="Python source code containing a PyRITInitializer subclass")


class CustomInitializerResponse(BaseModel):
    """Stored custom initializer source returned by the backend API."""

    initializer_name: str = Field(..., description="Initializer registry name.")
    script_content: str = Field(..., description="Stored Python source code.")
    source: str = Field(..., description="Credential-free local file path or Azure Blob URI.")


class CustomInitializerListResponse(BaseModel):
    """Custom initializer storage source and its Python definitions."""

    source: str = Field(..., description="Credential-free configured custom initializer source.")
    items: list[CustomInitializerResponse] = Field(..., description="Stored custom initializer definitions.")


class ConfiguredInitializerSetting(BaseModel):
    """A read-only initializer invocation from ``.pyrit_conf``."""

    initializer_name: str = Field(..., description="Registry name of the initializer this entry configures.")
    parameters: dict[str, Any] | None = Field(default=None, description="Parameters from the active config.")
    order_index: int = Field(..., ge=0, description="Zero-based position in the startup sequence.")


class InitializerSettingsResponse(BaseModel):
    """Response describing the initializers configured in ``.pyrit_conf``."""

    configured: list[ConfiguredInitializerSetting] = Field(
        ...,
        description="Read-only initializers from the active ``.pyrit_conf``, in run order.",
    )
