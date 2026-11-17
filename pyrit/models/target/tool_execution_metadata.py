# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ToolCaptureGap(str, Enum):
    """Why the stored messages for a model response may not show every tool that ran for it."""

    HOSTED_TOOL_CALL = "hosted_tool_call"
    UNRECOGNIZED_OUTPUT = "unrecognized_output"
    CALLS_NOT_EXECUTED = "calls_not_executed"


class ToolCaptureCoverage(BaseModel):
    """
    Whether a target stored every tool execution behind one model response.

    A target that runs the model's function calls itself records this on each model response.
    ``complete`` means every tool that ran for the response is stored as a function_call piece
    paired with a function_call_output piece, so the stored messages can rule out other tools.
    ``gaps`` name what the target could not store, such as a tool the provider ran itself.
    """

    METADATA_KEY: ClassVar[str] = "pyrit_tool_capture"
    model_config = ConfigDict(frozen=True, extra="forbid")

    gaps: tuple[ToolCaptureGap, ...] = ()

    @model_validator(mode="after")
    def _validate_gaps(self) -> Self:
        if len(set(self.gaps)) != len(self.gaps):
            raise ValueError("Tool capture gaps must be unique.")
        return self

    @property
    def complete(self) -> bool:
        """Whether the stored messages show every tool that ran for the response."""
        return not self.gaps

    def to_metadata(self) -> dict[str, Any]:
        """Return the metadata fragment for the response's first piece."""
        return {self.METADATA_KEY: [gap.value for gap in self.gaps]}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> Self | None:
        """
        Read the recorded capture coverage.

        Returns:
            Self | None: The recorded coverage, or None if the target recorded none.

        Raises:
            ValueError: If the stored coverage is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        gaps = metadata[cls.METADATA_KEY]
        if not isinstance(gaps, list):
            raise ValueError("Stored tool capture coverage must be a list of gaps.")
        return cls(gaps=tuple(ToolCaptureGap(gap) for gap in gaps))
