# Claude chat target

`ClaudeChatTarget` uses LiteLLM's native Anthropic Messages transport, not an
OpenAI-compatible proxy. Install `pip install "pyrit[litellm]"` (also included in
`pyrit[all]`). Production Docker installs include this extra. A basic PyRIT install
can still import the target; sending without LiteLLM raises an installation error.
LiteLLM 1.108.0 or later is required so tool-based JSON adaptation preserves user
calls alongside its internal formatting calls.

Set `ANTHROPIC_API_KEY`, or pass `api_key` explicitly. Sync and async callables
returning a current token are also accepted and resolved for each completion.
`endpoint` and `headers` are per-target overrides; no global environment is changed.
The target ignores generic `LITELLM_API_KEY` and `LITELLM_ENDPOINT` settings.
Operational OAuth login/refresh and Key Vault registration are outside this target.

Raw names such as `claude-sonnet-4-6` and `anthropic/claude-sonnet-4-6` resolve to
the same route and identity. Other provider prefixes are rejected. Known Claude
4.5/4.6 aliases declare multi-turn, system prompts, editable history, text/image
inputs, tool history, and JSON output. Unknown names use LiteLLM metadata with a
conservative fallback. Use `custom_configuration` for verified deployment-specific
capabilities; it takes precedence. The known profiles do **not** promise strict
native JSON-schema output.

## Harmless async tool

Run this in an async application or notebook after configuring credentials:

```python
from pyrit.models import JsonResponseConfig, MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import ClaudeChatTarget, tool
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)


@tool
async def add_one_async(*, value: int) -> int:
    """Add one to a number."""
    return value + 1


target = ClaudeChatTarget(
    model_name="claude-sonnet-4-6",
    tools=[add_one_async],
    max_tool_rounds=2,
    max_tokens=512,
)
normalizer = PromptNormalizer()
reply = await normalizer.send_prompt_async(
    target=target,
    conversation_id="claude-example",
    message=MessagePiece(
        role="user",
        original_value="Use add_one_async for 7, then return a JSON object with the answer.",
        prompt_metadata=JsonResponseConfig(enabled=True).to_metadata(),
    ).to_message(),
)
print(reply.get_value())

follow_up = await normalizer.send_prompt_async(
    target=target,
    conversation_id="claude-example",
    message=MessagePiece(role="user", original_value="What was the result? Do not call tools again.").to_message(),
)
```

`tools` accepts typed `Tool` instances; `tool_providers` discovers tools within
each provider's execution scope. `@tool` derives a schema from the async
function's typed keyword parameters. Automatic execution requires `n=1`.
`execute_tools=False` advertises tools but returns assistant calls without
executing them. Declaring wire tools only through `extra_body_parameters` does
not install executable implementations.

The inherited LiteLLM loop handles multiple calls per completion and multiple
rounds. `max_tool_rounds` defaults to 10; zero advertises tools without executing
a returned batch. Each round counts one completion with user-tool calls, not
each individual call or the terminal JSON formatter.

For unconstrained JSON-object requests, the target advertises a reserved internal
formatting tool, `pyrit_claude_json_output`, and converts its arguments to response
text. It is never locally executed or counted against the tool budget. With user
tools present, Anthropic can choose those tools before formatting the final answer.
Explicit JSON schemas use the shared configuration/normalization path and
LiteLLM's Anthropic adaptation. Never register the reserved formatter as a user tool.

Unknown tools, invalid arguments, and execution/serialization failures produce
matching tool-result error payloads. Reused/missing call IDs and truncated calls
stop execution with an explicit processing-error response. Calls beyond the
round budget receive unexecuted error results and a terminal processing error.
Continuation failures retain completed calls/results plus an error response;
the entire send is not retried, so completed side effects are not repeated.
Cancellation propagates and scoped provider resources are closed.

Targets return all interaction messages; `PromptNormalizer` persists them in
order and returns the last response. Later sends through the same conversation
replay stored user calls/results. Invalid/truncated calls marked as processing
errors are not replayed. Do not blindly resubmit a cancelled interaction whose
external effects may already have occurred.

## Opt-in integration coverage

The unit tests mock external HTTP only and exercise the actual LiteLLM Anthropic
adapter, including system/image requests, tools, JSON, and isolated-memory replay.
Live tests require both `RUN_ALL_TESTS=true` and `RUN_ANTHROPIC_TESTS=true`, plus
an explicitly authorized `ANTHROPIC_API_KEY`. These tests make billable API calls;
`ANTHROPIC_MODEL` can override the default `claude-sonnet-4-6`.

```text
uv run --frozen --extra litellm -m pytest tests/integration/targets/test_claude_chat_target_integration.py
```
