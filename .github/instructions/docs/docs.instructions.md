---
applyTo: 'doc/code/**/*.{py,ipynb}'
---

# Documentation File Synchronization

## CRITICAL: .ipynb and .py Files Are Linked

All Jupyter notebooks (.ipynb) in the `doc/` directory have corresponding Python (.py) files that are **tightly synchronized**. These files MUST always match exactly in content. They represent the same documentation in different formats.

**Locations:** `doc/code/**/*.ipynb` and `doc/code/**/*.py`

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

Generate .ipynb from .py (with execution):
```bash
jupytext --to ipynb --execute doc/path/to/your_notebook.py
```

Generate .py from .ipynb:
```bash
jupytext --to py:percent doc/path/to/notebook.ipynb
```

## Summary
- **Default strategy**: Update both files inline for simple changes
- **Be cautious and deliberate**: Out-of-sync files are worse than slow regeneration
- **Last resort**: Edit .py only, then regenerate .ipynb (slow but safe)
- **Never** edit only one file without addressing the other
```
