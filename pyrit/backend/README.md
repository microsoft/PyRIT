# PyRIT Backend

FastAPI-based REST API for PyRIT.

## Quick Start

### Run the Server

```bash
# Development server with auto-reload
python -m pyrit.backend.main

# Or with uvicorn directly
uvicorn pyrit.backend.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

### API Documentation

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## API Endpoints

### Health & Status
- `GET /api/health` - Health check
- `GET /api/version` - Version information

### Targets
- `GET /api/targets` - List available prompt targets
- `GET /api/targets/{id}` - Get target details

### Techniques

| Endpoint | Result |
| --- | --- |
| `GET /api/techniques` | All active registered technique factories |
| `GET /api/techniques/types` | Attack-class parameters and supported converter/adversarial controls |
| `GET /api/techniques/{name}` | Safe settings for one factory; 404 if not registered |
| `POST /api/techniques` | Validate and register a basic runtime technique; 201 on success |

Use a class name from `AttackRegistry` as `type`, with `name` and constructor
`params`. Optional fields are `description`, `tags`, ordered
`request_converters` and `response_converters` names, an `adversarial_chat`
target name, and inline `adversarial_system_prompt`, `adversarial_seed_prompt`,
and `adversarial_prompt_template` strings. References use existing registries.
Seeds, conversation settings, and advanced attack configurations remain
Python-only.

Creation does not run an attack. The scenario supplies the objective target and
scoring configuration; an omitted adversarial target uses the execution default.
Invalid settings or selector collisions return 400 without registering a factory;
invalid request shapes return 422. `all` and `default` are reserved selectors;
`types` is also a reserved name.

Additions are runtime-only and available in compatible scenarios. Restart or
reinitialize PyRIT to remove them. Existing scenario snapshots and runs do not
change. List and detail responses include Python-created factories without
disclosing target credentials.
Scenario-local factories take precedence over registered factories of the same name.
`AttackTechniqueRegistry.cache_scenario_technique_class` refreshes scenario selection
enums when registered factories change; it does not create attacks.
Responses also include `evaluation_identifier`: the registered factory's identity
with its evaluation hash, not the final attack or scenario run identity.

### Manual Messages

`POST /api/attacks/{id}/messages` remains synchronous: it waits for the send and returns
the existing attack and conversation views. `send=false` appends context without
dispatching, using any supported message role. `MessageSendService` owns preparation,
converter selection, dispatch through `PromptNormalizer`, and attack metadata updates.
The normalizer still owns request/response conversion and persistence; `AttackService`
maps the resulting stored data to the response, including stored target errors.
All three use the application's `CentralMemory` instance. Both message endpoints use
the same cached `MessageSendService`; the synchronous facade awaits its execution core
directly, while the submission endpoint schedules that core and returns a transient handle.

All manual-message service instances share one process-local scheduler. It admits up to
64 operations (active and waiting), with a semaphore allowing up to 4 to execute at once.
There is no cross-conversation FIFO guarantee; each conversation still has a single owner.
Targets retain their own per-target request pacing, including targets used by converters.
Only simultaneous conversion using the same converter instance is serialized. The guard
covers each actual conversion, not the send to the message target, other converter instances,
or skipped pieces.
These limits do not coordinate other backend processes, scenario runs, or converter previews.

Metadata read/merge/write operations are serialized per attack, including ordinary sends
to different conversations. Provider calls can still run concurrently; an older metadata
write cannot overwrite a newer response pointer, timestamp, or converter history.

A synchronous operation owns its conversation through attack-summary and conversation-response
assembly, or until failure or cancellation cleanup completes. Memory calls use the native
async methods. Final writes are shielded and joined before ownership is released, including
when cancellation arrives repeatedly; the metadata guard covers the full async read/merge/write.
Concurrent sends or `send=false` appends to the same conversation receive **409**;
exceeding the admission limit receives **429**. Neither response starts a send or appends
a message.

### Asynchronous Manual Messages

`POST /api/attacks/{id}/message-sends` accepts the same send fields plus a required
`submission_id` (1-128 characters), and returns **202** after validation and admission,
without waiting for media preparation, conversion, or target I/O. It sends exactly
one message to one conversation. `send=false` remains available only on `/messages`.
Both APIs use the same sending core and share the existing admission and execution budgets.
An attack saved without a target is bound before its first send through either API.
Binding validates every saved conversation and rejects history changes during validation.
An asynchronous operation retains its reservation through finalization or cancellation cleanup;
subsequent transcript and attack-detail reads are independent of that reservation.

`GET /api/attacks/{id}/message-sends/{send_id}` returns compact progress:
`queued`, `preparing`, `sending`, `finalizing`, `completed`, `failed`, or `interrupted`.
Failures carry an explicit `failure_stage`: `preparation`, `sending`, `finalization`,
or `interrupted`. Preparation includes target-side normalization and request validation.
A backend-owned `TargetSendContext` leaves persisted history unchanged and marks `sending`
at `mark_target_invoked()`, immediately before target-specific execution. Sending includes
provider I/O, response conversion, and persistence; this transition does not confirm delivery.
Finalization updates attack metadata. Stages are not inferred from stored message counts,
and no stage guarantees that retrying delivery is safe. Stored target errors and
`target_response_status` remain available through the ordinary conversation API.
Terminal states are published only after finalization and ownership release. Progress also
includes the `request_turn_number` assigned during preparation, so clients can distinguish
this send's response from a later turn written by another client.

The optional `wait_ms` query parameter (0-1000) waits for completion, returning immediately
if the operation settles. Cancelling this read, disconnecting, or navigating away does not
cancel an accepted send. Clients should keep at most one status read in flight and should
not add a fixed delay after a completed read. After completion, fetch the ordinary attack
and conversation views; a failed read can be refreshed without submitting another message.

Progress and submission deduplication are **worker-local and transient**, not a durable
job queue or an exactly-once delivery guarantee. Active handles are bounded by admission;
at most 128 terminal handles are retained for up to 10 minutes (expired entries are purged
on submission, status reads, and completion). Reusing a retained `submission_id` for the
same attack and identical payload returns the existing handle; different payloads return
409. Message-piece and converter order are significant. A missing/expired handle returns
404, including after a restart or a request reaching another worker. Use worker affinity
when running multiple workers. Missing progress never authorizes an automatic resubmission.

Shutdown stops admission, cancels accepted operations, and joins unavoidable async
writes before releasing conversation ownership and clearing loop-bound caches. Interrupted
delivery can be uncertain: refresh saved evidence instead of automatically resending.
Live runtime reinitialization treats accepted manual sends as active work even after
their submission requests have returned. It rejects replacement until they settle,
then clears the sender and scheduler caches along with the other runtime services.
The chat preserves failed drafts across replacement, but requires converter choices
to be reviewed again rather than restoring outputs tied to the previous registry.

The offline browser fixture `frontend.e2e.fixtures.manual_send_backend:app` uses the real
backend lifecycle with isolated in-memory SQLite, no environment files, and no default
providers. The recovery tests register deterministic loopback targets. For example, start
it with `uv run python -m uvicorn frontend.e2e.fixtures.manual_send_backend:app --host 127.0.0.1 --port 18213`
(set `PYTHONUTF8=1` on Windows). In another shell, set `PYRIT_BACKEND_URL=http://127.0.0.1:18213`
and `E2E_FRONTEND_PORT=31213`, then run `npx playwright test chat-recovery --project seeded --workers 1`
from `frontend`. Set `PYRIT_PYTHON` to this worktree's Python interpreter if Vite cannot
find `python` (for example, `<worktree>\.venv\Scripts\python.exe` on Windows).
Stop these test-owned servers after the run.

