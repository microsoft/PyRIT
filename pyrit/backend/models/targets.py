# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

The canonical target instance type (``TargetInstance``) lives in
``pyrit.models.catalog.target`` and should be imported from there directly.
"""

from pydantic import BaseModel, Field, model_validator

from pyrit.backend.models.common import MAX_ITEMS, REGISTRY_INSTANCE_NAME_PATTERN, IdentifierStr, PaginationInfo
from pyrit.common import AuthMode
from pyrit.models import JSONValue, Parameter, TargetCapabilities, TargetIdentifier
from pyrit.models.catalog import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec

__all__ = [
    "CreateTargetRequest",
    "TargetListResponse",
    "TargetTypeEntry",
    "TargetTypeResponse",
]


def _default_auth_modes() -> list[AuthMode]:
    return ["api_key"]


class TargetTypeEntry(BaseModel):
    """A target type available from the backend registry."""

    target_type: str = Field(..., description="Target class name (e.g., 'OpenAIChatTarget')")
    parameters: list[Parameter] = Field(
        default_factory=list,
        description="Constructor parameters for dynamic form generation",
    )
    supported_auth_modes: list[AuthMode] = Field(
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
    register: bool = Field(True, description="Register the object; false returns only its descriptor")
    source: SourceInstanceSpec | None = None
    type: IdentifierStr = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[IdentifierStr, JSONValue] = Field(
        default_factory=dict, max_length=MAX_ITEMS, description="Target constructor parameters"
    )
    auth_mode: AuthMode = Field(
        "api_key",
        description=(
            "Authentication mode. 'api_key' uses the api_key in params (default). "
            "'identity' omits the key so the target authenticates itself via an ambient "
            "Azure identity (Entra ID token or DefaultAzureCredential); requires an Azure "
            "endpoint and is supported by OpenAI-family targets, AzureMLChatTarget, "
            "AzureBlobStorageTarget, and PromptShieldTarget. 'none' reads no key and sends a fixed "
            "placeholder, for OpenAI-compatible servers that need no key; supported by OpenAI-family targets."
        ),
    )

    @model_validator(mode="after")
    def _validate_source(self) -> "CreateTargetRequest":
        if self.source is not None and (self.register or self.params or "auth_mode" in self.model_fields_set):
            raise ValueError("source requires register=false and overrides, including auth_mode, in source.params")
        return self


class UnregisteredTarget(BaseModel):
    """A constructed target descriptor with no registry key or object handle."""

    identifier: TargetIdentifier
    capabilities: TargetCapabilities
