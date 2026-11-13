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

### Memory text search

Text searches are case-insensitive literal substrings by default. Wildcards require
an explicit full LIKE pattern: `%` matches any number of characters, `_` matches
one, and `/` escapes the next character. Brackets, `*` and backslashes are literal.
Patterns are not automatically wrapped in `%`; a dangling `/` is invalid.

`GET /api/datasets/seeds` accepts `search` for literals or `value_pattern` for
patterns. `GET /api/operations/{operation_id}/findings` accepts `title` or
`title_pattern`. `POST /api/analytics/attacks/facets` accepts `search` or `search_pattern`.
Supplying both companion filters is invalid. An explicit empty pattern matches
empty text, whereas an empty literal query retains its no-filter behavior.
Dataset cursors are bound to the pattern and search mode as well as other filters.
Existing UI search boxes remain literal. Unicode lowercasing is used rather than
full case folding; SQL Server's locale, accent and character semantics depend on
its database collation.

### Health & Status
- `GET /api/health` - Health check
- `GET /api/version` - Version information

### Targets
- `GET /api/targets` - List available prompt targets
- `GET /api/targets/{id}` - Get target details
- `GET /api/targets/types` - List the target types that can be created, with their parameters and auth
  modes, the auth modes a saved target of each type can use (`saveable_auth_modes`), and why a type can't be saved
  at all (`not_saveable_reason`)
- `POST /api/targets` - Create a target
- `POST /api/targets/{id}/detect-capabilities` - Run live capability checks and
  apply the result to the registered target. Send `{"expected_hash": "<identifier hash>"}`.
  Detection uses a private target, has a four-minute operation deadline, and
  returns the updated target. Changes last only for the current backend session.
  Unsupported reconstruction returns 400, an unknown target 404, a changed or
  busy target 409, an inconclusive check 502, and an operation timeout 504.
  Checks send provider requests and can incur charges. Request acceptance does
  not verify full feature behavior; features without probes keep declared values.
  Shared target discovery first requires a successful control request with a
  declared input. A failed control request is inconclusive, not evidence that
  features are unsupported, and leaves the target unchanged.
  For round-robin targets, each child is checked on a private copy. The parent
  reports shared input combinations and feature support, and all possible outputs.
  Only the selected parent is updated, after every child check succeeds; child
  configuration and round-robin rotation stay unchanged. The four-minute deadline
  covers the entire operation. Other composite types remain unsupported.

REST target descriptors expose `capabilities.supports_temperature` and a separate
`generation_ranges` map, such as `{"temperature": [0, 2]}`, populated from the SDK's
`target.generation_ranges` snapshot. Bounds are inclusive; missing entries mean
unknown bounds, not lack of support. Clients must not substitute assumed bounds.
OpenAI SDK constructors accept `generation_ranges={"temperature": (0, 1)}`;
omitted entries retain inferred bounds, while descriptors include effective bounds.
Range declarations remain Python-only constructor inputs, not REST `params`
overrides; reconstruction preserves declarations from the source target.
Per-send values use
[`request_generation_config` metadata](../../doc/code/targets/0_prompt_targets.md#runtime-generation-configuration).

A created target is Temporary: a restart or a configuration apply removes it. With `"persist": true` (and `register`
left on), the target is saved under `registry_recipes_source` and restored at every start, and the signed-in user is
recorded as the one who saved it. A saved target can't hold or use a key: it signs in with Microsoft Entra ID
(`"auth_mode": "identity"`, through this server's Azure identity) or needs no key. An OpenAI target's empty `headers`
is set to `"{}"` first, so a saved one doesn't send this server's `OPENAI_ADDITIONAL_REQUEST_HEADERS`.

| A request with `persist` that... | Gets |
|---|---|
| names the target with anything but up to 64 lowercase letters, digits, and underscores, starting with a letter | **422** |
| would save a key, or use one of this server's keys or address settings | **400** not-saveable |
| gives an address that can carry a credential | **400** not-saveable |
| saves a target type that can't be saved, or one that a scan's launch scripts added or put in place of another | **400** not-saveable |
| saves an OpenAI target while this server sets `OPENAI_CUSTOM_HEADERS`, whose headers the OpenAI SDK adds to every request | **400** not-saveable |
| uses a Temporary target | **400** not-saveable |
| gives an address that isn't a full http(s) or ws(s) address, where this server's address setting would apply | **400** |
| takes a name that a target or a saved file already has | **409** |
| can't be written to storage | **503** |

A not-saveable **400** has `"type": "/errors/not-saveable"`, and the request can be sent again without `persist`; the
other **400** needs the address fixed. `GET /api/targets/types` gives the same class-level answer before a request is
sent. When storage can't be written, the server log names the target and its folder, since the target comes back at
the next start if the write reached storage anyway.

Each listed target's `origin` is `saved`, `configuration` (registered by the server's startup scripts), or `temporary`
(gone after a restart or a configuration apply, such as a target created without `persist` or registered by a scan's
launch scripts). A saved target can only use targets that come back after a restart; targets from configuration keep
their own sign-in. A saved target's `saved_by` is the name of the user who saved it (null when it was saved with sign-in
off), and `saved_by_me` says whether that's the signed-in user asking. The user's object ID isn't returned.

