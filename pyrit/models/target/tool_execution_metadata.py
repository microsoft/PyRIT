# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class ToolExecutionMetadata(BaseModel):
    """Target-recorded invocation status, separate from a tool's returned data."""

    METADATA_KEY: ClassVar[str] = "pyrit_tool_execution"
    model_config = ConfigDict(frozen=True, extra="forbid")

    invoked: bool = Field(strict=True)

    def to_metadata(self) -> dict[str, Any]:
        """Return the metadata fragment for a function-call output piece."""
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> ToolExecutionMetadata | None:
        """
        Read invocation status without interpreting tool-returned data.

        Returns:
            ToolExecutionMetadata | None: The recorded status, or None if absent.

        Raises:
            ValueError: If stored invocation metadata is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])
