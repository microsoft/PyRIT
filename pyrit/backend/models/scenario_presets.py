# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the scenario preset endpoints.

The canonical preset types (``ScenarioPreset``, ``StoredPreset``, ``PresetIssue``)
live in ``pyrit.models.catalog.scenario_preset`` and are imported from there
directly. ``PresetIssue`` is re-exported here so that a client of the wire models
does not have to reach across into the catalog for one field's element type.
The models here only add wire-level concerns: the storage source and the run
size attached to a listing.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pyrit.models.catalog.scenario import ScenarioRunSizeEstimate
from pyrit.models.catalog.scenario_preset import PresetIssue, ScenarioPreset

__all__ = [
    "PresetIssue",
    "ScenarioPresetListResponse",
    "ScenarioPresetResponse",
    "UpdateScenarioPresetRequest",
]


class ScenarioPresetResponse(BaseModel):
    """A preset, the version of the document it was read from, and its advisory issues."""

    preset: ScenarioPreset = Field(..., description="The preset")
    origin: Literal["stored", "registered"] = Field(
        "stored",
        description=(
            "Whether a stored document or registering code contributed this preset. A registered preset "
            "is held in memory by the code that registered it and cannot be updated or deleted here."
        ),
    )
    version: str | None = Field(
        ...,
        description=(
            "Opaque version of the stored document, required to update it. None for a registered preset, "
            "which has no document to guard and nothing to round-trip into a write."
        ),
    )
    issues: list[PresetIssue] = Field(
        default_factory=list,
        description="Advisory problems resolving this preset against the live registry; empty when it is runnable",
    )
    run_size: ScenarioRunSizeEstimate | None = Field(
        None,
        description=(
            "Run size for this preset's own techniques, datasets, and limits rather than the scenario default. "
            "Only the list endpoint fills this in, and only when estimates are requested; it stays None for a "
            "preset that cannot be resolved in this deployment."
        ),
    )


class ScenarioPresetListResponse(BaseModel):
    """The configured preset storage source and every preset readable from it."""

    source: str = Field(..., description="Credential-free configured preset source")
    items: list[ScenarioPresetResponse] = Field(..., description="Stored presets, sorted by name")


class UpdateScenarioPresetRequest(BaseModel):
    """
    Request body for updating an existing preset.

    ``expected_version`` is required rather than optional because updating is a
    distinct operation from creating: a client that has not read the document it
    is replacing has nothing to be optimistic about. Creation goes through POST,
    where no version exists to supply.
    An omitted or null ``preset.author`` preserves the stored author.
    """

    preset: ScenarioPreset = Field(..., description="The replacement preset")
    expected_version: str = Field(..., description="Version returned when the preset being edited was read")
