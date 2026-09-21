# 4. Running Tests

Testing plays a crucial role in PyRIT development. Ensuring robust tests in PyRIT is crucial for verifying that functionalities are implemented correctly and for preventing unintended alterations to these functionalities when changes are made to PyRIT.

For running PyRIT tests, you need to have `pytest` package installed, but if you've already set up your development environment with
`uv sync`, `pytest` should be included in that setup.


## Running PyRIT test files
PyRIT test files can be run using `pytest`.

  * You can invoke pytest if it's in your path or via python; either `pytest` or `python -m pytest`. For the following examples, we will use `pytest`.

  * To run all tests (both unit and integration), you can pass a directory:

      ```
      pytest tests
      ```

  * To run all unit tests you also can pass the unit test directory:

      ```
      pytest tests/unit
      ```

  * To run tests from a specific file (e.g. test_aml_online_endpoint.py), from the PyRIT directory, use:

     ```bash
     pytest tests\test_aml_online_endpoint_chat.py
     ```

  * To execute a specific test (`test_get_headers_with_empty_api_key`) within the test module(`test_aml_online_endpoint.py`),
     ```bash
     pytest tests\test_aml_online_endpoint_chat.py::test_get_headers_with_empty_api_key
     ```

## Coverage checks

`make unit-test-cov-xml` runs unit tests and enforces 78% overall coverage.
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
