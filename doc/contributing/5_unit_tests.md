# 5. Unit Tests

All new functionality should have unit test coverage. These are found in the `tests/unit` directory.

Testing is an art to get right! But here are some best practices in terms of unit testing in PyRIT, and some potential concepts to familiarize yourself with as you're writing these tests.

- Make a test that checks one thing and one thing only.
- Use `fixtures` generally, and specifically, if you're using something across classes, use `unit.mocks` or `integration.mocks`.
- Memory isolation: Use `sqlite_instance` for a real, isolated SQLite database, or `patch_central_database` when patching CentralMemory access.
- Code coverage and functionality should be checked with unit tests. Notebooks and integration tests should not be relied on for coverage.
- `MagicMock` and `AsyncMock`: these are the preferred way to mock calls.
- `with patch` is acceptable to patch external calls.
- Don't write to the actual database, use a `MagicMock` for the memory object or use `patch_central_database` as the database connection.


Not all of our current tests follow these practices (we're working on it!) But for some good examples, see [test_tts_send_prompt_file_save_async](../../tests/unit/prompt_target/target/test_tts_target.py), which has many of these best practices incorporated in the test.

## SQLite memory fixtures

`sqlite_instance` stays function-scoped. Each test gets a fresh in-memory database and results directory, and its SQLite singleton and CentralMemory registrations are restored afterward. The fixture owns disposal of its memory instance instead of registering process-exit cleanup callbacks for every test.

Declare the memory fixture explicitly even in constructor or identity tests that create targets, scorers, or attacks. Do not rely on another test leaving CentralMemory initialized.

To avoid replaying the migration history for every ordinary test, `sqlite_template` runs the real Alembic migrations and schema check once per pytest session (once in each xdist worker). SQLite's backup API copies that private, read-only template into each test's database. Rows, schema changes, temporary tables, and result files are not shared between tests.

Tests of initialization, upgrades, downgrades, schema checks, and `reset_database()` must still call those production paths explicitly. The fixture does not replace or patch migration or reset APIs.
