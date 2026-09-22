# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The trace context emitted for one target request."""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestTraceContext(BaseModel):
    """Request metadata linking stored evidence to a W3C trace."""

    METADATA_KEY: ClassVar[str] = "pyrit_request_trace"
    model_config = ConfigDict(frozen=True, extra="forbid")

    traceparent: str = Field(pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")

    @model_validator(mode="after")
    def _validate_identifiers(self) -> "RequestTraceContext":
        if int(self.trace_id, 16) == 0 or int(self.traceparent.split("-")[2], 16) == 0:
            raise ValueError("Trace and span identifiers must be nonzero.")
        return self

    @property
    def trace_id(self) -> str:
        """The trace retrieval key."""
        return self.traceparent.split("-")[1]

    def to_metadata(self) -> dict[str, str]:
        """Return the namespaced request metadata."""
        return {self.METADATA_KEY: self.traceparent}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "RequestTraceContext | None":
        """
        Read the request context, rejecting malformed stored metadata.

        Returns:
            RequestTraceContext | None: The recorded context, or None if absent.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls(traceparent=metadata[cls.METADATA_KEY])
