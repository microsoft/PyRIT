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
# Supply the trace IDs explicitly. This first version does not discover traces
# from messages or act as an automatic attack outcome scorer.
#
# Install PyRIT with the `otel` extra. This walkthrough uses a real SDK provider,
# local capture, and PyRIT's in-memory storage. It needs no model, service, credentials, or global
# provider changes.

# %%
import asyncio

from opentelemetry.context import Context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from pyrit.memory import CentralMemory
from pyrit.models import ScoreStatus, ScoringExpectation, ToolCallRequirement, ToolsCalled, TraceScorable
from pyrit.score import InMemoryTraceClient, InMemoryTraceExporter, OtelToolCallScorer, OtelTraceSource
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    load_defaults=False,
    env_files=[],
    silent=True,
)
memory = CentralMemory.get_memory_instance()
client = InMemoryTraceClient()
provider = TracerProvider(sampler=ALWAYS_ON)
provider.add_span_processor(SimpleSpanProcessor(InMemoryTraceExporter(trace_client=client)))
tracer = provider.get_tracer("pyrit-tool-example")
scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))


def expects(*names: str) -> ScoringExpectation:
    """Build a name-only condition."""
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


def lookup() -> str:
    """Execute a deterministic tool with real SDK instrumentation."""
    with tracer.start_as_current_span(
        "lookup",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup"},
    ):
        return "Local result"


# %% [markdown]
# ## Capture and score a real invocation
#
# The caller gets the trace ID from its instrumented execution. The source
# selects only that trace and stores normalized evidence with the score.

# %%
with tracer.start_as_current_span("agent", context=Context()) as root:
    lookup()
    trace_id = f"{root.get_span_context().trace_id:032x}"

scope = TraceScorable(trace_ids=(trace_id,))
positive = (await scorer.score_async(scorable=scope, expectation=expects("lookup")))[0]  # type: ignore
assert positive.get_value() is True
assert positive.scorable == scope
print(f"Observed lookup: {positive.get_value()}")

# %% [markdown]
# ## Missing evidence is not a negative result
#
# Capture starts incomplete. Observing a call proves invocation, but an absent
# tool is not false until coverage is explicitly complete.
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
# Arguments and results are not retained. Existing judgment observations still
# have their stricter, original-expectation replay rules.

# %%
saved = memory.get_observations(observation_ids=negative.observation_ids)[0]
assert saved.scorable == scope
before_replay = saved.model_dump_json()
await asyncio.to_thread(provider.shutdown)  # type: ignore
client.close()

replayed = (await scorer.score_observation_async(observation=saved, expectation=expects("lookup")))[0]  # type: ignore
assert replayed.get_value() is True
assert replayed.observation_ids == negative.observation_ids
assert memory.get_observations(observation_ids=negative.observation_ids)[0].model_dump_json() == before_replay
print(f"Lookup in saved evidence after capture is closed: {replayed.get_value()}")
memory.dispose_engine()

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
# The caller owns instrumentation and trace IDs. No backend adapters or automatic
# HTTP context propagation are included in this first version.
