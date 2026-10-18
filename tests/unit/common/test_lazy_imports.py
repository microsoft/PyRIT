# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from types import ModuleType
from unittest.mock import patch

import pytest

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export


def test_resolve_lazy_export_resolves_and_caches_attribute() -> None:
    implementation = ModuleType("example.implementation")
    implementation.ExportedClass = object
    module_globals: dict[str, object] = {}

    with patch("pyrit.common.lazy_imports.import_module", return_value=implementation) as import_module_mock:
        result = resolve_lazy_export(
            name="ExportedClass",
            module_name="example",
            module_globals=module_globals,
            exports={"ExportedClass": "example.implementation"},
        )

    assert result is object
    assert module_globals["ExportedClass"] is object
    import_module_mock.assert_called_once_with("example.implementation")


def test_resolve_lazy_export_supports_renamed_attribute() -> None:
    implementation = ModuleType("example.implementation")
    implementation.OriginalName = object

    with patch("pyrit.common.lazy_imports.import_module", return_value=implementation):
        result = resolve_lazy_export(
            name="PublicName",
            module_name="example",
            module_globals={},
            exports={"PublicName": ("example.implementation", "OriginalName")},
        )

    assert result is object


def test_resolve_lazy_export_supports_module_export() -> None:
    implementation = ModuleType("example.implementation")

    with patch("pyrit.common.lazy_imports.import_module", return_value=implementation):
        result = resolve_lazy_export(
            name="implementation",
            module_name="example",
            module_globals={},
            exports={"implementation": ("example.implementation", None)},
        )

    assert result is implementation


def test_resolve_lazy_export_rejects_unknown_name() -> None:
    with pytest.raises(AttributeError, match="module 'example' has no attribute 'missing'"):
        resolve_lazy_export(
            name="missing",
            module_name="example",
            module_globals={},
            exports={},
        )


def test_get_lazy_dir_includes_unresolved_exports() -> None:
    result = get_lazy_dir(
        module_globals={"existing": object()},
        exports={"LazyName": "example.implementation"},
    )

    assert "existing" in result
    assert "LazyName" in result
