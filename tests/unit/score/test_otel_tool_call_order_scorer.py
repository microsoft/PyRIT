# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.memory import SQLiteMemory
from pyrit.models import (
    Acquisition,
    Message,
    MessagePiece,
    MessageScorable,
    RequestTraceContext,
    ScoringExpectation,
    ToolCallRequirement,
    ToolEventsObservationPayload,
    ToolExecution,
    ToolsCalled,
    ToolsCalledInOrder,
    TraceCoverage,
    TraceScorable,
    TraceSpan,
)
from pyrit.score import (
    InMemoryTraceClient,
    OtelToolCallOrderScorer,
    OtelToolCallScorer,
    OtelTraceSource,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.otel_tool_call_scorer import match_tools_called_in_order

pytestmark = pytest.mark.usefixtures("patch_central_database")

_TRACE = "1" * 32
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return _EPOCH + timedelta(seconds=seconds)


def _execution(name: str, start: float, end: float | None, index: int) -> ToolExecution:
    return ToolExecution(
        name=name,
        trace_id=_TRACE,
        span_id=f"{index + 1:016x}",
        start_time=_at(start),
        end_time=None if end is None else _at(end),
    )


def _span(name: str, start: float, end: float | None, index: int) -> TraceSpan:
    return TraceSpan(
        trace_id=_TRACE,
        span_id=f"{index + 1:016x}",
        start_time=_at(start),
        end_time=None if end is None else _at(end),
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name},
    )


def _payload(*runs: tuple[str, float, float | None], complete: bool) -> ToolEventsObservationPayload:
    return ToolEventsObservationPayload(
        scope=TraceScorable(trace_ids=(_TRACE,)),
        events=tuple(_execution(name, start, end, index) for index, (name, start, end) in enumerate(runs)),
        coverage=TraceCoverage(complete=complete, reasons=() if complete else ("capture_not_complete",)),
    )


def _order(*names: str) -> ToolsCalledInOrder:
    return ToolsCalledInOrder(tools=tuple(ToolCallRequirement(name=name) for name in names))


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(_order(*names),))


def _match(*runs: tuple[str, float, float | None], complete: bool, tolerance: float = 0) -> bool | None:
    return match_tools_called_in_order(
        condition=_order("read", "send"),
        payload=_payload(*runs, complete=complete),
        acquisition=Acquisition.COMPLETE if complete else Acquisition.PARTIAL,
        clock_skew_tolerance=timedelta(seconds=tolerance),
    )


@pytest.mark.parametrize(
    ("runs", "complete", "expected"),
    [
        # Sequential in the required order: conclusive even with partial evidence.
        ((("read", 0, 1), ("send", 2, 3)), True, True),
        ((("read", 0, 1), ("send", 2, 3)), False, True),
        # Sequential the other way round: false only with complete evidence.
        ((("send", 0, 1), ("read", 2, 3)), True, False),
        ((("send", 0, 1), ("read", 2, 3)), False, None),
        # Concurrent attempts establish no order in either direction.
        ((("read", 0, 2), ("send", 1, 3)), True, None),
        ((("send", 0, 2), ("read", 1, 3)), True, None),
        # Equal timestamps are not an order.
        ((("read", 1, 1), ("send", 1, 1)), True, None),
        ((("read", 0, 1), ("send", 1, 2)), True, None),
        # A missing tool is false only with complete evidence.
        ((("read", 0, 1),), True, False),
        ((("read", 0, 1),), False, None),
        ((), True, False),
        # An unfinished attempt cannot precede anything.
        ((("read", 0, None), ("send", 2, 3)), False, None),
        ((("read", 0, None), ("read", 1, 2), ("send", 3, 4)), False, True),
        # Other tools and repeated attempts may run in between.
        ((("send", 0, 1), ("read", 2, 3), ("lookup", 4, 5), ("send", 6, 7)), True, True),
        ((("send", 0, 1), ("send", 2, 3), ("read", 4, 5)), True, False),
    ],
)
def test_order_is_matched_from_trusted_intervals_only(
    runs: tuple[tuple[str, float, float | None], ...], complete: bool, expected: bool | None
) -> None:
    assert _match(*runs, complete=complete) is expected


def test_chain_uses_the_earliest_finishing_attempt() -> None:
    payload = _payload(("read", 0, 10), ("read", 1, 2), ("send", 3, 4), ("summarize", 11, 12), complete=True)
    condition = _order("read", "send", "summarize")

    assert match_tools_called_in_order(condition=condition, payload=payload, acquisition=Acquisition.COMPLETE) is True


def test_possible_order_uses_the_earliest_starting_attempt() -> None:
    # send(4-5) can only follow read(3-6) by overlap, so the order is unknown, not false.
    payload = _payload(("read", 7, 8), ("read", 3, 6), ("send", 4, 5), complete=True)

    assert (
        match_tools_called_in_order(condition=_order("read", "send"), payload=payload, acquisition=Acquisition.COMPLETE)
        is None
    )


