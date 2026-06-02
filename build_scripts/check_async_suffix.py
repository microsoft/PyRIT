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

2. **Per-line ``# pyrit-async-suffix-exempt`` marker** on any line of the ``async def``
   header (the marker is scanned across the full signature, which the formatter may
   split across multiple lines). Common reasons: a deprecation shim that intentionally
   keeps the old non-``_async`` name for one release cycle; a one-off external-SDK or
   protocol method name.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Project layout — anchor everything off the repo root (directory containing pyrit/).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_ROOTS = ("pyrit",)

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


def _header_has_noqa(source_lines: list[str], node: ast.AsyncFunctionDef) -> bool:
    """Return True if any line of the def header carries the exempt marker.

    The header spans ``node.lineno`` through the line just before the function body
    starts (which is where the formatter may place the marker after splitting a
    long signature across multiple lines).
    """
    start = node.lineno
    end = node.body[0].lineno - 1 if node.body else start
    return any(_line_has_noqa(source_lines, lineno) for lineno in range(start, max(start, end) + 1))


def _scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return ``(relative_path, line, name)`` violations in ``path``.

    ``relative_path`` is forward-slash normalized relative to the repo root so that
    violations are reported portably between Windows and Linux checkouts.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        rel = path.relative_to(_REPO_ROOT).as_posix()
        # Surface the parse failure as a violation so an unparseable file can't
        # silently slip past the check. Other hooks (e.g. ruff) should flag the
        # syntax error too, but we don't rely on their ordering.
        message = f"{exc.msg} (line {exc.lineno})" if exc.lineno is not None else exc.msg
        return [(rel, exc.lineno or 0, f"<SyntaxError: {message}>")]
    source_lines = source.splitlines()
    rel = path.relative_to(_REPO_ROOT).as_posix()
    violations: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not _is_violation_name(node.name):
            continue
        if _header_has_noqa(source_lines, node):
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


def main() -> int:
    violations = _scan_repo()
    if not violations:
        return 0

    print(
        "[ERROR] Async functions are missing the `_async` suffix "
        "(see .github/instructions/style-guide.instructions.md §1):"
    )
    for path, line, name in violations:
        if name.startswith("<SyntaxError"):
            print(f"  {path}:{line}: could not parse file: {name[1:-1]}")
        else:
            print(f"  {path}:{line}: async def {name}(...)")
    print("")
    print("Rename each function to end in `_async`, or — if the name is dictated")
    print("by a framework — add `# pyrit-async-suffix-exempt` at the end of the `async def` line.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
