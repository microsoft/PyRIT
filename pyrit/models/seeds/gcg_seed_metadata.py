# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GCGSeedMetadata(BaseModel):
    """A seed's supplied GCG completion prefix, independent of inline job transport."""

    METADATA_KEY: ClassVar[str] = "pyrit_gcg"
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_prefix: str | None = Field(default=None, strict=True)

    @field_validator("target_prefix")
    @classmethod
    def _normalize_missing_prefix(cls, value: str | None) -> str | None:
        return value if value is not None and value.strip() else None

    def to_metadata(self) -> dict[str, Any]:
        """Return the namespaced metadata fragment for a seed."""
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> GCGSeedMetadata | None:
        """
        Read a supplied prefix without interpreting other seed metadata.

        Returns:
            GCGSeedMetadata | None: The stored payload, or None if absent.

        Raises:
            ValueError: If stored GCG metadata is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])
