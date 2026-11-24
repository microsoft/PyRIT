# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for scorer registry endpoints."""

from pydantic import BaseModel, Field, model_validator

from pyrit.backend.models.common import (
    MAX_ITEMS,
    REGISTRY_INSTANCE_NAME_PATTERN,
    IdentifierStr,
    PaginationInfo,
    RegistrationRequest,
)
from pyrit.models import JSONValue, Parameter, ScorerIdentifier
from pyrit.models.catalog import ScorerInstance


class ScorerTypeEntry(BaseModel):
    """A registered scorer class and its shared constructor contract."""

    scorer_type: str = Field(..., description="Scorer class name")
    parameters: list[Parameter] = Field(default_factory=list, description="Constructor parameters from ScorerRegistry")
    is_llm_based: bool = Field(False, description="Whether this scorer references a target")
    supports_objective_scoring: bool = Field(False, description="Whether this class implements true/false scoring")
    description: str | None = Field(None, description="Short description from the scorer class docstring")


class ScorerTypeResponse(BaseModel):
    """Available scorer classes."""

    items: list[ScorerTypeEntry]


class ScorerListResponse(BaseModel):
    """Paginated registered scorer instances."""

    items: list[ScorerInstance]
    pagination: PaginationInfo


class CreateScorerRequest(RegistrationRequest):
    """Request to construct and register a named scorer."""

    name: str | None = Field(
        None, min_length=1, pattern=REGISTRY_INSTANCE_NAME_PATTERN, description="Unique registry name"
    )
    type: IdentifierStr = Field(..., description="Scorer class name")
    params: dict[IdentifierStr, JSONValue] = Field(
        default_factory=dict, max_length=MAX_ITEMS, description="Scorer constructor parameters"
    )
    private: bool | None = Field(
        None,
        description=(
            "Whether only the signed-in user who creates it can see and use it. Omitted, it is shared, unless "
            "it is built on your private targets; then it is private and false is refused. true requires register=true."
        ),
    )

    @model_validator(mode="after")
    def _validate_registration(self) -> "CreateScorerRequest":
        if self.register_instance and not self.name:
            raise ValueError("name is required when register=true")
        return self


class UnregisteredScorer(BaseModel):
    """A scorer descriptor without a registry key."""

    identifier: ScorerIdentifier
