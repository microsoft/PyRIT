# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Self

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
    def from_metadata(cls, *, metadata: dict[str, Any]) -> Self | None:
        """
        Read invocation status without interpreting tool-returned data.

        Returns:
            Self | None: The recorded status, or None if absent.

        Raises:
            ValueError: If stored invocation metadata is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])


class ToolLoopStopReason(str, Enum):
    """Why a target's tool loop ended a turn before the model gave its answer."""

    MAX_TOOL_ROUNDS_EXHAUSTED = "max_tool_rounds_exhausted"
    TOOL_INFRASTRUCTURE_ERROR = "tool_infrastructure_error"


class ToolLoopStop(BaseModel):
    """The reason a target recorded on the error response that ended a tool loop early."""

    METADATA_KEY: ClassVar[str] = "pyrit_tool_loop_stop"
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: ToolLoopStopReason

    def to_metadata(self) -> dict[str, str | int]:
        """Return the metadata fragment for the error response."""
        return {self.METADATA_KEY: self.reason.value}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> Self | None:
        """
        Read the reason a tool loop stopped.

        Returns:
            Self | None: The recorded reason, or None if the loop did not stop early.

        Raises:
            ValueError: If the stored reason is unknown.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls(reason=ToolLoopStopReason(metadata[cls.METADATA_KEY]))
