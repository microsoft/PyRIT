# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical REST representation of a registered scorer instance."""

from pydantic import BaseModel, Field

from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier


class ScorerInstance(BaseModel):
    """A scorer registry name paired with its complete typed identifier."""

    scorer_registry_name: str = Field(..., description="Scorer instance registry key")
    identifier: ScorerIdentifier = Field(..., description="Complete scorer configuration and child identities")
    description: str | None = Field(None, description="Short description of the scorer type")
    version: str | None = Field(
        None, description="Version of the saved scorer, required to change or delete it; None if not saved"
    )
