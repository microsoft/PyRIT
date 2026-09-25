# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import SQLiteMemory
from pyrit.memory.memory_models import ScoreEntry
from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    ContentScorable,
    MessageScorable,
    Score,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
    TraceCoverage,
    TraceQuery,
    TraceQueryResult,
    TraceScorable,
    TraceSpan,
    TraceSpanStatus,
    UndeterminedScoreError,
)
from pyrit.score import (
    InMemoryTraceClient,
    InMemoryTraceExporter,
    NonReplayableObservationError,
    OtelToolCallScorer,
    OtelTraceSource,
    TraceAcquisitionError,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _span(
    *,
    name: str = "lookup",
    trace_id: str = "1" * 32,
    span_id: str = "2" * 16,
    convention: str = "genai",
    status: TraceSpanStatus = TraceSpanStatus.UNSET,
) -> TraceSpan:
    now = datetime.now(tz=UTC)
    attributes = (
        {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name}
        if convention == "genai"
        else {"openinference.span.kind": "TOOL", "tool.name": name}
    )
    return TraceSpan(
        trace_id=trace_id, span_id=span_id, start_time=now, end_time=now, attributes=attributes, status=status
    )


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


def test_scorer_identifier_retains_source_child() -> None:
    source = OtelTraceSource(trace_client=InMemoryTraceClient(source_id="first"))
    identifier = OtelToolCallScorer(source=source).get_identifier()
    other_source = OtelTraceSource(trace_client=InMemoryTraceClient(source_id="second"))

    assert identifier.children["source"] == source.get_identifier()
    assert "source_hash" not in identifier.params
    assert identifier.params["matching_version"] == 1
    assert identifier.hash != OtelToolCallScorer(source=other_source).get_identifier().hash
    assert ComponentIdentifier.model_validate_json(identifier.model_dump_json()).children == identifier.children


async def test_replay_preserves_source_identity_from_before_package_move_async(sqlite_instance: SQLiteMemory) -> None:
    client = InMemoryTraceClient()
    client.add_span(_span())
    scope = TraceScorable(trace_ids=("1" * 32,))
    source = OtelTraceSource(trace_client=client)
    old_identifier = ComponentIdentifier(
        class_name="OtelTraceSource",
        class_module="pyrit.score.otel_trace_source",
    )
    with patch.object(source, "get_identifier", return_value=old_identifier):
        scorer = OtelToolCallScorer(source=source)
        score = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert observation.source_identifier == old_identifier
    assert source.get_identifier().class_module == "pyrit.score.observation.otel_trace_source"
    client.close()

    replay_scorer = OtelToolCallScorer(source=source)
    replay = (await replay_scorer.score_observation_async(observation=observation, expectation=_expectation("lookup")))[
        0
    ]

    assert replay.get_value() is True
    assert replay.observation_ids == score.observation_ids
    assert sqlite_instance.get_observations(observation_ids=score.observation_ids)[0] == observation


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("present", [False, True])
async def test_tool_verdict_and_offline_replay_async(sqlite_instance, complete: bool, present: bool) -> None:
    client = InMemoryTraceClient()
    scope = TraceScorable(trace_ids=("1" * 32,))
    if present:
        client.add_span(_span())
    if complete:
        client.mark_complete(trace_ids=scope.trace_ids)
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))

    score = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
    if present or complete:
        assert score.get_value() is present
    else:
        with pytest.raises(UndeterminedScoreError):
            score.get_value()
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert observation.payload.scope == scope
    assert observation.acquisition is (Acquisition.COMPLETE if complete else Acquisition.PARTIAL)
    client.close()
    with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
        query.side_effect = AssertionError("Replay must not query a trace client.")
        replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation("summarize")))[
            0
        ]
        query.assert_not_called()
    assert replay.observation_ids == score.observation_ids
    if complete:
        assert replay.get_value() is False
    else:
        with pytest.raises(UndeterminedScoreError):
            replay.get_value()


