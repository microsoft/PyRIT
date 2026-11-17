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
    Resolve identifiers the caller recorded alongside their traces.

    Use this when the application or gateway reports which trace served which response or run,
    for example from its own logs. An identifier recorded for two different traces is ambiguous
    and resolves to None.
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
        self._traces: dict[tuple[str | None, str, str], set[str]] = {}
        self._lock = Lock()

    def get_identifier(self) -> ComponentIdentifier:
        """Return the caller's nonsecret identity for this mapping."""
        return ComponentIdentifier.of(self, params={"resolver_id": self._resolver_id})

    def record(
        self,
        *,
        trace_id: str,
        request_id: str | None = None,
        run_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        """
        Record that a response id or run id belongs to a trace.

        Args:
            trace_id (str): The trace, as 32 lowercase hex digits.
            request_id (str | None): The provider's response id. Defaults to None.
            run_id (str | None): The run or call id. Defaults to None.
            provider (str | None): Limit the match to this provider. None matches any provider.

        Raises:
            ValueError: If neither id is given or the trace id is malformed.
        """
        if re.fullmatch(r"[0-9a-f]{32}", trace_id) is None or int(trace_id, 16) == 0:
            raise ValueError("trace_id must be 32 lowercase hex digits and nonzero.")
        if request_id is None and run_id is None:
            raise ValueError("Record a request id, a run id, or both.")
        with self._lock:
            for kind, value in (("request", request_id), ("run", run_id)):
                if value is not None:
                    self._traces.setdefault((provider, kind, value), set()).add(trace_id)

    async def resolve_trace_ids_async(self, *, references: Sequence[ProviderTraceReference]) -> tuple[str | None, ...]:
        """
        Resolve each reference from the recorded mapping.

        Returns:
            tuple[str | None, ...]: The trace id per reference, or None when unknown or ambiguous.
        """
        with self._lock:
            return tuple(self._resolve(reference) for reference in references)

    def _resolve(self, reference: ProviderTraceReference) -> str | None:
        if reference.trace_id is not None:
            return reference.trace_id
        found: set[str] = set()
        for kind, value in (("request", reference.request_id), ("run", reference.run_id)):
            if value is None:
                continue
            found |= self._traces.get((reference.provider, kind, value), set())
            found |= self._traces.get((None, kind, value), set())
        return next(iter(found)) if len(found) == 1 else None


class SpanAttributeTraceResolver:
    """
    Resolve identifiers by finding the captured span that carries them as an attribute.

    By default a response id matches ``gen_ai.response.id``, the attribute OpenTelemetry's GenAI
    semantic conventions use for a model response. Run ids match only the attributes the caller
    names, since frameworks record them under different keys. A value found in more than one
    trace is ambiguous and resolves to None.
    """

    DEFAULT_REQUEST_ID_ATTRIBUTES: tuple[str, ...] = ("gen_ai.response.id",)

    def __init__(
        self,
        *,
        trace_client: InMemoryTraceClient,
        request_id_attributes: Sequence[str] = DEFAULT_REQUEST_ID_ATTRIBUTES,
        run_id_attributes: Sequence[str] = (),
    ) -> None:
        """
        Initialize over a capture that holds the provider's spans.

        Raises:
            ValueError: If no attribute is named or a name is blank.
        """
        names = (*request_id_attributes, *run_id_attributes)
        if not names or any(not name.strip() for name in names):
            raise ValueError("Name at least one attribute, and no blank names.")
        self._client = trace_client
        self._request_id_attributes = tuple(request_id_attributes)
        self._run_id_attributes = tuple(run_id_attributes)

    def get_identifier(self) -> ComponentIdentifier:
        """Return the attributes this resolver matches and the capture it reads."""
        return ComponentIdentifier.of(
            self,
            params={
                "request_id_attributes": list(self._request_id_attributes),
                "run_id_attributes": list(self._run_id_attributes),
            },
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
            if reference.trace_id is not None:
                resolved.append(reference.trace_id)
                continue
            found: set[str] = set()
            if reference.request_id is not None:
                found |= self._client.find_trace_ids(keys=self._request_id_attributes, value=reference.request_id)
            if reference.run_id is not None:
                found |= self._client.find_trace_ids(keys=self._run_id_attributes, value=reference.run_id)
            resolved.append(next(iter(found)) if len(found) == 1 else None)
        return tuple(resolved)
