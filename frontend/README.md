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

## Resuming a scenario run

Failed runs offer **Resume run** on the run page and **Resume** in Scanner History.
See the [GUI guide](../doc/gui/0_gui.md#resuming-a-failed-scanner-run) for resume
behavior and the saved launch configuration requirement.

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

## Deleting manually added targets

The **New Target** dialog requires a meaningful, unique registry name, such as
`team-image-model`. Names contain 1-64 letters, numbers, dots, underscores, or
hyphens and start with a letter or number. Older API clients may still omit a
name for compatibility.

Each Target Registry row has a three-dot **Actions** menu containing **Hide target**
(or **Show target**) and a separated **Delete target** option. Deletion is enabled
only for targets added through the GUI/API in the current runtime, including
manually created round-robin targets. Configuration-sourced targets, automatically
generated round-robin groups, and the generated canonical `adversarial_chat`
registration show a keyboard-focusable, disabled Delete option with a tooltip
and visible explanation of their ownership, directing
users to edit `.env` / `.pyrit_conf` and reinitialize. Entries without explicit
manual-origin metadata are also protected.

Deletion requires confirmation that removal is immediate, shared by all users,
and cannot be undone. It removes the registration from the backend itself, rather
than hiding it in one browser; it does not delete deployments, credentials, saved
conversations, or results.

The backend rejects deletion while that target is reserved by a manual send,
scenario preparation, queued/running scenario, or configured estimate. Unrelated
targets remain deletable. Registered targets, converters, scorers, and attack
techniques that reference the target also block deletion. Finish/cancel the work
or remove the dependency before retrying. A deleted selection becomes unavailable
rather than silently selecting another model.

The target list refreshes when users open the registry or click **Refresh**,
not on a timer or when the window gains focus. If that refresh finds a saved
objective or adversarial default missing, the app prompts its user to choose
a new default without silently switching models.
Deleting an already-removed target refreshes the list without a not-found error.
Deleting the last target restores **No Targets Configured**; after removal,
keyboard focus returns to **New Target**, or to the row's Actions control after
cancelling.

The isolated real-backend browser suite covers creation, confirmation, cross-client
removal and defaults, deletion races, keyboard focus, the empty state, and
configuration/round-robin refusals:

```bash
E2E_FRONTEND_PORT=13038 npx playwright test e2e/target-deletion.spec.ts --project=seeded --workers=1
```

Run this from `frontend/`. Each test starts its own in-memory backend without
datasets or provider calls and leaves the running GUI's registry unchanged.

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

```bash
# Seeded integration (no credentials needed)
npx playwright test --project seeded

# Live integration (uses API keys when present, otherwise Entra authentication)
E2E_LIVE_MODE=true npx playwright test --project live

# Run both
E2E_LIVE_MODE=true npx playwright test
```

The mock and seeded projects run in the **GitHub Actions** pull-request workflow. The live project is intended for a protected pipeline with an Entra identity or API keys.

CI runs two isolated mock shards alongside one serial seeded job, with one
worker per job. Each job starts its own Vite server and Python backend because
mock specs still depend on backend auth bootstrap and some API calls. The jobs
never share backend state. The required **Frontend E2E Tests** check merges their reports and fails
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
servers with reuse disabled, even when `E2E_FRONTEND_PORT` is set.

> **Note**: `test:e2e:ui` and `test:e2e:headed` require a graphical display and won't work in headless environments like devcontainers. Use `npm run test:e2e` for CI/headless testing.

## Configuration

The frontend proxies API requests to `http://localhost:8000` in development.
Configure this in `vite.config.ts` if needed.

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
