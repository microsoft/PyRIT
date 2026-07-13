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
- Key fixtures from `tests/unit/conftest.py`: `patch_central_database`, `sqlite_instance`
- No network calls should ever happen in unit tests, but file access is okay
- Unit tests should be fast and can be run in parallel.

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

## Test Structure Preferences

- **Standalone test functions preferred** over test classes (use classes only when `usefixtures` or grouping is needed)
- **Fixtures**: define at module level with `@pytest.fixture`, not as class methods. Use `yield` for setup/teardown
- **Test naming**: `test_<method_or_noun>_<scenario>` (e.g. `test_convert_async_default_settings`, `test_init_with_no_key_raises`)
- **Assertions**: use plain `assert` statements. Use `pytest.raises(ExceptionType, match="...")` for error cases
- **Test data**: define constants at module level, use `mocks.py` helpers, or inline small data. Don't create separate data files for unit tests
- **Parametrize**: use `@pytest.mark.parametrize` for data-driven tests with multiple inputs
