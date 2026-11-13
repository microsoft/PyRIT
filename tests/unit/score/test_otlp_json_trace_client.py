# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from pyrit.models import (
    Acquisition,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
    TraceQuery,
    TraceScorable,
    TraceSpan,
    TraceSpanStatus,
)
from pyrit.score import OtelToolCallScorer, OtelTraceSource, OtlpJsonTraceClient, TraceAcquisitionError

pytestmark = pytest.mark.usefixtures("patch_central_database")

_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_OTHER_TRACE = "5" * 32
_TEMPLATE = "https://traces.example/api/traces/{trace_id}"
_START = 1_767_225_600_123_456_789


def _otlp_span(
    *,
    tool: str | None = "lookup",
    trace_id: str = _TRACE,
    span_id: str = "00f067aa0ba902b7",
    start: int | str = str(_START),
    end: int | str = str(_START + 2_000),
    **extra: Any,
) -> dict[str, Any]:
    attributes = (
        [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
            {"key": "gen_ai.tool.name", "value": {"stringValue": tool}},
        ]
        if tool is not None
        else []
    )
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": f"execute_tool {tool}",
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "attributes": attributes,
        **extra,
    }


def _document(*spans: dict[str, Any], key: str = "resourceSpans") -> dict[str, Any]:
    return {key: [{"resource": {"attributes": []}, "scopeSpans": [{"scope": {"name": "agent"}, "spans": list(spans)}]}]}


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> tuple[OtlpJsonTraceClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return OtlpJsonTraceClient(url_template=_TEMPLATE, http_client=http_client, **kwargs), requests


def _serve(documents: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Serve one response per trace ID: a JSON document, an HTTP status, or an exception to raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        outcome = documents.get(request.url.path.rsplit("/", 1)[-1], 404)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            # A readable empty body, so only the status code can make this a failure.
            return httpx.Response(outcome, json={"resourceSpans": []})
        if isinstance(outcome, str):
            return httpx.Response(200, text=outcome)
        return httpx.Response(200, json=outcome)

    return handler


def _query(*trace_ids: str, limit: int = 10000) -> TraceQuery:
    return TraceQuery(scope=TraceScorable(trace_ids=trace_ids or (_TRACE,)), limit=limit)


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


async def test_otlp_json_spans_convert_to_trace_spans_async() -> None:
    attributes = [
        {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
        {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
        {"key": "retries", "value": {"intValue": "3"}},
        {"key": "ratio", "value": {"doubleValue": 0.5}},
        {"key": "cached", "value": {"boolValue": False}},
        {"key": "raw", "value": {"bytesValue": base64.b64encode(b"\x00\xff").decode()}},
        {"key": "tags", "value": {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": 2}]}}},
        {"key": "nested", "value": {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}}},
        {"key": "empty", "value": {}},
    ]
    span = _otlp_span(attributes=attributes, parentSpanId="b7ad6b7169203331", status={"code": "STATUS_CODE_ERROR"})
    client, requests = _client(_serve({_TRACE: _document(span)}))

    result = await client.get_spans_async(query=_query())

    assert requests[0].url == httpx.URL(f"https://traces.example/api/traces/{_TRACE}")
    assert result.spans == (
        TraceSpan(
            trace_id=_TRACE,
            span_id="00f067aa0ba902b7",
            parent_span_id="b7ad6b7169203331",
            start_time=datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, 0, 0, 0, 123458, tzinfo=UTC),
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "lookup",
                "retries": 3,
                "ratio": 0.5,
                "cached": False,
                "raw": [0, 255],
                "tags": ["a", 2],
                "nested": {"k": "v"},
                "empty": None,
            },
            status=TraceSpanStatus.ERROR,
        ),
    )


@pytest.mark.parametrize(
    "wrap",
    [
        lambda document: {"batches": document["resourceSpans"]},
        lambda document: {"result": document},
        lambda document: {"trace": document},
    ],
)
async def test_known_document_layouts_and_base64_identities_parse_the_same_async(
    wrap: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    hex_client, _ = _client(_serve({_TRACE: _document(_otlp_span(status={"code": 1}))}))
    encoded = _otlp_span(
        trace_id=base64.b64encode(bytes.fromhex(_TRACE)).decode(),
        span_id=base64.b64encode(bytes.fromhex("00f067aa0ba902b7")).decode(),
        start=_START,
        end=_START + 2_000,
        status={"code": "STATUS_CODE_OK"},
    )
    wrapped_client, _ = _client(_serve({_TRACE: wrap(_document(encoded))}))

    expected = await hex_client.get_spans_async(query=_query())
    actual = await wrapped_client.get_spans_async(query=_query())

    assert actual.spans == expected.spans
    assert actual.spans[0].status is TraceSpanStatus.OK


@pytest.mark.parametrize("parent", ["0" * 16, base64.b64encode(bytes(8)).decode()])
async def test_an_all_zero_parent_is_a_root_span_not_an_unreadable_trace_async(parent: str) -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(parentSpanId=parent))}))

    result = await client.get_spans_async(query=_query())

    assert len(result.spans) == 1
    assert result.spans[0].parent_span_id is None