A saved target records the create request without its name: the target type, `auth_mode`, and the parameters as the
API accepted them. Restore sends it through the same create, so it passes the same checks and follows the class's
constructor. A class that moves within PyRIT still restores. Renaming or removing a class or a constructor parameter
changes the saved format: such a change raises `schema_version` and adds a migration for older files, with a test that
restores one. A file that still doesn't match fails to restore with the reason, and the backend stays not ready until
the file is fixed or deleted, so nothing runs without the saved target.

### Converters and scorers

- `GET /api/converters/types` - List the converter types that can be created, with their parameters, and why a
  type can't be saved at all (`not_saveable_reason`)
- `POST /api/converters` - Create a converter
- `DELETE /api/converters/{converter_id}` - Remove a converter
- `POST /api/scorers` - Create a scorer

A created converter or scorer is Temporary unless the request sets `"persist": true`, which works as it does for
targets: it needs `register` left on and a name of lowercase letters, digits, and underscores (otherwise **422**), the
entry is saved under `registry_recipes_source` with the signed-in user recorded, and it's restored at every start. A
request receives **400** with `"type": "/errors/not-saveable"`, and can be sent again without `persist`, if the entry
would save a key, use one of this server's keys or its Azure identity (the Azure Speech converters, the Azure Content
Safety scorer, and the audio scorers), or a model from this server (the local refusal classifier scorer), use a
Temporary target, converter, or scorer, be of a type that only a scan's launch scripts added, or, for a converter, need
or keep an uploaded file. A target left out of the
request and filled from the configured defaults is saved by name, so it must be registered and not Temporary. A
converter copy made with `source` can't be saved yet (**422**). A taken name receives **409**, and storage that can't
be written receives **503**. Each listed converter's and scorer's `origin` is `saved`, `configuration`, or
`temporary`, as for targets. A saved converter can't be removed through the API yet (**409**): delete its file under
`registry_recipes_source` and restart the backend.

### Dataset creation, batch mutation, and deletion

`POST /api/datasets` creates a named dataset synchronously from multipart form data.
Send exactly one `manifest` part containing JSON (a form field or a JSON file), plus
named file parts referenced by its seeds. For example, `manifest.json`:

```json
{
  "name": "my_dataset",
  "seeds": [
    {"seed_type": "prompt", "data_type": "text", "value": "Example"},
    {"seed_type": "prompt", "data_type": "binary_path", "value": "upload:document"}
  ]
}
```

```bash
curl -X POST http://localhost:8000/api/datasets \
  -H "PyRIT-Compatibility-ID: $PYRIT_COMPATIBILITY_ID" \
  -F 'manifest=@manifest.json;type=application/json' \
  -F 'document=@local-document.bin;type=application/octet-stream'
```

Include the normal bearer token in Entra mode; this route does not bypass
authentication or build compatibility. A successful response is `201`:

```json
{"name":"my_dataset","selection_key":"dataset:named:my_dataset","seed_pieces":2,"logical_examples":2}
```

