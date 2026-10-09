# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for scorer registry endpoints."""

from pydantic import BaseModel, Field

from pyrit.backend.models.common import MAX_ITEMS, REGISTRY_INSTANCE_NAME_PATTERN, IdentifierStr, PaginationInfo
from pyrit.models import JSONValue, Parameter
from pyrit.models.catalog.instance_recipe import CredentialReference, UnrestorableInstance
from pyrit.models.catalog.scorer import ScorerInstance


class ScorerTypeEntry(BaseModel):
    """A registered scorer class and its shared constructor contract."""

    scorer_type: str = Field(..., description="Scorer class name")
    parameters: list[Parameter] = Field(default_factory=list, description="Constructor parameters from ScorerRegistry")
    is_llm_based: bool = Field(False, description="Whether this scorer references a target")
    description: str | None = Field(None, description="Short description from the scorer class docstring")


class ScorerTypeResponse(BaseModel):
    """Available scorer classes."""

    items: list[ScorerTypeEntry]


class ScorerListResponse(BaseModel):
    """Paginated registered scorer instances."""

    items: list[ScorerInstance]
    pagination: PaginationInfo
    unrestorable: list[UnrestorableInstance] = Field(
        default_factory=list,
        description="Saved scorers the last restore could not rebuild, with the reasons (not paginated)",
    )
    restore_error: str | None = Field(
        None, description="Why the last restore could not read the saved instance store, if it could not"
    )


class ScorerSettings(BaseModel):
    """The type, parameters, and credentials a scorer is built from."""

    type: str = Field(..., description="Scorer class name")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Scorer constructor parameters")
    credentials: dict[IdentifierStr, CredentialReference] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description=(
            "Credential parameters read from server environment variables. Only the variable name is saved. "
            "Requires administrator access."
        ),
    )


class CreateScorerRequest(ScorerSettings):
    """Request to construct and register a named scorer."""

    name: str = Field(..., min_length=1, pattern=REGISTRY_INSTANCE_NAME_PATTERN, description="Unique registry name")


class UpdateScorerRequest(ScorerSettings):
    """Request to replace a saved scorer; parameters and credentials it omits are removed."""

    version: IdentifierStr = Field(..., description="Version returned when the scorer was read")
