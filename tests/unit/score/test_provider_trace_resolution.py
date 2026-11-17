# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from pyrit.memory import SQLiteMemory
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    MessageScorable,
    ProviderTraceReference,
    RequestTraceContext,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
    TraceScorable,
    TraceSpan,
)
from pyrit.score import (
    InMemoryTraceClient,
    MappingProviderTraceResolver,
    OtelExecutionTraceSource,
    OtelToolCallOrderScorer,
    OtelToolCallScorer,
    OtelTraceSource,
    SpanAttributeTraceResolver,
    SubStringScorer,
    TraceAcquisitionError,
    create_conversation_scorer,
)
from pyrit.score.evidence.conversation_trace_evidence import ConversationTraceEvidence
from pyrit.score.observation.message_trace_resolver import (
    resolve_conversation_trace_links_async,
    resolve_message_trace_scope_async,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")

_TRACE_A = "a" * 32
_TRACE_B = "b" * 32
_TRACE_W3C = "c" * 32


def _span(*, trace_id: str, span_id: str, attributes: dict) -> TraceSpan:
    now = datetime.now(tz=UTC)
    return TraceSpan(
        trace_id=trace_id, span_id=span_id, start_time=now, end_time=now, name="span", attributes=attributes
    )


def _model_span(*, trace_id: str, span_id: str = "1" * 16, response_id: str = "chatcmpl-1") -> TraceSpan:
    return _span(
        trace_id=trace_id,
        span_id=span_id,
        attributes={"gen_ai.operation.name": "chat", "gen_ai.response.id": response_id},
    )


def _tool_span(*, trace_id: str, tool: str, span_id: str = "2" * 16) -> TraceSpan:
    return _span(
        trace_id=trace_id,
        span_id=span_id,
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool},
    )


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


async def _store_async(
    *,
    memory: SQLiteMemory,
    conversation: str,
    role: str = "user",
    reference: ProviderTraceReference | None = None,
    w3c_trace_id: str | None = None,
) -> Message:
    piece = MessagePiece(role=role, original_value="content", conversation_id=conversation)
    if reference is not None:
        piece.prompt_metadata.update(reference.to_metadata())
    if w3c_trace_id is not None:
        piece.prompt_metadata.update(RequestTraceContext(traceparent=f"00-{w3c_trace_id}-{'2' * 16}-01").to_metadata())
    message = piece.to_message()
    await memory.add_message_to_memory_async(request=message)
    return message


class _FixedResolver:
    def __init__(self, answers: Sequence[str | None]) -> None:
        self.answers = tuple(answers)
        self.calls: list[tuple[ProviderTraceReference, ...]] = []

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier.of(self, params={"answers": list(self.answers)})

    async def resolve_trace_ids_async(self, *, references: Sequence[ProviderTraceReference]) -> tuple[str | None, ...]:
        self.calls.append(tuple(references))
        return self.answers


# ---------------------------------------------------------------------------
# ProviderTraceReference
# ---------------------------------------------------------------------------


def test_reference_round_trips_through_metadata() -> None:
    reference = ProviderTraceReference(provider="openai", request_id="chatcmpl-1", run_id="call-1")
    metadata = reference.to_metadata()
    assert json.loads(metadata[ProviderTraceReference.METADATA_KEY]) == {
        "provider": "openai",
        "request_id": "chatcmpl-1",
        "run_id": "call-1",
    }
    assert ProviderTraceReference.from_metadata(metadata) == reference
    assert ProviderTraceReference.from_metadata({}) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"provider": "openai"},
        {"provider": "", "request_id": "x"},
        {"provider": "openai", "request_id": ""},
        {"provider": "openai", "trace_id": "0" * 32},
        {"provider": "openai", "trace_id": "A" * 32},
        {"provider": "openai", "trace_id": "a" * 31},
        {"provider": "openai", "request_id": "x", "traceparent": "00-x"},
    ],
)
def test_reference_rejects_unusable_identifiers(fields: dict) -> None:
    with pytest.raises(ValueError):
        ProviderTraceReference(**fields)


@pytest.mark.parametrize("raw", [1, {"provider": "openai"}, "not json", '{"provider":"openai"}'])
def test_malformed_stored_reference_is_rejected(raw: object) -> None:
    with pytest.raises(ValueError):
        ProviderTraceReference.from_metadata({ProviderTraceReference.METADATA_KEY: raw})


