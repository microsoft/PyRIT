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

### Manual Messages

`POST /api/attacks/{id}/messages` remains synchronous: it waits for the send and returns
the existing attack and conversation views. `send=false` appends context without
dispatching, using any supported message role. `MessageSendService` owns preparation,
converter selection, dispatch through `PromptNormalizer`, and attack metadata updates.
The normalizer still owns request/response conversion and persistence; `AttackService`
maps the resulting stored data to the response, including stored target errors.
All three use the application's `CentralMemory` instance.

All manual-message service instances share one process-local scheduler. It admits up to
64 operations (active and waiting), with up to 4 executing at once in FIFO order.
Targets retain their own per-target request pacing, including targets used by converters.
Only simultaneous conversion using the same converter instance is serialized. The guard
covers each actual conversion, not the send to the message target, other converter instances,
or skipped pieces.
These limits do not coordinate other backend processes, scenario runs, or converter previews.

Metadata read/merge/write operations are serialized per attack, including ordinary sends
to different conversations. Provider calls can still run concurrently; an older metadata
write cannot overwrite a newer response pointer, timestamp, or converter history.

A synchronous operation owns its conversation through attack-summary and conversation-response
assembly, or until failure or cancellation cleanup completes. Offloaded memory writes finish
before ownership is released.
Concurrent sends or `send=false` appends to the same conversation receive **409**;
exceeding the admission limit receives **429**. Neither response starts a send or appends
a message.

### Asynchronous Manual Messages

`POST /api/attacks/{id}/message-sends` accepts the same send fields plus a required
`submission_id` (1-128 characters), and returns **202** after validation and admission,
without waiting for media preparation, conversion, or target I/O. It sends exactly
one message to one conversation. `send=false` remains available only on `/messages`.
Both APIs use the same sending core and share the existing admission and execution budgets.
An asynchronous operation retains its reservation through finalization or cancellation cleanup;
subsequent transcript and attack-detail reads are independent of that reservation.

`GET /api/attacks/{id}/message-sends/{send_id}` returns compact progress:
`queued`, `preparing`, `sending`, `finalizing`, `completed`, `failed`, or `interrupted`.
Failures carry an explicit `failure_stage`: `preparation`, `sending`, `finalization`,
or `interrupted`. Preparation ends when the normalizer enters the target's send pipeline;
sending includes target-side normalization, provider I/O, response conversion, and persistence.
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

Shutdown stops admission, cancels accepted operations, and joins unavoidable offloaded
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
from `frontend`. Stop these test-owned servers after the run.

## Configuration

Environment variables:
- `PYRIT_API_HOST` - Host to bind to (default: localhost)
- `PYRIT_API_PORT` - Port to listen on (default: 8000)
- `PYRIT_API_RELOAD` - Enable auto-reload (default: false)
