# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Guards against sys.path manipulation and bare sibling imports in build_scripts/."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BUILD_SCRIPTS = Path(__file__).resolve().parents[3] / "build_scripts"
SCRIPT_PATHS = sorted(BUILD_SCRIPTS.glob("*.py"))
SIBLING_MODULES = {path.stem for path in SCRIPT_PATHS} - {"__init__"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _mutates_sys_path(node: ast.AST) -> bool:
    """Detect ``sys.path.insert(...)``/``append(...)`` and ``sys.path[...] = ...`` writes."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            return func.value.attr == "path" and isinstance(func.value.value, ast.Name) and func.value.value.id == "sys"
    if isinstance(node, (ast.Assign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            value = target.value if isinstance(target, ast.Subscript) else target
            if isinstance(value, ast.Attribute) and value.attr == "path":
                return isinstance(value.value, ast.Name) and value.value.id == "sys"
    return False


def test_build_scripts_is_a_package() -> None:
    assert (BUILD_SCRIPTS / "__init__.py").is_file()


def test_script_paths_were_discovered() -> None:
    # Guards against the glob silently matching nothing and vacuously passing.
    assert len(SIBLING_MODULES) > 1


@pytest.mark.parametrize("script", SCRIPT_PATHS, ids=lambda p: p.name)
def test_script_does_not_mutate_sys_path(script: Path) -> None:
    offenders = [node.lineno for node in ast.walk(_parse(script)) if _mutates_sys_path(node)]
    assert not offenders, (
        f"{script.name} mutates sys.path at line(s) {offenders}. "
        "Import siblings as 'from build_scripts import <module>' and run the script "
        "with 'python -m build_scripts.<name>' instead."
    )


@pytest.mark.parametrize("script", SCRIPT_PATHS, ids=lambda p: p.name)
def test_script_imports_siblings_through_the_package(script: Path) -> None:
    offenders: list[str] = []
    for node in ast.walk(_parse(script)):
        if isinstance(node, ast.Import):
            offenders += [alias.name for alias in node.names if alias.name in SIBLING_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in SIBLING_MODULES:
            offenders.append(node.module)

    assert not offenders, (
        f"{script.name} imports sibling module(s) {sorted(set(offenders))} by bare name. "
        "Use the package-qualified form 'from build_scripts import <module>'."
    )
