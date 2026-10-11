# Attack-result analytics

`AttackResultAnalytics` is an async Python SDK for saved attack outcomes. It reads
aggregate counts and lightweight metadata through the configured memory backend,
without loading conversations, messages, media, scores, or full `AttackResult`
objects. This API does not change History's existing result-selection behavior.
REST and GUI integration are separate work, not part of this SDK.

## Counting policy

Every distinct saved `attack_result_id` counts once. Different result IDs sharing
a conversation, including failed attempts followed by successful retries, remain
separate. Updating an existing ID changes its current outcome, not the result count.
Selecting a scenario is a cohort filter, not a switch to scenario-unit statistics.

The shared `compute_outcome_statistics` calculator returns both denominator
policies together:

| Field | Denominator | Meaning |
|---|---|---|
| `success_rate_decided` | Successes + failures | Success among decided outcomes, the existing ASR default. |
| `success_rate_all` | All four outcomes | Success among every selected result, including errors and undetermined outcomes. |

`success_rate` remains the compatible stored field; `success_rate_decided` is its
read-only alias, not an independently mutable value. Shared-model JSON includes
both names and accepts either when validating a payload; conflicting aliases fail.
An empty denominator produces `None`. For example, one success and one error
produce `success_rate_decided=1.0` and `success_rate_all=0.5`. An error-only population
has `success_rate_decided=None` and `success_rate_all=0.0`. Neither rate requires a second query.
`total_results` includes all four outcomes; `outcome_shares` and `decided_share`
use that whole selected cohort. Empty outcome shares are zero, and an empty
cohort's decided share is `None`. All proportions are between 0 and 1.
Both success rates are calculated from counts, never by averaging subgroup rates.

Population selection is separate from denominator selection. A failed
attempt followed by a successful retry yields 50% raw-result ASR even if the
scenario's latest-unit success rate is 100%. Both analyses have both denominator
policies; the difference in this example is which attempts count. No result-role
filtering or inference from conversation presence is applied.

Outcome restrictions apply to the entire report and its result page.
`outcome_filter_applied` asks a renderer to annotate the rate as **ASR*** and
explain the restricted population. For example, success-only results have a 100%
ASR when nonempty, not evidence of a 100% unfiltered success rate. Selecting all
four outcomes normalizes to unrestricted selection and removes that annotation.

## Python usage

Initialize PyRIT before constructing the SDK. The constructor uses the configured
`CentralMemory` or accepts an explicit initialized `memory` instance. It performs
no queries, migration, repair, or database-setting changes.

```python
from pyrit.analytics import AttackResultAnalytics
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsValue,
)

async with AttackResultAnalytics() as analytics:
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(
            filters=AttackAnalyticsFilters(
                dimensions=[
                    AttackAnalyticsFilter(
                        dimension=AttackAnalyticsDimension(name="operation"),
                        values=[AttackAnalyticsValue(value="operation-a")],
                    )
                ]
            ),
            group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
            compare_by=AttackAnalyticsDimension(name="attack_type"),
        )
    )
    print(report.summary.total_results, report.summary.success_rate_decided, report.summary.success_rate_all)

    if report.drilldown_unavailable_reason:
        print(report.drilldown_unavailable_reason)
    elif report.cells:
        cell = report.cells[0]
        narrowed = report.filters.model_copy(
            update={"dimensions": [*report.filters.dimensions, *cell.drilldown_filters]}
        )
        page = await analytics.results_async(query=AttackAnalyticsResultsQuery(filters=narrowed))
        if page.has_more:
            next_page = await analytics.results_async(
                query=AttackAnalyticsResultsQuery(filters=narrowed, cursor=page.next_cursor)
            )

    options = await analytics.facets_async(
        query=AttackAnalyticsFacetQuery(
            filters=report.filters,
            dimension=AttackAnalyticsDimension(name="converter_type", converter_direction="response"),
            search="base64",
        )
    )
```

### The same statistics for scenario units

`compute_scenario_statistics` selects each scenario execution unit's latest
attempt, then calls the same outcome calculator. Its overall, atomic-attack, and
display-group counts carry an `outcomes` object of the same `OutcomeStatistics`
type as attack-report summaries, groups, and cells.

```python
from pyrit.analytics import compute_scenario_statistics

statistics = compute_scenario_statistics(scenario_result)
outcomes = statistics.overall.outcomes
assert outcomes is not None  # Always populated by compute_scenario_statistics.
print(outcomes.success_rate_decided, outcomes.success_rate_all)
```

`ScenarioProgressCounts.success_percentage` retains its existing all-completed-unit
denominator and truncated 0-100 representation for compatibility. Both rates under
`outcomes` are unrounded 0-1 proportions. Historical `errors` and `retries` remain
separate: an error followed by a successful retry contributes one historical error
but no latest-unit error. Never derive the decided denominator by subtracting
historical errors from completed units.

Scenario progress projections and JSON reports preserve the shared `outcomes`
object; existing displayed percentages do not change. Count-only scenario payloads
from older callers deserialize with `outcomes=None` because their latest-outcome
breakdown cannot be reconstructed. Combining a nonempty such payload logs a warning
and leaves the combined breakdown unavailable rather than guessing failures.

