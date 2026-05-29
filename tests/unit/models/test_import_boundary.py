# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Enforce the ``pyrit.models`` import boundary.

``pyrit.models`` is the canonical data layer. Files in ``pyrit/models/``
may import only from stdlib, ``pydantic``, ``pyrit.common.deprecation``,
and other ``pyrit.models.*`` submodules.

This test uses a ratchet pattern: ``KNOWN_TOP_LEVEL_VIOLATIONS`` and
``KNOWN_LAZY_VIOLATIONS`` track imports that exist today and are expected to
disappear in a specific phase. The lists must shrink monotonically — if a known
violation is no longer in source, this test fails and the entry must be
removed.

See plan.md / ``.github/instructions/models.instructions.md`` for context.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pyrit.models

if TYPE_CHECKING:
    from collections.abc import Iterable  # noqa: F401

MODELS_PACKAGE = Path(pyrit.models.__file__).parent
EXCLUDE_FILES: frozenset[str] = frozenset()

# Always allowed at module level (in addition to pyrit.models.* self-imports).
ALLOWED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "pyrit.common.deprecation",
    }
)

# Transitional known top-level violations. Each entry names the phase that
# clears it (documentation only — the test does not parse the tag). New
# violations not in this list fail the test; entries that no longer match
# source also fail.
KNOWN_TOP_LEVEL_VIOLATIONS: dict[str, dict[str, str]] = {
    "pyrit.models.attack_result": {
        "pyrit.identifiers.atomic_attack_identifier": "phase-2",
        "pyrit.identifiers.component_identifier": "phase-2",
    },
    "pyrit.models.message_piece": {
        "pyrit.identifiers.component_identifier": "phase-2",
    },
    "pyrit.models.message": {
        "pyrit.common.utils": "phase-4",
    },
    "pyrit.models.harm_definition": {
        "pyrit.common.path": "phase-1",
    },
    "pyrit.models.data_type_serializer": {
        "pyrit.common.path": "phase-8",
    },
    "pyrit.models.seeds.seed": {
        "pyrit.common.utils": "seeds-followup",
        "pyrit.common.yaml_loadable": "seeds-followup",
    },
    "pyrit.models.seeds.seed_dataset": {
        "pyrit.common": "seeds-followup",
        "pyrit.common.utils": "seeds-followup",
        "pyrit.common.yaml_loadable": "seeds-followup",
    },
    "pyrit.models.seeds.seed_group": {
        "pyrit.common.yaml_loadable": "seeds-followup",
    },
    "pyrit.models.seeds.seed_objective": {
        "pyrit.common.path": "seeds-followup",
    },
    "pyrit.models.seeds.seed_prompt": {
        "pyrit.common.path": "seeds-followup",
    },
    "pyrit.models.seeds.seed_simulated_conversation": {
        "pyrit.common.path": "seeds-followup",
    },
}

# Lazy / TYPE_CHECKING imports of cross-package modules. Same ratchet, tracked
# separately so the phase that removes the lazy workaround is explicit.
KNOWN_LAZY_VIOLATIONS: dict[str, dict[str, str]] = {
    "pyrit.models.data_type_serializer": {
        "pyrit.memory": "phase-8",
        "pyrit.common.path": "phase-8",
    },
    "pyrit.models.scenario_result": {
        "pyrit.identifiers.component_identifier": "phase-2-and-7",
        "pyrit.identifiers.evaluation_identifier": "phase-2-and-7",
        "pyrit.score.scorer_evaluation.scorer_metrics": "phase-7",
        "pyrit.score.scorer_evaluation.scorer_metrics_io": "phase-7",
    },
    "pyrit.models.score": {
        "pyrit.identifiers.component_identifier": "phase-5-and-2",
    },
    "pyrit.models.storage_io": {
        "pyrit.auth": "phase-8",
    },
}


def _module_name_for(path: Path) -> str:
    """Return the dotted module name for a file inside ``pyrit/models/``."""
    rel = path.relative_to(MODELS_PACKAGE).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    if not parts:
        return "pyrit.models"
    return "pyrit.models." + ".".join(parts)


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


class _ImportCollector(ast.NodeVisitor):
    """Walk a module AST and bucket ``pyrit.*`` imports into top-level vs lazy."""

    def __init__(self, source_module: str) -> None:
        self.source_module = source_module
        self.top_level: set[str] = set()
        self.lazy: set[str] = set()
        self._in_lazy = False

    def _record(self, mod: str) -> None:
        if not mod.startswith("pyrit."):
            return
        # pyrit.models.* self-imports are always allowed (we are inside pyrit.models)
        if mod == "pyrit.models" or mod.startswith("pyrit.models."):
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


def _scan_files() -> list[Path]:
    """Return all ``pyrit/models/**/*.py`` files in scope."""
    return sorted(p for p in MODELS_PACKAGE.rglob("*.py") if p.name not in EXCLUDE_FILES)


def _analyze(path: Path) -> tuple[str, set[str], set[str]]:
    source_module = _module_name_for(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    collector = _ImportCollector(source_module)
    collector.visit(tree)
    return source_module, collector.top_level, collector.lazy


def _collect_actual_imports() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    top: dict[str, set[str]] = {}
    lazy: dict[str, set[str]] = {}
    for path in _scan_files():
        source, top_imports, lazy_imports = _analyze(path)
        top[source] = top_imports
        lazy[source] = lazy_imports
    return top, lazy


def test_no_new_top_level_violations() -> None:
    """Module-level pyrit imports outside the allowlist must be listed in KNOWN_TOP_LEVEL_VIOLATIONS."""
    actual_top, _ = _collect_actual_imports()
    new_violations: list[str] = []
    for source, imports in actual_top.items():
        known = set(KNOWN_TOP_LEVEL_VIOLATIONS.get(source, {}).keys())
        for imp in sorted(imports):
            if imp in ALLOWED_TOP_LEVEL or imp in known:
                continue
            new_violations.append(f"{source} -> {imp}")
    if new_violations:
        pytest.fail(
            "New top-level pyrit imports in pyrit.models (not allowed):\n  "
            + "\n  ".join(new_violations)
            + "\n\nEither remove the import or, if it is transitional, add it to "
            "KNOWN_TOP_LEVEL_VIOLATIONS in this file with a phase tag."
        )


def test_known_top_level_violations_still_apply() -> None:
    """Entries in KNOWN_TOP_LEVEL_VIOLATIONS that no longer exist in source must be removed."""
    actual_top, _ = _collect_actual_imports()
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
    _, actual_lazy = _collect_actual_imports()
    new_violations: list[str] = []
    for source, imports in actual_lazy.items():
        known = set(KNOWN_LAZY_VIOLATIONS.get(source, {}).keys())
        for imp in sorted(imports):
            if imp in ALLOWED_TOP_LEVEL or imp in known:
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
    _, actual_lazy = _collect_actual_imports()
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


def test_scan_finds_expected_files() -> None:
    """Sanity check: the scanner picks up the known model files."""
    scanned = {p.name for p in _scan_files()}
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
