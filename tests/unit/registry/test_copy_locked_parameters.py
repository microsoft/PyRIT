# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests that every class whose instances can be copied through the API keeps its credentials for the copies.
"""

from collections.abc import Iterator

import pytest

from pyrit.registry import ConverterRegistry, TargetRegistry

_REGISTRIES = (TargetRegistry, ConverterRegistry)


@pytest.fixture(autouse=True)
def fresh_registries() -> Iterator[None]:
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()
    yield
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()


@pytest.mark.parametrize("registry_class", _REGISTRIES)
def test_every_copyable_class_locks_its_credential_parameters(
    registry_class: type[TargetRegistry] | type[ConverterRegistry],
) -> None:
    registry = registry_class.get_registry_singleton()
    copyable: list[str] = []
    unlocked: dict[str, list[str]] = {}
    for metadata in registry.get_all_registered_class_metadata():
        cls = registry.get_class(metadata.class_name)
        if not callable(getattr(cls, "get_reconstruction_parameters", None)):
            continue
        copyable.append(metadata.class_name)
        sensitive = {parameter.name for parameter in metadata.parameters if parameter.sensitive}
        if missing := sensitive - cls.COPY_LOCKED_PARAMETER_NAMES:
            unlocked[metadata.class_name] = sorted(missing)

    assert copyable
    assert unlocked == {}
