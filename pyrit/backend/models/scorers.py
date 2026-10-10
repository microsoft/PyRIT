# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Request and response models for scorer registry endpoints."""

from pydantic import BaseModel, Field, model_validator

from pyrit.backend.models.common import REGISTRY_INSTANCE_NAME_PATTERN, PaginationInfo
from pyrit.models import JSONValue, Parameter, ScorerIdentifier
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


class CreateScorerRequest(BaseModel):
    """Request to construct and register a named scorer."""

    name: str | None = Field(
        None, min_length=1, pattern=REGISTRY_INSTANCE_NAME_PATTERN, description="Unique registry name"
    )
    register: bool = Field(True, description="Register the object; false returns only its descriptor")
    type: str = Field(..., description="Scorer class name")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Scorer constructor parameters")

    @model_validator(mode="after")
    def _validate_registration(self) -> "CreateScorerRequest":
        if self.register and not self.name:
            raise ValueError("name is required when register=true")
        return self


class UnregisteredScorer(BaseModel):
    """A scorer descriptor without a registry key."""

    identifier: ScorerIdentifier
