---
applyTo: 'doc/**/*.{py,ipynb}'
---

# Documentation File Synchronization

## CRITICAL: .ipynb and .py Files Are Linked

All Jupyter notebooks (.ipynb) in the `doc/` directory have corresponding Python (.py) files that are **tightly synchronized**. These files MUST always match exactly in content. They represent the same documentation in different formats.

**Locations:** `doc/**/*.ipynb` and `doc/**/*.py`

## Editing Guidelines

### Preferred Approach: Inline Updates to Both Files
For simple, straightforward changes (imports, variable names, paths, small code fixes):
- **UPDATE BOTH FILES INLINE** using search/replace operations
- This is the fastest and most reliable method for minor edits
- Ensures immediate synchronization without execution overhead
- **Exercise extreme caution**: Even small mismatches will break synchronization
- Also acceptable to just edit the .ipynb and regenerate the .py (this is fast)

### Last Resort: Regenerate the ipynb with Jupytext
For complex or extensive changes where inline editing is error-prone:
1. Edit ONLY the .py file
2. Regenerate the .ipynb using: `jupytext --to ipynb --execute doc/path/to/your_notebook.py`
3. **WARNING**: This process takes several minutes to execute
4. Use this ONLY when inline updates are too risky or complex

## Why This Matters
- Out-of-sync files create inconsistent documentation
- Users and CI/CD systems expect these files to match exactly
- Breaking synchronization causes maintenance headaches and confusion
- The .py files are managed by jupytext and must remain compatible

## Verification Approach
When making changes:
1. **Think carefully** before editing - can this be done inline safely?
2. If editing inline, ensure BOTH .ipynb and .py receive identical logical changes
3. Pay special attention to:
   - Code cell content must match exactly
   - Imports and function calls
   - File paths and constants
   - Variable names and values
4. After editing, verify the changes are truly equivalent


## Jupytext Usage Reference

### Critical pre-execution checklist

Before running `jupytext --execute`, make sure the kernel will exercise *the code in this checkout*, not some stale install:

1. **Run jupytext through this checkout's environment and pin the venv-local kernel.**
   Use `uv run` so the command inherits this worktree's `.venv`, and pass `--set-kernel python3`,
   the kernel that `uv sync` installs inside `.venv`:
   ```bash
   uv run jupytext --to ipynb --execute --set-kernel python3 doc/path/to/your_notebook.py
   ```
   - Quick check: `uv run python -c "import pyrit, pathlib; print(pathlib.Path(pyrit.__file__).resolve())"`
     should print a path inside this checkout.
   - Do **not** select a fixed machine-wide kernel name such as `pyrit-dev`. Those are installed
     with `--user`, so a single name is shared by every clone and worktree on the machine and
     resolves to whichever one registered it last. The notebook then runs against unrelated
     code and silently produces wrong output.
   - Do **not** use `--set-kernel -`. It matches kernels by comparing `argv[0]` to the running
     interpreter, which fails against the correct relocatable kernelspec (`argv[0] == "python"`)
     that `uv sync` installs.
   - If you genuinely need an isolated named kernel, scope it to the virtual environment:
     `uv run python -m ipykernel install --sys-prefix --name <name>`.
2. **Credentials must be pre-configured.** Most notebooks call live targets
   (OpenAI, Azure, etc.) and load creds from `~/.pyrit/.env`. Make sure the
   required keys are present before executing.

### Keep the cell outputs

**Do not strip cell outputs from notebooks under `doc/`.** Outputs are part of the
documentation — readers rely on seeing rendered tables, images, and printer output.
If a notebook can't execute end-to-end, that is exactly the regression we want
to surface in review; don't paper over it by committing an output-less notebook.
`nbstripout` is intentionally not run against `doc/` content for this reason.

### Commands

Generate .ipynb from .py (with execution — if it fails it means there are errors):
```bash
uv run jupytext --to ipynb --execute --set-kernel python3 doc/path/to/your_notebook.py
```

Generate .py from .ipynb:
```bash
uv run jupytext --to py:percent doc/path/to/notebook.ipynb
```

If a `doc/**/*.py` notebook fails during `jupytext --execute` with errors that look like uninitialized state (missing env vars, undefined names, `initialize_pyrit_async` apparently not run, failing cell shows `Cell In[1]` despite earlier code), **check the `# %%` cell separators in the .py file first**.

A missing `# %%` between a `# %% [markdown]` block and the following code causes jupytext to silently absorb the code into the markdown cell, so it never executes. Symptoms in isolation (small repro scripts) will work fine — the bug is purely in the cell structure of the .py file. Do not chase env-loading or runtime issues until cell markers are verified.

## Summary
- **Default strategy**: Update both files inline for simple changes
- **Be cautious and deliberate**: Out-of-sync files are worse than slow regeneration
- **Last resort**: Edit .py only, then regenerate .ipynb (slow but safe)
- **Never** edit only one file without addressing the other
```
