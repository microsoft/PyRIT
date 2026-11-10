# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Retrieve OTLP/JSON traces by ID from a remote HTTP trace backend."""

from __future__ import annotations

import base64
import binascii
import logging
import math
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from pyrit.common.net_utility import get_httpx_client
from pyrit.models import ComponentIdentifier, TraceCoverage, TraceQueryResult, TraceSpan, TraceSpanStatus
from pyrit.score.observation.trace_client import TraceAcquisitionError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from pydantic import JsonValue

    from pyrit.models import TraceQuery

logger = logging.getLogger(__name__)

_TRACE_ID_PLACEHOLDER = "{trace_id}"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_STATUS_CODES = {
    0: TraceSpanStatus.UNSET,
    1: TraceSpanStatus.OK,
    2: TraceSpanStatus.ERROR,
    "STATUS_CODE_UNSET": TraceSpanStatus.UNSET,
    "STATUS_CODE_OK": TraceSpanStatus.OK,
    "STATUS_CODE_ERROR": TraceSpanStatus.ERROR,
}


class OtlpJsonTraceClient:
    """
    Fetch each requested trace by ID from an HTTP endpoint that returns OTLP/JSON.

    ``url_template`` names the trace-by-ID endpoint with a ``{trace_id}`` placeholder, such as
    ``http://tempo:3200/api/traces/{trace_id}``. The response holds spans under
    ``resourceSpans`` (or the older ``batches`` key), at the top level or inside a ``result``
    or ``trace`` object, with hex or base64 span identities.

    A remote backend cannot say when every span of a trace has arrived, so coverage is never
    complete: an observed tool call stands, and an absent one stays undetermined. A trace the
    backend does not have is unavailable. A failed request, an error status, or a response that
    cannot be read raises ``TraceAcquisitionError`` when no trace was retrieved, and otherwise
    leaves coverage incomplete.
    """

    def __init__(
        self,
        *,
        url_template: str,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 10,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Initialize a client for one trace-by-ID endpoint.

        Args:
            url_template (str): An http(s) URL with one ``{trace_id}`` placeholder.
            headers (Mapping[str, str] | None): Request headers, such as credentials. They are
                sent with every request and never recorded in the identifier.
            timeout_seconds (float): The timeout for each request.
            http_client (httpx.AsyncClient | None): A caller-owned client to send requests
                through. Without one, each query opens and closes its own client.

        Raises:
            ValueError: If the template or timeout is invalid.
        """
        parsed = urlsplit(url_template)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or url_template.count(_TRACE_ID_PLACEHOLDER) != 1
        ):
            raise ValueError("url_template must be an http(s) URL with one {trace_id} placeholder.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url_template must not contain credentials; pass them in headers.")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self._url_template = url_template
        self._headers = dict(headers or {})
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def get_identifier(self) -> ComponentIdentifier:
        """Return the endpoint without its query, fragment, or headers, which may hold credentials."""
        parsed = urlsplit(self._url_template)
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return ComponentIdentifier.of(
            self, params={"endpoint": endpoint, "format": "otlp_json", "timeout_seconds": self._timeout_seconds}
        )

    async def get_spans_async(self, *, query: TraceQuery) -> TraceQueryResult:
        """
        Fetch every trace in the query's scope and report incomplete coverage.

        Returns:
            TraceQueryResult: The retrieved spans, or an unavailable result when the backend has
                none of the traces.

        Raises:
            TraceAcquisitionError: If no trace was retrieved and at least one request failed.
        """
        spans: dict[tuple[str, str], TraceSpan] = {}
        reasons: list[str] = []
        retrieved = 0
        failed = 0
        async with self._open_client_async() as client:
            for trace_id in query.scope.trace_ids:
                reason = await self._fetch_trace_async(client=client, trace_id=trace_id, spans=spans)
                if reason is None:
                    retrieved += 1
                    continue
                reasons.append(reason)
                if reason != "trace_not_found":
                    failed += 1
        if not retrieved:
            if failed:
                raise TraceAcquisitionError("No requested trace could be retrieved from the trace backend.")
            return TraceQueryResult(available=False, coverage=TraceCoverage(reasons=("trace_not_found",)))
        ordered = sorted(spans.values(), key=lambda span: (span.start_time, span.trace_id, span.span_id))
        if len(ordered) > query.limit:
            reasons.append("query_limit_exceeded")
        reasons.append("remote_completion_unknown")
        return TraceQueryResult(
            spans=tuple(ordered[: query.limit]),
            coverage=TraceCoverage(complete=False, reasons=tuple(dict.fromkeys(reasons))),
        )

    @asynccontextmanager
    async def _open_client_async(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        if self._http_client is not None:
            yield self._http_client
            return
        async with get_httpx_client(use_async=True, timeout=self._timeout_seconds) as client:
            yield client

    async def _fetch_trace_async(
        self, *, client: httpx.AsyncClient, trace_id: str, spans: dict[tuple[str, str], TraceSpan]
    ) -> str | None:
        """
        Add one trace's spans; a trace is added whole or not at all.

        Returns:
            str | None: None when the trace was retrieved, otherwise why it was not.
        """
        url = self._url_template.replace(_TRACE_ID_PLACEHOLDER, trace_id)
        try:
            response = await client.get(url, headers=self._headers, timeout=self._timeout_seconds)
        except httpx.HTTPError as error:
            logger.warning("Trace request failed (%s).", type(error).__name__)
            return "trace_query_failed"
        if response.status_code == 404:
            return "trace_not_found"
        if not response.is_success:
            logger.warning("Trace backend returned HTTP %d.", response.status_code)
            return "trace_query_failed"
        try:
            trace_spans = _parse_trace_document(response.json(), trace_id=trace_id)
        except (ValueError, TypeError) as error:
            logger.warning("Trace backend response could not be read (%s).", error)
            return "trace_response_invalid"
        spans.update(trace_spans)
        return None


def _parse_trace_document(document: Any, *, trace_id: str) -> dict[tuple[str, str], TraceSpan]:
    """
    Convert one OTLP/JSON trace document into spans of the requested trace.

    Returns:
        dict[tuple[str, str], TraceSpan]: The spans, keyed by trace and span identity.

    Raises:
        ValueError: If the document is not OTLP/JSON, names another trace, or repeats a
            span identity with different evidence.
        TypeError: If a field has the wrong JSON type.
    """
    if not isinstance(document, dict):
        raise ValueError("The trace document must be a JSON object.")
    for wrapper in ("result", "trace"):
        if isinstance(document.get(wrapper), dict):
            document = document[wrapper]
            break
    resource_spans = document.get("resourceSpans", document.get("batches"))
    if not isinstance(resource_spans, list):
        raise ValueError("The trace document has no resourceSpans.")
    spans: dict[tuple[str, str], TraceSpan] = {}
    for resource in resource_spans:
        for scope in _list(resource, "scopeSpans"):
            for raw in _list(scope, "spans"):
                span = _parse_span(raw)
                if span.trace_id != trace_id:
                    raise ValueError("The trace document contains a span of another trace.")
                key = (span.trace_id, span.span_id)
                if spans.setdefault(key, span) != span:
                    raise ValueError("The trace document repeats a span identity with different evidence.")
    return spans


def _list(container: Any, key: str) -> list[Any]:
    if not isinstance(container, dict):
        raise TypeError(f"Expected a JSON object holding {key}.")
    value = container.get(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a JSON array.")
    return value


def _parse_span(raw: Any) -> TraceSpan:
    if not isinstance(raw, dict):
        raise TypeError("A span must be a JSON object.")
    parent = raw.get("parentSpanId") or None
    end_nanos = _nanos(raw.get("endTimeUnixNano", 0))
    status = raw.get("status") or {}
    if not isinstance(status, dict):
        raise TypeError("A span status must be a JSON object.")
    code = status.get("code", 0)
    if code not in _STATUS_CODES:
        raise ValueError("A span has an unknown status code.")
    attributes = raw.get("attributes", [])
    if not isinstance(attributes, list):
        raise TypeError("Span attributes must be a JSON array.")
    return TraceSpan(
        trace_id=_hex_id(raw.get("traceId"), size=16),
        span_id=_hex_id(raw.get("spanId"), size=8),
        parent_span_id=None if parent is None else _hex_id(parent, size=8),
        start_time=_timestamp(_nanos(raw.get("startTimeUnixNano"))),
        end_time=_timestamp(end_nanos) if end_nanos else None,
        attributes=_key_values(attributes),
        status=_STATUS_CODES[code],
    )


def _hex_id(value: Any, *, size: int) -> str:
    """
    Normalize an OTLP/JSON identity, which is hex by specification and base64 in some backends.

    Returns:
        str: The lowercase hex identity.

    Raises:
        ValueError: If the value is neither encoding of an identity of this size.
    """
    if not isinstance(value, str):
        raise ValueError("A span identity must be a string.")
    if len(value) == size * 2:
        try:
            return bytes.fromhex(value).hex()
        except ValueError:
            pass
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise ValueError("A span identity is neither hex nor base64.") from error
    if len(decoded) != size:
        raise ValueError("A span identity has the wrong length.")
    return decoded.hex()


def _nanos(value: Any) -> int:
    # OTLP/JSON encodes 64-bit integers as decimal strings; some encoders emit numbers.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("A span timestamp must be an integer or a decimal string.")
    nanos = int(value)
    if nanos < 0:
        raise ValueError("A span timestamp must not be negative.")
    return nanos


def _timestamp(nanos: int) -> datetime:
    """
    Convert Unix nanoseconds exactly, truncating to the microseconds a datetime holds.

    Returns:
        datetime: The UTC timestamp.

    Raises:
        ValueError: If the timestamp is zero, which OTLP uses for an unset time.
    """
    if not nanos:
        raise ValueError("A span start time must be set.")
    return _EPOCH + timedelta(microseconds=nanos // 1000)


def _key_values(items: list[Any]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise TypeError("An attribute must be a JSON object with a string key.")
        result[item["key"]] = _any_value(item.get("value"))
    return result


def _any_value(value: Any) -> JsonValue:
    """
    Convert an OTLP AnyValue, representing bytes as integer arrays like the local exporter.

    Returns:
        JsonValue: The attribute value.

    Raises:
        ValueError: If the value has no supported OTLP type.
    """
    if value is None or value == {}:
        return None
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("An attribute value must hold exactly one OTLP value type.")
    ((kind, item),) = value.items()
    if kind == "stringValue" and isinstance(item, str):
        return item
    if kind == "boolValue" and isinstance(item, bool):
        return item
    if kind == "intValue":
        return _signed_int(item)
    if kind == "doubleValue" and isinstance(item, (int, float, str)) and not isinstance(item, bool):
        return float(item)
    if kind == "bytesValue" and isinstance(item, str):
        return list(base64.b64decode(item, validate=True))
    if kind == "arrayValue":
        return [_any_value(entry) for entry in _list(item, "values")]
    if kind == "kvlistValue":
        return dict(_key_values(_list(item, "values")))
    raise ValueError(f"Unsupported OTLP attribute value type: {kind}.")


def _signed_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("An integer attribute must be an integer or a decimal string.")
    return int(value)
