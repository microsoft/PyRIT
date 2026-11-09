# PyRIT Frontend

Modern TypeScript + React frontend for PyRIT, built with Fluent UI.

## Appearance

The **Theme** menu at the bottom of the sidebar offers System, Light, Dark,
Raccoon, Jimothy, Pirate, Seattle Rain, Evergreen, Blueprint, and Night Sky.
Each named preset combines a fixed palette with a decorative workspace
background. Content panels remain solid for readability.

Your choice is saved in this browser. System follows the operating system's
light/dark preference. High-contrast mode overrides every palette and hides
decorations without forgetting the selected preset.

## Contextual help

Hover over a term or field label to read its explanation. Its information
button also opens the help with a click, tap, or Enter/Space. Press Escape
to dismiss it. Label clicks still focus the field. Field descriptions remain
available to assistive technology while the help is closed. Required markers,
validation errors, input-format guidance, and unavailable-estimate reasons
stay visible.

## GUI targets

`TextTarget` is an SDK demonstration and manual-workflow target that writes to
the Python host's text stream without returning a response. It remains available
in the SDK and REST API, but is excluded from GUI creation, registries, and target
choices, including converter, technique, and round-robin references. Registered
round-robin targets containing a `TextTarget` are excluded as well.
Saved history remains readable, but chats bound to a `TextTarget` cannot send
further prompts from the GUI.

## Conversation selection

Opening a saved attack with related conversations automatically opens the
desktop conversation drawer. This default applies once per attack; later send
refreshes do not override your open/closed choice. Creating, copying, or repeating
conversations still opens the desktop drawer. On narrow screens the drawer opens
only when requested and closes after selecting a conversation.

Each saved turn, including a system turn, has a **Copy or Branch** menu with
visible descriptions for each action. **Copy to next message** copies only that message
to the prompt box. **Branch conversation** and **Branch attack** copy history
including that turn into the same attack or a new attack (a separate group of
conversations), open the
conversation panel, and select the copy at its full
`/attacks/{attack_id}/conversations/{conversation_id}` URL. Copying
within a target-free attack keeps it target-free until you send a prompt.
For single-turn targets or targets that cannot replay the first prompt as history,
**Branch conversation** on that first user turn restores it to the prompt box
without copying the response. After a successful single send, **Undo last send**
also restores the prompt in a new conversation, if the target can replay the
preceding history. Both actions restore the saved prompt and converter results
for text, image, audio, video, and mixed responses when the converter pipeline
has not changed. If you change the pipeline, both actions keep your
current converters and temporary settings, restore the original prompt, and
discard the old conversion results. You can then convert the prompt again.
Neither action overwrites new prompt or working-input edits, sends a prompt,
or runs a converter.
When you save an edited conversation, its final user turn moves to the prompt
box instead of saved history. Text and attachments retain their metadata,
including text-only video remix IDs. You can enter and edit prompts without a target.
Send stays disabled until you select a target; no target-warning box is shown.
Hover over Send, or focus it with the keyboard, to see why sending is disabled.
The warning button in the labels bar reports a placeholder operator and an
unselected operation. Operation remains optional and does not block sending.

**Use in scan** opens the configuration to apply a PyRIT attack technique after
prepending history including the selected turn. It saves a target-free copy
and opens `/scanner/technique.execute?prepended_conversation_id={conversation_id}`.
The scanner configuration fills **prepended_conversation_id** from this URL.
There is no clipboard step, and opening the configuration does not launch a scan.
Select **Add new conversation** beside that field to build simulated history,
or **Edit conversation** to load its current history. **Add to attack** saves all
turns to a new conversation and updates the field. It does not change the source
history, send prompts, or launch a scan. These controls are not in Registry.
Scan launch is disabled while a conversation loads or its editor is open.
The **Apply converters to conversation** toggle beside **Edit conversation** is
off by default and disabled when the conversation ID is empty. When enabled,
request converters run on the stored history during the scan, without changing
the source conversation. New outgoing turns are always converted.

