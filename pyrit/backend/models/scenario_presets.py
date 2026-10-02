# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
REST envelopes for the scenario preset endpoints.

The canonical preset types (``ScenarioPreset``, ``StoredPreset``) live in
``pyrit.models.catalog.scenario_preset`` and are imported from there directly.
The models here only add the wire-level concerns: the storage source, the
advisory issues attached to a read, and the launch-owned fields that a preset
deliberately does not carry.
"""

from typing import Any

from pydantic import BaseModel, Field

from pyrit.models.catalog.scenario_preset import ScenarioPreset

__all__ = [
    "PresetIssue",
    "ResolveScenarioPresetRequest",
    "ScenarioPresetListResponse",
    "ScenarioPresetResponse",
    "UpdateScenarioPresetRequest",
]


class PresetIssue(BaseModel):
    """
    One advisory problem found while checking a preset against the live registry.

    Issues are reported, never enforced. A preset that names a scenario this
    deployment has not registered is still a valid preset; it simply cannot run
    here yet. Blocking the save would make presets un-portable between
    deployments, which is the one property they exist to have.
    """

    field: str = Field(..., description="Preset field the issue applies to, e.g. 'scenario_name' or 'techniques'")
    message: str = Field(..., description="Human-readable description of why the reference does not resolve")


class ScenarioPresetResponse(BaseModel):
    """A stored preset, the version of the document it was read from, and its advisory issues."""

    preset: ScenarioPreset = Field(..., description="The stored preset")
    version: str = Field(..., description="Opaque version of the stored document; required to update it")
    issues: list[PresetIssue] = Field(
        default_factory=list,
        description="Advisory problems resolving this preset against the live registry; empty when it is runnable",
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
    """

    preset: ScenarioPreset = Field(..., description="The replacement preset")
    expected_version: str = Field(..., description="Version returned when the preset being edited was read")


class ResolveScenarioPresetRequest(BaseModel):
    """
    The launch-owned fields a preset deliberately omits.

    A preset answers *what to test*; these answer *how and where*. Resolution is
    a union of the two, which is why nothing here overlaps a preset field.
    """

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
    labels: dict[str, str] | None = Field(None, description="Labels to attach to memory entries")
