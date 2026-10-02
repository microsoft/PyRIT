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
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.models.catalog.scenario import _validate_dataset_filter_mapping
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
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique preset name, used as the storage key and as the reference from scans")
    scenario_name: str = Field(..., min_length=1, description="Registered scenario this preset configures")
    description: str | None = Field(None, description="Human-readable summary of what this preset tests")
    techniques: list[str] | None = Field(None, description="Technique names; None uses the scenario default")
    dataset_names: list[str] | None = Field(None, description="Dataset names; None uses the scenario default")
    max_dataset_size: int | None = Field(None, ge=1, description="Maximum selected logical seed groups")
    dataset_filters: dict[str, list[str]] | None = Field(
        None,
        description="Dataset seed filters keyed by field. Accepted keys: harm_categories, data_types.",
    )
    include_baseline: bool | None = Field(None, description="Override the scenario baseline default")
    scenario_params: dict[str, Any] | None = Field(
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