Drop files onto the chat or an editable prompt to attach them. Converter file
fields also accept drops, including **New converter** in the chat pane.
Drops use the same handling as **Attach files** or **Upload**. Read-only inputs
stay read-only. The attachment chooser's file-type filter is a browser hint,
not an application-enforced allowlist. Existing reading and target/component
checks still apply to both entry methods.
Newly selected converter files show their actual name and size
with **Replace** and **Remove**. Image previews use the existing safe display
rules. Existing encoded values use a generic label when file details are
unknown; manually typed or pasted values stay editable. **Remove** restores
the path field.
Drops outside writable media surfaces, including unrelated portal dialogs, do
not attach files or navigate away from the app. A converter read failure is
reported without replacing the previous field value.
Blocked chat file drags show the existing disabled-input reason only during the
drag. Converter file fields retain the first file and report ignored extras.

## Saved Scenario conversation evidence

Saved Scenario results reuse Chat's read-only viewer. Initial Chat metadata
reports a server reference count and validates the selected conversation without
downloading complete reference arrays. The conversation selector initially loads
50 main/pruned references, and the viewer requests 100 **message
pieces**, not turns, from only the selected conversation. **Load more
conversations** and **Load more message pieces** follow separate server cursors.
Reference totals come from the server; selecting a conversation outside the
loaded reference page does not replace it with the main conversation.

Split multimodal turns stay together as pieces arrive, retaining their order,
original/converted media and saved scores. **Incomplete turn** describes an
unfinished turn, while the paging footer describes the whole loaded transcript.
Continuation failures keep the loaded evidence available with **Retry** or
**Restart** controls. A restart begins a new bounded read rather than reusing a
stale cursor. **End of saved transcript** means the server has no further pieces;
it does not infer a final target-response status. The reference and transcript
status regions announce pending page loads, including retries and restarts.

Load the complete transcript before continuing manually, copying/branching,
exporting, or assessing/attaching its evidence. Adding or removing a human score
refreshes scores on the loaded pieces without discarding evidence, pagination,
or completeness. Existing live/manual Chat reads, drafts and send/edit/recovery
workflows are unchanged, and manual conversation-list refreshes retain keyed
rows and keyboard focus. Selected conversation URLs, return-to-scan links and
the originating History filters survive paging and reloads.
On narrow screens, the saved viewer scrolls as one surface so loaded evidence
and continuation controls remain reachable without squeezing the transcript.
There, selecting another saved conversation starts at its beginning;
continuation keeps the current reading position.

## Scan results and live scan attacks

A scan's run page shows its Operation as parent context above the title. Errors
and scan-level notices (paused updates, overload, an incomplete plan, unmatched
executions) stay explicit; a single attack's own error is not repeated as a run
error. While the scan is active, the progress area shows elapsed time, an
estimate of the time remaining, and **View live attacks**. When the scan
finishes, the progress area collapses in place without moving the results.

A run of one atomic attack shows that attack directly as one card, from
preparing through completion. Other runs list atomic attack groups, datasets
and objectives, and the scenario configuration; each result row opens the
attack details. Attack details list the objective and outcome (with its score),
the technique, objective scorer and dataset identities, and any findings that
link the attack, followed by its conversation and **Attach to findings**.

Every scan conversation opens in Chat at
`/attack/chat/{attack_id}/conversations/{conversation_id}?scenarioResultId={run_id}`,
whether the attack is still running or finished. Older
`/scanner/history/{run_id}/conversations/{conversation_id}` links redirect there.
While a scan is running the attack, Chat replaces the prompt bar with an
**Attack in progress** bar and a link back to the scan, keeps the attack
read-only, and refreshes its metadata, transcript, and conversation list at the
scan's polling interval. A failed refresh keeps the loaded messages and offers
**Retry**. When the attack finishes, Chat reloads its saved result in place on
the same URL.