Imports use the canonical discriminated seed union: `prompt` (`SeedPrompt`),
`objective` (`SeedObjective`), and `simulated_conversation`
(`SeedSimulatedConversation`). Omitted `seed_type` remains `prompt` for existing
clients. Prompts require an explicit `data_type` and string `value`; objectives
accept their canonical text-only default and preserve typed `conditions`.
Simulated conversations accept canonical inline prompt configuration, positive
`num_turns`, and nonnegative `sequence`; their text `value` is computed by the model,
not imported as a file or opaque serialized configuration.

Seeds sharing a `prompt_group_id` or `prompt_group_alias` remain one logical
example with a fresh server group ID. Mixed objective, simulated-conversation,
and prompt groups use canonical group cardinality, role, and sequence-overlap
validation. Objective metadata retains canonical `adversarial_prompts`
associations. For example, an objective and its follow-up prompt can be supplied as:

```json
{"name":"mixed","seeds":[
  {"seed_type":"objective","value":"Example objective","prompt_group_alias":"example",
   "conditions":[{"condition_type":"matches_objective"}]},
  {"seed_type":"prompt","data_type":"text","value":"Example prompt",
   "prompt_group_alias":"example","role":"user","sequence":0}
]}
```

File-valued prompts (`image_path`, `audio_path`,
`video_path`, `binary_path`) must use `upload:<part-name>`; server paths, remote
file fetching, and base64 ingestion are not supported. Part names use 1-128 ASCII
letters, digits, underscores, or hyphens. Multiple prompts may share one part;
missing, duplicate, non-file, and unreferenced media parts are rejected. Media
in any of a simulated conversation's three inline system-prompt fields follows
the same explicit-type, multipart, ownership, and shared-reference rules. Those
fields remain canonical `SeedPrompt` objects, not arbitrary configuration files.
Nested prompt types must be explicit, and deprecated simulation `*_system_prompt_path`
inputs are rejected before construction. Simulation configuration persists nested
prompt values, types, parameters, and response schemas using its canonical serializer,
not nested descriptive metadata. Adversarial association prompts remain text-only
under their canonical model.
Media bytes need not decode successfully as their declared modality. Files use
server-generated storage names and are never executed. Blob uploads are opaque
attachments; local retrieval uses the existing media route's safe inline allowlist.

Names are exact, nonempty, at most 256 UTF-16 code units, and have no surrounding
whitespace or control characters. An existing stored dataset or in-progress upload
with a database-collation-equivalent name returns `409`, never append/replace or
silent deduplication. SQLite keeps its normal case-sensitive identity; SQL Server
uses the existing seed-name column collation. Exact deletion remains exact even
when legacy datasets contain collated aliases. Seed and journal names use Unicode
storage on SQL Server, including databases with non-UTF-8 collations.
A provider-only name is allowed; its provider registration remains unchanged.
Descriptive metadata, response schema, roles, sequence numbers, and grouping are
preserved. Complete remapped groups must satisfy canonical `SeedGroup` and
`AttackSeedGroup` rules before any name reservation or media write. Validation
does not mutate the supplied roles. Seed/group IDs, checksums, timestamps, origin, and ingestion attribution
are assigned by the server. Attribution uses the authenticated object ID, or
`dataset-api` in local mode. Original multipart filename and content type are
recorded as `upload_filename` and `upload_content_type` metadata. Trusted Jinja
templates are rejected before constructing any seed or nested prompt; literal
template syntax stays literal. Client paths are never used to load prompt files.
Create mode remains an import, not a raw stored-record round-trip contract.

#### Updating an existing stored dataset

The same multipart `POST /api/datasets` also accepts explicit `"mode":"update"`.
The default `"mode":"create"` retains the create-only behavior and `201` response.
Update mode returns `200` and never implicitly creates or loads a dataset.
Named populations already stored by the SDK or a provider are eligible; provider
files/registrations are not modified. The unnamed aggregate cannot be mutated.