@pytest.mark.parametrize(
    ("runs", "tolerance", "expected"),
    [
        # lookup overlaps both neighbours, but send ended before read started, so read, lookup,
        # send cannot have happened in that order.
        ((("read", 5, 6), ("lookup", 0, 10), ("send", 1, 2)), 0, False),
        # Within the clock tolerance the same gap is no longer a contradiction.
        ((("read", 5, 6), ("lookup", 0, 10), ("send", 1, 2)), 3, None),
        # All three overlap one another, so the order stays unknown.
        ((("read", 0, 3), ("lookup", 1, 10), ("send", 2, 4)), 0, None),
        # A later attempt of send that does not end before read starts keeps the order possible.
        ((("read", 5, 6), ("lookup", 0, 10), ("send", 1, 2), ("send", 4, 7)), 0, None),
    ],
)
def test_possible_order_checks_every_earlier_tool_not_only_the_neighbour(
    runs: tuple[tuple[str, float, float | None], ...], tolerance: float, expected: bool | None
) -> None:
    payload = _payload(*runs, complete=True)

    assert (
        match_tools_called_in_order(
            condition=_order("read", "lookup", "send"),
            payload=payload,
            acquisition=Acquisition.COMPLETE,
            clock_skew_tolerance=timedelta(seconds=tolerance),
        )
        is expected
    )


@pytest.mark.parametrize(
    ("runs", "tolerance", "expected"),
    [
        ((("read", 0, 1), ("send", 1.5, 2)), 0, True),
        ((("read", 0, 1), ("send", 1.5, 2)), 1, None),
        ((("read", 0, 1), ("send", 2.5, 3)), 1, True),
        ((("send", 0, 1), ("read", 1.5, 2)), 0, False),
        ((("send", 0, 1), ("read", 1.5, 2)), 1, None),
    ],
)
def test_clock_skew_tolerance_widens_the_unordered_band(
    runs: tuple[tuple[str, float, float | None], ...], tolerance: float, expected: bool | None
) -> None:
    assert _match(*runs, complete=True, tolerance=tolerance) is expected


@pytest.mark.parametrize("acquisition", [Acquisition.ERROR, Acquisition.UNAVAILABLE])
def test_failed_or_unavailable_acquisition_is_never_a_verdict(acquisition: Acquisition) -> None:
    payload = _payload(("read", 0, 1), ("send", 2, 3), complete=False)

    assert (
        match_tools_called_in_order(condition=_order("read", "send"), payload=payload, acquisition=acquisition) is None
    )


def test_scorer_identifier_records_tolerance_and_source() -> None:
    source = OtelTraceSource(trace_client=InMemoryTraceClient())
    identifier = OtelToolCallOrderScorer(source=source).get_identifier()
    skewed = OtelToolCallOrderScorer(source=source, clock_skew_tolerance=timedelta(milliseconds=5)).get_identifier()

    assert identifier.children["source"] == source.get_identifier()
    assert identifier.params["clock_skew_tolerance_seconds"] == 0
    assert skewed.params["clock_skew_tolerance_seconds"] == 0.005
    assert len({identifier.hash, skewed.hash, OtelToolCallScorer(source=source).get_identifier().hash}) == 3


@pytest.mark.parametrize("tolerance", [timedelta(seconds=-1), timedelta(microseconds=-1)])
def test_negative_tolerance_is_rejected(tolerance: timedelta) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        OtelToolCallOrderScorer(
            source=OtelTraceSource(trace_client=InMemoryTraceClient()), clock_skew_tolerance=tolerance
        )


def test_scorer_declares_only_the_order_condition() -> None:
    scorer = OtelToolCallOrderScorer(source=OtelTraceSource(trace_client=InMemoryTraceClient()))

    assert scorer.get_condition_types() == frozenset({ToolsCalledInOrder})
    assert scorer.get_supported_scorable_types() == frozenset({MessageScorable, TraceScorable})


async def test_order_verdict_and_offline_replay_async(sqlite_instance: SQLiteMemory) -> None:
    client = InMemoryTraceClient()
    client.add_span(_span("read", 0, 1, 0))
    client.add_span(_span("send", 2, 3, 1))
    scope = TraceScorable(trace_ids=(_TRACE,))
    scorer = OtelToolCallOrderScorer(source=OtelTraceSource(trace_client=client))

    pending = (await scorer.score_async(scorable=scope, expectation=_expectation("send", "read")))[0]
    assert pending.is_undetermined
    client.mark_complete(trace_ids=scope.trace_ids)
    positive = (await scorer.score_async(scorable=scope, expectation=_expectation("read", "send")))[0]
    negative = (await scorer.score_async(scorable=scope, expectation=_expectation("send", "read")))[0]
    assert positive.get_value() is True
    assert negative.get_value() is False
    assert "read -> send" in positive.score_rationale

    observation = (await sqlite_instance.get_observations_async(observation_ids=negative.observation_ids))[0]
    assert observation.acquisition is Acquisition.COMPLETE
    client.close()
    with patch.object(client, "get_spans_async", new_callable=AsyncMock) as query:
        query.side_effect = AssertionError("Replay must not query a trace client.")
        replay = (
            await scorer.score_observation_async(observation=observation, expectation=_expectation("read", "send"))
        )[0]
        query.assert_not_called()
    assert replay.get_value() is True
    assert replay.observation_ids == negative.observation_ids