async def test_unset_end_time_is_an_unfinished_span_async() -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(end="0"))}))

    result = await client.get_spans_async(query=_query())

    assert result.spans[0].end_time is None


async def test_timestamps_truncate_and_never_round_up_async() -> None:
    # 999 ns past a microsecond must stay in that microsecond, so truncation cannot invent an order.
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(start=str(_START - 789 + 999), end=str(_START + 1_000)))}))

    span = (await client.get_spans_async(query=_query())).spans[0]

    assert span.start_time == datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    assert span.end_time == datetime(2026, 1, 1, 0, 0, 0, 123457, tzinfo=UTC)


async def test_an_end_just_before_the_start_is_rejected_before_truncation_async() -> None:
    # Both times fall in the same microsecond, so only the nanoseconds show the end comes first.
    document = _document(_otlp_span(start=str(_START), end=str(_START - 1)))
    client, _ = _client(_serve({_TRACE: document, _OTHER_TRACE: _document(_otlp_span(trace_id=_OTHER_TRACE))}))

    result = await client.get_spans_async(query=_query(_TRACE, _OTHER_TRACE))

    assert [span.trace_id for span in result.spans] == [_OTHER_TRACE]
    assert "trace_response_invalid" in result.coverage.reasons


@pytest.mark.parametrize("timestamp", [str(2**64), "1" + "0" * 30])
async def test_a_timestamp_beyond_the_otlp_range_is_an_unreadable_trace_async(timestamp: str) -> None:
    document = _document(_otlp_span(start=timestamp, end="0"))
    client, _ = _client(_serve({_TRACE: document, _OTHER_TRACE: _document(_otlp_span(trace_id=_OTHER_TRACE))}))

    result = await client.get_spans_async(query=_query(_TRACE, _OTHER_TRACE))

    assert [span.trace_id for span in result.spans] == [_OTHER_TRACE]
    assert "trace_response_invalid" in result.coverage.reasons


async def test_an_unreadable_trace_does_not_log_span_content_async(caplog: pytest.LogCaptureFixture) -> None:
    # int() repeats a rejected value in its error message, and span fields can hold private data.
    document = _document(_otlp_span(start="private-tool-argument"))
    client, _ = _client(_serve({_TRACE: document, _OTHER_TRACE: _document(_otlp_span(trace_id=_OTHER_TRACE))}))

    with caplog.at_level("WARNING"):
        result = await client.get_spans_async(query=_query(_TRACE, _OTHER_TRACE))

    assert "trace_response_invalid" in result.coverage.reasons
    assert "could not be read (ValueError)" in caplog.text
    assert "private-tool-argument" not in caplog.text


async def test_remote_coverage_is_never_complete_async() -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span())}))

    result = await client.get_spans_async(query=_query())

    assert result.available
    assert not result.coverage.complete
    assert result.coverage.reasons == ("remote_completion_unknown",)


