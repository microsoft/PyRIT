# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib.util
from pathlib import Path

import pytest

import pyrit.setup.initializers
from pyrit.models import DEFAULT_FINDING_FIELDS, FindingFields
from pyrit.registry import InitializerRegistry
from pyrit.registry.discovery import discover_in_directory
from pyrit.setup.finding_fields import get_active_finding_fields
from pyrit.setup.initializers import FindingsInitializer
from pyrit.setup.pyrit_initializer import PyRITInitializer

pytestmark = pytest.mark.usefixtures("reset_finding_fields")

CONTOSO_SCRIPT = '''
from pyrit.models import FindingField, FindingFields
from pyrit.setup.initializers import FindingsInitializer


class ContosoFindingFields(FindingsInitializer):
    """Contoso finding fields."""

    def get_finding_fields(self) -> FindingFields:
        return FindingFields(
            severity=FindingField(key="severity", label="Severity", type="choice", options=[("sev1", "Sev 1")])
        )
'''


@pytest.fixture
def example_initializer_class(custom_finding_fields: FindingFields) -> type[FindingsInitializer]:
    class ExampleFindingFields(FindingsInitializer):
        """Example finding fields."""

        def get_finding_fields(self) -> FindingFields:
            return custom_finding_fields

    return ExampleFindingFields


async def test_findings_initializer_sets_the_active_fields(
    example_initializer_class: type[FindingsInitializer], custom_finding_fields: FindingFields
) -> None:
    await example_initializer_class().initialize_async()
    assert get_active_finding_fields() is custom_finding_fields


@pytest.mark.usefixtures("patch_central_database")
async def test_get_info_leaves_the_active_fields_alone(example_initializer_class: type[FindingsInitializer]) -> None:
    await example_initializer_class.get_info_async()
    info = await example_initializer_class.get_info_async()

    assert get_active_finding_fields() is DEFAULT_FINDING_FIELDS
    assert info["default_values"] == []
    assert info["global_variables"] == []


def test_findings_initializer_is_skipped_by_builtin_discovery() -> None:
    directory = Path(pyrit.setup.initializers.__file__).parent
    names = [
        cls.__name__
        for _, _, cls in discover_in_directory(directory=directory, base_class=PyRITInitializer, recursive=True)
    ]
    assert "FindingsInitializer" not in names


async def test_discovery_copy_shares_the_active_finding_fields(custom_finding_fields: FindingFields) -> None:
    path = Path(pyrit.setup.initializers.__file__).parent / "findings.py"
    spec = importlib.util.spec_from_file_location("discovered_module.findings", path)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)

    class CopiedFindingFields(copy.FindingsInitializer):
        def get_finding_fields(self) -> FindingFields:
            return custom_finding_fields

    await CopiedFindingFields().initialize_async()
    assert get_active_finding_fields() is custom_finding_fields


async def test_custom_script_subclass_sets_the_active_fields(tmp_path: Path) -> None:
    script = tmp_path / "contoso_fields.py"
    script.write_text(CONTOSO_SCRIPT)
    [initializer] = InitializerRegistry.get_registry_singleton().create_from_script_paths(
        script_paths=[script], strict=True
    )
    assert type(initializer).__name__ == "ContosoFindingFields"
    await initializer.initialize_async()
    assert get_active_finding_fields().severity_order == ["sev1"]
