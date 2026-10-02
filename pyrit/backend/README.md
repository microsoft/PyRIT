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

An admitted operation owns its conversation through attack-summary and conversation-response
assembly, or until failure or cancellation cleanup completes. Offloaded memory writes finish
before ownership is released.
Concurrent sends or `send=false` appends to the same conversation receive **409**;
exceeding the admission limit receives **429**. Neither response starts a send or appends
a message. No background submission or status API is introduced.

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

## Input Validation

The backend is the part of PyRIT that accepts requests from other machines, so it checks
request values before using them:

- Media values in messages, previews, and prepended conversations, and file parameters of
  converters, must be uploaded content, a media URL, or a reference into this server's media
  storage: the `prompt-memory-entries` and `seed-prompt-entries` folders under the memory
  results path. Other file paths are rejected.
- Media URLs and `url` pieces are downloaded once into that storage (60 second limit,
  100 MiB limit, at most 3 redirects, no request credentials forwarded), and only the stored
  copy reaches converters and targets, so signed URLs are not passed to model providers.
  This includes messages that are stored without being sent, because stored history is
  replayed to targets later. A `url` piece takes the media type of its content
  (`image_path`, `audio_path`, `video_path`, or `binary_path`); send a literal URL as `text`.
  Blob URLs inside the configured results container are kept as references, without their
  query string, instead of being downloaded. Set `allow_media_url_import: false` in
  `.pyrit_conf` to reject media URLs instead.
- Target types that read local files or load model code (`HTTPXAPITarget`,
  `HuggingFaceChatTarget`) cannot be created through the API. Register them in Python or
  with an initializer, where the operator controls their settings; for example, set
  `HTTPXAPITarget(allowed_upload_directory=...)` so uploads stay inside one folder.

Intentional exceptions:

- Target endpoints, raw HTTP requests, media URLs, and their redirects are chosen by the
  operator and are not restricted to particular hosts. Limit outbound network access in the
  deployment instead.
- Prompt content is not filtered. It is adversarial test data by design.
- Any file type can be stored as a payload. `GET /api/media` only renders known image,
  audio, and video types inline; everything else downloads as a file.
- `GET /api/media` does not require authentication so the browser can load media. It only
  serves files from the media folders above.
- Custom initializer scripts are trusted Python. Uploading them requires an administrator
  and `allow_custom_initializers: true`.