First browse `GET /api/datasets/seeds?selection_key=dataset:named:my_dataset&editable=true`
or the existing `/seeds/{example_id}` detail with `editable=true`. These responses
include `version` and `editable_seeds`: submit-ready subtype replacements, not raw
`members` storage records. Logical `example_id`/`prompt_group_id` identifies an
example; **`editable_seeds[].id` is the record UUID** used for edits/deletions.
Records, projections, and the version originate from one database snapshot.
Ordinary listing/detail requests do not compute editing state or scan other
datasets for content tokens.
Editable and ordinary lists share the same literal `search` and explicit
`value_pattern` filters, including filter-bound pagination. Patterns match the
whole text value, ignoring case: `%` and `_` are wildcards and `/` escapes the
next character. Literal search uses Unicode casefolding; explicit patterns retain
Unicode lowercasing. Supply only one search mode; simulation configurations are not searched.

```json
{
  "name": "my_dataset",
  "mode": "update",
  "expected_version": "<64-character version from editable browsing>",
  "seeds": [
    {"data_type": "text", "value": "An addition"},
    {"id": "<existing record UUID>", "seed_type": "prompt",
     "data_type": "text", "value": "Replacement text"},
    {"seed_type": "objective", "value": "New objective", "prompt_group_alias": "new_example",
     "conditions": [{"condition_type": "matches_objective"}]},
    {"seed_type": "simulated_conversation", "prompt_group_alias": "new_example",
     "num_turns": 3, "sequence": 0,
     "adversarial_chat_system_prompt": {"data_type": "text", "value": "Literal guidance"},
     "simulated_target_system_prompt": {"data_type": "text", "value": "Literal target guidance"}},
    {"seed_type": "prompt", "data_type": "text", "value": "After simulation",
     "prompt_group_alias": "new_example", "sequence": 6, "role": "user"}
  ],
  "delete_seed_ids": ["<different existing record UUID>"]
}
```

No `id` adds a server-identified seed; an `id` replaces that exact record.
`delete_seed_ids` defaults to `[]`. Omitted records are **untouched**, never
implicitly deleted. Deletion-only updates send `"seeds":[]` with nonempty
`delete_seed_ids`; a true no-op is rejected. Create mode forbids deletions and
`expected_version`. Unknown/foreign record UUIDs return `404`; duplicate IDs,
edit/delete overlap, invalid immutable fields, and invalid final groups return
`422`. All additions, edits, and deletions are applied before validating copied
complete affected groups. A whole group can disappear; a surviving attack group
must retain its required objective. Validation never persists normalized roles,
metadata, or grouping on omitted siblings.

In update mode, a new seed's `prompt_group_id` explicitly attaches to an existing
group in **this dataset**. A `prompt_group_alias` instead creates a fresh group
shared by that batch's matching aliases; aliases are not existing-group lookups.
Do not supply both. Edited records retain their original group (including null),
type, dataset, record ID, origin, `date_added`, and `added_by`. These immutable
fields may be omitted; supplied conflicting values are rejected. Checksums and
simulation `value` are recomputed server-side. Original creation attribution
remains unchanged; additions use the request's normal ingestion attribution.
Embedded adversarial prompts are metadata, not stored dataset records: their
independent IDs, group IDs/aliases, dataset labels, and creation provenance remain
canonical nested fields on replacement and do not authorize top-level group
attachment or move the objective's stored group.

Each edit is a **full replacement of client-editable fields**, not a recursive
patch. Use the editable projection when preserving optional fields:

| Editable fields | Omission/default and null rules |
|---|---|
| All subtypes: `name`, `description`, `source` | Omitted or null clears the field. |
| All subtypes: `authors`, `groups`, `harm_categories`, `metadata` | Omitted uses canonical empty collections; null is accepted. Include prior metadata to preserve it. |
| Prompt: `value`, `data_type` | Required string and explicit supported type; null is rejected. |
| Prompt: `role`, `sequence`, `parameters`, `response_json_schema` | Defaults: null, 0, [], null. Null accepted except for sequence. Final group role constraints still apply. |
| Objective: `value`, `conditions` | Text value required; conditions defaults to [], [] clears, null is rejected. Typed conditions and text-only `metadata.adversarial_prompts` use canonical validation. |
| Simulation: `num_turns`, `sequence`, `adversarial_chat_system_prompt` | Defaults: 3 and 0; integers must be positive/nonnegative. The adversarial prompt is required and non-null. |
| Simulation: `simulated_target_system_prompt`, `next_message_system_prompt`, `pyrit_version` | Omitted/null target uses the trusted built-in compliant default; omitted/null next prompt removes it; omitted/null version uses the installed PyRIT version. |

