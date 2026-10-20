# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""References for constructing private variants of registered components."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.identifiers.component_identifier import JSONValue


class SourceInstanceSpec(BaseModel):
    """A source identity and constructor overrides, never a live object handle."""

    model_config = ConfigDict(extra="forbid")

    source_name: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    params: dict[str, JSONValue] = Field(default_factory=dict)
    effective_hash: str | None = None


class TargetBinding(BaseModel):
    """Non-secret reconstruction information saved with a manual attack."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    source_name: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    effective_hash: str = Field(min_length=1)

    def to_spec(self) -> SourceInstanceSpec:
        """Return the constructor specification."""
        return SourceInstanceSpec(
            source_name=self.source_name,
            source_hash=self.source_hash,
            params={"temperature": self.temperature},
            effective_hash=self.effective_hash,
        )

    def to_metadata(self) -> dict[str, JSONValue]:
        """Return the metadata entry owned by this value."""
        return {"target_binding": self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, metadata: dict[str, object]) -> Self | None:
        """
        Read a binding without treating invalid saved data as a default.

        Returns:
            Self | None: The validated binding, or None when no binding is stored.
        """
        value = metadata.get("target_binding")
        return cls.model_validate(value) if value is not None else None
