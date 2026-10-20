# Attack-result analytics

`AttackResultAnalytics` is an async Python SDK for saved attack outcomes. It reads
aggregate counts and lightweight metadata through the configured memory backend,
without loading conversations, messages, media, scores, or full `AttackResult`
objects. The backend exposes the same shared contracts through three POST
endpoints. Attack History also lists distinct result IDs; the analytics explorer
GUI is a separate changeset.

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
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFilter,
    AttackAnalyticsFilters,
    AttackAnalyticsQuery,
    AttackAnalyticsResultsQuery,
    AttackAnalyticsValue,
)

operation = (await CentralMemory.get_memory_instance().get_operations_async(name="operation-a"))[0]

async with AttackResultAnalytics() as analytics:
    report = await analytics.query_async(
        query=AttackAnalyticsQuery(
            filters=AttackAnalyticsFilters(
                dimensions=[
                    AttackAnalyticsFilter(
                        dimension=AttackAnalyticsDimension(name="operation"),
                        values=[AttackAnalyticsValue(value=str(operation.id))],
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

Activity saved with only an Operation name, not an Operation ID, uses
`AttackAnalyticsValue(kind="legacy_operation_name", value="...")`.

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

### Compare matched baseline and attack outcomes

For scenarios that saved a normalized run plan with explicit baseline and attack
groups, `compute_paired_outcome_statistics` shows what changed for the **same
logical seed groups**. Supply the two atomic-group IDs from
`scenario_result.metadata["run_plan"]["atomic_groups"]`; this deliberately
avoids guessing which baseline belongs to a particular attack configuration.

```python
from pyrit.analytics import compute_paired_outcome_statistics
from pyrit.models import ScenarioRunPlan, ScenarioRunPlanGroupKind

# scenario_result is an already loaded ScenarioResult containing hydrated AttackResults.
plan = ScenarioRunPlan.model_validate(scenario_result.metadata["run_plan"])
baseline = next(group for group in plan.atomic_groups if group.kind == ScenarioRunPlanGroupKind.BASELINE)
attack = next(group for group in plan.atomic_groups if group.kind == ScenarioRunPlanGroupKind.ATTACK)
# If several groups share a kind, choose the intended pair explicitly.
report = compute_paired_outcome_statistics(
    scenario_result,
    baseline_group_id=baseline.id,
    attack_group_id=attack.id,
)
print(report.failure_to_success, report.success_to_failure)
print(report.failure_to_failure, report.success_to_success)
print(report.baseline_success_rate, report.attack_success_rate, report.paired_difference)
print(report.decided_pairs, report.excluded_pairs)
```

The four transitions are **failure→success**, **success→failure**,
**failure→failure**, and **success→success**. The denominator for both rates
(`baseline_success_rate` and `attack_success_rate`) is `decided_pairs`:
seed-group IDs scheduled in **both** selected atomic groups whose latest
top-level baseline and attack outcomes are both success or failure. The
`paired_difference` is attack rate minus baseline rate over that same cohort,
in proportions (not percentage points); for example, `0.25` means 25
percentage points. If there are no decided pairs, all three values are `None`.
Unlike a subtraction of two overall success rates, the transition counts show
which outcomes changed, even when the marginal rates are identical.
For example, with four decided pairs (one of each transition), both success
rates are `0.5` and the paired difference is `0.0`, but one previously failed
objective succeeds and one previously successful objective fails.

The function selects the effective **latest top-level result** per
`(atomic_group_id, seed_group_id)`, ordering by timestamp then result ID as
`compute_scenario_statistics` does. Child results and superseded retries never
create extra pairs. `planned_pairs` covers the **intersection** of planned seed IDs;
`decided_pairs` is the decided shared subset, and
`excluded_pairs = planned_pairs - decided_pairs`. The
`unmatched_baseline_seeds` and `unmatched_attack_seeds` counts cover seed IDs
planned on one side only, and are not included in `excluded_pairs`.
For shared IDs, `missing_both`, `missing_baseline`,
and `missing_attack` count absent top-level completions (the latter two
are exclusive of `missing_both`). `baseline_errors`, `attack_errors`,
`baseline_undetermined`, and `attack_undetermined` count each side's latest
outcomes, including when the other side is absent or undecided. These reason
counts can overlap across sides, so do not sum them to reconstruct exclusions.
`unattributed_attempts` counts results recorded under the selected atomic
names that resolve to neither selected planned group.

Comparisons require one valid saved plan, distinct explicitly marked baseline
and attack groups, and overlapping logical seed-group IDs (unless both
groups are empty). Ambiguous group configurations, duplicated result IDs,
conflicting seed attribution, malformed attempt metadata, or incompatible
scoring evidence raise `ValueError` rather than silently pairing observations.
When scores are available, the comparison checks scorer identities and score
types both within pairs and across the decided cohort. Within each pair, it
also checks complete scoring expectations, including their conditions and any
optional scoring context, which may legitimately differ from the attack objective.
For compound outcomes, these checks follow all explicitly linked descendants,
including nested compounds. Missing child results or cyclic links are rejected;
descendants still do not add pairs or change the rate denominator.
The groups belong to the **same** ScenarioResult and share its scenario-level
target and scorer identity. When a run uses different per-group scorers,
persisted per-result scorer identities can reveal incompatibility; if scorer
identity, score type, or scoring expectation is absent, compatibility cannot be
fully verified. Different matched seed groups may use different scoring contexts
if each individual baseline/attack pair has the same expectation.
Older runs without a saved run plan and separately executed runs are not
supported. The reported difference is descriptive and does not establish that
the attack caused an outcome to change. No confidence interval is provided,
because small and constant paired samples need careful uncertainty treatment.

### Scenario producer accounting

Scenario counts also expose `producer_counts`, separating target-facing,
orchestration, and unknown-role attempts, errors, and persisted retries.
The overall producer totals include all saved attempts, including unmatched
configurations and superseded results. Atomic-name producer totals include all
configurations recorded with that name. Display groups use the resolved saved
plan or, for unmatched configurations, the saved `display_group_map`.

These populations do not change planned units, latest-attempt outcomes, or
success-rate denominators. Configuration-specific progress groups only include
attempts whose group attribution resolves unambiguously; `unattributed_attempts`
still identifies rows excluded from logical-unit counts. JSON and HTML reports
present the same producer counts. A target-facing preparation error is a producer
attempt, not proof of a physical target request. Unknown or malformed roles stay
in the unknown bucket rather than being inferred from conversation presence.

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
- `LEGACY_OPERATION_NAME` applies only to Operation. It holds the name stored on
  activity saved without an Operation ID and is labeled with the name followed by
  `"(legacy name)"`.

Case-insensitive categorical keys use the reader's backend semantics. In SQLite,
the SQL function and SDK profiles both use Python `str.lower`, including Unicode.
They do not use ASCII-only lowercasing or `casefold`. Display labels retain original
spellings, choosing the binary-smallest label for equivalent keys.
SQLite avoids a Python callback when byte/character lengths prove a text value
is ASCII and native `lower` is equivalent. Unicode, embedded NULs, and non-text
values retain the existing Python conversion and lowercasing.

Objective-target keys are persisted frozen-v1 behavioral evaluation hashes.
Deployment changes need not split behaviorally equivalent targets. Targets
without a model name, such as HTTP or Gandalf targets, are keyed by their
content hash instead, so different endpoints or levels stay separate. The
`target_identifier_hash` in a result row is instead its exact content hash for
inspection. Neither key is recomputed from current registry state. Scenario keys
identify saved runs, not potentially repeated scenario names. The SDK adds short
identity suffixes to target/scenario display labels and forwards reader warnings.
Whitespace-only model labels use the target class name and short identity suffix;
SQLite and SQL Server recognize the same Unicode whitespace as Python `str.strip`.

Operation keys are saved Operation IDs; labels show the Operation's current name,
so renaming an Operation never changes its key. Operation options list
`"Not recorded"` first, then saved Operations by name, then legacy names by name;
groups with equal counts follow the same order.

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

The read-only `has_active_work` property includes admitted work and cleanup after
a caller leaves. `is_closed` indicates that the facade is terminal and its attached
controller has drained. Neither property starts a controller or opens a session.
These are lifecycle observations, not substitutes for closing admission and
awaiting `close_async()` before memory replacement.

## REST and backend lifecycle

| POST endpoint | Shared request | Shared response |
|---|---|---|
| `/api/analytics/attacks/query` | `AttackAnalyticsQuery` | `AttackAnalyticsReport` |
| `/api/analytics/attacks/results` | `AttackAnalyticsResultsQuery` | `AttackAnalyticsResults` |
| `/api/analytics/attacks/facets` | `AttackAnalyticsFacetQuery` | `AttackAnalyticsFacets` |

The routes await the corresponding SDK method. They do not calculate statistics,
rebuild filters, hydrate attacks, or recalculate charts for pages/facets. Shared
Pydantic serialization preserves both `success_rate` and `success_rate_decided`,
including nested group and cell statistics. The report's first result page and
`computed_at` come from its coherent SDK read.

Business requests require the caller's packaged `PyRIT-Compatibility-ID` and the
deployment's normal authentication and authorization. Compare the caller's own
build identity with `/api/version`; never copy the backend's identity to make an
incompatible client appear compatible. Runtime readiness/admission, request IDs,
security headers, the 8,192-byte request-target cap, and the existing body-size
policy apply. Potentially large filter selections belong in POST bodies, not URLs.

```python
import asyncio

from pyrit import _compatibility
from pyrit.models import AttackAnalyticsReport

# client is an authenticated httpx.AsyncClient connected to the backend.
identity = await asyncio.to_thread(_compatibility.get_compatibility_id)
response = await client.post(
    "/api/analytics/attacks/query",
    headers={_compatibility.COMPATIBILITY_HEADER: identity},
    json={
        "group_by": {"name": "targeted_harm_category"},
        "compare_by": {"name": "attack_type"},
        "filters": {"outcomes": ["success", "failure"]},
    },
)
response.raise_for_status()
report = AttackAnalyticsReport.model_validate(response.json())
```

Malformed inputs return the standard 422 problem response. Invalid or mismatched
analytics cursors return 400, rather than silently restarting pagination.
Overload returns 503 with `Retry-After: 1`; execution deadlines return 504.
Stored-data failures and unexpected errors remain explicit 500 responses, never
successful empty reports. Outcome-filtered reports retain their ASR* annotation,
and legal full-budget reports retain `drilldown_unavailable_reason`.

The backend lazily caches a direct, long-lived `AttackResultAnalytics` facade.
Its async HTTP dependency resolves the synchronous cache on the runtime loop,
so simultaneous first requests do not construct competing threadpool owners.
There is no pass-through analytics service class, per-request SDK context, or
SDK authorization scope. The shared runtime treats analytics cleanup as active
work even after a timeout response. Live apply rejects busy work, closes admission
at its existing idle barrier, then drains services and invalidates the SDK cache
before replacing runtime state. Shutdown drains on the owning event loop before
disposing memory. Repeated shutdown cancellation cannot release that ownership.
If analytics closure fails before draining, its cached owner and memory are
retained, the failure is surfaced, and live apply requires a restart.

Attack History explicitly passes `AttackResultSelection.ALL_RESULTS` to
`get_attack_results_async`. Two saved result IDs sharing one conversation both
appear, including retries with different outcomes. History retains its filters,
recency/UUID ordering, SQL keyset pagination, and page-only conversation-stat
hydration. Its cursor fingerprint now includes selection mode; an old History
cursor restarts at the first page under History's existing fallback policy.
Analytics and History cursors are not interchangeable. Unrelated SDK callers
still default to `LATEST_PER_CONVERSATION`, and scenario latest-unit/history
statistics keep their separate population.

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

Profile SQL decodes each distinct raw membership array once, removes duplicate
members without changing their spellings, and sums weights of equivalent
profiles before applying the same caps. Every saved result still contributes
its full weight. For complete normalized attack/converter metadata, SQLite
checks completeness per identifier instead of per result; incomplete and
legacy rows retain their embedded metadata. Identifier decoding is shared only
within the query for an exact reference/document pair, so different embedded
documents under one missing or incomplete reference remain distinct.
Accepted profiles partition the complete cohort, allowing the reader to sum
their validated weights for raw outcome totals without a redundant scan. This
does not sum overlapping groups or cells; overflow still obtains complete SQL
totals. The report and its initial page retain one consistent read transaction.

Scalar membership filters use indexable set predicates. Scalar facets project
values once, then combine deduplication and ordering before paging instead of
sorting the same values twice. SQLite 3.35+ uses query-local materialized projections to
avoid repeated JSON decoding; older SQLite keeps semantically equivalent
unhinted queries. None of this changes storage, persisted identities, schema,
global settings, or response caching.

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
admission/cancellation/cleanup lifetimes. Backend tests separately exercise native
session cleanup, reinitialization, actual application routing/middleware, and
shared-model JSON. These correctness tests are not performance evidence.

## Reproducible REST workload

The full REST/SDK/SQL performance workload is an **integration test** that runs
by default with the integration suite. It uses only a temporary local SQLite
database, with no Azure SQL or external credentials. No opt-in marker or
environment variable is required.
Run the 100,000-result case from the repository root after other heavy checks
have finished:

```powershell
uv run pytest tests\integration\backend\test_attack_analytics_integration.py -q -s
```

The latency check is outside ordinary
unit runs, so unrelated CI load cannot turn a correctness check into a one-second
performance assertion. `tests/unit/backend/test_analytics_benchmark.py` retains a
smaller SQLite workload with mocked elapsed-time gates and a 120-second test
timeout. It retains real admission limits, cancellation, cleanup, HTTP requests,
and SQL queries, plus tests that reject wrong counts, missing continuations,
errors, and unmet thresholds. Both tiers
reuse the workload and isolated fixtures from the test-only `test_support`
package, which is not included in the PyRIT distribution. Neither suite imports
the other's `conftest`, and pytest retains its existing repository-root-only
Python path. There is no separate build script or benchmark CLI.

The workload's pytest fixtures own a new temporary directory, configuration, and file-backed
SQLite database built from the current schema. They scope CentralMemory using the
shared SQLite isolation helper and never load the user's configuration or
database, run deployment migrations, or change global database settings. WAL is
explicitly enabled only on the disposable database. The actual runtime starts
with local technique registration, then closes services and memory before the
temporary directory is removed. Compatibility identity and headers use the
shared backend test fixtures; no packaged development stamp is needed.
No server or frontend is started.
The integration suite's normal bootstrap still applies. For a local-only run
without discovering user dotenv files, the existing `PYTHON_DOTENV_DISABLED=1`
setting can be used; it does not control whether the test runs.

The deterministic data contains 100,000 result IDs, two per conversation, all
outcomes and result roles, skewed operations (one saved Operation holds 60%; the
rest use 100 legacy Operation names), 50
operators, 40 overlapping harm categories, 64 shared identifier configurations,
eight models, overlapping converter pairs, known empty pipelines, supported
legacy documents, missing identifiers, and 100,000 distinct case labels.
Duplicated memberships must not multiply counts. An independent native async
writer updates outcomes/recency and inserts IDs sharing an existing conversation.

Five independent HTTPX clients concurrently request both categorical report
shapes, high-cardinality label facets, first result pages, and actual
`next_cursor` continuations. Report shapes have separate distributions and gates.
No identity/request coalescing, row sampling, or deadline overrides are used.
Metadata profile limits remain transfer/work bounds; overflow still uses the
complete SQL fallback.

Timing includes request encoding, all production middleware (with external auth
disabled only within the test fixture), admission/queueing, the real SDK and SQL,
native session cleanup, shared response serialization, and client model
validation. It excludes schema/seeding, runtime startup/shutdown, external
authentication I/O, TCP/TLS/network, frontend/static serving, and final count
verification. "Cold" means the first request of each class after seeding/startup,
not flushed OS or SQLite caches.

The test prints its JSON report and saves it in pytest's cache at
`.pytest_cache\v\analytics\attack-results`. It records environment/package
versions, data shape, every measured
sample, first-request/cold time, nearest-rank p95 and maximum per class, request
success/error counts, concurrent callers, and committed writes. Final SQL and
REST outcome counts must agree with the exact seed/write ledger. Pages must have
distinct IDs, bounded output and correct recency/UUID order; continuations must
follow the preceding page's cursor and seek past its anchor.

The test fails for request/write errors, missing workload classes or
continuations, incorrect counts/output, or p95 above **2 seconds per report
shape** or **0.5 seconds for facets, first pages, or continuations**. Errors remain
in timing samples and cannot pass through successful-only sampling. The ordinary
smoke test is explicitly marked non-representative in its report and does not
establish the 100k/5-user performance gate. The integration case retains production SDK
budgets and always uses 100,000 results, five analysts, and six iterations.

### Recorded integration 100k gate

Measured on **2026-10-12 at 06:30 UTC**, using the integration pytest command above, on
`67d6ad89b5b1c1a31dc967fd83c4fa03ccec93e2` plus the query optimizations described above.
The backend and client used the existing test compatibility identity
`0.14.0+gaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`. Compatibility middleware
remained active; no external authentication or credentials were used. The
existing `PYTHON_DOTENV_DISABLED=1` setting disabled ambient dotenv discovery
during verification.

Environment: Windows 11 build 26200, 16 logical CPUs, AMD64 family 25/model 117;
Python 3.14.4, SQLite 3.50.4, FastAPI 0.142.2, Starlette 1.7.0, Pydantic 2.13.5,
SQLAlchemy 2.1.1, aiosqlite 0.22.1, and HTTPX 0.28.1. WAL applied only to the
disposable database. The data shape and timing inclusions/exclusions are those
described above. These recorded numbers predate the saved Operation in that data
shape: they were measured with every row using a legacy Operation name and with
a facet workload filtered by Operation name.

| Workload | Cold (s) | Concurrent p95 (s) | Maximum (s) | Successful / error requests |
|---|---:|---:|---:|---:|
| Harm category x attack type report | 0.412512 | 1.505472 | 1.519278 | 31 / 0 |
| Harm category x converter type report | 0.510630 | 1.513099 | 1.692439 | 31 / 0 |
| Case-label facets | 0.085879 | 0.456433 | 0.462182 | 31 / 0 |
| First result pages | 0.021311 | 0.284401 | 0.308339 | 31 / 0 |
| Actual cursor continuations | 0.023050 | 0.355649 | 0.377080 | 31 / 0 |

Each class has one cold request and 30 concurrent samples; p95/max refer to the
concurrent samples. Both final verification requests also succeeded, for **157
successful requests and zero errors** overall. The concurrent phase took
16.918652 seconds and reached five overlapping callers. All 107 write
transactions committed: 107 outcome updates and 53 inserts, with write p95
0.140424 seconds and maximum 0.360693 seconds. Final SQL and both REST reports
matched the exact **100,053-result** write ledger. Temporary databases were removed.
Pytest reported one passing performance test in 49.85 seconds including setup
and teardown, which are excluded from the latency samples.

The initial measurement before the query optimizations failed: report p95s were 2.985537/5.026765 seconds, facets
1.360661 seconds, first pages 0.773046 seconds, and continuations 0.225980 seconds,
with 20 HTTP 504 responses. SQL profiling identified repeated profile/fallback
work, repeated identifier/JSON interpretation, and Unicode callbacks on ASCII
text. A later retry without query changes still missed the limits, with report
p95s of 3.738381/2.388267 seconds and facets at 0.577069 seconds. The
reference/document projection, totals reuse, and facet sort changes addressed
that remaining redundant work before the recorded passing run. The data distribution,
concurrency, production budgets, profile caps, and latency thresholds were not
relaxed. These measurements establish this local workload result, not a latency
guarantee for other machines or deployments.
