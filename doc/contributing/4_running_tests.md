# 4. Running Tests

Testing plays a crucial role in PyRIT development. Ensuring robust tests in PyRIT is crucial for verifying that functionalities are implemented correctly and for preventing unintended alterations to these functionalities when changes are made to PyRIT.

For running PyRIT tests, you need to have `pytest` package installed, but if you've already set up your development environment with
`uv sync`, `pytest` should be included in that setup.


## Running the unit tests

Unit tests are the tier you will run most often. Run the full suite through `make` rather
than invoking `pytest` directly on `tests/unit`. The target runs in parallel
(`pytest -n 4 --dist=loadfile`), which is several times faster than a serial run:

```bash
make unit-test
```

```{note}
`make` is not part of the [local dev setup](../getting_started/install_local_dev.md) and is
often missing on Windows. If you get `make: command not found` (or
`'make' is not recognized`), run the equivalent command directly — this is exactly what the
target expands to:

    uv run -m pytest -n 4 --dist=loadfile tests/unit

The same substitution works for the other targets on this page.
```

`make unit-test-junit` also writes per-test timings to `junit/test-results.xml` and
prints the 25 slowest test phases. Override the report path with
`make unit-test-junit JUNIT_XML=junit/custom-results.xml`.
CI uploads JUnit reports for every OS, Python version, and extras combination,
including failed test runs, with those dimensions in each artifact name.

LiteLLM unit tests mock the SDK and do not need tokenizer data or a local HTTP
server, including when the `litellm` or `all` extra is installed. Real adapter
and outgoing-header checks run separately as
[loopback integration tests](./6_integration_tests.md#litellm-loopback-integration-tests).

### CodeQL merge-group uploads

CodeQL still analyzes all three languages on PRs, merge groups, main/release pushes,
manual runs and its weekly schedule. Merge groups publish the analyzer's prepared
SARIF through the documented code-scanning API so an expired queue ref can be
distinguished from a genuine upload failure. Categories and fingerprints are
preserved; failed analysis retains CodeQL's native diagnostic upload.
The merge-group helper uses the authenticated client and context supplied by the
pinned `actions/github-script` action, with no separate HTTP client or Python setup.

Only a successful matching-refs response proving that the exact queue ref is absent
or points to another SHA permits retirement. A missing-ref upload rejection is
rechecked to cover deletion between verification and upload. Authentication,
service, processing and analysis failures stay failures. Retirement cancels the
verified current CodeQL run, never reports a skipped scan as success, and refuses
cancellation when a matrix job has already failed. The CodeQL job needs
`actions: write` for self-cancellation; workflow-wide permissions remain empty.
Fork PRs use native CodeQL publishing and GitHub's read-only fork token policy.

Run the deterministic, offline regression coverage with Node.js installed.
The pytest entry point runs the Node scenario tests and checks workflow wiring:

```bash
uv run --frozen pytest tests/unit/build_scripts/test_codeql_merge_group.py -q
```

The helper's scenarios can also run directly with
`node --test tests/unit/build_scripts/test_codeql_merge_group.cjs`.

## Running a subset while iterating

For a narrower run, invoke `pytest` directly. You can invoke pytest if it's in your path or via python; either `pytest` or `python -m pytest`. For the following examples, we will use `pytest`.

Correctness tests using the `analytics` fixture in `tests/unit/analytics/` or the
`analytics_service` fixture in `tests/unit/backend/` use finite 300-second query
budgets to allow for SQL compilation and scheduling delays on busy
CI workers. These are deadlock backstops, not expected runtimes. Production deadlines
are unchanged. Deadline and cancellation tests use explicit controls and keep their
separate timing assertions.

Async correctness tests can use `TEST_WATCHDOG_SECONDS` from `tests/unit/async_utils.py`
for a finite 300-second deadlock backstop. Tests that start background tasks must
release any test-controlled waits and join their tasks before removing mocks or
disposing fixtures, including when a watchdog expires. When waiting for a task to
signal readiness, also watch for that task finishing so an early exception is not
hidden by the readiness timeout.

The discovery provider-state test and Copilot cancellation-test startup use the
shared `TEST_WATCHDOG_SECONDS` backstop in `tests/unit/async_utils.py`. Discovery
timeout coverage expires the real cancellation path only after the request starts.
Event-controlled analytics expiry tests freeze both deadline clocks so floating-point
rounding cannot turn a valid query budget into a failed assertion.

The scorer-validation cancellation test keeps its five-second observer around
scorer draining and native SQLite connection cleanup. ERROR-result persistence is
held behind an event until those assertions pass, then awaited separately with the
finite completion watchdog. The blocked validation read is not released to make
either step finish.

For scenario correctness tests, wait for the owned task to finish, including
persistence and scheduler handoff. Use events to control concurrent steps and a
finite test watchdog to detect deadlocks, not short per-step latency limits.
Catalog queue-admission and configured-estimate independence tests use the existing
estimate watchdog fixture. Observe the queued semaphore acquisition before checking
that its execution deadline has not started, and verify configured completion while
catalog capacity is still occupied. Check each deadline against completed admissions
and spy on catalog acquisition during configured work to assert independence directly.
Release gates and drain owned requests and service workers in `finally`, including
when a readiness assertion fails.
For frontend modal tests, use fake timers and wait for an accessible control on
the destination page. A route change does not mean that modal cleanup has restored
background accessibility.

  * To run every test under a subdirectory:

      ```bash
      pytest tests/unit/converter
      ```

  * To run tests from a specific file (e.g. `test_base64_converter.py`), from the PyRIT directory, use:

     ```bash
     pytest tests/unit/converter/test_base64_converter.py
     ```

  * To execute a specific test (`test_base64_converter_default`) within the test module (`test_base64_converter.py`),

     ```bash
     pytest tests/unit/converter/test_base64_converter.py::test_base64_converter_default
     ```

## Running the other test tiers

```{note}
Avoid `pytest tests`. It collects the integration, end-to-end, and partner-integration tiers
alongside the unit tests. The end-to-end tier is not gated behind `RUN_ALL_TESTS`, and its
session fixture starts a real `pyrit_backend` process and drives scenarios against live
endpoints.
```

Run those tiers deliberately, through their own targets:

```bash
make integration-test
make end-to-end-test
make partner-integration-test
```

Integration tests additionally require `RUN_ALL_TESTS=true` and real credentials. See
[Unit Tests](./5_unit_tests.md) and [Integration Tests](./6_integration_tests.md) for details.

## Coverage checks

`make unit-test-cov-xml` runs unit tests, enforces 78% overall coverage, and produces
the same JUnit timing report.
`make unit-test-diff-cover` checks an existing `coverage.xml` and requires at least
90% coverage on changed executable lines. `make diff-cover` runs both checks.

Local diff coverage defaults to a two-dot comparison against `origin/main`.
Override the baseline with `make unit-test-diff-cover DIFF_COVER_BASE=<revision>`
(or the same variable with `make diff-cover`).

For pull requests targeting `main`, CI tests GitHub's PR merge commit and sets
`DIFF_COVER_BASE=HEAD^1`, its first parent. This compares the tested merge against
the exact target-branch revision it was built from, rather than a moving
`origin/main`. Full checkout history supplies that parent, including for fork PRs;
no fetch of the contributor's branch is needed. This baseline assumes the default
PR merge checkout, not an explicit checkout of the PR head. Push, merge-group,
and manually dispatched runs still enforce overall coverage only.
