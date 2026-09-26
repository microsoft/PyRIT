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

## Configuration

Environment variables:
- `PYRIT_API_HOST` - Host to bind to (default: localhost)
- `PYRIT_API_PORT` - Port to listen on (default: 8000)
- `PYRIT_API_RELOAD` - Enable auto-reload (default: false)