### Request Limits

The backend reads at most 100 MiB of a request body; larger bodies receive **413**, and
API requests with URLs over 8 KiB receive **414**. Identifiers, names, labels, filters,
cursors, configuration files, and initializer scripts also have length or item limits,
listed in the OpenAPI schema; values over a limit receive **422**. Prompt content (message
pieces, system prompts, and converter preview input) and free-form values (prompt
metadata, scenario and initializer arguments, and target and converter parameter values)
are limited only by the body size, so long prompts and base64 media keep working.

## Strict Lockstep Compatibility

The backend, CLI, and frontend bundle use one stamped identity:
`<Python package version>+g<full source commit>`. The package `version` remains
unchanged. Different source commits with the same package version are incompatible.

Backend startup fails before initialization if provenance is missing or malformed.
In a source checkout run `python -m build_scripts.stamp_compatibility --development`
after switching commits, then restart the backend and frontend development server.

After authentication, request `GET /api/version` and compare its `compatibility_id`
with the caller's own build stamp. This endpoint stays authenticated because its
existing fields include database and label metadata. Missing/malformed metadata
blocks startup. Send `PyRIT-Compatibility-ID` on every business request, including
`/api/auth/access`. Do not adopt the backend's identity as the client's identity.

The backend rejects requests before business handlers and state-changing dependencies:

| Condition | Status | Stable problem `type` |
| --- | --- | --- |
| Missing, malformed, or duplicate header | 400 | `urn:pyrit:compatibility:invalid` |
| Valid identity differs from backend | 409 | `urn:pyrit:compatibility:mismatch` |

Problem responses use `application/problem+json` and include `expected` (backend),
`actual` (caller or null), `status`, `title`, and `detail`. On failure, stop further
business requests and explain which matching artifacts are needed. Do not replay
mutations. Backend replacement is detected by the next request even after a successful
startup handshake. Health, authentication discovery, version, and media retain their
existing authentication behavior and bypass only compatibility enforcement.

Example using the packaged CLI client (authentication and handshake are automatic):

```python
import asyncio

from pyrit.cli.api_client import PyRITApiClient


async def list_scenarios():
    async with PyRITApiClient(base_url="http://127.0.0.1:8000", auth_mode="auto") as client:
        print(await client.list_scenarios_async())


asyncio.run(list_scenarios())
```

For raw HTTP tooling, obtain the local marker with
`python -c "from pyrit._compatibility import get_compatibility_id; print(get_compatibility_id())"`,
authenticate and compare `/api/version`, then pass that local marker as the header.
The development Swagger UI's **Try it out** exposes this required header on each
business operation. Enter the same local marker there; neutral operations do not
require it. Swagger does not perform the compatibility handshake for you.
Launcher health checks use `/api/health` independently of the gated client lifecycle.

Commit equality cannot distinguish uncommitted edits or dependency differences.
An older, pre-enforcement backend can ignore the header. **Never roll back below the
first guarded release while lockstep clients remain active.** Matching frontend,
CLI wheel, and backend must be available before enabling enforcement in deployment.

## Configuration

Environment variables:
- `PYRIT_API_HOST` - Host to bind to (default: localhost)
- `PYRIT_API_PORT` - Port to listen on (default: 8000)
- `PYRIT_API_RELOAD` - Enable auto-reload (default: false)