An interrupted scan can retain recorded conversations without a saved attack
result. Chat verifies that run, attack and conversation together and reads the
stored evidence through the run-scoped API, including after reload or an older
scan link. These conversations stay read-only; no result, score, main
conversation or message count is invented. Result-dependent actions remain
unavailable.

Live and manual transcripts follow new messages only while the reader is within
48 px of the end; scrolling further up keeps the reading position. Historical
saved evidence keeps its bounded paging and narrow-screen reading behavior
described above.

**Evaluation metrics** in attack details shows the official evaluation quality
of the recorded objective-scorer configuration, including configurations no
longer in the registry. This is separate from the individual attack score in
the outcome popover and the scan's attack-success rate. Missing evaluation
metrics have an explicit unavailable state and a link to the scorer-metrics guide.

The Scorer Registry keeps its compact comparison table; each scorer's
**Evaluation metrics** opens the complete supplied metrics, including standard
errors and sample counts. Runtime identity and behavioral evaluation identity
remain separate. Registry names are not used to substitute metrics for a
recorded run.

## Dataset catalog

Chat drafts stay in memory while you visit Datasets and return. Text, attachments,
system prompts, the selected target, and converter setup/results return to their
original attack and conversation, including across sidebar navigation and direct
conversation switches. Each conversation retains its own draft. A loading,
missing, or read-only destination does not consume the draft. Importing into a
new conversation uses the source conversation's setup, not another retained draft.
Starting a new attack clears retained drafts; refreshing the browser does not
retain them. Switching conversations does not cancel an in-flight send or let
its completion clear another conversation's input.