@pytest.mark.parametrize("convention", ["genai", "openinference"])
async def test_failed_execution_counts_but_sensitive_attributes_are_not_retained_async(
    sqlite_instance, convention: str
) -> None:
    client = InMemoryTraceClient()
    span = _span(convention=convention, status=TraceSpanStatus.ERROR)
    span.attributes["input.value"] = "private-argument"
    span.attributes["output.value"] = "private-result"
    client.add_span(span)
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    score = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=(span.trace_id,)), expectation=_expectation("lookup"))
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert score.get_value() is True
    assert observation.payload.events[0].status is TraceSpanStatus.ERROR
    assert "private-" not in observation.model_dump_json()


@pytest.mark.parametrize("convention", ["genai", "openinference"])
@pytest.mark.parametrize("call_id", ["", None, 123, "call-1"])
async def test_optional_call_id_does_not_block_name_matching_async(
    sqlite_instance: SQLiteMemory, convention: str, call_id: str | int | None
) -> None:
    client = InMemoryTraceClient()
    span = _span(convention=convention)
    span.attributes["gen_ai.tool.call.id"] = call_id
    client.add_span(span)
    client.mark_complete(trace_ids=(span.trace_id,))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))

    score = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=(span.trace_id,)), expectation=_expectation("lookup"))
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert score.get_value() is True
    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.payload.events[0].call_id == (call_id if isinstance(call_id, str) and call_id else None)
    client.close()
    replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation("lookup")))[0]
    assert replay.get_value() is True


async def test_name_matching_is_case_sensitive_and_requires_all_tools_async() -> None:
    client = InMemoryTraceClient()
    client.add_span(_span(name="Lookup"))
    client.mark_complete(trace_ids=("1" * 32,))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    scope = TraceScorable(trace_ids=("1" * 32,))
    assert (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0].get_value() is False
    assert (await scorer.score_async(scorable=scope, expectation=_expectation("Lookup", "other")))[
        0
    ].get_value() is False


@pytest.mark.parametrize(
    "scorable",
    [ContentScorable(value="I called lookup")],
)
async def test_scorer_requires_explicit_trace_without_acquisition_async(
    scorable: ContentScorable | MessageScorable,
) -> None:
    source = OtelTraceSource(trace_client=InMemoryTraceClient())
    scorer = OtelToolCallScorer(source=source)
    with patch.object(source, "acquire_async", new_callable=AsyncMock) as acquire:
        with pytest.raises(RuntimeError, match="explicit TraceScorable") as error:
            await scorer.score_async(scorable=scorable, expectation=_expectation("lookup"))
        assert isinstance(error.value.__cause__, TypeError)
        acquire.assert_not_called()


async def test_acquisition_error_is_not_false_async(sqlite_instance) -> None:
    client = InMemoryTraceClient()
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    client.close()
    failed = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=("1" * 32,)), expectation=_expectation("lookup"))
    )[0]
    with pytest.raises(UndeterminedScoreError):
        failed.get_value()
    observation = sqlite_instance.get_observations(observation_ids=failed.observation_ids)[0]
    assert observation.acquisition is Acquisition.ERROR


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("reasons", [(), ("retention_expired",)])
async def test_empty_trace_result_preserves_availability_in_storage_and_replay_async(
    sqlite_instance: SQLiteMemory, available: bool, reasons: tuple[str, ...]
) -> None:
    client = InMemoryTraceClient()
    scope = TraceScorable(trace_ids=("1" * 32,))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
        query.return_value = TraceQueryResult(available=available, coverage=TraceCoverage(reasons=reasons))
        score = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
        query.assert_called_once()
        assert query.call_args.kwargs["query"].scope == scope
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        assert observation.acquisition is (Acquisition.PARTIAL if available else Acquisition.UNAVAILABLE)
        assert observation.scorable == scope
        assert observation.payload.events == ()
        expected_reasons = (*reasons, "source_coverage_incomplete") if available else reasons or ("trace_unavailable",)
        assert observation.payload.coverage.reasons == expected_reasons
        with pytest.raises(UndeterminedScoreError):
            score.get_value()

        query.side_effect = AssertionError("Replay must not query a trace client.")
        replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation("different")))[
            0
        ]
        query.assert_called_once()
    assert replay.observation_ids == score.observation_ids
    with pytest.raises(UndeterminedScoreError):
        replay.get_value()


