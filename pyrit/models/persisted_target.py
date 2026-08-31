# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persisted definitions for targets created through the backend API."""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from pyrit.models.identifiers import JSONValue


class PersistedTarget(BaseModel):
    """A reconstructable API-created target definition without inline secrets."""

    id: str = Field(default_factory=lambda: str(uuid4()), description="Stable unique row id.")
    target_registry_name: str = Field(..., description="Registry name assigned when the target was created.")
    target_type: str = Field(..., description="Target class name used by TargetRegistry.")
    parameters: dict[str, JSONValue] = Field(
        default_factory=dict,
        description="JSON-serializable constructor parameters with secrets removed.",
    )
    auth_mode: Literal["api_key", "identity"] = Field(default="api_key", description="Target authentication mode.")
    secret_uri: str | None = Field(
        default=None,
        description="Versionless Azure Key Vault secret URI containing the API key.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation time used to restore dependent targets in order.",
    )
