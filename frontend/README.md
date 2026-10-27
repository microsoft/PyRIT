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

## Dataset catalog

Chat drafts stay in memory while you visit Datasets and return. Text, attachments,
system prompts, the selected target, and converter setup/results return to their
original attack and conversation. A loading, missing, or read-only destination
does not consume the draft. Starting a new attack or leaving the Chat/Datasets
journey discards the handoff; refreshing the browser does not retain it.

The **Datasets** page shows summaries of datasets already loaded into the backend's
memory. When memory is empty, **Learn how to load datasets into memory** opens the
[dataset-loading guide](https://microsoft.github.io/PyRIT/latest/code/datasets/loading-datasets/).
Use the same memory as the backend, then select **Refresh** in the catalog.
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
**Back to datasets** returns to the catalog search, facets, and sort you came
from. A direct link without that return state goes to `/datasets`.
On narrow screens, scroll the example table horizontally to read its columns.
Expanded details stack each label above its stored value.

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
npm run test:e2e:paired-outcomes # Real SQLite/API paired-outcome acceptance tests
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