def test_reference_with_only_a_trace_id_is_valid() -> None:
    assert ProviderTraceReference(provider="gateway", trace_id=_TRACE_A).trace_id == _TRACE_A


# ---------------------------------------------------------------------------
# MappingProviderTraceResolver
# ---------------------------------------------------------------------------


async def test_mapping_resolves_request_and_run_ids() -> None:
    resolver = MappingProviderTraceResolver()
    resolver.record(trace_id=_TRACE_A, request_id="chatcmpl-1")
    resolver.record(trace_id=_TRACE_B, run_id="run-2", provider="anthropic")
    references = [
        ProviderTraceReference(provider="openai", request_id="chatcmpl-1"),
        ProviderTraceReference(provider="anthropic", run_id="run-2"),
        ProviderTraceReference(provider="openai", run_id="run-2"),
        ProviderTraceReference(provider="openai", request_id="unknown"),
        ProviderTraceReference(provider="openai", request_id="unknown", trace_id=_TRACE_W3C),
    ]
    assert await resolver.resolve_trace_ids_async(references=references) == (
        _TRACE_A,
        _TRACE_B,
        None,
        None,
        _TRACE_W3C,
    )


async def test_mapping_treats_an_identifier_in_two_traces_as_ambiguous() -> None:
    resolver = MappingProviderTraceResolver()
    resolver.record(trace_id=_TRACE_A, request_id="chatcmpl-1")
    resolver.record(trace_id=_TRACE_B, request_id="chatcmpl-1", provider="openai")
    resolver.record(trace_id=_TRACE_A, run_id="run-1")
    resolver.record(trace_id=_TRACE_B, request_id="chatcmpl-2")
    assert await resolver.resolve_trace_ids_async(
        references=[
            ProviderTraceReference(provider="openai", request_id="chatcmpl-1"),
            ProviderTraceReference(provider="anthropic", request_id="chatcmpl-1"),
            ProviderTraceReference(provider="openai", request_id="chatcmpl-2", run_id="run-1"),
        ]
    ) == (None, _TRACE_A, None)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trace_id": _TRACE_A},
        {"trace_id": "0" * 32, "request_id": "x"},
        {"trace_id": "A" * 32, "request_id": "x"},
        {"trace_id": "a" * 33, "request_id": "x"},
    ],
)
def test_mapping_rejects_invalid_records(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        MappingProviderTraceResolver().record(**kwargs)


def test_mapping_identity_carries_only_its_name() -> None:
    assert MappingProviderTraceResolver(resolver_id="gateway-logs").get_identifier().params == {
        "resolver_id": "gateway-logs"
    }
    with pytest.raises(ValueError):
        MappingProviderTraceResolver(resolver_id=" ")


# ---------------------------------------------------------------------------
# SpanAttributeTraceResolver and InMemoryTraceClient.find_trace_ids
# ---------------------------------------------------------------------------


def test_find_trace_ids_matches_attribute_values_exactly() -> None:
    client = InMemoryTraceClient()
    client.add_span(_model_span(trace_id=_TRACE_A, response_id="chatcmpl-1"))
    client.add_span(_span(trace_id=_TRACE_B, span_id="3" * 16, attributes={"run.id": "chatcmpl-1"}))
    assert client.find_trace_ids(keys=("gen_ai.response.id",), value="chatcmpl-1") == {_TRACE_A}
    assert client.find_trace_ids(keys=("gen_ai.response.id", "run.id"), value="chatcmpl-1") == {_TRACE_A, _TRACE_B}
    assert client.find_trace_ids(keys=("gen_ai.response.id",), value="chatcmpl") == set()
    client.close()
    with pytest.raises(TraceAcquisitionError):
        client.find_trace_ids(keys=("gen_ai.response.id",), value="chatcmpl-1")


async def test_span_resolver_finds_the_trace_that_recorded_the_response() -> None:
    client = InMemoryTraceClient()
    client.add_span(_model_span(trace_id=_TRACE_A, response_id="chatcmpl-1"))
    client.add_span(_model_span(trace_id=_TRACE_B, response_id="chatcmpl-2"))
    client.add_span(_model_span(trace_id=_TRACE_W3C, response_id="chatcmpl-2"))
    client.add_span(_span(trace_id=_TRACE_B, span_id="4" * 16, attributes={"agent.run_id": "run-1"}))
    resolver = SpanAttributeTraceResolver(trace_client=client, run_id_attributes=("agent.run_id",))
    assert await resolver.resolve_trace_ids_async(
        references=[
            ProviderTraceReference(provider="openai", request_id="chatcmpl-1"),
            ProviderTraceReference(provider="openai", request_id="chatcmpl-2"),
            ProviderTraceReference(provider="openai", run_id="run-1"),
            ProviderTraceReference(provider="openai", request_id="chatcmpl-1", run_id="run-1"),
            ProviderTraceReference(provider="openai", request_id="missing"),
            ProviderTraceReference(provider="openai", request_id="missing", trace_id=_TRACE_W3C),
        ]
    ) == (_TRACE_A, None, _TRACE_B, None, None, _TRACE_W3C)


async def test_span_resolver_ignores_run_ids_unless_attributes_are_named() -> None:
    client = InMemoryTraceClient()
    client.add_span(_span(trace_id=_TRACE_A, span_id="4" * 16, attributes={"gen_ai.response.id": "run-1"}))
    resolver = SpanAttributeTraceResolver(trace_client=client)
    assert await resolver.resolve_trace_ids_async(
        references=[ProviderTraceReference(provider="openai", run_id="run-1")]
    ) == (None,)


@pytest.mark.parametrize(
    "kwargs", [{"request_id_attributes": ()}, {"request_id_attributes": (" ",)}, {"run_id_attributes": ("",)}]
)
def test_span_resolver_requires_named_attributes(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        SpanAttributeTraceResolver(trace_client=InMemoryTraceClient(), **kwargs)


def test_span_resolver_identity_includes_attributes_and_capture() -> None:
    client = InMemoryTraceClient(source_id="capture-1")
    default = SpanAttributeTraceResolver(trace_client=client).get_identifier()
    with_runs = SpanAttributeTraceResolver(trace_client=client, run_id_attributes=("run.id",)).get_identifier()
    other_capture = SpanAttributeTraceResolver(trace_client=InMemoryTraceClient(source_id="capture-2")).get_identifier()
    assert default.params == {"request_id_attributes": ["gen_ai.response.id"], "run_id_attributes": []}
    assert len({default.hash, with_runs.hash, other_capture.hash}) == 3


# ---------------------------------------------------------------------------
# Request links
# ---------------------------------------------------------------------------


async def test_provider_reported_trace_links_without_a_resolver(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="gateway", request_id="r-1", trace_id=_TRACE_A),
    )
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="gateway", request_id="r-2"),
    )
    links = await resolve_conversation_trace_links_async(conversation_id=conversation, memory=sqlite_instance)
    assert [(link.trace_id, link.linked) for link in links] == [(_TRACE_A, True), (None, False)]
    assert links[1].provider_reference == ProviderTraceReference(provider="gateway", request_id="r-2")


