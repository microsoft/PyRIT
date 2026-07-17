---
applyTo: "pyrit/models/**"
---

# `pyrit.models` Guidelines

**Responsibility**: `pyrit.models` is the lightweight, canonical data layer — the core types shared across components (and preferred in REST) so representations don't drift. It depends only on lightweight Python (the standard library and pydantic) and `pyrit.common`.

## Import Boundary

PyRIT enforces a two-layer rule for its foundational packages. `pyrit.common`
is the foundation layer and `pyrit.models` is the canonical data layer that sits
directly on top of it.

**Forward (models).** Files in `pyrit/models/` may import only from:

- the standard library
- `pydantic`
- any `pyrit.common.*` submodule (the whole prefix)
- other `pyrit.models.*` submodules

If a helper needs another `pyrit.*` package (e.g. `pyrit.memory`,
`pyrit.score`), it does not belong on a model — put it in that package as a free
function or static helper.

**Reverse guard (common).** Files in `pyrit/common/` may import only from the
standard library, third-party libraries, and other `pyrit.common.*` submodules.
`pyrit.common` may never import any other `pyrit.*` package — this is what keeps
it a true foundation and prevents an import cycle with `pyrit.models`. If a
`pyrit.common` helper needs `pyrit.models` (or anything higher), it belongs in
that higher layer, not in `common`.

The CI test `tests/unit/models/test_import_boundary.py` enforces both directions
using allowlists of known transitional violations, each tagged with the phase
that removes it. The lists must shrink monotonically: removing an import from
source without also removing its allowlist entry fails the test, and adding a
new unlisted import also fails the test.