Objectives and simulations default to text-only `data_type`; any explicit type
must also be text. Inline prompts require explicit types and string values.
Simulation configuration stores nested behavioral value/type/parameters/response
schema, not nested descriptive fields, exactly as canonical SDK persistence does.
No submitted legacy `*_system_prompt_path` or trusted-template flag can trigger
filesystem loading/rendering, including in historical configuration. Historical
records that are not safely submit-ready remain inspectable through ordinary
reads; editing an affected invalid group fails without changing its siblings.

For retained media, leave the projection's `retain:<opaque handle>` in its
original media slot. The server binds it to the dataset, record UUID, exact
top-level/nested slot, and expected snapshot. It cannot be moved to another seed
or slot, used for an addition, or replaced with an arbitrary server path. To
replace media, use `upload:<part-name>` with a new file part. All four existing
media types and all three inline simulation prompt slots are supported.

`expected_version` is a deterministic token of the **complete persisted dataset
state**, including IDs, membership, metadata, conditions, and configuration.
It excludes signed media URL parameters and display URLs. SDK/provider writes
through memory participate in the same comparison lock; stale state returns
`409` before any mutation or media write. This is not a monotonic history
counter: an equivalent-state ABA is accepted. Fetch another editable snapshot
after a conflict; never blindly retry an addition with a changed version.

The response includes `added`, `updated`, `deleted`, indexed `seeds` results
(`index`, record `id`, `operation`), exact `delete_seed_ids`, final population
counts, `dataset_exists`, `version`, and `cleanup`/`cleanup_location`. An unchanged
submitted edit still counts as updated. Deleting the final record returns
`dataset_exists:false`, zero population, and `version:null`; no empty-dataset
registry or new editable token is created.

Fresh paths are durably journaled before bytes are written. Failed validation,
stale state, rollback, storage failure, cancellation, and restart recovery preserve
old rows/media and clean only the new staged assets. After commit, only replaced
or explicitly deleted API-owned assets without any remaining seed, message, or
scorer reference are cleaned. Other files from the original upload stay intact;
external/unowned and shared assets are not removed. **A committed mutation with
pending cleanup still returns `200`**, not a retry-shaped `503`, with an explicit
status resource in `Location` and `cleanup_location`. An unavailable cleanup
status has `pending_media:null` rather than an invented count. That resource
retries durable cleanup without replaying the batch.
Native lease-release errors after commit are logged and likewise return the
committed `200` result with pending cleanup and its status location; they do not
hide a committed batch behind a retry-shaped failure.
The same rule applies to creation: it retains `201` and the saved population,
adding `cleanup`, `cleanup_location`, and `Location` only when native release
needs follow-up. Normal create responses keep their original fields.

| Environment variable | Default |
|---|---|
| `PYRIT_DATASET_MAX_REQUEST_BYTES` | `104857600` (100 MiB, including multipart framing) |
| `PYRIT_DATASET_MAX_MANIFEST_BYTES` | `10485760` (10 MiB JSON) |
| `PYRIT_DATASET_MAX_PROMPTS` | `10000` seed records of any supported variant (legacy variable name retained) |

Limits must be positive integers. Actual incoming bytes are bounded even without
`Content-Length`; exceeding a byte/count limit returns `413`. Unsupported content
types return `415`; malformed manifests, unsafe template flags, and invalid
prompt/reference fields return `422`, without echoing prompt content. Media is
spooled during parsing and streamed to the configured disk/blob results store.
No seed rows become visible until the complete import commits.
For updates, the record limit bounds additions + edits + deletions together,
and complete affected-group work (including deleted and surviving members).
The manifest byte limit also bounds the copied affected stored state and submitted
replacements. Exceeding these work budgets returns `413` without partial changes.
The multipart file budget allows all three simulation prompt-media slots per
seed record plus an optional file-valued manifest. Every media part must still
be referenced, and the total request-byte limit applies to all parts together.

Use the existing `GET /api/datasets`, `/api/datasets/seeds`,
`/api/datasets/seeds/{example_id}`, and media URLs to browse uploaded datasets.
No export or dataset-detail endpoint is added.

