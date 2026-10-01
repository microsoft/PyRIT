# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
# ---

# %% [markdown]
# # Trace-backed tool-call scoring
#
# `OtelToolCallScorer` answers "Did these tools run?" It matches exact,
# case-sensitive names in execution spans, not claims in a response or
# model-proposed calls. A failed execution attempt counts; the scorer does not
# check tool success, arguments, order, or call count.
#
# The example tool is named **`my_tool_call`**, implemented by the `my_tool_call()` function
# below. Its span records `"gen_ai.tool.name": "my_tool_call"`.
# `expects("my_tool_call")` asks the scorer to find that exact tool name in the trace;
# `"summarize"` is a different tool name used to demonstrate a missing tool.
#
# Supply trace IDs explicitly, or use stored message evidence from an attack.
# For messages, the scorer resolves request traces in the conversation through
# the scored response, without reading later turns.
#
# The OpenTelemetry SDK is included with PyRIT. This walkthrough uses a real SDK provider,
# local capture, and PyRIT's in-memory storage. It needs no model, service, credentials, or global
# provider changes.

# %%
import asyncio

from opentelemetry.context import Context
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from pyrit.memory import CentralMemory
from pyrit.models import ScoreStatus, ScoringExpectation, ToolCallRequirement, ToolsCalled, TraceScorable
from pyrit.score import OtelToolCallScorer
from pyrit.score.observation import InMemoryTraceClient, InMemoryTraceExporter, OtelTraceSource
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    load_defaults=False,
    env_files=[],
    silent=True,
)
memory = CentralMemory.get_memory_instance()
client = InMemoryTraceClient()
provider = TracerProvider(sampler=ALWAYS_ON, span_limits=SpanLimits(max_span_attribute_length=SpanLimits.UNSET))
provider.add_span_processor(SimpleSpanProcessor(InMemoryTraceExporter(trace_client=client)))
tracer = provider.get_tracer("pyrit-tool-example")
scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))


def expects(*names: str) -> ScoringExpectation:
    """Build a name-only condition."""
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


def my_tool_call() -> str:
    """Execute a deterministic tool with real SDK instrumentation."""
    with tracer.start_as_current_span(
        "my_tool_call",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "my_tool_call"},
    ):
        return "Local result"


# %% [markdown]
# ## Capture and score a real invocation
#
# The caller gets the trace ID from its instrumented execution. The source
# selects only that trace and stores normalized evidence with the score.

# %%
with tracer.start_as_current_span("agent", context=Context()) as root:
    my_tool_call()
    trace_id = f"{root.get_span_context().trace_id:032x}"

scope = TraceScorable(trace_ids=(trace_id,))
positive = (await scorer.score_async(scorable=scope, expectation=expects("my_tool_call")))[0]  # type: ignore
assert positive.get_value() is True
assert positive.scorable == scope
print(f"Observed my_tool_call: {positive.get_value()}")

# %% [markdown]
# ## Missing evidence is not a negative result
#
# Capture starts incomplete. Observing a call proves invocation, but an absent
# tool is not false until coverage is explicitly complete.
#
# This example disables span attribute string truncation, including limits set by
# environment variables. The exporter rejects finite or unknown length limits:
# shortened names cannot prove which tools ran, even with incomplete coverage.
#
# This example has no background work and uses always-on sampling. Both spans
# have ended, so after checking export we can declare this controlled capture
# complete. A flush alone would not prove that an arbitrary remote trace is
# complete.

# %%
unknown = (await scorer.score_async(scorable=scope, expectation=expects("summarize")))[0]  # type: ignore
assert unknown.status is ScoreStatus.UNDETERMINED
print(f"Missing tool before completion: {unknown.status.value}")

flushed = await asyncio.to_thread(provider.force_flush)  # type: ignore
assert flushed
client.mark_complete(trace_ids=(trace_id,))
negative = (await scorer.score_async(scorable=scope, expectation=expects("summarize")))[0]  # type: ignore
assert negative.get_value() is False
print(f"Missing tool after controlled completion: {negative.get_value()}")

# %% [markdown]
# ## Re-match saved evidence with capture closed
#
# Load the observation from PyRIT memory, stop capture, and change the expected name.
# Replay reads the same immutable snapshot; it does not call the trace client.
# Arguments and results are not retained. Scorer target response observations still
# have their stricter, original-expectation replay rules.

# %%
saved = memory.get_observations(observation_ids=negative.observation_ids)[0]
assert saved.scorable == scope
before_replay = saved.model_dump_json()
await asyncio.to_thread(provider.shutdown)  # type: ignore
client.close()

replayed = (await scorer.score_observation_async(observation=saved, expectation=expects("my_tool_call")))[0]  # type: ignore
assert replayed.get_value() is True
assert replayed.observation_ids == negative.observation_ids
assert memory.get_observations(observation_ids=negative.observation_ids)[0].model_dump_json() == before_replay
print(f"my_tool_call in saved evidence after capture is closed: {replayed.get_value()}")

# %% [markdown]
# ## Score a tool call through an attack
#
# This local agent uses a real SDK provider and an in-process HTTP transport.
# No server, model, credentials, or global instrumentation is needed.
# `HTTPTarget` sends a fresh `traceparent` per request when you enable tracing, and
# PyRIT saves the same context on the request. The agent extracts that context
# before it runs a tool.
#
# Here, the prompt `"my_tool_call"` tells the local agent to call the `my_tool_call()` tool.
# The prompt and tool name are the same only to keep the example simple.
# The scorer matches the tool name recorded in the span, not the prompt text.
#
# The caller owns capture completeness. This controlled agent has no background
# work, sampling is disabled, and it checks export after its spans end. Only
# then does it mark a request trace complete. An HTTP response alone would not
# prove this for an arbitrary remote agent.

