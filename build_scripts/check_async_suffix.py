# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Enforce ``.github/instructions/style-guide.instructions.md`` §1: every ``async def`` in
``pyrit/`` must end with the ``_async`` suffix.

Mechanism: walk every ``pyrit/**/*.py`` file with ``ast`` and flag every ``AsyncFunctionDef``
whose name does not end in ``_async`` and is not exempted via either:

1. **Hard-coded framework exemptions** (``_FRAMEWORK_EXEMPT_NAMES``) — names whose meaning
   is dictated by an external framework or by the Python data model
   (e.g. ``lifespan`` for FastAPI, ``dispatch`` for Starlette middleware, ``__call__``
   on Protocol classes). The set is intentionally small; one-off exemptions
   should use the per-line ``# pyrit-async-suffix-exempt`` marker instead.

2. **Per-line ``# pyrit-async-suffix-exempt`` marker** on the ``async def`` line.

3. **Transitional baseline** (``build_scripts/async_suffix_baseline.txt``) — every known
   pre-existing violation at the time this hook was introduced. The baseline must shrink
   monotonically: if a baseline entry no longer matches a violation in the source, the
   hook fails with a "drift" message instructing the developer to remove the stale entry.
   This mirrors the ``tests/unit/models/test_import_boundary.py`` allowlist pattern.

To regenerate the baseline (only do this after a deliberate, reviewed cleanup):

    python build_scripts/check_async_suffix.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Project layout — anchor everything off the repo root (directory containing pyrit/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_ROOTS = ("pyrit",)
_BASELINE_PATH = _REPO_ROOT / "build_scripts" / "async_suffix_baseline.txt"

# Framework-mandated names: do NOT add to this set for one-off exemptions.
# Use a per-line ``# pyrit-async-suffix-exempt`` marker instead so each exemption is
# visible at the violation site.
_FRAMEWORK_EXEMPT_NAMES: frozenset[str] = frozenset(
    {
        "lifespan",  # FastAPI app lifespan context manager
        "dispatch",  # Starlette BaseHTTPMiddleware.dispatch override
        "__call__",  # Python dunder; Protocol classes commonly define async __call__
    }
)

_NOQA_MARKER = "# pyrit-async-suffix-exempt"


def _is_violation_name(name: str) -> bool:
    """Return True if ``name`` violates the async-suffix rule."""
    if name.endswith("_async"):
        return False
    if name.startswith("__a"):
        # Async dunders: __aenter__, __aexit__, __aiter__, __anext__.
        return False
    return name not in _FRAMEWORK_EXEMPT_NAMES


def _line_has_noqa(source_lines: list[str], lineno: int) -> bool:
    """Return True if ``source_lines[lineno - 1]`` carries the exempt marker."""
    if lineno < 1 or lineno > len(source_lines):
        return False
    return _NOQA_MARKER in source_lines[lineno - 1]


def _scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return ``(relative_path, line, name)`` violations in ``path``.

    ``relative_path`` is forward-slash normalized relative to the repo root so that
    baseline entries are portable between Windows and Linux checkouts.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    source_lines = source.splitlines()
    rel = path.relative_to(_REPO_ROOT).as_posix()
    violations: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not _is_violation_name(node.name):
            continue
        if _line_has_noqa(source_lines, node.lineno):
            continue
        violations.append((rel, node.lineno, node.name))
    return violations


def _scan_repo() -> list[tuple[str, int, str]]:
    """Return all violations across the scanned roots, sorted for determinism."""
    violations: list[tuple[str, int, str]] = []
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            violations.extend(_scan_file(path))
    return violations


def _load_baseline() -> set[tuple[str, str]]:
    """Return the baseline as a set of ``(path, name)`` pairs.

    Line numbers are intentionally NOT part of the baseline key because unrelated edits
    (e.g. adding imports) shift line numbers and would otherwise produce false drift.
    """
    if not _BASELINE_PATH.exists():
        return set()
    entries: set[tuple[str, str]] = set()
    for raw in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 3:
            continue
        path = parts[0]
        # parts[1] is the line number (ignored for keying; kept in the file for humans)
        name = parts[-1]
        entries.add((path, name))
    return entries


def _write_baseline(violations: list[tuple[str, int, str]]) -> None:
    """Write a fresh baseline file from the current violations."""
    header = [
        "# Async-suffix baseline — transitional allowlist of pre-existing violations.",
        "# Each entry is `<path>:<line>:<name>`. The line number is informational only;",
        "# baseline membership is keyed on (path, name).",
        "#",
        "# This file must shrink monotonically. After renaming a function to add the",
        "# `_async` suffix, remove its baseline entry in the same commit.",
        "#",
        "# To regenerate (only after a deliberate, reviewed cleanup):",
        "#   python build_scripts/check_async_suffix.py --write-baseline",
        "",
    ]
    body = [f"{path}:{line}:{name}" for path, line, name in violations]
    _BASELINE_PATH.write_text("\n".join(header + body) + "\n", encoding="utf-8")


def _report_failures(
    new_violations: list[tuple[str, int, str]],
    drifted_entries: list[tuple[str, str]],
) -> None:
    if new_violations:
        print(
            "[ERROR] Async functions are missing the `_async` suffix "
            "(see .github/instructions/style-guide.instructions.md §1):"
        )
        for path, line, name in new_violations:
            print(f"  {path}:{line}: async def {name}(...)")
        print("")
        print("Rename each function to end in `_async`, or — if the name is dictated")
        print("by a framework — add `# pyrit-async-suffix-exempt` at the end of the `async def` line.")
    if drifted_entries:
        if new_violations:
            print("")
        print("[ERROR] Stale entries in build_scripts/async_suffix_baseline.txt:")
        for path, name in drifted_entries:
            print(f"  {path}: {name} (no longer a violation — remove this line)")
        print("")
        print("The baseline must shrink monotonically. Remove the stale entries in the")
        print("same commit that renames the function.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate the baseline file from the current violations. "
        "Only do this after a deliberate, reviewed cleanup.",
    )
    args = parser.parse_args()

    violations = _scan_repo()

    if args.write_baseline:
        _write_baseline(violations)
        print(f"[OK] Wrote {len(violations)} entries to {_BASELINE_PATH.relative_to(_REPO_ROOT)}")
        return 0

    baseline = _load_baseline()
    current_keys = {(path, name) for path, _, name in violations}

    new_violations = [(path, line, name) for path, line, name in violations if (path, name) not in baseline]
    drifted_entries = sorted(baseline - current_keys)

    if new_violations or drifted_entries:
        _report_failures(new_violations, drifted_entries)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
