# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify all scenarios are documented in the scanner notebooks.

Mirrors ``test_converter_documentation.py``: every scenario discovered by the
``ScenarioRegistry`` must be mentioned by name in the documentation for its
package. Scenarios are keyed by a dotted ``<package>.<module>`` registry name
(e.g. ``garak.encoding``), and each package has a per-package scanner notebook
under ``doc/scanner/<package>.py``. This keeps the docs in sync when a new
scenario is added.
"""

import re
from pathlib import Path

import pytest

from pyrit.registry import ScenarioRegistry

# tests/unit/docs -> tests/unit -> tests -> workspace_root
_WORKSPACE_ROOT = Path(__file__).parent.parent.parent.parent
_SCANNER_DOC_PATH = _WORKSPACE_ROOT / "doc" / "scanner"

# Packages whose scenarios are documented outside doc/scanner. The adaptive
# scenarios are covered in the scenarios programming guide rather than a
# per-package scanner notebook. Values are directories; every ``*.py`` notebook
# in the directory is searched.
_SPECIAL_PACKAGE_DOC_DIRS: dict[str, Path] = {
    "adaptive": _WORKSPACE_ROOT / "doc" / "code" / "scenarios",
}


def get_all_scenarios() -> list[tuple[str, str, str]]:
    """Return ``(registry_name, package, class_name)`` for every registered scenario."""
    registry = ScenarioRegistry.get_registry_singleton()
    scenarios = []
    for registry_name in registry.get_class_names():
        package = registry_name.split(".")[0]
        class_name = registry.get_class(registry_name).__name__
        scenarios.append((registry_name, package, class_name))
    return scenarios


def _doc_files_for_package(package: str) -> list[Path]:
    """Return the doc notebook(s) expected to document a package's scenarios."""
    if package in _SPECIAL_PACKAGE_DOC_DIRS:
        return sorted(_SPECIAL_PACKAGE_DOC_DIRS[package].glob("*.py"))
    return [_SCANNER_DOC_PATH / f"{package}.py"]


def test_all_scenarios_are_documented():
    """Test that every scenario class is mentioned in its package documentation."""
    undocumented = []
    for registry_name, package, class_name in get_all_scenarios():
        doc_files = _doc_files_for_package(package)
        text = "\n".join(f.read_text(encoding="utf-8") for f in doc_files if f.exists())
        if not re.search(rf"\b{re.escape(class_name)}\b", text):
            expected = ", ".join(str(f.relative_to(_WORKSPACE_ROOT)) for f in doc_files)
            undocumented.append(f"{registry_name} (class {class_name}) — expected in: {expected}")

    if undocumented:
        pytest.fail(
            "The following scenarios are not documented:\n"
            + "\n".join(undocumented)
            + "\n\nPlease document each scenario in its package notebook under doc/scanner/."
        )


if __name__ == "__main__":
    for name, pkg, cls in get_all_scenarios():
        files = _doc_files_for_package(pkg)
        joined = "\n".join(f.read_text(encoding="utf-8") for f in files if f.exists())
        status = "OK" if re.search(rf"\b{re.escape(cls)}\b", joined) else "MISSING"
        print(f"[{status}] {name} ({cls})")