# %%
import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import AttackOutcome, MessageScorable
from pyrit.prompt_target import HTTPTarget, TargetTraceConfig
from pyrit.score import SubStringScorer, TrueFalseCompositeScorer, TrueFalseScoreAggregator

client = InMemoryTraceClient()
provider = TracerProvider(sampler=ALWAYS_ON, span_limits=SpanLimits(max_span_attribute_length=SpanLimits.UNSET))
provider.add_span_processor(SimpleSpanProcessor(InMemoryTraceExporter(trace_client=client)))
tracer = provider.get_tracer("local-attack-agent")


async def local_agent_async(request: httpx.Request) -> httpx.Response:
    """Execute the local agent under the received request context."""
    context = TraceContextTextMapPropagator().extract(dict(request.headers))
    prompt = request.content.decode()
    with tracer.start_as_current_span("agent", context=context) as root:
        if prompt == "my_tool_call":
            my_tool_call()
    if prompt != "pending":
        if not await asyncio.to_thread(provider.force_flush):
            raise RuntimeError("Local trace export did not finish.")
        client.mark_complete(trace_ids=(f"{root.get_span_context().trace_id:032x}",))
    return httpx.Response(200, text="done")


target = HTTPTarget(
    http_request="POST / HTTP/1.1\nHost: local-agent.test\n\n{PROMPT}",
    transport=httpx.MockTransport(local_agent_async),
    trace_config=TargetTraceConfig(enabled=True),
)
tool_scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
attack = PromptSendingAttack(
    objective_target=target,
    attack_scoring_config=AttackScoringConfig(objective_scorer=tool_scorer),
    max_attempts_on_failure=0,
)
for prompt, expected in (
    ("my_tool_call", AttackOutcome.SUCCESS),
    ("no tool", AttackOutcome.FAILURE),
    ("pending", AttackOutcome.UNDETERMINED),
):
    result = await attack.execute_async(objective=prompt, expectation=expects("my_tool_call"))  # type: ignore
    assert result.outcome is expected
    assert isinstance(result.automated_score.scorable, MessageScorable)
    print(f"{prompt}: {result.outcome.value}")

# %% [markdown]
# A composite receives the same message reference in each child. The message
# scorer reads the response, and the tool scorer reads linked execution evidence.
# This deterministic message scorer keeps the example offline; a normal LLM
# objective scorer can use the same composite path.

# %%
composite_attack = PromptSendingAttack(
    objective_target=target,
    attack_scoring_config=AttackScoringConfig(
        objective_scorer=TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[SubStringScorer(substring="done"), tool_scorer],
        ),
    ),
)
result = await composite_attack.execute_async(  # type: ignore
    objective="my_tool_call", expectation=expects("my_tool_call")
)
assert result.outcome is AttackOutcome.SUCCESS
print(f"Message and tool evidence: {result.outcome.value}")
saved = memory.get_observations(observation_ids=result.automated_score.observation_ids)[0]
await asyncio.to_thread(provider.shutdown)  # type: ignore
client.close()
replayed = (  # type: ignore
    await tool_scorer.score_observation_async(observation=saved, expectation=expects("my_tool_call"))
)[0]
assert replayed.get_value() is True
print(f"Saved attack tool evidence: {replayed.get_value()}")
memory.dispose_engine()

# %% [markdown]
# ## Configure tracing
#
# `HTTPTarget` and `HTTPXAPITarget` disable tracing by default, because an arbitrary
# HTTP endpoint is not known to accept W3C trace context. Set
# `trace_config=TargetTraceConfig(enabled=True)` for an instrumented endpoint. Keep
# tracing disabled if you supply manual `traceparent` or `tracestate` headers.
#
# A custom local target can pass `TargetTraceConfig(tracer=provider.get_tracer(...))`
# to its `PromptTarget` constructor. This captures the target invocation with a
# caller-owned provider. PyRIT does not install a global provider or exporter.
#
# For a remote agent, use the normal HTTP transport. The agent must accept W3C
# trace context and export its tool spans. Supply a `TraceClient` that can read
# those spans; sending a header does not create a trace-store connection.
# Provider-specific SDK targets are not changed by this example.

# %% [markdown]
# ## Use another trace source
#
# A caller can provide a `TraceClient` that returns neutral spans for the
# requested trace IDs and reports coverage honestly. `OtelTraceSource` handles
# supported GenAI/OpenInference execution spans; matching remains backend-neutral.
# A client reports `TraceQueryResult(available=False)` when it knows no evidence
# can be retrieved, such as after retention expires. This produces an unavailable
# observation and an undetermined score. Pending capture stays available with
# incomplete coverage; an empty result alone does not mean unavailable.
# Optional empty call IDs are omitted; they do not prevent name-only scoring.
#
# `ObservationSource[TraceScorable]` is the source contract for this scorer.
# Other sources can use the same protocol with their own scorable types.
# The caller owns instrumentation and trace retrieval. Automatic request
# correlation does not install a remote collector or a backend adapter.
