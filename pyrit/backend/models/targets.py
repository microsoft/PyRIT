# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

The canonical target instance type (``TargetInstance``) lives in
``pyrit.models.catalog.target`` and should be imported from there directly.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import MAX_ITEMS, REGISTRY_INSTANCE_NAME_PATTERN, IdentifierStr, PaginationInfo
from pyrit.models import JSONValue, Parameter
from pyrit.models.catalog.instance_recipe import CredentialReference, UnrestorableInstance
from pyrit.models.catalog.target import TargetInstance

__all__ = [
    "CreateTargetRequest",
    "TargetListResponse",
    "TargetSettings",
    "TargetTypeEntry",
    "TargetTypeResponse",
    "UpdateTargetRequest",
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
    unrestorable: list[UnrestorableInstance] = Field(
        default_factory=list,
        description="Saved targets the last restore could not rebuild, with the reasons (not paginated)",
    )
    restore_error: str | None = Field(
        None, description="Why the last restore could not read the saved instance store, if it could not"
    )


class TargetSettings(BaseModel):
    """The type, parameters, and credentials a target is built from."""

    type: IdentifierStr = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[IdentifierStr, JSONValue] = Field(
        default_factory=dict, max_length=MAX_ITEMS, description="Target constructor parameters"
    )
    credentials: dict[IdentifierStr, CredentialReference] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description=(
            "Credential parameters read from server environment variables, for example "
            "{'api_key': {'env_var': 'MY_OPENAI_KEY'}}. Only the variable name is saved. "
            "Requires administrator access."
        ),
    )
    auth_mode: Literal["api_key", "identity"] = Field(
        "api_key",
        description=(
            "Authentication mode. 'api_key' uses the api_key credential, or the target's default "
            "environment variable when none is referenced (default). "
            "'identity' omits the key so the target authenticates itself via an ambient "
            "Azure identity (Entra ID token or DefaultAzureCredential); requires an Azure "
            "endpoint and is supported by OpenAI-family targets, AzureMLChatTarget, "
            "AzureBlobStorageTarget, and PromptShieldTarget."
        ),
    )


class CreateTargetRequest(TargetSettings):
    """Request to create a new target instance."""

    # LEGACY COMPATIBILITY: Older clients do not send a name. Make this field
    # required when the temporary registry compatibility routes are removed.
    name: str | None = Field(
        None,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name; omitted only for legacy client compatibility",
    )


class UpdateTargetRequest(TargetSettings):
    """Request to replace a saved target; parameters and credentials it omits are removed."""

    version: IdentifierStr = Field(..., description="Version returned when the target was read")
