# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A provider's own identifier for a request, stored apart from any propagation format."""

import json
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field


class ProviderTraceReference(BaseModel):
    """
    The identifier a provider returned for one request, for resolving its trace later.

    PyRIT-created context is recorded as a W3C ``traceparent`` (``RequestTraceContext``), which
    only works when the provider accepts and continues that context. Many providers and gateways
    do not, but they return their own id for the response. This record keeps that id as it came
    back, with no propagation format, so a trace backend can resolve it to a trace.

    Attributes:
        provider (str): The provider route the target sent the request through. For
            ``LiteLLMChatTarget`` this is LiteLLM's provider (``custom_llm_provider``, such as
            ``"openai"`` for any OpenAI-compatible gateway), not a gateway's own name.
        request_id (str): The provider's id for the response, such as a completion id.
    """

    METADATA_KEY: ClassVar[str] = "pyrit_provider_trace"
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=256)

    def to_metadata(self) -> dict[str, str]:
        """
        Return the namespaced request metadata.

        Returns:
            dict[str, str]: The reference as compact JSON under ``METADATA_KEY``.
        """
        return {self.METADATA_KEY: json.dumps(self.model_dump(), separators=(",", ":"))}

    @classmethod
    def clear_metadata(cls, metadata: dict[str, Any]) -> None:
        """Remove the reference without touching unrelated metadata."""
        metadata.pop(cls.METADATA_KEY, None)

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
