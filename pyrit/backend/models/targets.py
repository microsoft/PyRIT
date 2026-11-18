# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

The canonical target instance type (``TargetInstance``) lives in
``pyrit.models.catalog.target`` and should be imported from there directly.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.backend.models.common import (
    MAX_ITEMS,
    REGISTRY_INSTANCE_NAME_PATTERN,
    IdentifierStr,
    PaginationInfo,
    RegistrationRequest,
)
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
    "UpdateTargetCredentialsRequest",
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
    description: str | None = Field(None, description="Short description of the target from its docstring")


class TargetTypeResponse(BaseModel):
    """Response for listing available target types from the registry."""

    items: list[TargetTypeEntry] = Field(..., description="List of available target types")


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class CreateTargetRequest(RegistrationRequest):
    """Request to create a new target instance."""

    # LEGACY COMPATIBILITY: The current target configuration UI does not send a
    # name. Make this field required after that UI sends explicit registry names.
    name: str | None = Field(
        None,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name; omitted only for legacy UI compatibility",
    )
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
    private: bool | None = Field(
        None,
        description=(
            "Whether only the signed-in user who creates the target can see and use it, and anything built with "
            "it. Omitted, it is shared, unless it is built on your private targets; then it is private and false "
            "is refused. A private target requires register=true and can't be saved with persist."
        ),
    )
    allow_credential_updates: bool | None = Field(
        None,
        description=(
            "Whether the target's API key and request headers can be updated in place later, through "
            "PUT /api/targets/{name}/credentials: by you alone when the target is private, and by anyone signed in "
            "when it is shared. Omitted, a private target allows updates if its type can make them (OpenAI-family "
            "targets other than RealtimeTarget), and a shared target doesn't. True is refused for a type that "
            "can't, and for a saved target."
        ),
    )

    @model_validator(mode="after")
    def _validate_source(self) -> "CreateTargetRequest":
        if self.source is None:
            return self
        if self.register_instance:
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
        if not self.register_instance:
            raise ValueError("persist requires register=true")
        if self.name is None:
            raise ValueError("persist requires a name")
        # A saved target is restored with no caller, so it would come back shared, and its credentials can't change.
        if self.private:
            raise ValueError("A private target can't be saved; leave out persist or private")
        if self.allow_credential_updates:
            raise ValueError(
                "A saved target's credentials can't be updated; leave out persist or allow_credential_updates"
            )
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


class UpdateTargetCredentialsRequest(BaseModel):
    """
    Request to update a target's API key or request headers in place.

    Only targets created with credential updates allowed accept it: a private one from its owner, a shared one
    from anyone signed in.
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(
        None,
        max_length=16_384,
        description=(
            "The new API key or access token. If the target has an explicit Authorization header, update that "
            "header instead of sending just api_key. Never returned."
        ),
    )
    headers: dict[str, str] | None = Field(
        None,
        min_length=1,
        max_length=MAX_ITEMS,
        description=(
            "Request headers to set, each replacing any header of the same name (case-insensitive), wherever the "
            "target was configured with it. Any header that carries credentials can be set, such as Authorization, "
            "x-api-key, or Cookie (the whole cookie string). Values are never returned."
        ),
    )
    private: bool | None = Field(
        None,
        description=(
            "Whether you expect the target to be private. When set and the target's visibility differs, nothing "
            "changes and the request fails with 409, so a token meant for your private target never reaches a "
            "shared target that took its name."
        ),
    )

    @model_validator(mode="after")
    def _require_a_credential(self) -> "UpdateTargetCredentialsRequest":
        if self.api_key is None and not self.headers:
            raise ValueError("Set api_key, headers, or both")
        return self