```bash
curl -X DELETE \
  'http://localhost:8000/api/datasets?selection_key=dataset%3Anamed%3Amy_dataset' \
  -H "PyRIT-Compatibility-ID: $PYRIT_COMPATIBILITY_ID"
```

Deletion permanently removes all stored seeds with the **exact** named dataset,
including stored SDK/provider seeds, but never unregisters a provider. Missing or
provider-only populations return `404`; deleting `dataset:unnamed` is rejected.
External/unowned media is never deleted. API-owned media is removed only if
unreferenced by remaining seeds, original/converted message values, or loose
scorer content. Shared media is retained rather than scheduled for later deletion.

Normal cleanup finishes inline with `200`. If storage cleanup fails, seeds are
still deleted and the response is `202` with `deletion_id`, `pending_media`, and a
sanitized `error`. Follow `Location` with
`GET /api/datasets/deletions/{deletion_id}` to read durable status and retry cleanup.
An upload that cannot finish rollback cleanup returns `503` with the same
`Location` guidance. Database failure after seed deletion likewise returns `503`
with its cleanup resource, not a false success.

Ownership and incomplete cleanup survive restarts. Initialization recovers abandoned
imports and pending deletions after memory/schema setup, before configured initializers
can write seeds; status requests can also
retry them. A live upload holds native ownership across its reservation and
publication transactions: SQLite uses an OS-locked sidecar beside the database
(temporary storage for in-memory databases), and SQL Server uses a detached,
session-owned application lock. Recovery skips live owners; process/connection
loss or explicit shutdown releases ownership. Tiny SQLite sidecars are retained
like journal records so deleting a lock file cannot split its identity. Do not
remove them while any runtime uses the database. A status request for an active
upload returns `409`, not permission to cancel it.
The lifecycle uses a database-backed lock shared by seed/message/scorer-media
writers on SQLite and SQL Server. Upload storage writes hold that lock; deletion
claims media transactionally before removing bytes, and later writes cannot
attach claimed/deleted assets. Rejected Core/bulk DML is rolled back to a
savepoint even if its caller catches the error and commits other work. Azure blob
URLs and relative blob names use the configured adapter's same storage identity.
Writers may wait during an upload. Direct SQL or
external filesystem/blob modifications outside PyRIT's memory APIs do not
participate in this protocol. Deploy the new Alembic migration before starting
backends configured to require an already-current Azure SQL schema.

### Manual attack creation

`POST /api/attacks` creates a `ManualAttack`. Send its goal in `objective`,
for example `{"target_registry_name": "my-target", "objective": "Evaluate the response"}`.
An omitted, null, or empty objective creates a chat without a goal. The objective
never sets the strategy type, and there is no separate attack display-name field.
Objective edits use `PATCH /api/attacks/{attack_result_id}` without changing the
strategy identifier. New attacks copied from conversations or saved through
`POST /api/attacks/save-conversation` also use `ManualAttack`; their automated
source results keep their original strategy types.
New and edited manual objectives are marked as non-placeholder, so even the literal
`"Manual attack via GUI"` is preserved in summaries. Unmarked historical manual
placeholders retain their legacy empty-summary behavior.

Until PyRIT 1.4, `name` remains a deprecated input alias for `objective`, with
the same text validation. When `objective` is omitted or null, `name` supplies
the goal. If both are non-null they must match exactly, including empty strings;
conflicts return 422 rather than silently discarding either value.

This fixes new results, not existing malformed identifiers. Older manual results
may have their initial objective stored as the strategy class name. History,
attack-type filters/options, and analytics still use those persisted class names.
Manual editing and scoring remain available because they recognize the
`pyrit.backend` strategy module, not the malformed class name. Objective edits
do not repair identifiers. Any historical repair requires a separate, audited
migration scoped to verified backend-created manual results, creating corrected
content-addressed identifiers and relinking affected results rather than mutating
shared identifiers. Do not infer manual status from objective text or GUI labels,
or reclassify automated results.

### Saved conversations

`GET /api/attacks/conversations/{conversation_id}` returns the saved chat history
and its owning attack. The scanner uses this read-only lookup to open its
conversation editor. For upgraded history without an ownership link, the lookup
uses a verified main or pruned attack reference without changing stored rows.
Missing IDs, standalone conversations, and internal conversations return 404.
Saving from the scanner creates a copy through `POST /api/attacks/save-conversation`;
it does not change the source or send to a target.

