# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer API response models.

Initializers configure the PyRIT environment (targets, datasets, env vars)
before scenario execution. These models represent initializer metadata.
"""

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import REGISTRY_NAME_PATTERN


class InitializerParameterSummary(BaseModel):
    """Summary of an initializer-declared parameter."""

    name: str = Field(..., description="Parameter name")
    description: str = Field(..., description="Human-readable description of the parameter")
    default: list[str] | None = Field(None, description="Default value(s), or None if required")


class RegisteredInitializer(BaseModel):
    """Summary of a registered initializer."""

    initializer_name: str = Field(..., description="Initializer registry name (e.g., 'target')")
    initializer_type: str = Field(..., description="Initializer class name (e.g., 'TargetInitializer')")
    description: str = Field("", description="Human-readable description of the initializer")
    required_env_vars: list[str] = Field(
        default_factory=list, description="Environment variables required by this initializer"
    )
    supported_parameters: list[InitializerParameterSummary] = Field(
        default_factory=list, description="Parameters accepted by this initializer"
    )


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