async def test_source_rejects_conflicts_and_unrelated_spans_async() -> None:
    client = InMemoryTraceClient()
    source = OtelTraceSource(trace_client=client)
    scope = TraceScorable(trace_ids=("1" * 32,))
    for spans in ((_span(trace_id="3" * 32),), (_span(), _span(name="other"))):
        with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
            query.return_value = TraceQueryResult(spans=spans)
            with pytest.raises(ValueError):
                await source.acquire_async(scorable=scope)


async def test_model_requested_calls_are_not_execution_evidence_async() -> None:
    client = InMemoryTraceClient()
    span = _span()
    span.attributes["gen_ai.operation.name"] = "chat"
    client.add_span(span)
    client.mark_complete(trace_ids=(span.trace_id,))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    score = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=(span.trace_id,)), expectation=_expectation("lookup"))
    )[0]
    assert score.get_value() is False


async def test_real_sdk_export_and_existing_global_provider_are_preserved_async(sqlite_instance) -> None:
    previous_provider = trace.get_tracer_provider()
    client = InMemoryTraceClient()
    with patch.dict(
        "os.environ", {"OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "3", "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "3"}
    ):
        provider = TracerProvider(sampler=ALWAYS_ON, span_limits=SpanLimits(max_span_attribute_length=SpanLimits.UNSET))
    exporter = InMemoryTraceExporter(trace_client=client)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        tracer = provider.get_tracer(__name__)
        with tracer.start_as_current_span("agent") as root:
            with tracer.start_as_current_span(
                "tool", attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup"}
            ) as tool:
                tool_id = f"{tool.get_span_context().span_id:016x}"
            trace_id = f"{root.get_span_context().trace_id:032x}"
            parent_id = f"{root.get_span_context().span_id:016x}"
        assert await asyncio.to_thread(provider.force_flush)
        client.mark_complete(trace_ids=(trace_id,))
        scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
        score = (
            await scorer.score_async(scorable=TraceScorable(trace_ids=(trace_id,)), expectation=_expectation("lookup"))
        )[0]
        event = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0].payload.events[0]
        assert score.get_value() is True
        assert (event.trace_id, event.span_id, event.parent_span_id) == (trace_id, tool_id, parent_id)
        assert event.end_time is not None
    finally:
        await asyncio.to_thread(provider.shutdown)
    assert trace.get_tracer_provider() is previous_provider
    assert not exporter.force_flush()


async def test_limits_duplicates_and_late_spans_keep_coverage_honest_async() -> None:
    client = InMemoryTraceClient(max_spans=2)
    first = _span()
    client.add_span(first)
    client.add_span(first)
    client.mark_complete(trace_ids=(first.trace_id,))
    client.add_span(_span(name="summarize", span_id="3" * 16))
    query = TraceQuery(scope=TraceScorable(trace_ids=(first.trace_id,)), limit=1)
    result = await client.get_spans_async(query=query)
    assert len(result.spans) == 1
    assert not result.coverage.complete
    assert "query_limit_exceeded" in result.coverage.reasons
    with pytest.raises(ValueError, match="incomplete"):
        client.mark_complete(trace_ids=(first.trace_id,))
    with pytest.raises(TraceAcquisitionError, match="capacity"):
        client.add_span(_span(span_id="4" * 16))
    assert "capture_capacity_exceeded" in (await client.get_spans_async(query=query)).coverage.reasons