Shared outcome statistics validate supplied counts, totals, rates, and shares at
construction. Scenario progress totals must agree with the nested outcome counts.
Combining statistics also revalidates existing objects, including nested values
modified after construction, instead of silently repairing contradictions.
Objects remain mutable for compatibility: make a new result through the shared
calculator when changing counts, rather than editing cached totals or rates.

For already counted populations, call `compute_outcome_statistics` directly.
`combine_outcome_statistics` sums counts from **disjoint** populations and
recalculates both rates. It also accepts existing `AttackStats` returned by
`analyze_results` or technique analytics; those APIs retain their existing result
shape and decided-only default but use the same shared calculation internally.
`OutcomeStatistics` is an alias for the existing `AttackAnalyticsStatistics` class,
not a second representation.

```python
from pyrit.analytics import combine_outcome_statistics, compute_outcome_statistics

first = compute_outcome_statistics({"success": 1, "error": 1})
second = compute_outcome_statistics({"success": 2, "failure": 1})
combined = combine_outcome_statistics([first, second])
print(combined.success_rate_decided, combined.success_rate_all)  # 0.75, 0.6
```

Do not combine overlapping converter/harm groups to reconstruct a report's
summary; use its already-computed summary instead.

### Maintained APIs and deprecated wrappers

`analyze_results` and `compute_technique_stats_async` remain supported. Their default
results keep the six-field `AttackStats` dataclass and its existing constructor,
`asdict`, and decided-only rate. Pass `include_outcome_statistics=True` to obtain
the shared `OutcomeStatistics` directly, without an extra conversion or query:

```python
from pyrit.analytics import analyze_results, compute_technique_stats_async

analysis = analyze_results(attack_results, include_outcome_statistics=True)
print(analysis["Overall"].success_rate_decided, analysis["Overall"].success_rate_all)

techniques = await compute_technique_stats_async(
    technique_eval_hashes=technique_hashes,
    include_outcome_statistics=True,
)
for technique_hash, outcomes in techniques.items():
    print(technique_hash, outcomes.success_rate_decided, outcomes.success_rate_all)
```

The opt-in changes returned statistics, not grouping, result selection, or retry
policy. Default `AttackStats` also exposes the read-only `success_rate_decided`
property, without adding dataclass fields.

| API | Status |
|---|---|
| `analyze_results`, `compute_technique_stats_async`, `get_cached_results_for_technique_async` | Maintained. |
| Synchronous `compute_technique_stats` and `get_cached_results_for_technique` | Deprecated; scheduled for removal in 1.4.0. Use the async equivalents. |
| `ScenarioResult.objective_achieved_rate` | Deprecated; scheduled for removal in 1.4.0. Use `compute_scenario_statistics`. |

No additional API is deprecated by these changes.

### Filters and drill-downs

Omit `compare_by` for a one-dimensional breakdown. Supported dimensions include
operation, operator, targeted harm category, attack type, converter type,
objective target, model, scenario, and custom labels. Custom labels require a
literal `label_key`; dots in that key do not select nested JSON. Converter
dimensions select the request or response pipeline.

Within a predicate, values use ANY matching by default. Converter predicates can
also request ALL matching. Separate predicates are AND-combined, even when they
refer to the same dimension. A chart drill-down therefore **appends** its one
group predicate or two cell predicates. Replacing an existing converter ANY
predicate could broaden the cohort instead of drilling into it.

Queries allow at most 16 predicates, 500 values overall, and 100 values in one
predicate. A final legal click can reach the limit. The resulting report and
result pages remain valid, but `drilldown_unavailable_reason` explains why another
click would exceed the budget. Do not drop predicates to make room.
The SDK snapshots and revalidates nested request models before admission,
including UTC normalization of updated-date bounds.

### Keys, labels, and overlapping groups

Harm categories and converter types are multi-valued. Their groups overlap, but
duplicate memberships count each result only once per key or cell and once in the
overall cohort. `groups_overlap` warns against summing groups to reconstruct the
cohort total. Empty heatmap cells explicitly contain zero counts and `None` ASR.

Use typed keys, not display labels, when filtering:

- `VALUE` preserves real metadata, including blank strings and the literal
  `"Unknown"`. Blank display text is labeled `"(Blank)"`.
- `MISSING` is labeled `"Not recorded"` and is not a string sentinel.
- `NO_CONVERTERS` is labeled `"No converters"` and distinguishes a known empty
  pipeline from missing converter metadata.

Case-insensitive categorical keys use the reader's backend semantics. In SQLite,
the SQL function and SDK profiles both use Python `str.lower`, including Unicode.
They do not use ASCII-only lowercasing or `casefold`. Display labels retain original
spellings, choosing the binary-smallest label for equivalent keys.

Objective-target keys are persisted frozen-v1 behavioral evaluation hashes.
Deployment changes need not split behaviorally equivalent targets. The
`target_identifier_hash` in a result row is instead its exact content hash for
inspection. Neither key is recomputed from current registry state. Scenario keys
identify saved runs, not potentially repeated scenario names. The SDK adds short
identity suffixes to target/scenario display labels and forwards reader warnings.

