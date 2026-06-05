# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Enforce the ``pyrit.models`` / ``pyrit.common`` import boundary.

PyRIT uses a two-layer rule for its foundational packages:

* **Forward (models):** files in ``pyrit/models/`` may import only from stdlib,
  ``pydantic``, all of ``pyrit.common`` (the whole prefix), and other
  ``pyrit.models.*`` submodules.
* **Reverse guard (common):** files in ``pyrit/common/`` may import only from
  stdlib, third-party libraries, and other ``pyrit.common.*`` submodules — never
  any other ``pyrit.*`` package. This keeps ``pyrit.common`` a true foundation
  layer and prevents an import cycle with ``pyrit.models``.

Both directions use a ratchet pattern: the ``KNOWN_*_VIOLATIONS`` lists track
imports that exist today and are expected to disappear in a later phase. The
lists must shrink monotonically — if a known violation is no longer in source,
this test fails and the entry must be removed.

See plan.md / ``.github/instructions/models.instructions.md`` for context.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pyrit.common
import pyrit.models

if TYPE_CHECKING:
    from collections.abc import Iterable

MODELS_PACKAGE = Path(pyrit.models.__file__).parent
COMMON_PACKAGE = Path(pyrit.common.__file__).parent
EXCLUDE_FILES: frozenset[str] = frozenset()

# Forward rule: pyrit.models may import these pyrit prefixes freely.
MODELS_ALLOWED_PREFIXES: tuple[str, ...] = ("pyrit.models", "pyrit.common")

# Reverse guard: pyrit.common may import only itself within the pyrit namespace.
COMMON_ALLOWED_PREFIXES: tuple[str, ...] = ("pyrit.common",)

# Transitional known top-level violations for pyrit.models. Each entry names the
# phase that clears it (documentation only — the test does not parse the tag).
# New violations not in this list fail the test; entries that no longer match
# source also fail.
KNOWN_TOP_LEVEL_VIOLATIONS: dict[str, dict[str, str]] = {}

# Lazy / TYPE_CHECKING imports of cross-package modules from pyrit.models. Same
# ratchet, tracked separately so the phase that removes the lazy workaround is
# explicit.
KNOWN_LAZY_VIOLATIONS: dict[str, dict[str, str]] = {
    "pyrit.models.identifiers.evaluation_identifier": {
        "pyrit.executor.attack.core.attack_strategy": "phase-7",
    },
}

# Reverse-guard violations: pyrit.common modules that still reach up into higher
# layers. These are slated to relocate; the ratchet forces them to shrink.
KNOWN_COMMON_VIOLATIONS: dict[str, dict[str, str]] = {
    "pyrit.common.data_url_converter": {
        "pyrit.memory": "relocate",
    },
    "pyrit.common.display_response": {
        "pyrit.memory": "relocate",
        "pyrit.models": "relocate",
    },
    "pyrit.common.question_answer_helpers": {
        "pyrit.models": "relocate",
    },
}


def _module_name_for(path: Path, *, package_root: Path, package_prefix: str) -> str:
    """Return the dotted module name for a file inside ``package_root``."""
    rel = path.relative_to(package_root).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    if not parts:
        return package_prefix
    return package_prefix + "." + ".".join(parts)


def _resolve_from_import(node: ast.ImportFrom, source_module: str) -> str:
    """Return the absolute module name targeted by a ``from ... import ...`` node."""
    if not node.level:
        return node.module or ""
    parts = source_module.split(".")
    base = parts[: -node.level]
    if node.module:
        return ".".join([*base, node.module])
    return ".".join(base)


