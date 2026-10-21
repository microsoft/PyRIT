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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pyrit.models.catalog.scenario import (
    _RequestName,
    _RequestParams,
    _RequestTechniques,
)
from pyrit.models.identifiers.class_name_utils import validate_registry_name
from pyrit.models.identifiers.component_identifier import ComponentIdentifier, Identifiable
from pyrit.models.scenario_dataset import ScenarioDatasetRequest

PRESET_SCHEMA_VERSION = 1

# Excluded from the behavioral identity: ``name`` is the registry key rather than
# configuration, ``schema_version`` describes the document shape, and ``description`` and
# ``author`` are descriptive. Naming the exclusions rather than the inclusions means a
# configuration field added later counts as behavioral without touching this.
_NON_BEHAVIORAL_FIELDS = frozenset({"name", "schema_version", "description", "author"})


class ScenarioPreset(BaseModel, Identifiable):
    """
    A named, reusable, target-agnostic scenario configuration.

    Presets own *what to test*; a launch owns *how and where* — target, concurrency,
    retries, and labels — so those fields are absent here by design and the two sets are
    combined by union rather than by precedence.

    Unknown keys are rejected rather than ignored: these documents are hand-edited, and
    because an absent field is a meaningful state, a misspelled key would otherwise parse
    cleanly and leave the preset testing the scenario default.

    A document names what to build by *registered name*, not by class identity, so a
    preset can move between deployments that register the same name from different code.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique preset name, used as the storage key and as the reference from scans")
    # Literal rather than PRESET_SCHEMA_VERSION: a document written before this field existed
    # is a version 1 document, and must keep reading back as one after the constant is bumped.
    # Defaulting to the constant would relabel those documents as the newest version while they
    # still hold the oldest content, so a migration would skip exactly the documents needing it.
    schema_version: int = Field(1, ge=1, description="Shape of this document")
    scenario_name: _RequestName = Field(..., min_length=1, description="Registered scenario this preset configures")
    description: str | None = Field(None, description="Human-readable summary of what this preset tests")
    author: str | None = Field(
        None,
        description="Who created this preset; descriptive only, never used to authorize a read or a write",
    )
    techniques: _RequestTechniques | None = Field(None, description="Technique names; None uses the scenario default")
    datasets: ScenarioDatasetRequest | None = Field(
        None,
        description="Dataset sources, limits, fetch policy, and filters. Omit to use the scenario defaults.",
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

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        """
        Reject a document written by a newer PyRIT than this one.

        Unknown fields are already refused, so without this check a future document that
        only reinterprets existing fields would parse and run as something else.

        Returns:
            int: The validated schema version.

        Raises:
            ValueError: If the document declares a schema this build cannot read.
        """
        if value > PRESET_SCHEMA_VERSION:
            raise ValueError(
                f"Preset schema version {value} is newer than this build reads "
                f"(up to {PRESET_SCHEMA_VERSION}); upgrade PyRIT to use this preset."
            )
        return value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity of this preset.

        Returns:
            ComponentIdentifier: Identity covering the configuration fields only, so two
            presets differing just by name or description share one identity.
        """
        return ComponentIdentifier(
            class_name=type(self).__name__,
            class_module=type(self).__module__,
            params=self.model_dump(mode="json", exclude=set(_NON_BEHAVIORAL_FIELDS), exclude_none=True),
        )

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the behavioral identity of this preset, rebuilt on every call.

        The inherited implementation caches on the instance, which suits a component
        configured once at construction. A preset is a mutable document, so a cached
        value would survive ``model_copy(update=...)`` and a modified copy would report
        the original's identity.

        Returns:
            ComponentIdentifier: Identity derived from the current field values.
        """
        return self._build_identifier()


class StoredPreset(BaseModel):
    """
    A preset together with the version of the document it was read from.

    The version describes the *stored document*, not the preset, so it is paired with the
    preset rather than carried as a field on it: storing the token inside the file it
    guards would let a hand-edit rewrite the value used to detect that edit.

    The token is opaque. Callers round-trip it from a read back into a write.
    """

    preset: ScenarioPreset = Field(..., description="The stored preset")
    version: str = Field(..., description="Opaque version of the document this preset was read from")


class PresetEntry(BaseModel):
    """
    A preset as the registry sees it, whichever layer contributed it.

    ``version`` is set only for a stored preset. A registered preset is held in memory by
    the code that registered it, so there is no document to guard and nothing to
    round-trip into a write.
    """

    preset: ScenarioPreset = Field(..., description="The preset")
    origin: Literal["stored", "registered"] = Field(
        ..., description="Whether a stored document or registering code contributed this preset"
    )
    version: str | None = Field(
        None, description="Opaque version of the stored document, or None for a registered preset"
    )


class PresetIssue(BaseModel):
    """
    One advisory problem found while checking a preset against the live registry.

    Issues are reported, never enforced. A preset naming a scenario this deployment has
    not registered is still a valid preset; it simply cannot run here yet. Blocking the
    save would make presets un-portable between deployments, which is the one property
    they exist to have.
    """

    field: str = Field(..., description="Preset field the issue applies to, e.g. 'scenario_name' or 'techniques'")
    message: str = Field(..., description="Human-readable description of why the reference does not resolve")
