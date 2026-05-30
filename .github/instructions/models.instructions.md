---
applyTo: "pyrit/models/**"
---

# `pyrit.models` Guidelines

## Import Boundary

`pyrit.models` is the canonical data layer. Files in `pyrit/models/` may
import only from:

- the standard library
- `pydantic`
- `pyrit.common.deprecation`
- other `pyrit.models.*` submodules

If a helper needs another `pyrit.*` package, it does not belong on a model —
put it in that package as a free function or static helper.

The CI test `tests/unit/models/test_import_boundary.py` enforces this using an
allowlist of known transitional violations, each tagged with the phase that
removes it. The list must shrink monotonically: removing an import from source
without also removing its allowlist entry fails the test, and adding a new
unlisted import also fails the test.
