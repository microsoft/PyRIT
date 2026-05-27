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

1. **Use a kernel bound to a Python env that has this worktree installed editable.**
   Reusing an existing `pyrit` kernel is fine *only if* it points at the current
   checkout — otherwise it will resolve imports against an unrelated copy and
   either pass on stale code or fail on missing new symbols.
   - Quick check: `python -c "import pyrit, pathlib; print(pathlib.Path(pyrit.__file__).resolve())"`
   - If it doesn't match this worktree, install editable here: `pip install -e .`
     (this rebinds the existing kernel to this checkout, no new kernel needed).
   - Only create a new kernel (`python -m ipykernel install --user --name <name>`)
     if you actually need an isolated env.
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

Generate .ipynb from .py (with execution — preferred):
```bash
jupytext --to ipynb --execute doc/path/to/your_notebook.py
```

Generate .py from .ipynb:
```bash
jupytext --to py:percent doc/path/to/notebook.ipynb
```

Sync structure only without executing (rarely correct — outputs will be empty):
```bash
jupytext --to ipynb doc/path/to/your_notebook.py
```

## Summary
- **Default strategy**: Update both files inline for simple changes
- **Be cautious and deliberate**: Out-of-sync files are worse than slow regeneration
- **Last resort**: Edit .py only, then regenerate .ipynb (slow but safe)
- **Never** edit only one file without addressing the other
```
