# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from pyrit.executor.attack import AttackScoringConfig, MultiPromptSendingAttack, PromptSendingAttack
from pyrit.executor.attack.component.conversation_manager import ConversationManager
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    Acquisition,
    AttackOutcome,
    Message,
    MessagePiece,
    MessageScorable,
    RequestTraceContext,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
    TraceScorable,
)
from pyrit.prompt_target import HTTPTarget, TargetCapabilities, TargetConfiguration, TargetTraceConfig
from pyrit.score import (
    InMemoryTraceClient,
    InMemoryTraceExporter,
    OtelToolCallScorer,
    OtelTraceSource,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.observation.message_trace_resolver import resolve_message_trace_scope

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture
def capture() -> Iterator[tuple[InMemoryTraceClient, TracerProvider]]:
    client = InMemoryTraceClient()
    provider = TracerProvider(sampler=ALWAYS_ON, span_limits=SpanLimits(max_span_attribute_length=SpanLimits.UNSET))
    provider.add_span_processor(SimpleSpanProcessor(InMemoryTraceExporter(trace_client=client)))
    yield client, provider
    provider.shutdown()
    client.close()


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


def _agent_target(*, client: InMemoryTraceClient, provider: TracerProvider, complete: bool = True) -> HTTPTarget:
    tracer = provider.get_tracer("test-agent")

    def respond(request: httpx.Request) -> httpx.Response:
        context = TraceContextTextMapPropagator().extract(dict(request.headers))
        with tracer.start_as_current_span("agent", context=context) as root:
            tool_name = request.content.decode()
            with tracer.start_as_current_span(
                tool_name,
                attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool_name},
            ):
                pass
        if complete:
            client.mark_complete(trace_ids=(f"{root.get_span_context().trace_id:032x}",))
        return httpx.Response(200, text="done")

    return HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}",
        transport=httpx.MockTransport(respond),
        trace_config=TargetTraceConfig(enabled=True),
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True)),
    )


@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize(
    ("tool", "complete", "outcome"),
    [
        ("lookup", True, AttackOutcome.SUCCESS),
        ("other", True, AttackOutcome.FAILURE),
        ("other", False, AttackOutcome.UNDETERMINED),
        ("lookup", False, AttackOutcome.SUCCESS),
    ],
)
async def test_attack_scores_tool_evidence_and_replays_async(
    sqlite_instance: SQLiteMemory,
    capture: tuple[InMemoryTraceClient, TracerProvider],
    composite: bool,
    tool: str,
    complete: bool,
    outcome: AttackOutcome,
) -> None:
    client, provider = capture
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    objective_scorer = (
        TrueFalseCompositeScorer(
            scorers=[SubStringScorer(substring="done"), scorer],
            aggregator=TrueFalseScoreAggregator.AND,
        )
        if composite
        else scorer
    )
    attack = PromptSendingAttack(
        objective_target=_agent_target(client=client, provider=provider, complete=complete),
        attack_scoring_config=AttackScoringConfig(objective_scorer=objective_scorer),
        max_attempts_on_failure=0,
    )
    result = await attack.execute_async(objective=tool, expectation=_expectation("lookup"))
    assert result.outcome is outcome
    score = result.automated_score
    assert score is not None
    assert result.last_response is not None
    assert score.scorable == MessageScorable(message_piece_ids=(result.last_response.id,))
    assert score.message_piece_id == result.last_response.id
    assert score.scored_expectation.conditions == _expectation("lookup").conditions
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert isinstance(observation.scorable, TraceScorable)
    assert observation.payload.scope == observation.scorable
    stored_result = sqlite_instance.get_attack_results(attack_result_ids=[result.attack_result_id])[0]
    assert stored_result.automated_score.id == score.id
    assert stored_result.outcome is outcome
    client.close()
    replay = (await scorer.score_observation_async(observation=observation, expectation=_expectation("lookup")))[0]
    assert replay.status == score.status
    if not score.is_undetermined:
        assert replay.get_value() == score.get_value()


