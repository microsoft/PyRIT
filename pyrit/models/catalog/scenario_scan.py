# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario scan definition models."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.models.identifiers.class_name_utils import validate_registry_name
from pyrit.models.request_limits import MAX_ITEMS

SCAN_SCHEMA_VERSION = 1


class ScenarioScanEntry(BaseModel):
    """One ordered reference to a scenario preset."""

    model_config = ConfigDict(extra="forbid")

    entry_id: UUID = Field(..., description="Stable entry identifier, preserved while the entry is reordered")
    preset_name: str = Field(..., description="Scenario preset name resolved when the scan is read")

    @field_validator("preset_name")
    @classmethod
    def _validate_preset_name(cls, value: str) -> str:
        """
        Validate that the referenced preset name is a legal registry name.

        Returns:
            str: The validated preset name.

        Raises:
            ValueError: If the name is not a legal registry name.
        """
        validate_registry_name(value)
        return value


class ScenarioScan(BaseModel):
    """
    A named, ordered collection of scenario preset references.

    Only preset names are stored. Preset origin, version, and advisory issues are
    deployment-specific projections added when the scan is read.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique scan name, used as the storage key")
    schema_version: int = Field(1, ge=1, description="Shape of this document")
    description: str | None = Field(None, description="Human-readable summary of the scan")
    author: str | None = Field(
        None,
        description="Who created this scan; descriptive only, never used to authorize a read or a write",
    )
    entries: list[ScenarioScanEntry] = Field(
        default_factory=list,
        max_length=MAX_ITEMS,
        description="Ordered preset references; duplicate preset names are allowed",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """
        Validate that the scan name is a legal registry name.

        Returns:
            str: The validated scan name.

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

        Returns:
            int: The validated schema version.

        Raises:
            ValueError: If the document declares an unsupported schema version.
        """
        if value > SCAN_SCHEMA_VERSION:
            raise ValueError(
                f"Scenario scan schema version {value} is newer than this build reads "
                f"(up to {SCAN_SCHEMA_VERSION}); upgrade PyRIT to use this scan."
            )
        return value

    @model_validator(mode="after")
    def _validate_unique_entry_ids(self) -> Self:
        """
        Reject duplicate entry identifiers without constraining preset reuse.

        Returns:
            Self: The validated scan.

        Raises:
            ValueError: If two entries share one ``entry_id``.
        """
        seen: set[UUID] = set()
        for entry in self.entries:
            if entry.entry_id in seen:
                raise ValueError(f"Scenario scan entry_id '{entry.entry_id}' is duplicated.")
            seen.add(entry.entry_id)
        return self


class StoredScenarioScan(BaseModel):
    """A scenario scan together with the version of its stored document."""

    scan: ScenarioScan = Field(..., description="The stored scenario scan")
    version: str = Field(..., description="Opaque version of the document this scan was read from")