The **Datasets** page shows summaries of datasets already loaded into the backend's
memory. When memory is empty, **Learn how to load datasets into memory** opens the
[dataset-loading guide](https://microsoft.github.io/PyRIT/latest/code/datasets/loading-datasets/).
Use the same memory as the backend. The visible catalog checks for changes each minute.
The catalog does not download providers or prefetch individual examples.

Open a dataset to inspect its stored examples. Search matches text prompts and
objectives on the server, not dataset names, and it does not search
simulated-conversation JSON. Filter by that seed type to find those examples.
Matching ignores ASCII case only. Examples stay in the groups the server
returns, including multimodal members. Expanding an example loads its stored
records for inspection only: nothing is executed or imported into chat.
**Show image/audio/video preview** loads supported media on demand, without
autoplay. Reopening waits for a fresh display URL, with Show disabled while the
request is pending. If refreshing fails, the preview stays closed and shows a
message so you can try again. Hiding a preview stops playback.
Filters, page size, and the open example stay in the address bar.
The visible example page and open detail also check for stored changes each minute,
using the same filters and page cursor. Transient background errors keep the
previously loaded content visible with an error message until a check succeeds.
Deleted examples and invalid page cursors still show their explicit recovery state.
**Back to datasets** returns to the catalog search, facets, and sort you came
from. A direct link without that return state goes to `/datasets`.
On narrow screens, scroll the example table horizontally to read its columns.
Expanded details stack each label above its stored value.

## Automatic data updates

Home, registries, history, analytics, catalogs, and configuration data update every
60 seconds while their view is visible, and after relevant actions. Updates do not
reload the page or reset filters, selection, expanded details, or drafts. Dirty
configuration and environment files are not replaced by background reads. Successful
checks show no status message; failed checks show the error text. Active scan and
chat progress keep their faster polling. Runtime initialization and dataset loading
remain explicit actions.

## Saved scenario parameters

Parameters from a preset or saved run that have no field when its launch form
opens are carried unchanged and shown read-only in the run preview. They remain
read-only in that open form if a runtime refresh adds a field; reopen the
configuration to edit them against the refreshed catalog. Editable parameters
removed by a refresh are not submitted, and other drafts are preserved.

## Resuming a scenario run

Failed runs offer **Resume run** on the run page and **Resume** in Scanner History.
See the [GUI History guide](../doc/gui/0_gui.md#history) for a short overview
of resume behavior.

## Default labels after runtime changes

When the runtime configuration changes, CoPyRIT reloads the server's default
labels before allowing new chat attacks, copies saved to new attacks, or scans. Browser label
overrides and saved attack/run attribution are preserved. If loading fails,
use **Retry default labels** in the error banner without restarting the runtime.
The interface stays mounted while the runtime is unavailable, even if its
readiness response has no generation; defaults remain unloaded until recovery.
If the runtime changes while preparing a new chat message or saving a draft to
a new attack, the draft is kept so you can retry after loading finishes. Copies
and drafts saved to an existing attack remain available during defaults reloads
and keep that attack's attribution.

## Attack results analytics

Open **Analytics** to filter saved results, compare SDK-computed outcome
statistics, and drill into matching attacks. See the
[analytics GUI guide](../doc/gui/0_gui.md#analytics) for a short overview.
The implementation follows
[part 5 of the analytics roadmap](https://gist.github.com/romanlutz/34230aba87f7fa07996772a4099e9a78).

The shared `AttackResults/OutcomeSummaryBar` accepts `OutcomeStatistics`, not
result rows. This keeps it reusable without counting a paginated sample or
inferring historical scenario breakdowns from retries.

### Isolated analytics browser checks

From `frontend`, run:

```bash
npm run test:e2e:analytics
```

This builds the current frontend and starts the real application routes and
compatibility/readiness middleware over a disposable local SQLite database.
Its synthetic results include repeated conversation IDs, all result roles,
converter memberships, and a saved retry scenario. It uses a private home,
explicit empty environment-file/initializer lists, and no provider calls,
shared database, or Azure credentials. The normal E2E configuration excludes
this fixture-specific spec; its dedicated configuration owns the server.
CI runs it as an additional isolated shard of the required Frontend E2E check,
using the same report upload, merge, and failure gate as the other projects.
Python dependencies must first be available through `uv sync` at the repository
root. `PYRIT_E2E_ANALYTICS_PORT` changes the loopback port when needed.
If Python is managed only by uv, point Vite's existing Python override at that
environment before running the command. In PowerShell:

```powershell
$env:PYRIT_PYTHON = uv run python -c "import sys; print(sys.executable)"
npm run test:e2e:analytics
```

Set `PYRIT_ANALYTICS_SCREENSHOT_DIR` to an artifact directory to also capture the
verified overview in light/dark, a heatmap drill-down, and narrow controls/report views.
The browser checks do not mock analytics APIs. They do not repeat the separate
100,000-result backend performance workload.

### Isolated Scenario evidence browser checks

From `frontend`, use the existing hierarchy fixture:

```powershell
$env:PYRIT_PYTHON = uv run --no-sync python -c "import sys; print(sys.executable)"
npm run test:e2e:scenario-hierarchy
```

It builds the current frontend and serves real application routes over private,
disposable SQLite data without providers or user configuration. Its saved
evidence includes 222 pieces, a split scored multimodal turn, overlapping stable
piece IDs and 121 references. Journeys cover bounded requests, continuation
retry/restart failures, pending-reader ownership, URL/History context and
keyboard/touch use. `PYRIT_E2E_HIERARCHY_PORT` overrides the loopback port.

### Isolated Scenario dataset and estimate browser checks

From `frontend`, run:

```powershell
$env:PYRIT_PYTHON = uv run --no-sync python -c "import sys; print(sys.executable)"
npm run test:e2e:scenario-datasets
```

The desktop and narrow projects use real configuration, preset, estimate, and
launch handlers over disposable SQLite data. Three local datasets have 8, 6, and
3 examples, with fixed seed/group IDs and reproducible sampling. A harmless local
target and scorer complete the launch without paid model calls. The fixture uses
private configuration and explicitly enables local
administrator access on its loopback server so the real preset-write routes can
be exercised. It never uses a shared database or user credentials.
Startup pins both imported default-configuration paths and explicitly configures
the preset registry's private source. Before every preset save or delete, a guard
checks the actual storage source, document, and lock paths. The report retains
these containment checks alongside the real estimate responses.
Preset names distinguish the viewport, repetition, and retry so test attempts
cannot collide with presets saved by earlier attempts.
`PYRIT_E2E_DATASETS_PORT` overrides the default port, 18612.

The journeys cover keyboard preset creation, scenario and target selection,
dataset search and limit editing, saved-preset reloads, estimates without a
target, target-required launch, inherited and per-source caps, and the persisted
launch configuration. Configured budgets
remain approximate, not selected population counts. An unlimited configuration
is genuinely unavailable before initialization; the production benchmark's
cached-results policy supplies the tested conditional range. Exact counts are
checked only on the initialized run plan.
An additional real-handler launch removes the combined cap and verifies source
overrides and the inherited per-dataset cap against locally delivered objectives.

Loading and error evidence uses explicitly labeled, one-shot backend holds and
failures. Dataset checking has a visible status outside the table's responsive
columns, including while cached metadata is refreshed. Successful requests still
use the real handlers, and guards reject dataset preparation during estimates or
any provider lookup. Screenshots and JSON estimate/launch evidence are attached
to the Playwright report for both
viewports. The dedicated CI projects share the existing report merge and failure
gate; the normal E2E configuration excludes this fixture-only spec.

## Development

```bash
# Install dependencies
npm install

# Start both backend and frontend (cross-platform)
python dev.py start
# OR use npm script
npm run start

# Start backend only (with airt initializer by default)
python dev.py backend

# Start frontend only (backend must be started separately)
python dev.py frontend
# OR
npm run dev

# Restart both servers
python dev.py restart
# OR
npm run restart

# Stop all servers
python dev.py stop
# OR
npm run stop

# Build for production
npm run build

# Preview production build
npm run preview
```

### Backend CLI

The browser starts only after an authenticated `/api/version` handshake confirms
that its embedded compatibility identity matches the backend. Use frontend, CLI,
and backend artifacts from the same package version **and full source commit**.
`dev.py` stamps before starting the backend; Vite uses the same source stamp.
After changing commits, restart both servers. When starting the backend manually,
first run `python -m build_scripts.stamp_compatibility --development` at the repository root.
Dirty local edits warn without changing the identity; they are not publishable.
If the backend changes incompatibly during a session, requests stop and the mounted
UI state stays retained until you explicitly reload; failed mutations are not replayed.

Wheel/sdist builds prepare matching bundled assets automatically. A standalone
`npm run build` is a local build, not approval to publish dirty sources. See the
[release gate](../doc/contributing/10_release_process.md#coordinated-api-release-gate).

The backend uses `pyrit_backend` CLI which supports initializers:

```bash
# Start with default airt initializer (loads targets from env vars)
pyrit_backend --initializers airt

# Start without initializers
pyrit_backend

# Start with custom initialization script
pyrit_backend --initialization-scripts ./my_targets.py

# List available initializers
pyrit_backend --list-initializers

# Custom host/port
pyrit_backend --host 127.0.0.1 --port 8080
```

**Development Mode**: The `dev.py` script sets `PYRIT_DEV_MODE=true` so the backend expects the frontend to run separately on port 3000.

**Production Mode**: When installed from PyPI, the backend serves the bundled frontend and will exit if frontend files are missing.

## Chat converters

Chat keeps one ordered converter pipeline per input modality in memory. Closing
the converter panel does not clear these pipelines. Sending a message clears
its conversion results, but keeps the pipelines for the next message.
Use the arrow keys on a stage's reorder button to move it. Focus stays on that
stage, including when the same converter occurs more than once.

**Convert** processes each input piece separately, including multiple attachments
of the same type. **Add converted value** replaces the applied selection with the
current successful results. Failed pieces remain unconverted and show an error.
Changing an input or its pipeline clears the affected results and selections;
late responses cannot restore them.

Send uses the applied pieces' exact message indexes and runs their configured
converters on the backend. A nondeterministic converter can produce a different
value at Send than the value shown in the converter panel.

## Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Fluent UI v9** - Microsoft design system
- **Vite** - Fast build tool
- **Axios** - HTTP client

## Testing

```bash
# Unit & Integration Tests (Jest + React Testing Library)
npm test              # Run all tests
npm run test:watch    # Watch mode for development
npm run test:coverage # Run with coverage report (85%+ threshold)

# End-to-End Tests (Playwright)
npm run test:e2e          # Run headless (default local setup uses dev.py)
npm run test:e2e:headed   # Run with visible browser windows (requires display)
npm run test:e2e:ui       # Interactive UI mode (requires display)
```

Jest's shared setup in `src/setupTests.ts` supplies the minimal layout signals
Fluent UI needs for dialog focus. No per-suite layout mocks are needed. Hidden
and detached elements remain excluded. Await role queries after dialog
transitions, including when returning to background controls. This is not a
layout engine; use Playwright for assertions about element dimensions or positioning.

### E2E Test Modes

E2E flow tests run in two modes controlled by Playwright projects and an environment variable:

- **Seeded** (`--project seeded`, default for CI): Messages are stored directly in the database with `send: false` using dummy credentials. No real API keys needed. Tests cover the full UI flow (display, branching, conversation switching, promoting) without calling any external service.

- **Live** (`--project live`, requires `E2E_LIVE_MODE=true`): Messages are sent to real OpenAI endpoints with `send: true`. Each target variant requires endpoint and model environment variables plus either an API key or an Azure endpoint accessible through the current Entra identity. Variants without a usable configuration are automatically skipped. Tests verify that real target responses render correctly.

Conversation-selection helpers check the toggle's `aria-expanded` state, not
drawer visibility during an animation. Use accessible row clicks and wait for
the destination URL and transcript before asserting its contents.

```bash
# Seeded integration (no credentials needed)
npx playwright test --project seeded

# Live integration (uses API keys when present, otherwise Entra authentication)
E2E_LIVE_MODE=true npx playwright test --project live

# Run both
E2E_LIVE_MODE=true npx playwright test
```

The mock and seeded projects run in the **GitHub Actions** pull-request workflow. The live project is intended for a protected pipeline with an Entra identity or API keys.

For ordinary interactions with startup controls, call `waitForAppReady(page)`
from `e2e/_runtime.ts` after navigation or reload. A visible control can still
move as runtime readiness and configured defaults arrive. Tests that deliberately
interact before startup finishes should control those responses instead.

Before reloading to check a saved preference, poll its stored value rather than
relying on the optimistic input value. After reload, wait for app readiness
before asserting the restored value, especially when an empty input is expected:
an empty Operation picker can mean its saved-ID lookup is still loading.
The operation-picker suite keeps this persistence barrier local so its deliberate
early-startup interactions remain unsynchronized.

CI runs two isolated mock shards, one serial seeded job, and one isolated
analytics job, with one worker per job. Mock and seeded jobs start their own
Vite server and Python backend because mock specs still depend on backend auth
bootstrap and some API calls. The seeded job serves a production build through
`vite preview`, and the analytics job serves its production build from the
disposable SQLite fixture. Jobs never share backend state.
The required **Frontend E2E Tests** check merges their reports and fails
if any shard fails, is cancelled, or is missing a report. Skipped and flaky tests
also fail CI.

The `playwright-report` artifact contains the combined HTML report. CI retains
traces for every failing test attempt, including the original attempt before a
retry, and uploads diagnostic artifacts named by project, shard, and workflow
attempt. Per-shard blob reports are replaced on reruns so **Re-run failed jobs**
can reuse the reports from successful shards.

Default local E2E runs (`CI` and `E2E_FRONTEND_PORT` unset) use `dev.py` to
automatically start both frontend and backend servers and can reuse servers
that are already running. Setting `E2E_FRONTEND_PORT` locally instead starts
only a dedicated Vite server, expects the backend to already be running, and
disables server reuse.

In CI with `E2E_SEEDED_MODE=true`, Playwright starts its own backend and Vite
servers with reuse disabled, even when `E2E_FRONTEND_PORT` is set. Adding
`E2E_PRODUCTION_FRONTEND=true` builds the frontend and serves it with
`vite preview` instead of the dev server. CI sets it for the seeded job because
those specs reload the app often, and each production-bundle load is much
faster. Mock specs need the dev server because some import `/src` modules in
the browser.

> **Note**: `test:e2e:ui` and `test:e2e:headed` require a graphical display and won't work in headless environments like devcontainers. Use `npm run test:e2e` for CI/headless testing.

## Configuration

The frontend proxies API requests to `http://localhost:8000` in development.
Configure this in `vite.config.ts` if needed.

The Vite development server binds to `127.0.0.1` by default so its `/api`
proxy cannot expose a local unauthenticated backend to the LAN. Environments
that already provide an isolated network boundary, such as a devcontainer with
controlled port forwarding, can explicitly set `PYRIT_FRONTEND_HOST=0.0.0.0`.

The Vite development server disables its own CORS handling. Use the frontend's
same-origin `/api` proxy for API requests. Cross-origin API preflights pass to
the backend, which applies its configured origin policy. Do not enable
unrestricted Vite CORS: it can bypass the backend's preflight checks.

## Adding a theme preset

The catalog in `src/themes/themePresets.ts` is the source of truth for preset
IDs, labels, palettes, backgrounds, menu entries, and stored-value validation.

1. Draw a new, self-contained SVG in `public/backgrounds/`. Use a transparent
   background and keep prominent artwork away from the upper-left reading area.
   Do not embed scripts, external resources, fonts, or raster images.
2. Add one entry to `THEME_PRESETS`, using a unique, stable ID. For example:

   ```ts
   'my-background': {
     label: 'My Background',
     resolved: 'light',
     theme: webLightTheme,
     background: {
       imageUrl: '/backgrounds/my-background.svg',
       opacity: 0.08,
     },
   },
   ```

3. For a coordinated palette, follow a nearby preset's `createPaletteTheme`
   definition instead of changing colors in individual components. Keep
   `resolved` consistent with the palette's light/dark base. Its status
   foregrounds cover custom surfaces while preserving Fluent's semantic
   backgrounds and borders.
4. Document how the artwork was made and keep the palette accessibility tests
   passing. They check neutral/status text and button contrast, including the
   strongest possible artwork at the configured opacity, plus semantic
   foreground/background pairs used by badges and messages.

No hook, menu switch, or page-specific background needs to be added for a new
preset. Existing page canvases share one decorative layer; controls, dialogs,
cards, tables, and message bubbles continue using opaque Fluent UI tokens.
An unknown or removed stored preset returns to System.

### Background artwork provenance

All seven SVGs in `public/backgrounds/` were newly drawn from scratch for this
change with Copilot assistance and are provided under this repository's MIT
license. No artist's illustration, photograph, or stock wallpaper was copied,
traced, vectorized, or used as image-generation input.

The Jimothy drawing uses the real Seattle raccoon's distinctive compact,
rounded appearance. [Know Your Meme](https://knowyourmeme.com/memes/jimothy-the-raccoon)
and [Wikipedia](https://en.wikipedia.org/wiki/Jimothy_(Raccoon)) were consulted
for factual descriptions only. Their displayed artwork and photographs were
not reused. The existing CoPyRIT logo is unchanged.
