# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the initializer endpoints.

Canonical initializer catalog types (``RegisteredInitializer``) live in
``pyrit.models.catalog.initializer`` and should be imported from there directly.
Initializer parameters are described by the shared ``pyrit.models.Parameter``.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import REGISTRY_NAME_PATTERN
from pyrit.models.catalog.initializer import RegisteredInitializer

__all__ = [
    "AdditionalInitializerSetting",
    "ApplyInitializerRequest",
    "ApplyInitializerResponse",
    "BaselineInitializerSetting",
    "CreateAdditionalInitializerRequest",
    "CustomInitializerListResponse",
    "CustomInitializerResponse",
    "InitializerSettingsResponse",
    "ListRegisteredInitializersResponse",
    "RegisterInitializerRequest",
    "UpdateAdditionalInitializerRequest",
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


class BaselineInitializerSetting(BaseModel):
    """A read-only baseline initializer entry, referencing its registry definition by name."""

    initializer_name: str = Field(..., description="Registry name of the initializer this entry configures.")
    parameters: dict[str, Any] | None = Field(default=None, description="Baseline parameters from the config.")
    order_index: int = Field(..., ge=0, description="Zero-based position in the baseline startup sequence.")


class AdditionalInitializerSetting(BaseModel):
    """A persisted additional initializer entry, referencing its registry definition by name."""

    id: str = Field(..., description="Stable unique row id.")
    initializer_name: str = Field(..., description="Registry name of the initializer this entry configures.")
    parameters: dict[str, Any] | None = Field(default=None, description="Persisted parameters for this invocation.")
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based position among the additional initializers.",
    )


class InitializerSettingsResponse(BaseModel):
    """Response describing the read-only baseline plus the editable additional initializers."""

    baseline: list[BaselineInitializerSetting] = Field(
        ...,
        description="Read-only initializers from the ``.pyrit_conf`` baseline, in run order.",
    )
    additional: list[AdditionalInitializerSetting] = Field(
        ...,
        description="Persisted additional initializers that run after the baseline, in run order.",
    )


class CreateAdditionalInitializerRequest(BaseModel):
    """Request body for adding a new additional initializer."""

    initializer_name: str = Field(
        ...,
        pattern=REGISTRY_NAME_PATTERN,
        description="Registry name of the initializer to add.",
    )
    parameters: dict[str, Any] | None = Field(default=None, description="Parameters to persist for this invocation.")
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based position among the additional initializers.",
    )


class UpdateAdditionalInitializerRequest(BaseModel):
    """Request body for updating one existing additional initializer."""

    parameters: dict[str, Any] | None = Field(default=None, description="Parameters to persist for this invocation.")
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based position among the additional initializers.",
    )


class ApplyInitializerRequest(BaseModel):
    """Optional request body for applying an initializer immediately."""

    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Optional one-time parameters for this apply-now request.",
    )


class ApplyInitializerResponse(BaseModel):
    """Response for a successful apply-now initializer run."""

    initializer_name: str = Field(..., description="Initializer registry name that was applied.")
    status: Literal["applied"] = Field(default="applied", description="Result status.")
    applied_parameters: dict[str, Any] | None = Field(
        default=None,
        description="Parameters used for this apply-now execution.",
    )
