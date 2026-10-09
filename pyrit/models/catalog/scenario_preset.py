# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario preset model.

A scenario preset is a named, reusable, target-agnostic answer to *what to test*:
a scenario plus the scenario-owned configuration fields. It deliberately excludes
everything environment-specific — target, concurrency, retries, and labels — which
belongs to a launch rather than to the preset. That split is what lets one preset
run unchanged against dev, staging, and production.

Every configurable field is tri-state. ``None`` means "not set by this preset, use
the scenario's own default", which is distinct from an explicit value that happens
to equal that default. Collapsing the two would silently pin a scenario default at
the moment a preset was saved and stop it tracking upstream changes.

``PresetIssue`` lives here rather than with the REST envelopes because checking a
preset against the live registry is the registry's job, not the wire layer's, and
both the registry and its callers need to name the result type.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.models.catalog.scenario import (
    _RequestFilters,
    _RequestName,
    _RequestNames,
    _RequestParams,
    _RequestTechniques,
    _validate_dataset_filter_mapping,
)
from pyrit.models.identifiers.class_name_utils import validate_registry_name


class ScenarioPreset(BaseModel):
    """
    A named, reusable, target-agnostic scenario configuration.

    Presets own *what to test*. A launch owns *how and where* — the target,
    concurrency, retries, and labels — so those fields are absent here by design
    and the two sets are combined by union rather than by precedence.

    Unknown keys are rejected rather than ignored. These documents are hand-edited, and
    because an absent field is a meaningful state, a misspelled key would otherwise parse
    cleanly and leave the preset silently testing the scenario default instead of the
    value the file plainly states.

    ``description`` and ``author`` describe the preset rather than configure the run, so
    they are not tri-state: an absent value means unknown, not "use the scenario default".
    ``author`` is filled in from the signed-in user when a preset is created through the
    API and is informational only — these documents are hand-editable, so it records
    provenance rather than proving it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique preset name, used as the storage key and as the reference from scans")
    scenario_name: _RequestName = Field(..., min_length=1, description="Registered scenario this preset configures")
    description: str | None = Field(None, description="Human-readable summary of what this preset tests")
    author: str | None = Field(
        None,
        description="Who created this preset; descriptive only, never used to authorize a read or a write",
    )
    techniques: _RequestTechniques | None = Field(None, description="Technique names; None uses the scenario default")
    dataset_names: _RequestNames | None = Field(None, description="Dataset names; None uses the scenario default")
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum selected logical seed groups")
    dataset_filters: _RequestFilters | None = Field(
        None,
        description="Dataset seed filters keyed by field. Accepted keys: harm_categories, data_types.",
    )
    include_baseline: bool | None = Field(None, description="Override the scenario baseline default")
    scenario_params: _RequestParams | None = Field(
        None, description="Scenario-declared parameters such as template names and attempt counts"
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """
        Validate that the preset name is a legal registry name.

        Returns:
            str: The validated name.

        Raises:
            ValueError: If the name is not a legal registry name.
        """
        validate_registry_name(value)
        return value

    @field_validator("dataset_filters")
    @classmethod
    def _validate_dataset_filters(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        """
        Validate dataset filters against the shared allow-list.

        Returns:
            dict[str, list[str]] | None: Validated filters.
        """
        return _validate_dataset_filter_mapping(value)


class StoredPreset(BaseModel):
    """
    A preset together with the version of the document it was read from.

    The version describes the *stored document*, not the preset, so it is paired with
    the preset rather than carried as a field on it. Storing the token inside the file
    it guards would let a hand-edit rewrite the very value used to detect that edit.

    The token is opaque. Callers round-trip it from a read back into a write and must
    not parse, compare, or order it.
    """

    preset: ScenarioPreset = Field(..., description="The stored preset")
    version: str = Field(..., description="Opaque version of the document this preset was read from")


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