async def test_sdk_dropped_execution_marker_prevents_false_negative_async(sqlite_instance: SQLiteMemory) -> None:
    client = InMemoryTraceClient()
    provider = TracerProvider(
        sampler=ALWAYS_ON,
        span_limits=SpanLimits(max_span_attributes=1, max_span_attribute_length=SpanLimits.UNSET),
    )
    provider.add_span_processor(SimpleSpanProcessor(InMemoryTraceExporter(trace_client=client)))
    try:
        with provider.get_tracer(__name__).start_as_current_span("tool") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", "lookup")
            scope = TraceScorable(trace_ids=(f"{span.get_span_context().trace_id:032x}",))
        assert await asyncio.to_thread(provider.force_flush)
        result = await client.get_spans_async(query=TraceQuery(scope=scope))
        assert len(result.spans) == 1
        assert result.spans[0].attributes == {"gen_ai.tool.name": "lookup"}
        assert not result.coverage.complete
        assert "capture_failed" in result.coverage.reasons
        with pytest.raises(ValueError, match="incomplete or lossy"):
            client.mark_complete(trace_ids=scope.trace_ids)

        scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
        score = (await scorer.score_async(scorable=scope, expectation=_expectation("lookup")))[0]
        assert score.is_undetermined
        observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
        assert observation.acquisition is Acquisition.PARTIAL
        client.close()
        replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation("lookup")))[0]
        assert replay.is_undetermined
    finally:
        await asyncio.to_thread(provider.shutdown)


@pytest.mark.parametrize(
    ("limit_kwargs", "limit_env"),
    [
        ({"max_span_attribute_length": 12}, {}),
        ({"max_span_attribute_length": 3}, {}),
        ({"max_attribute_length": 12}, {}),
        ({}, {"OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT": "12"}),
        ({}, {"OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT": "12"}),
    ],
)
@pytest.mark.parametrize("convention", ["genai", "openinference"])
async def test_sdk_truncation_cannot_prove_tool_calls_async(
    *, sqlite_instance: SQLiteMemory, limit_kwargs: dict[str, int], limit_env: dict[str, str], convention: str
) -> None:
    client = InMemoryTraceClient()
    exporter = InMemoryTraceExporter(trace_client=client)
    capture = InMemorySpanExporter()
    with patch.dict("os.environ", limit_env, clear=True):
        limits = SpanLimits(**limit_kwargs)
    provider = TracerProvider(sampler=ALWAYS_ON, span_limits=limits)
    provider.add_span_processor(SimpleSpanProcessor(capture))
    try:
        with provider.get_tracer(__name__).start_as_current_span(
            "tool", attributes=_span(name="lookup_customer", convention=convention).attributes
        ) as span:
            scope = TraceScorable(trace_ids=(f"{span.get_span_context().trace_id:032x}",))
        captured = capture.get_finished_spans()
        assert captured[0].dropped_attributes == 0
        name_key = "gen_ai.tool.name" if convention == "genai" else "tool.name"
        shortened_name = "lookup_customer"[: limits.max_span_attribute_length]
        assert captured[0].attributes[name_key] == shortened_name
        assert shortened_name != "lookup_customer"
        assert exporter.export(captured) is SpanExportResult.FAILURE
        result = await client.get_spans_async(query=TraceQuery(scope=scope))
        assert result.spans == ()
        assert "capture_failed" in result.coverage.reasons
        with pytest.raises(ValueError, match="incomplete or lossy"):
            client.mark_complete(trace_ids=scope.trace_ids)

        scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
        observations = []
        for name in ("lookup_customer", shortened_name):
            score = (await scorer.score_async(scorable=scope, expectation=_expectation(name)))[0]
            assert score.is_undetermined
            observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
            assert observation.payload.events == ()
            observations.append((name, observation))
        client.close()
        for name, observation in observations:
            replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation(name)))[0]
            assert replay.is_undetermined
    finally:
        await asyncio.to_thread(provider.shutdown)


