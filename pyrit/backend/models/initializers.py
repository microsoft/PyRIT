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
from pyrit.models import REGISTRY_NAME_PATTERN, InitializerSetting
from pyrit.models.catalog.initializer import RegisteredInitializer

__all__ = [
    "ApplyInitializerRequest",
    "ApplyInitializerResponse",
    "EffectiveInitializerSetting",
    "InitializerSettingResponse",
    "ListEffectiveInitializerSettingsResponse",
    "ListRegisteredInitializersResponse",
    "RegisterInitializerRequest",
    "UpdateInitializerSettingRequest",
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


class EffectiveInitializerSetting(RegisteredInitializer):
    """Merged initializer settings plus registry metadata."""

    enabled: bool = Field(..., description="Whether the initializer is enabled in the effective list.")
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Effective parameters that will be used for this initializer.",
    )
    order_index: int = Field(..., ge=0, description="Effective zero-based order position.")
    saved_order_index: int | None = Field(
        default=None,
        ge=0,
        description="Saved override order, if one exists.",
    )
    source: Literal["baseline", "override", "baseline+override"] = Field(
        ...,
        description="Whether this effective row comes from the config baseline, a saved override, or both.",
    )


class ListEffectiveInitializerSettingsResponse(BaseModel):
    """Response for listing merged initializer settings."""

    items: list[EffectiveInitializerSetting] = Field(
        ...,
        description="Merged baseline and saved initializer settings.",
    )


class UpdateInitializerSettingRequest(BaseModel):
    """Request body for saving one initializer override."""

    enabled: bool = Field(default=True, description="Whether the initializer should remain enabled.")
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Parameter overrides to persist for this initializer.",
    )
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based order override for this initializer.",
    )


class InitializerSettingResponse(InitializerSetting):
    """Saved initializer override row."""


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
