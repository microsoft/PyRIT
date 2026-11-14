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
from pyrit.models import (
    GenerationRange,
    JSONValue,
    Parameter,
    TargetCapabilities,
    TargetIdentifier,
    validate_registry_name,
)
from pyrit.models.catalog import TargetInstance
from pyrit.models.component_spec import SourceInstanceSpec

__all__ = [
    "CreateTargetRequest",
    "DetectTargetCapabilitiesRequest",
    "TargetListResponse",
    "TargetTypeEntry",
    "TargetTypeResponse",
]


class DetectTargetCapabilitiesRequest(BaseModel):
    """The target identity selected before starting discovery."""

    expected_hash: str = Field(..., min_length=1, max_length=128)


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
    saveable_auth_modes: list[AuthMode] = Field(
        default_factory=list,
        description=(
            "The supported authentication modes a saved target of this type can use. Empty when the type can't "
            "be saved; not_saveable_reason says why. The values a target is given are checked when it's saved"
        ),
    )
    not_saveable_reason: str | None = Field(
        None, description="Why a target of this type can't be saved, or None when it can be with a saveable mode"
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

    # LEGACY COMPATIBILITY: The GUI now sends a name whenever it registers a target. Requiring the name for
    # registered targets would break API callers that leave it out, so that is left to a separate breaking change.
    name: str | None = Field(
        None,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name, required with persist; when omitted, the backend generates one",
    )
    register: bool = Field(True, description="Register the object; false returns only its descriptor")
    persist: bool = Field(
        False,
        description=(
            "Save the target so it comes back after the backend restarts or is reinitialized (true), or keep it "
            "only until then (false). A saved target needs a name of lowercase letters, digits, and underscores, "
            "and can't hold or use a key: it authenticates with Microsoft Entra ID ('identity') or needs no key."
        ),
    )
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
        if self.source is None:
            return self
        if self.register:
            raise ValueError("source requires register=false")
        if self.params:
            raise ValueError("a copy takes its overrides in source.params")
        if "auth_mode" in self.model_fields_set:
            raise ValueError("a copy uses its source's auth mode, so auth_mode can't be set")
        return self

    @model_validator(mode="after")
    def _validate_persist(self) -> "CreateTargetRequest":
        if not self.persist:
            return self
        if not self.register:
            raise ValueError("persist requires register=true")
        if self.name is None:
            raise ValueError("persist requires a name")
        validate_registry_name(self.name)
        return self


class UnregisteredTarget(BaseModel):
    """A constructed target descriptor with no registry key or object handle."""

    identifier: TargetIdentifier
    capabilities: TargetCapabilities
    generation_ranges: dict[str, GenerationRange] = Field(
        default_factory=dict,
        description="Inclusive runtime bounds keyed by generation option name; missing entries mean unknown bounds",
    )
