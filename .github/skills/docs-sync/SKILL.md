---
name: docs-sync
description: PyRIT documentation synchronization rules for paired jupytext .py (percent format) and .ipynb notebooks under doc/. Use when creating, modifying, or reviewing files under doc/.
---

> Applies to `doc/**/*.{py,ipynb}`.

# Documentation File Synchronization

Every notebook under `doc/` is a **jupytext pair**: a `.py` (percent format) and a `.ipynb`. They MUST stay in sync — the `.py` is the source jupytext manages, and the `.ipynb` carries the rendered outputs readers see. Never change one without addressing the other; out-of-sync pairs are worse than a slow regeneration.

## How to make a change

**Small edits** (imports, paths, variable names, one-line fixes) — edit **both files inline** with identical logical changes. This is fastest and avoids running anything. Even a one-character mismatch breaks the pair, so diff the two cells afterward to confirm code cells, imports, paths, and values match exactly.

**Large or structural edits** — edit the `.py` only, then regenerate the notebook:

```bash
jupytext --to ipynb --execute doc/path/to/notebook.py
```

`--execute` runs every cell (takes minutes and needs working credentials — see below), which is why it's reserved for changes too complex to mirror by hand.

Regenerating the **other** direction (`.py` from an edited `.ipynb`) is fast because it does **not** execute:

```bash
jupytext --to py:percent doc/path/to/notebook.ipynb
```

## Keep cell outputs

Do **not** strip outputs from `doc/` notebooks — rendered tables, images, and printer output are part of the documentation. `nbstripout` is intentionally not run on `doc/`. If a notebook can't execute end-to-end, surface that in review rather than committing an output-less notebook.

## Before running `jupytext --execute`

1. **Kernel must point at this checkout.** Reusing an existing `pyrit` kernel is fine only if it resolves to this worktree:
   `python -c "import pyrit, pathlib; print(pathlib.Path(pyrit.__file__).resolve())"`
   If it doesn't match, run `pip install -e .` here (rebinds the kernel; no new kernel needed). Only create a new kernel if you actually need an isolated env.
2. **Credentials must be present.** Most notebooks call live targets (OpenAI, Azure, etc.) and load creds from `~/.pyrit/.env`.

## Debugging: a cell silently never executes

If `--execute` fails with errors that look like uninitialized state (missing env vars, undefined names, `initialize_pyrit_async` apparently not run, a failing `Cell In[1]` despite earlier code), **check the `# %%` cell separators in the `.py` first.** A missing `# %%` between a `# %% [markdown]` block and the following code makes jupytext absorb the code into the markdown cell, so it never runs. Verify cell markers before chasing env-loading or runtime issues.
