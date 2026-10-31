# PyRIT Backend

FastAPI-based REST API for PyRIT.

## Quick Start

### Run the Server

```bash
# Loopback-only local development without Entra authentication
python -m pyrit.backend.pyrit_backend --auth-mode local --host 127.0.0.1 --reload

# Equivalent direct Uvicorn launch
PYRIT_AUTH_MODE=local python -m uvicorn pyrit.backend.main:app --reload --host 127.0.0.1 --port 8000
```

The API will be available at `http://localhost:8000`

For a shared deployment, set `PYRIT_AUTH_MODE=entra`, configure
`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, and `ENTRA_ALLOWED_GROUP_IDS`, and then
bind the server to the intended network interface. Startup fails when neither
explicit local mode nor complete Entra configuration is present. Target-provider
API keys do not authenticate incoming GUI users.

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

### Saved conversations

`GET /api/attacks/conversations/{conversation_id}` returns the saved chat history
and its owning attack. The scanner uses this read-only lookup to open its
conversation editor. For upgraded history without an ownership link, the lookup
uses a verified main or pruned attack reference without changing stored rows.
Missing IDs, standalone conversations, and internal conversations return 404.
Saving from the scanner creates a copy through `POST /api/attacks/save-conversation`;
it does not change the source or send to a target.

For on-demand saved-result browsing, use
`GET /api/attacks/{attack_result_id}/conversations?limit=50` to page main and pruned
references in saved-reference order. This reads counts
and previews, not conversation bodies. The limit is 1-200; follow
`pagination.next_cursor` while `pagination.has_more` is true.

Load only the selected conversation with
`GET /api/attacks/{attack_result_id}/messages?conversation_id={conversation_id}&limit=100`.
Here the limit is 1-500 **message pieces**, not turns or bytes. Results use the
canonical message, piece, and score views, ordered by sequence, timestamp, and ID.
A page can split a multimodal turn: `partial_message_sequences` identifies partial
turns. Append pieces by ID to their turn across pages, and follow
`pagination.next_cursor`. A page does not infer a final target-response status
from an incomplete transcript.

Message cursors belong to one attack and conversation. Reference cursors also
validate the saved ordering and timestamp; restart reference paging if the saved
attack changes. Invalid or mismatched cursors return 400. Passing a cursor requires
a limit. Omitting both preserves the existing active-conversation listing and
full-transcript behavior.
Both paged and unpaged reads expose only main and pruned conversations.
Adversarial and preparation contexts are excluded from lists, reference totals,
and transcript reads, including hierarchy node expansion.

### Scenario result hierarchy

These read-only APIs provide the backend foundation for compound-attack browsing.
The hierarchy GUI is separate work; the current scanner still shows flat executions.

`GET /api/scenarios/runs/{scenario_result_id}/hierarchy?max_nodes=1000` returns a
bounded tree preview grouped by atomic attack group. Roots are saved results with
no incoming child reference from the same run, not results inferred from their
attempt indexes. The node budget is 1-5000, with at most 500 roots per page and
bounded descendant queries through depth 10. `has_more` and `next_cursor` refer
only to **root continuation**. `truncated` also covers deferred descendants,
depth limits, and other results not represented by the preview.

Run/group completion, outcomes, producer counts, and raw error/retry diagnostics
use the shared exact accounting path independently of the selected page. Exact
statistics may scan lightweight summaries; a small tree request does not hydrate
the full progress snapshot, attack results, or conversation bodies.

Expand one result with
`GET /api/scenarios/runs/{scenario_result_id}/hierarchy/nodes/{attack_result_id}?limit=200`.
The limit is 1-500 saved child-reference positions. Follow `next_children_cursor`
using `children_cursor`; `children_count` is the full reference count and
`child_offset` locates this page. `child_index` remains the absolute position under
the immediate parent. Missing, malformed, duplicate, cyclic, or cross-run references
remain explicit and consume positions, so even a wholly unresolved page can advance.
Child cursors validate the run, node, timestamp, and saved reference ordering;
restart child paging after those references change.

Tree previews leave conversation metadata deferred: `conversations` is empty,
`conversations_count` is unknown, and `conversations_truncated` is true when saved
references exist. The node's `conversation_id` still links to its main conversation.
Node expansion returns up to 50 main/pruned conversation summaries, the exact reference
count, and a clipping flag. These use the same metadata model as the generic attack API.
Use the saved-conversation APIs above for reference continuation and all transcript
bodies. There is no hierarchy-specific transcript format or conversation-preview mode.

An entirely rootless saved graph, such as a closed cycle, returns a bounded
`diagnostics.rootless_result_ids` sample with its total count and clipping flag.
It does not invent lineage or promote a cycle member to a true root. The sampled
IDs remain available through the single-node and saved-conversation APIs.

### Techniques

| Endpoint | Result |
| --- | --- |
| `GET /api/techniques` | A page of active registered technique factories |
| `GET /api/techniques/types` | Attack-class parameters and supported converter/adversarial controls |
| `GET /api/techniques/{name}` | Safe settings for one factory; 404 if not registered |
| `POST /api/techniques` | Validate and register a basic runtime technique; 201 on success |

The list accepts `limit` (default 50, range 1-200) and `cursor` (the last returned
registry name). Results use registry-name order. Follow `pagination.next_cursor`
while `pagination.has_more` is true. The GUI loads all pages for search and filters.

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
`types` is also a reserved name. Names cannot replace inherited `ScenarioTechnique`
attributes such as `resolve` or `get_all_techniques`.
Admission also checks the effective dynamic scenario pools, including local factory
tags and scenario filters, before changing the registry. Attack-owned constructor
validators check bounds and other configuration constraints without constructing
or executing attacks. Target and scorer compatibility stays an initialization check.
Constrained list inputs are resolved to their declared enum or literal values.
Deferred Python validation uses the same configured-default rules as decorated
constructors; the factory retains omitted arguments and explicit `None` values.

The basic GUI supports scalar inputs and one level of declared structured settings.
It hides nested structured settings that the shared resolver cannot construct.

Additions are runtime-only and available in compatible scenarios. Restart or
reinitialize PyRIT to remove them. Existing scenario snapshots and runs do not
change. List and detail responses include Python-created factories without
disclosing target credentials.
Scenario-local factories take precedence over registered factories of the same name.
`AttackTechniqueRegistry.cache_scenario_technique_class` refreshes scenario selection
enums when registered factories change; it does not create attacks.
Selections from an earlier enum remain valid in the same scenario and registry
generation if their factories are unchanged. Earlier aggregate selections retain
their original members; they do not gain newly registered techniques.
Responses include `creation_statement`, a Python-style display of the factory's
supplied arguments. Helper constructors retain their original call form.
Live components without captured inputs use `...`; this display is not executable
or lossless source. Responses do not include identifier or expanded seed JSON.

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

### Input Validation and Trust Boundary

The backend can accept requests from other machines, so it checks request values before it
uses them:

- Request bodies, URLs, and fields have the limits above.
- Targets, converters, and scorers accept only the parameters their type catalogs list
  (`GET /api/targets/types`, `GET /api/converters/types`, `GET /api/scorers/types`), and a
  parameter that refers to another component takes the name of a registered one. Values are
  passed to the component, which is responsible for checking them; a `ValueError` from its
  constructor returns **400**.
- File values (`image_path`, `audio_path`, `video_path`, and `binary_path`) in messages,
  previews, and saved or prepended conversations must be uploaded content, a URL, or a file in
  this server's media folders (`prompt-memory-entries` and `seed-prompt-entries` under the
  memory results path), the folders `GET /api/media` serves. Converter file parameters must be
  uploaded content, or an Azure Blob URL where the parameter accepts one.
- When results are stored in Azure Blob Storage, URLs in those file values and converter file
  parameters must name a file in those folders of the results container, and the server signs
  only those URLs for display.
- The configuration routes (`/api/config`), which read, change, and apply the server
  configuration and environment files, require an administrator.
- Through the API, only an administrator can create `HuggingFaceChatTarget` with
  `trust_remote_code` enabled, which runs model repository code on the server, or
  `AzureBlobStorageTarget` in identity mode or without a `sas_token`, which writes with this
  server's credentials to a container the caller can choose.
- `HTTPXAPITarget`, which uploads local files, can be created through the API only when
  `target_upload_directory` is set in `.pyrit_conf`; it then uploads files only from that
  folder, and callers cannot supply their own.

Targets that need a setting the API does not allow can be registered in Python or with an
initializer.

In Entra mode (`PYRIT_AUTH_MODE=entra`), API callers are members of the `ENTRA_ALLOWED_GROUP_IDS`
or `ENTRA_ADMIN_GROUP_ID` groups, and members of `ENTRA_ADMIN_GROUP_ID` are administrators. In
local mode (`PYRIT_AUTH_MODE=local`), meant for loopback-only development, requests are not
authenticated: anyone who can reach the server is an API caller, and callers are administrators
only when the server sets `PYRIT_ALLOW_UNAUTHENTICATED_ADMIN=true`. Other API callers receive
**403** for administrator-only operations.

API callers are trusted operators, so these are intentional exceptions:

- Callers choose target endpoints, headers, raw HTTP requests, and redirects, and converters
  that accept `url` values download them. The backend has no network destination policy (no
  host allow-list, private-network blocking, or DNS pinning); limit outbound network access in
  the deployment instead.
- Some targets use this server's credentials when the caller gives no key: an API key from the
  server's environment, sent even to an endpoint the caller chose, or a token for the server's
  Azure identity, sent to the Azure endpoint or container the caller chose.
- Registered targets, converters, and scorers are shared: any API caller can use them, whoever
  created them.
- Prompt content is not filtered; it is adversarial test data by design.
- Any file type can be stored as a payload. `GET /api/media` renders only allow-listed image,
  audio, and video file extensions inline; everything else downloads as a file.
- `GET /api/media` does not require authentication, so the browser can load media. It serves
  only files from the media folders above.
- Initializers and custom initializer scripts are trusted Python. Uploading scripts requires
  an administrator and `allow_custom_initializers: true`.
- `GitHubCopilotTarget.working_directory`, `HuggingFaceChatTarget.model_path`, and
  `LocalRefusalClassifierScorer.model_id` may name local folders; callers set them like any
  other operator setting.

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
- `PYRIT_AUTH_MODE` - Incoming-request authentication mode: `local` or `entra`
- `ENTRA_TENANT_ID` - Entra directory used for GUI sign-in
- `ENTRA_CLIENT_ID` - Entra application registration used by the GUI
- `ENTRA_ALLOWED_GROUP_IDS` - Comma-separated groups allowed to use protected APIs
- `ENTRA_ADMIN_GROUP_ID` - Optional group allowed to use administrator APIs
- `PYRIT_ALLOW_UNAUTHENTICATED_ADMIN` - Local-mode-only administrator override
- `PYRIT_API_HOST` - Host to bind to (default: localhost)
- `PYRIT_API_PORT` - Port to listen on (default: 8000)
- `PYRIT_API_RELOAD` - Enable auto-reload (default: false)
