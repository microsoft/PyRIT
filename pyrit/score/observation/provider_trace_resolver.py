# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Resolve the identifiers a provider returned for a request to the trace it recorded."""

from __future__ import annotations

import re
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from pyrit.models import ComponentIdentifier, ProviderTraceReference

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.score.observation.trace_client import InMemoryTraceClient


class ProviderTraceResolver(Protocol):
    """
    Map provider-returned identifiers to trace ids, without assuming W3C propagation.

    A resolver answers only what it knows. An identifier it cannot map, or one that matches more
    than one trace, resolves to None, so the request stays unlinked and a verdict that rests on
    absence stays undetermined instead of becoming a false negative.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return a stable resolver identity without credentials."""
        ...

    async def resolve_trace_ids_async(self, *, references: Sequence[ProviderTraceReference]) -> tuple[str | None, ...]:
        """Return one trace id, or None, per reference, in order."""
        ...


class MappingProviderTraceResolver:
    """
    Resolve response ids the caller recorded alongside their traces.

    Use this when the application or gateway reports which trace served which response, for
    example from its own logs. A response id recorded for two different traces is ambiguous and
    resolves to None.
    """

    def __init__(self, *, resolver_id: str = "local") -> None:
        """
        Initialize an empty mapping.

        Raises:
            ValueError: If the identity is empty.
        """
        if not resolver_id.strip():
            raise ValueError("resolver_id must be nonempty.")
        self._resolver_id = resolver_id
        self._traces: dict[tuple[str | None, str], set[str]] = {}
        self._lock = Lock()

    def get_identifier(self) -> ComponentIdentifier:
        """Return the caller's nonsecret identity for this mapping."""
        return ComponentIdentifier.of(self, params={"resolver_id": self._resolver_id})

    def record(self, *, trace_id: str, request_id: str, provider: str | None = None) -> None:
        """
        Record that a response id belongs to a trace.

        Args:
            trace_id (str): The trace, as 32 lowercase hex digits.
            request_id (str): The provider's response id.
            provider (str | None): Limit the match to this provider. None matches any provider.

        Raises:
            ValueError: If the response id is empty or the trace id is malformed.
        """
        if re.fullmatch(r"[0-9a-f]{32}", trace_id) is None or int(trace_id, 16) == 0:
            raise ValueError("trace_id must be 32 lowercase hex digits and nonzero.")
        if not request_id:
            raise ValueError("request_id must be nonempty.")
        with self._lock:
            self._traces.setdefault((provider, request_id), set()).add(trace_id)

    async def resolve_trace_ids_async(self, *, references: Sequence[ProviderTraceReference]) -> tuple[str | None, ...]:
        """
        Resolve each reference from the recorded mapping.

        Returns:
            tuple[str | None, ...]: The trace id per reference, or None when unknown or ambiguous.
        """
        with self._lock:
            return tuple(self._resolve(reference) for reference in references)

    def _resolve(self, reference: ProviderTraceReference) -> str | None:
        found = self._traces.get((reference.provider, reference.request_id), set())
        found = found | self._traces.get((None, reference.request_id), set())
        return next(iter(found)) if len(found) == 1 else None


class SpanAttributeTraceResolver:
    """
    Resolve response ids by finding the captured span that carries them as an attribute.

    By default a response id matches ``gen_ai.response.id``, the attribute OpenTelemetry's GenAI
    semantic conventions use for a model response. A value found in more than one trace is
    ambiguous and resolves to None.
    """

    DEFAULT_REQUEST_ID_ATTRIBUTES: tuple[str, ...] = ("gen_ai.response.id",)

    def __init__(
        self,
        *,
        trace_client: InMemoryTraceClient,
        request_id_attributes: Sequence[str] = DEFAULT_REQUEST_ID_ATTRIBUTES,
    ) -> None:
        """
        Initialize over a capture that holds the provider's spans.

        Raises:
            ValueError: If no attribute is named or a name is blank.
        """
        if not request_id_attributes or any(not name.strip() for name in request_id_attributes):
            raise ValueError("Name at least one attribute, and no blank names.")
        self._client = trace_client
        self._request_id_attributes = tuple(request_id_attributes)

    def get_identifier(self) -> ComponentIdentifier:
        """Return the attributes this resolver matches and the capture it reads."""
        return ComponentIdentifier.of(
            self,
            params={"request_id_attributes": list(self._request_id_attributes)},
            children={"trace_client": self._client.get_identifier()},
        )

    async def resolve_trace_ids_async(self, *, references: Sequence[ProviderTraceReference]) -> tuple[str | None, ...]:
        """
        Resolve each reference by searching the capture's spans.

        Returns:
            tuple[str | None, ...]: The trace id per reference, or None when unknown or ambiguous.
        """
        resolved: list[str | None] = []
        for reference in references:
            found = self._client.find_trace_ids(keys=self._request_id_attributes, value=reference.request_id)
            resolved.append(next(iter(found)) if len(found) == 1 else None)
        return tuple(resolved)