For on-demand saved-result browsing, use
`GET /api/attacks/{attack_result_id}/metadata?conversation_id={conversation_id}`
for initial Chat metadata. It returns `related_conversation_count` (pruned
references, excluding main) and `selected_conversation_valid`, independently of
reference paging. An omitted conversation ID selects main. This response omits
both complete reference arrays, transcript bodies, and result hierarchies; the
stored SDK result and reference JSON are still materialized server-side.
`GET /api/attacks/{attack_result_id}` keeps its full legacy summary contract.

Use
`GET /api/attacks/{attack_result_id}/conversations?limit=50` to page main and pruned
references in saved-reference order. This reads counts
and previews, not conversation bodies. The limit is 1-200; follow
`pagination.next_cursor` while `pagination.has_more` is true.

Load only the selected conversation with
`GET /api/attacks/{attack_result_id}/messages?conversation_id={conversation_id}&limit=100`.
Here the limit is 1-500 **message pieces**, not turns or bytes. Results use the
canonical message, piece, and score views, ordered by sequence, full timestamp
(including fractional seconds), and ID.
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

### Running scan attacks

A scan saves each `AttackResult` only when its attack finishes. While an active scan
is still running an attack, `GET /api/attacks/{attack_result_id}`, `/metadata`,
`/conversations`, and `/messages` (paged or not) resolve it from the live run instead
of returning 404. The attack summary and metadata responses set `in_progress: true`,
report outcome `undetermined`, and set `execution_read_only: true`. Read these
endpoints for running state; conversation and message responses do not include
the running-state flags. Write routes still require a saved result. The
conversation the attack is working on is the main conversation,
and every other objective conversation started so far is listed as a pruned
reference. Messages are read from memory as they are saved. A saved result always
wins, so once the attack finishes the same IDs resolve to its saved result; restart
reference paging if a cursor becomes stale at that transition. An attack that has
not started an objective conversation yet still returns 404.

Attack summaries and metadata also include `scenario_result_id`, the scan run that
produced the attack (`null` for attacks outside a scan). Scan progress
`active_conversations` entries list every objective conversation so far in
`conversation_ids`, alongside the current `conversation_id`. Each scan progress
result and hierarchy node `score` summarizes the objective score: `scorer_name`,
`score_type`, `status`, `score_value`, `score_rationale`, and `score_category`.

The attack service reads running attacks through a read-only lookup
(`LiveAttackLookup`) that the scenario run service provides; it does not import
scenario scheduling.

### Findings linked to an attack

`GET /api/attacks/{attack_result_id}/findings` lists every finding, across all
Operations, that links the attack as evidence: highest severity first, then
newest. Each item is the finding plus `operation_name` and the `evidence_id` that
unlinks it through the Operation evidence route. An attack with no linked
findings, including one that is not saved yet, returns an empty list; a non-UUID
ID returns 422.

The saved Scenario Chat viewer consumes these bounded reads with independent
reference/message continuation controls. It merges stable piece IDs into their
ordered turns and reconciles page-local partial-sequence markers against the
accumulated prefix, rather than treating a leading partial marker as a new turn.
Loaded evidence remains available after a continuation failure. Invalid cursors
offer a bounded restart. Copy/branch, manual continuation, export and assessment
stay disabled until the selected transcript is complete; legacy active/manual
read behavior is unchanged.

### Scenario result hierarchy

These read-only APIs provide the backend foundation for compound-attack browsing.
The Scenario run page integrates them into its existing "Atomic attack groups"
tables, rather than displaying a second results section. Group summaries retain
their server-derived totals while result rows expand into ordered saved children.
Completion and success count logical execution units. The execution count uses
the backend's raw producer attempt counts; neither is inferred from rendered
rows. Missing or duplicate child references remain visible as saved positions,
not additional results.
Available flat execution rows remain usable while relationships load or if the
hierarchy request fails, with an explicit loading/error notice.

