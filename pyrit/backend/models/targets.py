# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

Canonical target catalog types (``TargetInstance``) live in
``pyrit.models.catalog.target`` and should be imported from there directly.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import JSONValue, Parameter
from pyrit.models.catalog.target import TargetInstance

__all__ = [
    "CreateTargetRequest",
    "TargetCatalogEntry",
    "TargetCatalogResponse",
    "TargetListResponse",
]


def _default_auth_modes() -> list[Literal["api_key", "identity"]]:
    return ["api_key"]


class TargetCatalogEntry(BaseModel):
    """A target type available from the backend registry."""

    target_type: str = Field(..., description="Target class name (e.g., 'OpenAIChatTarget')")
    parameters: list[Parameter] = Field(
        default_factory=list,
        description="Constructor parameters for dynamic form generation",
    )
    supported_auth_modes: list[Literal["api_key", "identity"]] = Field(
        default_factory=_default_auth_modes,
        description="Authentication modes this target type supports",
    )
    description: str | None = Field(None, description="Short description of the target from its docstring")


class TargetCatalogResponse(BaseModel):
    """Response for listing available target types from the registry."""

    items: list[TargetCatalogEntry] = Field(..., description="List of available target types")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class CreateTargetRequest(BaseModel):
    """Request to create a new target instance."""

    type: str = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Target constructor parameters")
    auth_mode: Literal["api_key", "identity"] = Field(
        "api_key",
        description=(
            "Authentication mode. 'api_key' uses the api_key in params (default). "
            "'identity' omits the key so the target authenticates itself via an ambient "
            "Azure identity (Entra ID token or DefaultAzureCredential); requires an Azure "
            "endpoint and is supported by OpenAI-family targets, AzureMLChatTarget, "
            "AzureBlobStorageTarget, and PromptShieldTarget."
        ),
    )
