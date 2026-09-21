# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

The canonical target instance type (``TargetInstance``) lives in
``pyrit.models.catalog.target`` and should be imported from there directly.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import REGISTRY_INSTANCE_NAME_PATTERN, PaginationInfo
from pyrit.models import JSONValue, Parameter
from pyrit.models.catalog.target import TargetInstance

__all__ = [
    "CreateTargetRequest",
    "TargetListResponse",
    "TargetTypeEntry",
    "TargetTypeResponse",
]


def _default_auth_modes() -> list[Literal["api_key", "identity"]]:
    return ["api_key"]


class TargetTypeEntry(BaseModel):
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


class TargetTypeResponse(BaseModel):
    """Response for listing available target types from the registry."""

    items: list[TargetTypeEntry] = Field(..., description="List of available target types")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class CreateTargetRequest(BaseModel):
    """Request to create a new target instance."""

    # LEGACY COMPATIBILITY: The current target configuration UI does not send a
    # name. Make this field required after that UI sends explicit registry names.
    name: str | None = Field(
        None,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name; omitted only for legacy UI compatibility",
    )
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
