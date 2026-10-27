---
applyTo: '**/tests/**'
---

# PyRIT Test Instructions

Readable, maintainable tests. Reuse helpers from `conftest.py` and `mocks.py` in each test tier.

## General Rules

- Do NOT add `@pytest.mark.asyncio` — `asyncio_mode = "auto"` is configured project-wide so all async tests are discovered automatically.
- Use `AsyncMock` for async methods, `MagicMock` for sync.
- When running a full test pass, use `make unit-test` rather than invoking `pytest` directly on `tests/unit/`. It's significantly faster because it runs in parallel (`pytest -n 4`).

## Test Tiers

Most tests should be unit tests. Integration and end-to-end tests are for testing that systems work toegether.

- **Unit** (`tests/unit/`): Mock all external dependencies. Fast, parallel (`pytest -n 4`). Run: `make unit-test`
- **Integration** (`tests/integration/`): Real APIs, real credentials. Requires `RUN_ALL_TESTS=true`. Sequential. Run: `make integration-test`
- **End-to-End** (`tests/end_to_end/`): Full scenarios via `pyrit_scan` CLI, no mocking, very slow. Run: `make end-to-end-test`

## Unit Test Rules

- Directory mirrors `pyrit/` (e.g. `pyrit/converter/` → `tests/unit/converter/`)
- File naming: `test_[component].py`
- Group tests in classes prefixed with `Test`
- Use `@pytest.mark.usefixtures("patch_central_database")` on classes touching Central Memory
- Reuse `tests/unit/mocks.py` helpers: `MockPromptTarget`, `get_sample_conversations`, `get_mock_target_identifier`, `openai_chat_response_json_dict`
- Key memory fixtures from `tests/unit/conftest.py`:
  - Use `patch_central_database` for tests that access `CentralMemory` or construct targets, scorers, or attacks. Declare it explicitly even in constructor, validation, or identifier tests that do not directly query memory.
  - Use `sqlite_instance` when the test needs to interact with the real isolated SQLite backend.
  - Do not rely on memory or singleton state left behind by another test. Each test must declare its own memory dependency.
- When changing shared memory fixtures, install all optional dependencies with `uv sync --extra all` and run `make unit-test`. Optional component tests must also exercise fixture isolation.
- No network calls should ever happen in unit tests, but file access is okay
- Scenario unit tests automatically enable the shared `block_dataset_provider_lookup` fixture from
  `tests/unit/conftest.py`. Scenario callers in other directories must opt into this fixture too.
  It blocks unexpected provider lookup and discovery, including direct bulk fetches.
  Mocking seed resolution alone does not isolate initialization, which prepares datasets first.
  Use inline seeds or the opt-in `mock_dataset_preparation` fixture for mocked populations.
  Tests exercising preparation must explicitly patch lookup with fake providers; local Garak
  corpus tests can use `mock_garak_dataset_fetch`. Do not disable the shared guard.
- Unit tests should be fast and can be run in parallel.

## Correctness Test Timeouts

- For unit-test subprocesses whose timeout only prevents a hang, use `TEST_WATCHDOG_SECONDS` from `unit.async_utils` rather than a short wall-clock deadline. The shared watchdog allows for startup, filesystem, and scheduling delays on busy CI workers; it is not an expected runtime.
- Use the same watchdog for thread-barrier rendezvous that test concurrency correctness, not worker startup speed. Keep the barrier and assertions that require distinct workers.
- Preserve intentional performance limits, operation deadlines, timeout/cancellation assertions, and JavaScript VM execution limits. Do not increase them merely to make a test pass.
- For a single `node:test` suite that does not need child-process isolation, run `node --test-reporter=tap <suite>` without `--test` to avoid an extra Node process. Preserve fresh subprocesses where isolation is part of the test contract, such as import guards.
- Still require a successful process exit and, for Node suites, a positive collected-test count with every collected test passing. A longer watchdog must not hide failures, skipped tests, or an empty suite.

## Integration Test Rules

- Mark with `@pytest.mark.run_only_if_all_tests` (skipped unless `RUN_ALL_TESTS=true`)
- File naming: `test_[component]_integration.py`
- Has its own `conftest.py` (`azuresql_instance` fixture, `initialize_pyrit_async`) and `mocks.py`
- Minimize mocking — test real integrations

## What to Test

- **Init**: valid/invalid params, defaults, required-only vs all-optional
- **Core methods**: normal inputs, boundary conditions, return values, side effects
- **Errors**: invalid input, exception propagation, cleanup on failure

## Mocking & Style Preferences

- Use `unittest.mock.patch` / `patch.object` — not `monkeypatch`
- Prefer `patch.object(instance, "method", new_callable=AsyncMock)` over broad module-path patches
- Use `spec=ClassName` on `MagicMock` whenever the mocked object stands in for a real class that has a fixed attribute surface (Pydantic models, dataclasses, domain entities). Without `spec=`, manually assigned attributes (e.g. `mock.foo = []`) silently mask access to attributes that don't exist on the real class — a common cause of regressions when a field is renamed or removed.
- Use `side_effect` for sequences or raising exceptions
- For environment variables: `patch.dict("os.environ", {...})`
- Check calls with `assert_called_once()`, `.call_args`, or `.call_count` — avoid `assert_called_once_with` for complex args, prefer `.call_args` inspection

## Mapper / Serializer / DTO Tests

Code that maps domain models to DTOs (or vice versa) — e.g. `pyrit/backend/mappers/`, custom Pydantic serializers, anything translating persisted records into wire formats — is especially prone to silent breakage when a field is renamed or removed from the source model. Mock-only tests cannot catch this class of regression, because `mock.removed_field` keeps returning whatever you assigned to it (or a fresh `MagicMock`) regardless of the real model's shape.

For every mapper / serializer / DTO conversion:

- Include **at least one test that exercises the mapper with real domain instances** (real Pydantic models, real ORM rows, etc.) instead of `MagicMock`. Use the `sqlite_instance` fixture (with `@pytest.mark.usefixtures("patch_central_database")`) when persistence is part of the round-trip.
- It is fine to keep mock-based tests for fast coverage of branching logic. The real-object tests are a backstop against field-shape drift, not a replacement for the mock-based suite.
- When `MagicMock` is unavoidable for a mapper test, pass `spec=RealModelClass` so attempted access to a removed field raises `AttributeError` immediately.

## Scorer Contract Tests

For provider-response and metadata regressions, exercise the scorer's public entry point with realistic responses, real `Score` / `UnvalidatedScore` conversion, and `sqlite_instance`. Mock only external I/O, not parsing, validation, aggregation, or persistence. Read persisted scores using explicit score IDs or filters (`get_scores_async()` without filters deliberately returns no scores). Assert verdict/value, category, rationale, and metadata, including after wrapper forwarding or metadata mutation. Parser-only tests and generic model-construction tests do not cover this boundary.

## Test Structure Preferences

- **Standalone test functions preferred** over test classes (use classes only when `usefixtures` or grouping is needed)
- **Fixtures**: define at module level with `@pytest.fixture`, not as class methods. Use `yield` for setup/teardown
- **Test naming**: `test_<method_or_noun>_<scenario>` (e.g. `test_convert_async_default_settings`, `test_init_with_no_key_raises`)
- **Assertions**: use plain `assert` statements. Use `pytest.raises(ExceptionType, match="...")` for error cases
- **Test data**: define constants at module level, use `mocks.py` helpers, or inline small data. Don't create separate data files for unit tests
- **Parametrize**: use `@pytest.mark.parametrize` for data-driven tests with multiple inputs
