# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prefer existing public package exports over implementation-module imports.

Read lazy export declarations without importing PyRIT or its optional dependencies.
Run with ``uv run python -m build_scripts.check_public_imports`` or pass filenames.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
ExportIndex = dict[tuple[str, str], str]


def _export_index(root: Path) -> ExportIndex:
    declarations: dict[tuple[str, str], tuple[str, str]] = {}
    for path in sorted((root / "pyrit").rglob("__init__.py")):
        package = ".".join(path.parent.relative_to(root).parts)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            if not any(isinstance(target, ast.Name) and target.id == "_LAZY_EXPORTS" for target in targets):
                continue
            for name, value in ast.literal_eval(node.value).items():
                module, attribute = (value, name) if isinstance(value, str) else value
                if attribute == name:
                    declarations[package, name] = (module, attribute)

    exports: ExportIndex = {}
    for (package, _name), target in declarations.items():
        visited: set[tuple[str, str]] = set()
        while target not in visited:
            visited.add(target)
            module, _ = target
            if module.startswith(package + "."):
                previous = exports.get(target, module)
                if len(package.split(".")) < len(previous.split(".")):
                    exports[target] = package
            if target not in declarations:
                break
            target = declarations[target]
    return exports


def _preferred_package(*, module: str, name: str, source_module: str, exports: ExportIndex) -> str | None:
    # Internal sibling imports avoid circular imports and package initialization side effects.
    package = exports.get((module, name))
    if package and (source_module == package or source_module.startswith(package + ".")):
        return None
    return package


def _check_source(*, source: str, source_module: str, exports: ExportIndex) -> list[str]:
    tree = ast.parse(source, filename=source_module)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        for alias in node.names:
            package = _preferred_package(
                module=node.module, name=alias.name, source_module=source_module, exports=exports
            )
            if package:
                suffix = f" as {alias.asname}" if alias.asname else ""
                findings.append(
                    f"{node.lineno}: import {alias.name} from {package}, not {node.module} "
                    f"(from {package} import {alias.name}{suffix})"
                )
    return findings


def _check_file(*, path: Path, root: Path, exports: ExportIndex) -> list[str]:
    relative = path.resolve().relative_to(root)
    if path.name == "__init__.py" or relative.parts[:4] == ("pyrit", "memory", "alembic", "versions"):
        return []
    text = path.read_text(encoding="utf-8-sig")
    source_module = ".".join(relative.with_suffix("").parts)
    if path.suffix == ".py":
        return [
            f"{relative}:{finding}"
            for finding in _check_source(source=text, source_module=source_module, exports=exports)
        ]
    # IPython syntax is valid in notebooks but not in Python source files.
    from IPython.core.inputtransformer2 import TransformerManager

    transformer = TransformerManager()
    findings: list[str] = []
    for number, cell in enumerate(json.loads(text)["cells"], start=1):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        source = transformer.transform_cell(source)
        findings.extend(
            f"{relative}:cell {number}:{finding}"
            for finding in _check_source(source=source, source_module=source_module, exports=exports)
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        args = (
            subprocess.check_output(["git", "ls-files", "-z", "--", "*.py", "*.ipynb"], cwd=_REPO_ROOT)
            .decode("utf-8")
            .split("\0")
        )
    try:
        exports = _export_index(_REPO_ROOT)
        if not exports:
            raise ValueError("No PyRIT public exports found")
        findings = []
        for raw in args:
            path = _REPO_ROOT / raw
            if path.suffix in {".py", ".ipynb"}:
                findings.extend(_check_file(path=path, root=_REPO_ROOT, exports=exports))
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        print(f"Public import check failed: {exc}", file=sys.stderr)
        return 1
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
