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

All manual-message service instances share one process-local scheduler. It admits up to
64 operations (active and waiting), with up to 4 executing at once. Sends using an
RPM-limited target or request/response converters execute exclusively, conservatively
covering targets used inside converters. Ready operations enter execution in FIFO order.
These limits do not coordinate other backend processes, scenario runs, or converter previews.

An admitted operation owns its conversation until it finishes, fails, or completes
cancellation cleanup. Offloaded memory writes finish before ownership is released.
Concurrent sends or `send=false` appends to the same conversation receive **409**;
exceeding the admission limit receives **429**. Neither response starts a send or appends
a message. No background submission or status API is introduced.

## Configuration

Environment variables:
- `PYRIT_API_HOST` - Host to bind to (default: localhost)
- `PYRIT_API_PORT` - Port to listen on (default: 8000)
- `PYRIT_API_RELOAD` - Enable auto-reload (default: false)
