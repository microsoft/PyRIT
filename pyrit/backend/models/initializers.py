# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the initializer endpoints.

Canonical initializer catalog types (``RegisteredInitializer``,
``InitializerParameterSummary``) live in ``pyrit.models.catalog.initializer``
and should be imported from there directly.
"""

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import REGISTRY_NAME_PATTERN
from pyrit.models.catalog.initializer import RegisteredInitializer

__all__ = [
    "ListRegisteredInitializersResponse",
    "RegisterInitializerRequest",
]


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