async def test_multiturn_attack_combines_only_its_requests_async(
    capture: tuple[InMemoryTraceClient, TracerProvider],
) -> None:
    client, provider = capture
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    target = _agent_target(client=client, provider=provider)
    attack = MultiPromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
    )
    result = await attack.execute_async(
        objective="use both tools",
        user_messages=[
            Message.from_prompt(prompt="lookup", role="user"),
            Message.from_prompt(prompt="summarize", role="user"),
        ],
        expectation=_expectation("lookup", "summarize"),
    )
    assert result.outcome is AttackOutcome.SUCCESS
    isolated = await attack.execute_async(
        objective="use both tools",
        user_messages=[Message.from_prompt(prompt="summarize", role="user")],
        expectation=_expectation("lookup", "summarize"),
    )
    assert isolated.outcome is AttackOutcome.FAILURE


async def test_auxiliary_trace_score_is_stored_async(
    sqlite_instance: SQLiteMemory, capture: tuple[InMemoryTraceClient, TracerProvider]
) -> None:
    client, provider = capture
    source = OtelTraceSource(trace_client=client)
    attack = PromptSendingAttack(
        objective_target=_agent_target(client=client, provider=provider),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=SubStringScorer(substring="done"),
            auxiliary_scorers=[OtelToolCallScorer(source=source)],
        ),
    )
    with patch.object(source, "acquire_async", wraps=source.acquire_async) as acquire:
        result = await attack.execute_async(objective="lookup", expectation=_expectation("lookup"))
        acquire.assert_awaited_once()
    assert result.outcome is AttackOutcome.SUCCESS
    scores = sqlite_instance.get_prompt_scores(prompt_ids=[result.last_response.id])
    assert len(scores) == 2
    assert all(score.get_value() is True for score in scores)
    assert all(score.message_piece_id == result.last_response.id for score in scores)


def _store(*, memory: SQLiteMemory, conversation: str, role: str = "user", trace_id: str | None = None) -> Message:
    piece = MessagePiece(role=role, original_value="content", conversation_id=conversation)
    if trace_id:
        piece.prompt_metadata.update(RequestTraceContext(traceparent=f"00-{trace_id}-{'2' * 16}-01").to_metadata())
    message = piece.to_message()
    memory.add_message_to_memory(request=message)
    return message


