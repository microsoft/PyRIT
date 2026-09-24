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
