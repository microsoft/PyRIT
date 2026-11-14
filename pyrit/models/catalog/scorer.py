# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical REST representation of a registered scorer instance."""

from pydantic import BaseModel, Field

from pyrit.models.catalog.registry_recipe import RegistryEntryOrigin
from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier


class ScorerInstance(BaseModel):
    """A scorer registry name paired with its complete typed identifier."""

    scorer_registry_name: str = Field(..., description="Scorer instance registry key")
    identifier: ScorerIdentifier = Field(..., description="Complete scorer configuration and child identities")
    description: str | None = Field(None, description="Short description of the scorer type")
    display_type: str | None = Field(None, description="Compact type including nested scorer wrappers")
    scorable_types: list[str] = Field(default_factory=list, description="Declared evidence reference types")
    condition_types: list[str] = Field(default_factory=list, description="Criteria the scorer evaluates")
    evaluation_identifier: ComponentIdentifier | None = Field(None, description="Behavioral evaluation identity")
    metrics: dict[str, str | int | float | None] | None = Field(
        None, description="Stored evaluation metrics, without raw trials"
    )
    chat_scoring_disabled_reason: str | None = Field(
        None, description="Why this instance cannot be selected as a chat objective scorer"
    )
    origin: RegistryEntryOrigin | None = Field(
        None,
        description=(
            "Where the scorer comes from: 'saved' (restored at every start), 'configuration' (the server's "
            "startup scripts), or 'temporary' (gone after a restart or reinitialization)"
        ),
    )
    saved_by: str | None = Field(
        None,
        description=(
            "Name of the user who saved the scorer, for display only; None when it isn't saved or was saved "
            "with sign-in off"
        ),
    )
    saved_by_me: bool = Field(False, description="Whether the signed-in user asking is the one who saved the scorer")