async def test_sdk_unknown_attribute_limits_fail_closed_async() -> None:
    client = InMemoryTraceClient()
    exporter = InMemoryTraceExporter(trace_client=client)
    span = ReadableSpan(name="tool", attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup"})

    assert exporter.export((span,)) is SpanExportResult.FAILURE
    scope = TraceScorable(trace_ids=("1" * 32,))
    result = await client.get_spans_async(query=TraceQuery(scope=scope))
    assert result.spans == ()
    assert "capture_failed" in result.coverage.reasons
    with pytest.raises(ValueError, match="incomplete or lossy"):
        client.mark_complete(trace_ids=scope.trace_ids)


async def test_cancellation_and_programming_errors_propagate_async() -> None:
    client = InMemoryTraceClient()
    source = OtelTraceSource(trace_client=client)
    scope = TraceScorable(trace_ids=("1" * 32,))
    for error in (asyncio.CancelledError(), TypeError("invalid adapter")):
        with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
            query.side_effect = error
            with pytest.raises(type(error)):
                await source.acquire_async(scorable=scope)


async def test_normalization_gaps_prevent_complete_negative_async() -> None:
    client = InMemoryTraceClient()
    source = OtelTraceSource(trace_client=client)
    scope = TraceScorable(trace_ids=("1" * 32,))
    span = _span()
    span.attributes.pop("gen_ai.tool.name")
    with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
        query.return_value = TraceQueryResult(spans=(span,), coverage=TraceCoverage(complete=True))
        observation = await source.acquire_async(scorable=scope)
    assert observation.acquisition is Acquisition.PARTIAL
    assert "tool_name_missing" in observation.payload.coverage.reasons


async def test_client_filters_traces_and_detaches_snapshots_async() -> None:
    client = InMemoryTraceClient()
    original = _span()
    client.add_span(original)
    client.add_span(_span(trace_id="3" * 32, name="unrelated"))
    original.attributes["gen_ai.tool.name"] = "changed"
    query = TraceQuery(scope=TraceScorable(trace_ids=("1" * 32,)))
    snapshot = await client.get_spans_async(query=query)
    assert len(snapshot.spans) == 1
    assert snapshot.spans[0].attributes["gen_ai.tool.name"] == "lookup"
    snapshot.spans[0].attributes["gen_ai.tool.name"] = "changed-again"
    assert (await client.get_spans_async(query=query)).spans[0].attributes["gen_ai.tool.name"] == "lookup"


async def test_source_deduplicates_exact_span_identities_async() -> None:
    client = InMemoryTraceClient()
    source = OtelTraceSource(trace_client=client)
    scope = TraceScorable(trace_ids=("1" * 32,))
    span = _span()
    with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
        query.return_value = TraceQueryResult(spans=(span, span), coverage=TraceCoverage(complete=True))
        observation = await source.acquire_async(scorable=scope)
    assert len(observation.payload.events) == 1
    assert observation.acquisition is Acquisition.COMPLETE


async def test_tool_replay_rejects_modified_snapshot_async(sqlite_instance) -> None:
    client = InMemoryTraceClient()
    client.add_span(_span())
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    score = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=("1" * 32,)), expectation=_expectation("lookup"))
    )[0]
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    modified = observation.model_copy(update={"payload": observation.payload.model_copy(update={"events": ()})})
    with pytest.raises(NonReplayableObservationError, match="canonical"):
        await scorer.score_observation_async(observation=modified, expectation=_expectation("lookup"))


async def test_tool_observation_and_score_roll_back_together_async(sqlite_instance: SQLiteMemory) -> None:
    client = InMemoryTraceClient()
    client.add_span(_span())
    scope = TraceScorable(trace_ids=("1" * 32,))
    observation = await OtelTraceSource(trace_client=client).acquire_async(scorable=scope)
    score = Score(scorable=scope, score_type="true_false", score_value="true", observation_ids=[observation.id])
    with sqlite_instance.get_session() as session:
        with (
            patch.object(sqlite_instance, "get_session", return_value=session),
            patch.object(session, "commit", side_effect=SQLAlchemyError("commit failed")),
            pytest.raises(SQLAlchemyError, match="commit failed"),
        ):
            sqlite_instance.add_scores_to_memory(scores=[score], observations=[observation])

    assert sqlite_instance._query_entries(ScoreEntry) == []
    assert sqlite_instance.get_observations(observation_ids=[observation.id]) == []
