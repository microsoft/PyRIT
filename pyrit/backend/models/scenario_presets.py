# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the scenario preset endpoints.

The canonical preset types (``ScenarioPreset``, ``StoredPreset``, ``PresetIssue``)
live in ``pyrit.models.catalog.scenario_preset`` and are imported from there
directly. ``PresetIssue`` is re-exported here so that a client of the wire models
does not have to reach across into the catalog for one field's element type.
The models here only add the wire-level concerns: the storage source, the run
size attached to a listing, and the launch-owned fields that a preset
deliberately does not carry.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from pyrit.models.catalog.scenario import ScenarioRunSizeEstimate
from pyrit.models.catalog.scenario_preset import PresetIssue, ScenarioPreset

__all__ = [
    "PresetIssue",
    "ResolveScenarioPresetRequest",
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


class ResolveScenarioPresetRequest(BaseModel):
    """
    The launch-owned fields a preset deliberately omits.

    A preset answers *what to test*; these answer *how and where*. Resolution is
    a union of the two, which is why nothing here overlaps a preset field.

    ``expected_version`` is the exception: it is a precondition rather than a
    launch field. A client that previewed a resolution sends the version it
    previewed so an edit landing in between is reported instead of silently
    launching a configuration the operator never saw.
    """

    expected_version: str | None = Field(
        None, description="Version the caller resolved against; omit to resolve whatever is stored now"
    )
    target_name: str = Field(..., description="Name of a registered target from the TargetRegistry")
    adversarial_target_name: str | None = Field(
        None, description="Name of a registered adversarial target, when the scenario uses one"
    )
    initializers: list[str] | None = Field(None, description="Initializer names to run before the scenario")
    initializer_args: dict[str, dict[str, Any]] | None = Field(
        None, description="Per-initializer parameter overrides keyed by initializer name"
    )
    max_concurrency: int | None = Field(
        None, ge=1, le=100, description="Maximum concurrent operations; omit to use the run default"
    )
    max_retries: int | None = Field(
        None, ge=0, le=20, description="Maximum retry attempts on failure; omit to use the run default"
    )
    operation_id: UUID | None = Field(None, description="Saved Operation reference, or unassigned")
    labels: dict[str, str] | None = Field(None, description="Labels to attach to memory entries")
