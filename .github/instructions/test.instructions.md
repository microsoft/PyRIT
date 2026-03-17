---
applyTo: '**/tests/**'
---

# PyRIT Test Instructions

Readable, maintainable tests. Reuse helpers from `conftest.py` and `mocks.py` in each test tier.

## Test Tiers

Most tests should be unit tests. Integration and end-to-end tests are for testing that systems work toegether.

- **Unit** (`tests/unit/`): Mock all external dependencies. Fast, parallel (`pytest -n 4`). Run: `make unit-test`
- **Integration** (`tests/integration/`): Real APIs, real credentials. Requires `RUN_ALL_TESTS=true`. Sequential. Run: `make integration-test`
- **End-to-End** (`tests/end_to_end/`): Full scenarios via `pyrit_scan` CLI, no mocking, very slow. Run: `make end-to-end-test`

## Unit Test Rules

- Directory mirrors `pyrit/` (e.g. `pyrit/prompt_converter/` → `tests/unit/converter/`)
- File naming: `test_[component].py`
- Group tests in classes prefixed with `Test`
- Use `@pytest.mark.usefixtures("patch_central_database")` on classes touching Central Memory
- Use `@pytest.mark.asyncio` and `AsyncMock` for async methods
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
- Use `AsyncMock` directly for async methods, `MagicMock` for sync
- Use `spec=ClassName` on mocks when you need to constrain to a real interface
- Use `side_effect` for sequences or raising exceptions
- For environment variables: `patch.dict("os.environ", {...})`
- Check calls with `assert_called_once()`, `.call_args`, or `.call_count` — avoid `assert_called_once_with` for complex args, prefer `.call_args` inspection

## Test Structure Preferences

- **Standalone test functions preferred** over test classes (use classes only when `usefixtures` or grouping is needed)
- **Fixtures**: define at module level with `@pytest.fixture`, not as class methods. Use `yield` for setup/teardown
- **Test naming**: `test_<method_or_noun>_<scenario>` (e.g. `test_convert_async_default_settings`, `test_init_with_no_key_raises`)
- **Assertions**: use plain `assert` statements. Use `pytest.raises(ExceptionType, match="...")` for error cases
- **Test data**: define constants at module level, use `mocks.py` helpers, or inline small data. Don't create separate data files for unit tests
- **Parametrize**: use `@pytest.mark.parametrize` for data-driven tests with multiple inputs