async def test_remote_positive_stands_and_absence_stays_undetermined_async() -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(tool="lookup"))}))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    scope = TraceScorable(trace_ids=(_TRACE,))

    positive = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
    absent = (await scorer.score_async(scorable=scope, expectation=_expectation("delete_records")))[0]

    assert positive.get_value() is True
    assert absent.is_undetermined
    assert absent.score_value is None


async def test_missing_traces_are_unavailable_not_negative_async() -> None:
    client, _ = _client(_serve({}))
    source = OtelTraceSource(trace_client=client)

    result = await client.get_spans_async(query=_query())
    observation = await source.acquire_async(scorable=TraceScorable(trace_ids=(_TRACE,)))
    score = (
        await OtelToolCallScorer(source=source).score_async(
            scorable=TraceScorable(trace_ids=(_TRACE,)), expectation=_expectation("lookup")
        )
    )[0]

    assert not result.available
    assert result.coverage.reasons == ("trace_not_found",)
    assert observation.acquisition is Acquisition.UNAVAILABLE
    assert score.is_undetermined


@pytest.mark.parametrize(
    "outcome",
    [
        500,
        503,
        401,
        403,
        429,
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        "not json",
        json.dumps({"data": []}),
        json.dumps(_document(_otlp_span(trace_id=_OTHER_TRACE))),
        json.dumps(_document(_otlp_span(span_id="xyz"))),
        json.dumps(_document(_otlp_span(start="0"))),
        json.dumps(_document(_otlp_span(start=str(_START), end=str(_START - 1_000)))),
        json.dumps(_document(_otlp_span(status={"code": 7}))),
        json.dumps(_document(_otlp_span(), _otlp_span(tool="other"))),
    ],
)
async def test_failed_or_unreadable_retrieval_is_an_acquisition_error_async(outcome: object) -> None:
    client, _ = _client(_serve({_TRACE: outcome}))
    source = OtelTraceSource(trace_client=client)

    with pytest.raises(TraceAcquisitionError):
        await client.get_spans_async(query=_query())
    observation = await source.acquire_async(scorable=TraceScorable(trace_ids=(_TRACE,)))
    score = (
        await OtelToolCallScorer(source=source).score_async(
            scorable=TraceScorable(trace_ids=(_TRACE,)), expectation=_expectation("missing")
        )
    )[0]

    assert observation.acquisition is Acquisition.ERROR
    assert score.is_undetermined


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [(404, "trace_not_found"), (500, "trace_query_failed"), ("{}", "trace_response_invalid")],
)
async def test_one_failed_trace_leaves_other_evidence_partial_async(outcome: object, reason: str) -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(tool="lookup")), _OTHER_TRACE: outcome}))
    scope = TraceScorable(trace_ids=(_TRACE, _OTHER_TRACE))
    source = OtelTraceSource(trace_client=client)
    scorer = OtelToolCallScorer(source=source)

    result = await client.get_spans_async(query=TraceQuery(scope=scope))
    observation = await source.acquire_async(scorable=scope)
    positive = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
    absent = (await scorer.score_async(scorable=scope, expectation=_expectation("missing")))[0]

    assert [span.trace_id for span in result.spans] == [_TRACE]
    assert result.coverage.reasons == (reason, "remote_completion_unknown")
    assert observation.acquisition is Acquisition.PARTIAL
    assert positive.get_value() is True
    assert absent.is_undetermined


async def test_a_trace_with_an_unreadable_span_contributes_no_spans_async() -> None:
    # A trace with one unreadable span contributes nothing, rather than its readable remainder.
    document = _document(_otlp_span(tool="lookup"), _otlp_span(tool="other", span_id="0" * 16))
    client, _ = _client(_serve({_TRACE: document, _OTHER_TRACE: _document(_otlp_span(trace_id=_OTHER_TRACE))}))

    result = await client.get_spans_async(query=_query(_TRACE, _OTHER_TRACE))

    assert [span.trace_id for span in result.spans] == [_OTHER_TRACE]
    assert "trace_response_invalid" in result.coverage.reasons


