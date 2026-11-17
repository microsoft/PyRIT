# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A provider's own identifiers for a request, stored apart from any propagation format."""

import json
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID = Field(default=None, min_length=1, max_length=256)


class ProviderTraceReference(BaseModel):
    """
    The identifiers a provider returned for one request, for resolving its trace later.

    PyRIT-created context is recorded as a W3C ``traceparent`` (``RequestTraceContext``), which
    only works when the provider accepts and continues that context. Many providers and gateways
    do not, but they return their own identifiers: a response id, a run or call id, and sometimes
    the id of the trace they recorded. This record keeps those identifiers as they came back, with
    no propagation format, so a trace backend can resolve them to a trace.

    Attributes:
        provider (str): The provider or gateway that returned the identifiers.
        request_id (str | None): The provider's id for the response, such as a completion id.
        run_id (str | None): A run or call id the provider or gateway assigned to the request.
        trace_id (str | None): The trace the provider recorded, as 32 lowercase hex digits, when
            it reports one. A known trace id needs no resolution.
    """

    METADATA_KEY: ClassVar[str] = "pyrit_provider_trace"
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=128)
    request_id: str | None = _ID
    run_id: str | None = _ID
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def _validate_identifiers(self) -> Self:
        if self.request_id is None and self.run_id is None and self.trace_id is None:
            raise ValueError("A provider trace reference needs a request id, a run id, or a trace id.")
        if self.trace_id is not None and int(self.trace_id, 16) == 0:
            raise ValueError("A provider trace id must be nonzero.")
        return self

    def to_metadata(self) -> dict[str, str]:
        """
        Return the namespaced request metadata.

        Returns:
            dict[str, str]: The reference as compact JSON under ``METADATA_KEY``.
        """
        return {self.METADATA_KEY: json.dumps(self.model_dump(exclude_none=True), separators=(",", ":"))}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> Self | None:
        """
        Read the reference, rejecting malformed stored metadata.

        Returns:
            Self | None: The recorded reference, or None if absent.

        Raises:
            ValueError: If the stored value is not a valid reference.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        raw = metadata[cls.METADATA_KEY]
        if not isinstance(raw, str):
            raise ValueError("A stored provider trace reference must be a JSON string.")
        return cls.model_validate_json(raw)
