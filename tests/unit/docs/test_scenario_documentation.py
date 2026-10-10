# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to verify all scenarios are documented in the scanner notebooks.

Mirrors ``test_converter_documentation.py``: every scenario discovered by the
``ScenarioRegistry`` must be mentioned by name in the documentation for its
package. Scenarios are keyed by a dotted ``<package>.<module>`` registry name
(e.g. ``garak.encoding``), and each package has a per-package scanner notebook
under ``doc/scanner/<package>.py``. This keeps the docs in sync when a new
scenario is added. Each notebook must also contain saved output for every
documented scenario.
"""

import json
import re
from pathlib import Path

import pytest

from pyrit.registry import ScenarioRegistry

# tests/unit/docs -> tests/unit -> tests -> workspace_root
_WORKSPACE_ROOT = Path(__file__).parent.parent.parent.parent
_SCANNER_DOC_PATH = _WORKSPACE_ROOT / "doc" / "scanner"


def _get_all_scenarios() -> list[tuple[str, str, str]]:
    """Return ``(registry_name, package, class_name)`` for every registered scenario."""
    registry = ScenarioRegistry.get_registry_singleton()
    scenarios: list[tuple[str, str, str]] = []
    for registry_name in registry.get_class_names():
        package = registry_name.split(".")[0]
        class_name = registry.get_class(registry_name).__name__
        scenarios.append((registry_name, package, class_name))
    return scenarios


def _source_text(cell: dict[str, object]) -> str:
    """Return a notebook cell's source as text."""
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def test_all_scenarios_are_documented() -> None:
    """Test that every scenario class is mentioned in its package documentation."""
    undocumented: list[str] = []
    for registry_name, package, class_name in _get_all_scenarios():
        doc_file = _SCANNER_DOC_PATH / f"{package}.py"
        text = doc_file.read_text(encoding="utf-8") if doc_file.exists() else ""
        has_class_name = re.search(rf"\b{re.escape(class_name)}\b", text)
        if not has_class_name or registry_name not in text:
            expected = doc_file.relative_to(_WORKSPACE_ROOT)
            undocumented.append(f"{registry_name} (class {class_name}) - expected in: {expected}")

    if undocumented:
        pytest.fail(
            "The following scenarios are not documented:\n"
            + "\n".join(undocumented)
            + "\n\nPlease document each scenario in its package notebook under doc/scanner/."
        )


def test_all_scenario_notebooks_have_saved_output() -> None:
    """Test that each scenario notebook contains one successful saved result per scenario."""
    scenarios_by_package: dict[str, list[str]] = {}
    for registry_name, package, _ in _get_all_scenarios():
        scenarios_by_package.setdefault(package, []).append(registry_name)

    failures: list[str] = []
    for package, registry_names in scenarios_by_package.items():
        notebook_path = _SCANNER_DOC_PATH / f"{package}.ipynb"
        if not notebook_path.exists():
            failures.append(f"{package}: missing {notebook_path.relative_to(_WORKSPACE_ROOT)}")
            continue

        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        code_cells = [cell for cell in notebook["cells"] if cell.get("cell_type") == "code"]
        result_cells = [cell for cell in code_cells if "output_scenario_async(" in _source_text(cell)]
        error_outputs = [
            output for cell in code_cells for output in cell.get("outputs", []) if output.get("output_type") == "error"
        ]
        cells_without_output = [cell for cell in result_cells if not cell.get("outputs")]

        if len(result_cells) != len(registry_names):
            failures.append(f"{package}: expected {len(registry_names)} result cells, found {len(result_cells)}")
        if cells_without_output:
            failures.append(f"{package}: {len(cells_without_output)} result cells have no saved output")
        if error_outputs:
            failures.append(f"{package}: {len(error_outputs)} saved error outputs found")

    if failures:
        pytest.fail("Scenario notebook output checks failed:\n" + "\n".join(failures))
