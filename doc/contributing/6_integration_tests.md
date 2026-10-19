# 6. Integration Tests

Integration testing is often optional, but is important for us to test interaction with other systems (and in our terminology this is also lumped with end-to-end tests). These tests are found in the `tests/integration` directory.

Unlike unit tests, these tests can use real secrets. To test locally, these secrets should be configured as usual and it will make use of your `.env`.

These are tested regularly by the PyRIT team but not necessarily run every pull request. Here are some general guidelines.

- Unit tests should test all scenarios and more is often better. Integration tests should be scoped and be careful to not run too long.
- Integration tests can sometimes test end-to-end. But almost always, these should target one scenario.

## LiteLLM loopback integration tests

`tests/integration/targets/test_litellm_trace_integration.py` checks the real
OpenAI and Anthropic LiteLLM adapters against a local HTTP server. It verifies
the actual outgoing trace and routing headers and response parsing. These tests
use a fake API key and isolated SQLite memory, not live provider credentials.
Like other integration tests, they are gated by `RUN_ALL_TESTS=true` and need the
optional `litellm` dependency.

Anthropic token counting requires the official
[`cl100k_base.tiktoken`](https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken)
data, which is not included in the tiktoken wheel. Obtain a local copy separately
before offline execution and point the integration fixture to it:

```powershell
$env:PYRIT_TEST_TIKTOKEN_ASSET = 'C:\test-inputs\cl100k_base.tiktoken'
$env:RUN_ALL_TESTS = 'true'
uv run -m pytest tests\integration\targets\test_litellm_trace_integration.py
```

On a POSIX shell:

```bash
PYRIT_TEST_TIKTOKEN_ASSET=/tmp/test-inputs/cl100k_base.tiktoken RUN_ALL_TESTS=true \
  uv run -m pytest tests/integration/targets/test_litellm_trace_integration.py
```

The fixture checks SHA-256
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`,
creates a private temporary tokenizer cache, and loads the real encoding without
relying on a warm process cache. It selects LiteLLM's bundled model-cost and
Anthropic-header metadata through supported local-only flags. It never downloads
data. Missing or corrupt tokenizer input is an explicit preparation error once
the tests are enabled, not a skipped test.

Keep the data outside Git. Do not disable offline guards or allow downloads
inside pytest. Ordinary unit runs need neither this asset nor the integration
environment variables.