Clicking a result row or pressing Enter on it opens the attack details dialog.
An explicit expansion button shows a compound result's saved children. The separate
Conversation link opens Chat and preserves scenario-run provenance for its return
breadcrumb. Hierarchy requests do not load transcript bodies;
the Chat viewer loads saved evidence through the bounded conversation reads above.

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
| `POST /api/techniques` | Validate and register a basic runtime technique, saving it with `persist`; 201 on success |

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

A created technique is Temporary unless the request sets `"persist": true`. A technique to save needs a name of
lowercase letters, digits, and underscores that starts with a letter (otherwise **422**). Its request, without the
name, is saved under `registry_recipes_source` with the signed-in user recorded, and it's rebuilt with the same call
at every start. A request receives **400** with `"type": "/errors/not-saveable"`, and can be sent again without
`persist`, if its attack type was added by a scan's launch scripts, or a converter or the adversarial target it names
is Temporary. A name or tag that clashes with a
registered technique receives **409**, and storage that can't be written receives **503**. Each listed technique's
`origin` is `saved`, `configuration` (registered by a startup script), or `temporary`, as for targets. Settings
changed for a single run aren't saved.

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
Responses include `identifier`, the canonical factory identity shown inline in
the Registry. It can contain raw prompt and seed values, including nested
technique seeds. This JSON is not a redacted or hash-only view.
Responses also include `creation_statement`, a Python-style display of the factory's
supplied arguments. Helper constructors retain their original call form.
Live components without captured inputs use `...`; this display is not executable
or lossless source. The GUI displays identity JSON, not the creation statement.

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

`_pyrit_generation` is server-authored evidence. Saves retain it only from verified,
unchanged source pieces; edits, new pieces, prepended history, and `send=false`
appends discard it. This does not strip unrelated metadata or
[requested generation options](../../doc/code/targets/0_prompt_targets.md#runtime-generation-configuration).

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
Each conversation receipt also retains `target_invoked`, set only at the target
invocation boundary. It remains false during preparation and retains its value
after interruption. An allocated request turn alone is not evidence of target use,
and an invocation does not guarantee successful delivery.

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
backend lifecycle with isolated in-memory SQLite, no environment files, no saved registry
entries, and no default providers. The recovery tests register deterministic loopback
targets. For example, start
it with `uv run python -m uvicorn frontend.e2e.fixtures.manual_send_backend:app --host 127.0.0.1 --port 18213`
(set `PYTHONUTF8=1` on Windows). In another shell, set `PYRIT_BACKEND_URL=http://127.0.0.1:18213`
and `E2E_FRONTEND_PORT=31213`, then run `npx playwright test chat-recovery --project seeded --workers 1`
from `frontend`. Set `PYRIT_PYTHON` to this worktree's Python interpreter if Vite cannot
find `python` (for example, `<worktree>\.venv\Scripts\python.exe` on Windows).
Stop these test-owned servers after the run.

### Request Limits

Attachments upload in the background while editing remains available, with up to three
uploads at a time. Send and Save reuse completed uploads; failures expose Retry and
removal controls. Unused uploads expire after 24 hours, including across restarts.
Only media used in a message or conversion enters permanent results storage.

`POST /api/media/uploads` accepts `data_type`, base64 `content`, and `mime_type`.
It returns **201** with an opaque `value` and `expires_at` (UTC Unix seconds).
Retained drafts refresh uploads one hour before that expiration without changing their
references. Older backends without `expires_at` use a 23-hour refresh interval.
The shared local staging directory has a **1 GiB** quota; uploads exceeding it receive
**507** and can be retried after unused uploads expire. Expired bytes are reclaimed
before admitting new uploads. Renewing existing bytes does not consume extra quota.

The backend reads at most 100 MiB of a request body; larger bodies receive **413**, and
API requests with URLs over 8 KiB receive **414**. Identifiers, names, titles, descriptions,
labels, filters, cursors, configuration files, and initializer scripts also have length or
item limits, listed in the OpenAPI schema; values over a limit receive **422**. Prompt content
(message pieces, system prompts, and converter preview input) and free-form values (prompt
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
  Azure identity, sent to the Azure endpoint or container the caller chose. For an
  OpenAI-compatible server that needs no key, set `auth_mode` to `none` so the target uses none
  of these.
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