def _is_typecheck_test(test: ast.expr) -> bool:
    """Return True iff the expression is ``TYPE_CHECKING`` or ``typing.TYPE_CHECKING``."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
        and test.attr == "TYPE_CHECKING"
    )


def _is_allowed(mod: str, allowed_prefixes: tuple[str, ...]) -> bool:
    """Return True iff ``mod`` is within one of the allowed self-prefixes."""
    return any(mod == prefix or mod.startswith(prefix + ".") for prefix in allowed_prefixes)


class _ImportCollector(ast.NodeVisitor):
    """Walk a module AST and bucket disallowed ``pyrit.*`` imports into top-level vs lazy."""

    def __init__(self, source_module: str, *, allowed_prefixes: tuple[str, ...]) -> None:
        self.source_module = source_module
        self.allowed_prefixes = allowed_prefixes
        self.top_level: set[str] = set()
        self.lazy: set[str] = set()
        self._in_lazy = False

    def _record(self, mod: str) -> None:
        if not mod.startswith("pyrit."):
            return
        if _is_allowed(mod, self.allowed_prefixes):
            return
        bucket = self.lazy if self._in_lazy else self.top_level
        bucket.add(mod)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = _resolve_from_import(node, self.source_module)
        self._record(mod)

    def _visit_lazy_block(self, body: Iterable[ast.stmt]) -> None:
        saved = self._in_lazy
        self._in_lazy = True
        for child in body:
            self.visit(child)
        self._in_lazy = saved

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_lazy_block(node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_lazy_block(node.body)

    def visit_If(self, node: ast.If) -> None:
        if _is_typecheck_test(node.test):
            self._visit_lazy_block(node.body)
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)


def _scan_files(package_root: Path) -> list[Path]:
    """Return all ``*.py`` files under ``package_root`` in scope."""
    return sorted(p for p in package_root.rglob("*.py") if p.name not in EXCLUDE_FILES)


def _analyze(
    path: Path, *, package_root: Path, package_prefix: str, allowed_prefixes: tuple[str, ...]
) -> tuple[str, set[str], set[str]]:
    source_module = _module_name_for(path, package_root=package_root, package_prefix=package_prefix)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    collector = _ImportCollector(source_module, allowed_prefixes=allowed_prefixes)
    collector.visit(tree)
    return source_module, collector.top_level, collector.lazy


def _collect_actual_imports(
    *, package_root: Path, package_prefix: str, allowed_prefixes: tuple[str, ...]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    top: dict[str, set[str]] = {}
    lazy: dict[str, set[str]] = {}
    for path in _scan_files(package_root):
        source, top_imports, lazy_imports = _analyze(
            path,
            package_root=package_root,
            package_prefix=package_prefix,
            allowed_prefixes=allowed_prefixes,
        )
        top[source] = top_imports
        lazy[source] = lazy_imports
    return top, lazy


def _collect_models_imports() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    return _collect_actual_imports(
        package_root=MODELS_PACKAGE,
        package_prefix="pyrit.models",
        allowed_prefixes=MODELS_ALLOWED_PREFIXES,
    )


def _collect_common_imports() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    return _collect_actual_imports(
        package_root=COMMON_PACKAGE,
        package_prefix="pyrit.common",
        allowed_prefixes=COMMON_ALLOWED_PREFIXES,
    )


def test_no_new_top_level_violations() -> None:
    """Module-level pyrit imports outside the allowlist must be listed in KNOWN_TOP_LEVEL_VIOLATIONS."""
    actual_top, _ = _collect_models_imports()
    new_violations: list[str] = []
    for source, imports in actual_top.items():
        known = set(KNOWN_TOP_LEVEL_VIOLATIONS.get(source, {}).keys())
        for imp in sorted(imports):
            if imp in known:
                continue
            new_violations.append(f"{source} -> {imp}")
    if new_violations:
        pytest.fail(
            "New top-level pyrit imports in pyrit.models (not allowed):\n  "
            + "\n  ".join(new_violations)
            + "\n\npyrit.models may import stdlib, pydantic, pyrit.common.*, and pyrit.models.*. "
            "Either remove the import or, if it is transitional, add it to "
            "KNOWN_TOP_LEVEL_VIOLATIONS in this file with a phase tag."
        )


def test_known_top_level_violations_still_apply() -> None:
    """Entries in KNOWN_TOP_LEVEL_VIOLATIONS that no longer exist in source must be removed."""
    actual_top, _ = _collect_models_imports()
    stale: list[str] = []
    for source, allowed in KNOWN_TOP_LEVEL_VIOLATIONS.items():
        present = actual_top.get(source, set())
        stale.extend(f"{source} -> {imp}" for imp in allowed if imp not in present)
    if stale:
        pytest.fail(
            "KNOWN_TOP_LEVEL_VIOLATIONS entries that no longer exist in source:\n  "
            + "\n  ".join(stale)
            + "\n\nThe allowlist must shrink monotonically. Remove these entries."
        )


def test_no_new_lazy_violations() -> None:
    """Lazy/TYPE_CHECKING pyrit imports outside the allowlist must be listed in KNOWN_LAZY_VIOLATIONS."""
    _, actual_lazy = _collect_models_imports()
    new_violations: list[str] = []
    for source, imports in actual_lazy.items():
        known = set(KNOWN_LAZY_VIOLATIONS.get(source, {}).keys())
        for imp in sorted(imports):
            if imp in known:
                continue
            new_violations.append(f"{source} -> {imp}")
    if new_violations:
        pytest.fail(
            "New lazy / TYPE_CHECKING pyrit imports in pyrit.models:\n  "
            + "\n  ".join(new_violations)
            + "\n\nLazy imports are tracked separately. Add to KNOWN_LAZY_VIOLATIONS "
            "with a phase tag or remove."
        )


def test_known_lazy_violations_still_apply() -> None:
    """Entries in KNOWN_LAZY_VIOLATIONS that no longer exist in source must be removed."""
    _, actual_lazy = _collect_models_imports()
    stale: list[str] = []
    for source, allowed in KNOWN_LAZY_VIOLATIONS.items():
        present = actual_lazy.get(source, set())
        stale.extend(f"{source} -> {imp}" for imp in allowed if imp not in present)
    if stale:
        pytest.fail(
            "KNOWN_LAZY_VIOLATIONS entries that no longer exist in source:\n  "
            + "\n  ".join(stale)
            + "\n\nThe allowlist must shrink monotonically. Remove these entries."
        )


def test_no_new_common_violations() -> None:
    """pyrit.common may not import any pyrit.* outside pyrit.common (reverse guard)."""
    actual_top, actual_lazy = _collect_common_imports()
    new_violations: list[str] = []
    for source in sorted({*actual_top, *actual_lazy}):
        imports = actual_top.get(source, set()) | actual_lazy.get(source, set())
        known = set(KNOWN_COMMON_VIOLATIONS.get(source, {}).keys())
        for imp in sorted(imports):
            if imp in known:
                continue
            new_violations.append(f"{source} -> {imp}")
    if new_violations:
        pytest.fail(
            "pyrit.common modules importing outside pyrit.common (forbidden):\n  "
            + "\n  ".join(new_violations)
            + "\n\npyrit.common is the foundation layer and may import only stdlib, "
            "third-party libraries, and pyrit.common.*. Either remove the import or, if it "
            "is transitional, add it to KNOWN_COMMON_VIOLATIONS in this file with a tag."
        )


def test_known_common_violations_still_apply() -> None:
    """Entries in KNOWN_COMMON_VIOLATIONS that no longer exist in source must be removed."""
    actual_top, actual_lazy = _collect_common_imports()
    stale: list[str] = []
    for source, allowed in KNOWN_COMMON_VIOLATIONS.items():
        present = actual_top.get(source, set()) | actual_lazy.get(source, set())
        stale.extend(f"{source} -> {imp}" for imp in allowed if imp not in present)
    if stale:
        pytest.fail(
            "KNOWN_COMMON_VIOLATIONS entries that no longer exist in source:\n  "
            + "\n  ".join(stale)
            + "\n\nThe allowlist must shrink monotonically. Remove these entries."
        )


def test_scan_finds_expected_files() -> None:
    """Sanity check: the scanner picks up the known model files."""
    scanned = {p.name for p in _scan_files(MODELS_PACKAGE)}
    # A non-exhaustive sample of files that must exist for this test to be meaningful.
    expected = {
        "attack_result.py",
        "message.py",
        "message_piece.py",
        "score.py",
        "scenario_result.py",
        "storage_io.py",
        "data_type_serializer.py",
        "seed.py",
        "seed_dataset.py",
    }
    missing = expected - scanned
    assert not missing, f"Expected pyrit.models files not found by scanner: {missing}"