async def test_identical_repeated_spans_are_kept_once_async() -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span(), _otlp_span())}))

    result = await client.get_spans_async(query=_query())

    assert len(result.spans) == 1


async def test_query_limit_truncates_and_is_reported_async() -> None:
    spans = [_otlp_span(span_id=f"{index + 1:016x}", start=str(_START + index * 1_000)) for index in range(3)]
    client, _ = _client(_serve({_TRACE: _document(*spans)}))

    result = await client.get_spans_async(query=_query(limit=2))

    assert [span.span_id for span in result.spans] == [f"{index + 1:016x}" for index in range(2)]
    assert result.coverage.reasons == ("query_limit_exceeded", "remote_completion_unknown")


async def test_source_timeout_on_a_slow_backend_is_an_error_async() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_document(_otlp_span()))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    client = OtlpJsonTraceClient(url_template=_TEMPLATE, http_client=http_client)
    source = OtelTraceSource(trace_client=client, timeout_seconds=0.05)

    observation = await source.acquire_async(scorable=TraceScorable(trace_ids=(_TRACE,)))

    assert observation.acquisition is Acquisition.ERROR
    assert observation.payload.coverage.reasons == ("trace_query_timeout",)


async def test_headers_and_timeout_are_sent_with_each_request_async() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_document(_otlp_span(trace_id=request.url.path.rsplit("/", 1)[-1])))

    client, _ = _client(handler, headers={"Authorization": "Bearer secret"}, timeout_seconds=2.5)

    await client.get_spans_async(query=_query(_TRACE, _OTHER_TRACE))

    assert [request.headers["Authorization"] for request in seen] == ["Bearer secret"] * 2
    assert [request.extensions["timeout"]["read"] for request in seen] == [2.5, 2.5]


async def test_without_an_injected_client_each_query_opens_and_closes_one_async() -> None:
    opened: list[httpx.AsyncClient] = []

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs == {"use_async": True, "timeout": 10}
        opened.append(httpx.AsyncClient(transport=httpx.MockTransport(_serve({_TRACE: _document(_otlp_span())}))))
        return opened[-1]

    client = OtlpJsonTraceClient(url_template=_TEMPLATE)
    with patch("pyrit.score.observation.otlp_json_trace_client.get_httpx_client", side_effect=factory):
        result = await client.get_spans_async(query=_query())

    assert len(result.spans) == 1
    assert len(opened) == 1
    assert opened[0].is_closed


async def test_injected_client_is_left_open_for_its_owner_async() -> None:
    client, _ = _client(_serve({_TRACE: _document(_otlp_span())}))

    await client.get_spans_async(query=_query())

    assert not client._http_client.is_closed  # type: ignore[union-attr]


def test_identifier_omits_credentials_and_query() -> None:
    client = OtlpJsonTraceClient(
        url_template="https://traces.example/api/traces/{trace_id}?api_key=secret",
        headers={"Authorization": "Bearer secret"},
    )

    identifier = client.get_identifier()

    assert identifier.params == {
        "endpoint": "https://traces.example/api/traces/{trace_id}",
        "format": "otlp_json",
        "timeout_seconds": 10,
    }
    assert "secret" not in identifier.model_dump_json()
    other = OtlpJsonTraceClient(url_template="https://other.example/api/traces/{trace_id}").get_identifier()
    assert other.hash != identifier.hash


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"url_template": "https://traces.example/api/traces"}, "placeholder"),
        ({"url_template": "https://traces.example/{trace_id}/{trace_id}"}, "placeholder"),
        ({"url_template": "file:///traces/{trace_id}"}, "placeholder"),
        ({"url_template": "https://user:pass@traces.example/{trace_id}"}, "credentials"),
        ({"url_template": _TEMPLATE, "timeout_seconds": 0}, "positive"),
        ({"url_template": _TEMPLATE, "timeout_seconds": float("nan")}, "positive"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        OtlpJsonTraceClient(**kwargs)
