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

## Running a subset while iterating

For a narrower run, invoke `pytest` directly. You can invoke pytest if it's in your path or via python; either `pytest` or `python -m pytest`. For the following examples, we will use `pytest`.

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
