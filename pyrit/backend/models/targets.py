# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes and write-request types for the target endpoints.

Canonical target catalog types (``TargetInstance``, ``TargetCapabilitiesInfo``)
live in ``pyrit.models.catalog.target`` and should be imported from there
directly.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import JSONValue
from pyrit.models.catalog.target import TargetInstance

__all__ = [
    "CreateTargetRequest",
    "TargetListResponse",
]


class TargetListResponse(BaseModel):
    """Response for listing target instances."""

    items: list[TargetInstance] = Field(..., description="List of target instances")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")


class CreateTargetRequest(BaseModel):
    """Request to create a new target instance."""

    type: str = Field(..., description="Target type (e.g., 'OpenAIChatTarget')")
    params: dict[str, JSONValue] = Field(default_factory=dict, description="Target constructor parameters")
    auth_mode: Literal["api_key", "entra"] = Field(
        "api_key",
        description=(
            "Authentication mode. 'api_key' uses the api_key in params (default). "
            "'entra' uses Microsoft Entra ID; requires an Azure endpoint and is "
            "supported by OpenAI-family targets and AzureMLChatTarget."
        ),
    )
