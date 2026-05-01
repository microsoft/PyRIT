# 5. Unit Tests

All new functionality should have unit test coverage. These are found in the `tests/unit` directory.

Testing is an art to get right! But here are some best practices in terms of unit testing in PyRIT, and some potential concepts to familiarize yourself with as you're writing these tests.

- Make a test that checks one thing and one thing only.
- Use `fixtures` generally, and specifically, if you're using something across classes, use `unit.mocks` or `integration.mocks`.
- Memory isolation: Use the `patch_central_database` fixture for test database isolation and reset.
- Code coverage and functionality should be checked with unit tests. Notebooks and integration tests should not be relied on for coverage.
- `MagicMock` and `AsyncMock`: these are the preferred way to mock calls.
- `with patch` is acceptable to patch external calls.
- Don't write to the actual database, use a `MagicMock` for the memory object or use `patch_central_database` as the database connection.


Not all of our current tests follow these practices (we're working on it!) But for some good examples, see [test_tts_send_prompt_file_save_async](../../tests/unit/target/test_tts_target.py), which has many of these best practices incorporated in the test.