## Freshness and projection limits

`query_async` reads overall counts, chart inputs, and the first result page in one
short consistent database view. The report and that page share `computed_at`.
Later `results_async` pages and `facets_async` lookups are separate fresh reads with
their own timestamps. Neither operation recalculates reports. No long-lived
snapshot freezes results between calls.

Result cursors are bound to cohort filters and distinct-result selection. Invalid
or stale cursors raise an explicit error. A facet excludes predicates only on its
exact dimension, retaining other label keys, pipeline directions, outcome filters,
and date restrictions so alternatives remain discoverable.

Defaults display 15 groups, 25 results, and 50 facet options. Groups are bounded
at 50, result/facet pages at 100, and each heatmap axis at 20. These are output
limits, not sampling: every matching saved result contributes to the statistics.
Updated-date filters are half-open intervals over last modification timestamps,
not attack execution-time trends.

## Execution ownership and shutdown

Reuse a long-lived SDK owner on one event loop, then await `close_async()` or exit
its async context before disposing or replacing memory. Do not create and close an
SDK context per request in an application with concurrent callers.

Facades using the **same memory object** share one controller, even if constructed
independently. This prevents multiplying that backend's capacity by constructing
more facades. A controller rejects use or shutdown from another event loop rather
than creating another pool. Separate memory objects and processes have separate
budgets; applications should share one initialized memory object per backend.

| Lane | Active operations | Additional queued calls | Queue wait | Execution budget |
|---|---:|---:|---:|---:|
| Reports | 5 | 10 | 1 second | 5 seconds |
| Result pages and facets, combined | 2 | 10 | 1 second | 1 second |

These defaults are overload safeguards, **not latency guarantees**. Queues are
FIFO. Full or expired admission raises `AnalyticsBusyException`; execution expiry
raises `AnalyticsTimeoutException`. Native async readers own the `QueryControl`
database deadlines, connection acquisition, interruption, and session restoration.
The SDK adds no second SQL timeout mechanism, sync DB facade, or event-loop thread.

Cancelling a queued call removes it without starting database work, including
cancellation racing with an admission grant.

Cancelling a caller or reaching its response deadline signals cooperative
cancellation. It does **not** release a still-running query's slot. Capacity
remains occupied until the operation and its session cleanup actually finish.
Closing rejects queued/new calls, signals active work, and waits for that cleanup,
even when it outlasts the response deadline. Cancelling `close_async` is propagated
only after draining; repeated cancellation cannot open an overlapping controller.

If the event loop cannot schedule an operation, the scheduling error propagates
and its unused capacity is released. If scheduling shutdown fails, admission
stays closed and `close_async()` can be retried. Unexpected operation failures
after a caller leaves are logged, including failures racing with cancellation.

Closing one bound facade closes the shared controller for every facade attached
to it. Those facades are terminal. Construct a new SDK owner only after closing
completes to begin another lifetime. Closing an unused facade does not affect
other facades. Memory engines remain caller-owned and are not disposed by the SDK.

There is no in-flight coalescing, access-scope parameter, or persistent result
cache. Every request has independent result objects and cancellation. The SDK is
not an authorization layer; applications must enforce access policy themselves.

## Bounded profile aggregation and backend limits

For eligible SQLite category, attack-type, and converter-type charts, memory
probes at most 4,097 pre-counted metadata profiles. It accepts at most 4,096,
subject to 4,096-character source limits and a 1,000,000-character combined-text
limit. The SDK expands their deduplicated memberships and sums saved-outcome
weights, yielding between bounded batches. Converter arrays already contain the
reader's canonical names, including supported legacy names and missing members.

Overflow selects the **complete SQL fallback**, never partial totals or sampling.
`profiles=None` means SQL supplied the chart; `profiles=[]` is a valid empty
cohort. The combined-text check happens after fetching the probe. These caps bound
transferred profiles and SDK work, not SQL scans, intermediate SQL work, or peak
network bytes.

SQLite reports preserve reader warnings about rollback journaling and never enable
WAL themselves. Choose journaling and deployment settings explicitly outside the
SDK. SQL Server uses the reader's SQL path and requires its configured SNAPSHOT
support. Cross-backend collation details, malformed historical metadata, and live
Azure SQL validation remain storage/deployment concerns.

Functional SDK tests use finite test-specific execution budgets while still
running the native reader, admission controller, and cleanup. They are correctness
checks, not assertions that a loaded CI worker can finish each read within the
production one-second quick-query budget. Separate execution tests assert the
unchanged default budgets and drive deadline/cleanup transitions with events.

Focused tests compare the SDK/profile path with the actual SQLite SQL fallback,
including Unicode, typed absence, duplicate/legacy memberships, and cap boundaries.
They also exercise weighted aggregation at the profile cap and deterministic
admission/cancellation/cleanup lifetimes. They are not a 100,000-row REST benchmark
or evidence of production latency. End-to-end application performance and runtime
integration require their own validation.