def test_resolver_bounds_scope_to_the_named_message(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    _store(memory=sqlite_instance, conversation=conversation, trace_id="1" * 32)
    response = _store(memory=sqlite_instance, conversation=conversation, role="assistant", trace_id="3" * 32)
    _store(memory=sqlite_instance, conversation=conversation, trace_id="4" * 32)
    _store(memory=sqlite_instance, conversation=str(uuid.uuid4()), trace_id="5" * 32)
    scope, complete = resolve_message_trace_scope(
        scorable=MessageScorable.from_message(response), memory=sqlite_instance
    )
    assert scope == TraceScorable(trace_ids=("1" * 32,))
    assert complete


async def test_prepended_history_does_not_carry_trace_links_async(sqlite_instance: SQLiteMemory) -> None:
    source_conversation = str(uuid.uuid4())
    prepended = _store(memory=sqlite_instance, conversation=source_conversation, trace_id="1" * 32)
    conversation = str(uuid.uuid4())
    await ConversationManager().add_prepended_conversation_to_memory_async(
        prepended_conversation=[prepended], conversation_id=conversation
    )
    copied = next(
        piece for piece in sqlite_instance.get_message_pieces(conversation_id=conversation) if piece.role == "user"
    )
    assert RequestTraceContext.from_metadata(copied.prompt_metadata) is None
    assert RequestTraceContext.from_metadata(prepended.get_piece().prompt_metadata) is not None
    response = _store(memory=sqlite_instance, conversation=conversation, role="assistant")
    assert resolve_message_trace_scope(scorable=MessageScorable.from_message(response), memory=sqlite_instance) == (
        None,
        False,
    )


async def test_unlinked_request_prevents_a_false_verdict_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    _store(memory=sqlite_instance, conversation=conversation)
    _store(memory=sqlite_instance, conversation=conversation, role="assistant")
    _store(memory=sqlite_instance, conversation=conversation, trace_id="1" * 32)
    response = _store(memory=sqlite_instance, conversation=conversation, role="assistant")
    client = InMemoryTraceClient()
    client.mark_complete(trace_ids=("1" * 32,))
    scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
    score = (
        await scorer.score_async(scorable=MessageScorable.from_message(response), expectation=_expectation("lookup"))
    )[0]
    assert score.is_undetermined
    observation = sqlite_instance.get_observations(observation_ids=score.observation_ids)[0]
    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.payload.coverage.complete
    client.close()
    replayed = (await scorer.score_observation_async(observation=observation, expectation=_expectation("lookup")))[0]
    assert replayed.get_value() is False


async def test_no_links_does_not_query_source_async(sqlite_instance: SQLiteMemory) -> None:
    response = _store(memory=sqlite_instance, conversation=str(uuid.uuid4()), role="assistant")
    source = OtelTraceSource(trace_client=InMemoryTraceClient())
    scorer = OtelToolCallScorer(source=source)
    with patch.object(source, "acquire_async", new_callable=AsyncMock) as acquire:
        score = (
            await scorer.score_async(
                scorable=MessageScorable.from_message(response), expectation=_expectation("lookup")
            )
        )[0]
        acquire.assert_not_called()
    assert score.is_undetermined
    assert score.observation_ids == []
    assert score.message_piece_id == response.get_piece().id


async def test_missing_message_is_an_error_without_acquisition_async() -> None:
    source = OtelTraceSource(trace_client=InMemoryTraceClient())
    with patch.object(source, "acquire_async", new_callable=AsyncMock) as acquire:
        with pytest.raises(RuntimeError, match="No message pieces found"):
            await OtelToolCallScorer(source=source).score_async(
                scorable=MessageScorable(message_piece_ids=(uuid.uuid4(),)), expectation=_expectation("lookup")
            )
        acquire.assert_not_called()


@pytest.mark.parametrize("error", ["blocked", "empty"])
async def test_response_errors_do_not_suppress_tool_evidence_async(
    sqlite_instance: SQLiteMemory, capture: tuple[InMemoryTraceClient, TracerProvider], error: str
) -> None:
    client, provider = capture
    target = _agent_target(client=client, provider=provider)
    original_send = target._send_prompt_to_target_async

    async def send_with_error_async(*, normalized_conversation: list[Message]) -> list[Message]:
        responses = await original_send(normalized_conversation=normalized_conversation)
        responses[0].get_piece().response_error = error
        responses[0].get_piece().converted_value = ""
        return responses

    with patch.object(target, "_send_prompt_to_target_async", side_effect=send_with_error_async):
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
            ),
        )
        result = await attack.execute_async(objective="lookup", expectation=_expectation("lookup"))
    assert result.outcome is AttackOutcome.SUCCESS


async def test_malformed_request_metadata_raises_before_query_async(sqlite_instance: SQLiteMemory) -> None:
    request = MessagePiece(
        role="user",
        original_value="request",
        conversation_id=str(uuid.uuid4()),
        prompt_metadata={RequestTraceContext.METADATA_KEY: "invalid"},
    )
    sqlite_instance.add_message_to_memory(request=request.to_message())
    response = _store(memory=sqlite_instance, conversation=request.conversation_id, role="assistant")
    source = OtelTraceSource(trace_client=InMemoryTraceClient())
    with patch.object(source, "acquire_async", new_callable=AsyncMock) as acquire:
        with pytest.raises(RuntimeError, match="traceparent"):
            await OtelToolCallScorer(source=source).score_async(
                scorable=MessageScorable.from_message(response), expectation=_expectation("lookup")
            )
        acquire.assert_not_called()


def test_multipart_request_deduplicates_trace_scope(sqlite_instance: SQLiteMemory) -> None:
    conversation = str(uuid.uuid4())
    metadata = RequestTraceContext(traceparent=f"00-{'1' * 32}-{'2' * 16}-01").to_metadata()
    sqlite_instance.add_message_to_memory(
        request=Message(
            message_pieces=[
                MessagePiece(role="user", original_value="a", conversation_id=conversation, prompt_metadata=metadata),
                MessagePiece(role="user", original_value="b", conversation_id=conversation, prompt_metadata=metadata),
            ]
        )
    )
    response = _store(memory=sqlite_instance, conversation=conversation, role="assistant")
    assert resolve_message_trace_scope(scorable=MessageScorable.from_message(response), memory=sqlite_instance) == (
        TraceScorable(trace_ids=("1" * 32,)),
        True,
    )