async def test_w3c_link_wins_and_resolver_sees_only_unlinked_requests(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="r-1"),
        w3c_trace_id=_TRACE_W3C,
    )
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="r-2"),
    )
    await _store_async(memory=sqlite_instance, conversation=conversation)
    resolver = _FixedResolver([_TRACE_B])
    links = await resolve_conversation_trace_links_async(
        conversation_id=conversation, memory=sqlite_instance, provider_resolver=resolver
    )
    assert [(link.trace_id, link.linked) for link in links] == [(_TRACE_W3C, True), (_TRACE_B, True), (None, False)]
    assert resolver.calls == [(ProviderTraceReference(provider="openai", request_id="r-2"),)]


async def test_no_provider_references_skips_the_resolver(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    await _store_async(memory=sqlite_instance, conversation=conversation, w3c_trace_id=_TRACE_W3C)
    resolver = _FixedResolver([])
    await resolve_conversation_trace_links_async(
        conversation_id=conversation, memory=sqlite_instance, provider_resolver=resolver
    )
    assert resolver.calls == []


@pytest.mark.parametrize("answers", [[], [_TRACE_A, _TRACE_B]])
async def test_resolver_must_answer_each_reference_once(sqlite_instance: SQLiteMemory, answers: list) -> None:
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="r-1"),
    )
    with pytest.raises(ValueError, match="answer each reference once"):
        await resolve_conversation_trace_links_async(
            conversation_id=conversation, memory=sqlite_instance, provider_resolver=_FixedResolver(answers)
        )