async def test_concurrent_attempts_stay_undetermined_with_complete_capture_async() -> None:
    client = InMemoryTraceClient()
    client.add_span(_span("read", 0, 2, 0))
    client.add_span(_span("send", 1, 3, 1))
    client.mark_complete(trace_ids=(_TRACE,))
    scorer = OtelToolCallOrderScorer(source=OtelTraceSource(trace_client=client))

    score = (
        await scorer.score_async(scorable=TraceScorable(trace_ids=(_TRACE,)), expectation=_expectation("read", "send"))
    )[0]

    assert score.is_undetermined
    assert "cannot establish" in score.score_rationale


async def test_unlinked_request_prevents_a_false_order_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    unlinked = MessagePiece(role="user", original_value="content", conversation_id=conversation)
    linked = MessagePiece(role="user", original_value="content", conversation_id=conversation)
    linked.prompt_metadata.update(RequestTraceContext(traceparent=f"00-{_TRACE}-{'2' * 16}-01").to_metadata())
    for piece in (
        unlinked,
        MessagePiece(role="assistant", original_value="content", conversation_id=conversation),
        linked,
    ):
        (await sqlite_instance.add_message_to_memory_async(request=piece.to_message()))
    response: Message = MessagePiece(
        role="assistant", original_value="content", conversation_id=conversation
    ).to_message()
    (await sqlite_instance.add_message_to_memory_async(request=response))
    client = InMemoryTraceClient()
    client.add_span(_span("send", 0, 1, 0))
    client.add_span(_span("read", 2, 3, 1))
    client.mark_complete(trace_ids=(_TRACE,))
    scorer = OtelToolCallOrderScorer(source=OtelTraceSource(trace_client=client))

    score = (
        await scorer.score_async(
            scorable=MessageScorable.from_message(response), expectation=_expectation("read", "send")
        )
    )[0]

    assert score.is_undetermined
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
    assert observation.acquisition is Acquisition.COMPLETE


async def test_composite_routes_presence_and_order_to_separate_leaves_async() -> None:
    client = InMemoryTraceClient()
    client.add_span(_span("send", 0, 1, 0))
    client.add_span(_span("read", 2, 3, 1))
    client.mark_complete(trace_ids=(_TRACE,))
    source = OtelTraceSource(trace_client=client)
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[OtelToolCallScorer(source=source), OtelToolCallOrderScorer(source=source)],
    )
    presence = ToolsCalled(tools=(ToolCallRequirement(name="read"), ToolCallRequirement(name="send")))
    expectation = ScoringExpectation(conditions=(presence, _order("read", "send")))

    score = (await composite.score_async(scorable=TraceScorable(trace_ids=(_TRACE,)), expectation=expectation))[0]

    assert composite.get_condition_types() == frozenset({ToolsCalled, ToolsCalledInOrder})
    assert score.get_value() is False


async def test_presence_condition_alone_is_rejected_async() -> None:
    scorer = OtelToolCallOrderScorer(source=OtelTraceSource(trace_client=InMemoryTraceClient()))
    presence = ScoringExpectation(conditions=(ToolsCalled(tools=(ToolCallRequirement(name="read"),)),))

    with pytest.raises(ValueError):
        await scorer.score_async(scorable=TraceScorable(trace_ids=(_TRACE,)), expectation=presence)


async def test_scorer_applies_its_clock_skew_tolerance_async() -> None:
    client = InMemoryTraceClient()
    client.add_span(_span("send", 0, 1, 0))
    client.add_span(_span("read", 1.5, 2, 1))
    client.mark_complete(trace_ids=(_TRACE,))
    source = OtelTraceSource(trace_client=client)
    scope = TraceScorable(trace_ids=(_TRACE,))
    strict = OtelToolCallOrderScorer(source=source)
    tolerant = OtelToolCallOrderScorer(source=source, clock_skew_tolerance=timedelta(seconds=1))

    assert (await strict.score_async(scorable=scope, expectation=_expectation("read", "send")))[0].get_value() is False
    assert (await tolerant.score_async(scorable=scope, expectation=_expectation("read", "send")))[0].is_undetermined