async def test_message_scope_counts_resolved_requests_as_linked(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="r-1"),
    )
    response = await _store_async(memory=sqlite_instance, conversation=conversation, role="assistant")
    scorable = MessageScorable.from_message(response)
    assert await resolve_message_trace_scope_async(scorable=scorable, memory=sqlite_instance) == (None, False)
    assert await resolve_message_trace_scope_async(
        scorable=scorable, memory=sqlite_instance, provider_resolver=_FixedResolver([_TRACE_A])
    ) == (TraceScorable(trace_ids=(_TRACE_A,)), True)


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "resolve", "expected"),
    [("lookup", True, True), ("other", True, False), ("lookup", False, None), ("other", False, None)],
)
async def test_tool_call_scorer_uses_provider_identity_without_w3c(
    sqlite_instance: SQLiteMemory, tool: str, resolve: bool, expected: bool | None
) -> None:
    client = InMemoryTraceClient()
    client.add_span(_model_span(trace_id=_TRACE_A, response_id="chatcmpl-1"))
    client.add_span(_tool_span(trace_id=_TRACE_A, tool=tool))
    client.mark_complete(trace_ids=(_TRACE_A,))
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="chatcmpl-1"),
    )
    response = await _store_async(memory=sqlite_instance, conversation=conversation, role="assistant")
    scorer = OtelToolCallScorer(
        source=OtelTraceSource(trace_client=client),
        provider_trace_resolver=SpanAttributeTraceResolver(trace_client=client) if resolve else None,
    )
    score = (
        await scorer.score_async(scorable=MessageScorable.from_message(response), expectation=_expectation("lookup"))
    )[0]
    if expected is None:
        assert score.is_undetermined
    else:
        assert not score.is_undetermined
        assert score.get_value() is expected
        observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]
        assert observation.scorable == TraceScorable(trace_ids=(_TRACE_A,))
    client.close()


@pytest.mark.parametrize("scorer_class", [OtelToolCallScorer, OtelToolCallOrderScorer])
def test_scorer_identity_changes_only_when_a_resolver_is_set(scorer_class: type) -> None:
    client = InMemoryTraceClient()
    source = OtelTraceSource(trace_client=client)
    plain = scorer_class(source=source).get_identifier()
    explicit_none = scorer_class(source=source, provider_trace_resolver=None).get_identifier()
    resolved = scorer_class(
        source=source, provider_trace_resolver=SpanAttributeTraceResolver(trace_client=client)
    ).get_identifier()
    assert plain.hash == explicit_none.hash
    assert "provider_trace_resolver" not in (plain.children or {})
    assert resolved.hash != plain.hash
    assert "provider_trace_resolver" in resolved.children


async def test_conversation_evidence_links_through_the_resolver(sqlite_instance: SQLiteMemory) -> None:
    client = InMemoryTraceClient()
    client.add_span(_model_span(trace_id=_TRACE_A, response_id="chatcmpl-1"))
    client.mark_complete(trace_ids=(_TRACE_A,))
    conversation = str(uuid.uuid4())
    await _store_async(
        memory=sqlite_instance,
        conversation=conversation,
        reference=ProviderTraceReference(provider="openai", request_id="chatcmpl-1"),
    )
    source = OtelExecutionTraceSource(trace_client=client)
    unresolved = await ConversationTraceEvidence(source=source).acquire_async(
        conversation_id=conversation, memory=sqlite_instance
    )
    assert not unresolved.complete
    assert unresolved.observation is None
    resolved = await ConversationTraceEvidence(
        source=source, provider_trace_resolver=SpanAttributeTraceResolver(trace_client=client)
    ).acquire_async(conversation_id=conversation, memory=sqlite_instance)
    assert resolved.complete
    assert resolved.observation is not None
    assert resolved.observation.scorable == TraceScorable(trace_ids=(_TRACE_A,))
    client.close()


def test_conversation_scorer_identity_and_validation_cover_the_resolver() -> None:
    client = InMemoryTraceClient()
    source = OtelExecutionTraceSource(trace_client=client)
    judge = SubStringScorer(substring="x")
    plain = create_conversation_scorer(scorer=judge, trace_source=source).get_identifier()
    resolved = create_conversation_scorer(
        scorer=judge, trace_source=source, provider_trace_resolver=SpanAttributeTraceResolver(trace_client=client)
    ).get_identifier()
    assert "provider_trace_resolver" not in plain.children
    assert "provider_trace_resolver" in resolved.children
    assert resolved.hash != plain.hash
    with pytest.raises(ValueError, match="provider_trace_resolver needs a trace_source"):
        create_conversation_scorer(
            scorer=judge, provider_trace_resolver=SpanAttributeTraceResolver(trace_client=client)
        )
