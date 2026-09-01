# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the converter initializer."""

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from pyrit.converter import Base64Converter, ROT13Converter, VariationConverter
from pyrit.models.parameter import ComponentType
from pyrit.registry import ConverterRegistry, InitializerRegistry, TargetRegistry
from pyrit.registry.components import ConverterMetadata
from pyrit.setup.initializers import ConverterInitializer
from tests.unit.mocks import MockPromptTarget


@pytest.fixture(autouse=True)
def reset_registries() -> Iterator[None]:
    """Reset the component registries around each test."""
    ConverterRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    ConverterRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _get_metadata(*, registry: ConverterRegistry, name: str) -> ConverterMetadata:
    metadata = registry.get_registered_class_metadata(name)
    assert metadata is not None
    return metadata


async def test_initialize_registers_converter_with_default_arguments() -> None:
    registry = ConverterRegistry.get_registry_singleton()
    metadata = _get_metadata(registry=registry, name="Base64Converter")

    with patch.object(registry, "get_all_registered_class_metadata", return_value=[metadata]):
        await ConverterInitializer().initialize_async()

    assert isinstance(registry.instances.get("base64"), Base64Converter)


@pytest.mark.usefixtures("patch_central_database")
async def test_initialize_registers_target_only_converter_with_adversarial_chat() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()
    target_registry = TargetRegistry.get_registry_singleton()
    adversarial_chat = MockPromptTarget()
    target_registry.instances.register(adversarial_chat, name="adversarial_chat")
    metadata = _get_metadata(registry=converter_registry, name="VariationConverter")

    with patch.object(converter_registry, "get_all_registered_class_metadata", return_value=[metadata]):
        await ConverterInitializer().initialize_async()

    converter = converter_registry.instances.get("variation")
    assert isinstance(converter, VariationConverter)
    assert converter._converter_target is adversarial_chat


async def test_initialize_skips_target_only_converter_without_adversarial_chat() -> None:
    registry = ConverterRegistry.get_registry_singleton()
    metadata = _get_metadata(registry=registry, name="VariationConverter")

    with patch.object(registry, "get_all_registered_class_metadata", return_value=[metadata]):
        await ConverterInitializer().initialize_async()

    assert "variation" not in registry.instances


@pytest.mark.usefixtures("patch_central_database")
async def test_initialize_skips_converter_with_additional_required_argument() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="adversarial_chat")
    metadata = _get_metadata(registry=converter_registry, name="TenseConverter")

    with patch.object(converter_registry, "get_all_registered_class_metadata", return_value=[metadata]):
        await ConverterInitializer().initialize_async()

    assert "tense" not in converter_registry.instances


@pytest.mark.usefixtures("patch_central_database")
async def test_initialize_attempts_every_eligible_catalog_converter() -> None:
    converter_registry = ConverterRegistry.get_registry_singleton()
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="adversarial_chat")
    metadata_items = converter_registry.get_all_registered_class_metadata()
    expected_classes = {
        metadata.registry_name
        for metadata in metadata_items
        if not (required := [parameter for parameter in metadata.parameters if parameter.required])
        or all(parameter.is_reference_to(ComponentType.TARGET) for parameter in required)
    }

    with patch.object(converter_registry, "create_instance", return_value=Base64Converter()) as create_instance:
        await ConverterInitializer().initialize_async()

    calls_by_class = {call.args[0]: call.kwargs for call in create_instance.call_args_list}
    assert set(calls_by_class) == expected_classes
    assert calls_by_class["Base64Converter"] == {}
    assert calls_by_class["VariationConverter"] == {"converter_target": "adversarial_chat"}
    assert "TenseConverter" not in calls_by_class


async def test_initialize_continues_after_expected_construction_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ConverterRegistry.get_registry_singleton()
    metadata_items = [
        _get_metadata(registry=registry, name="Base64Converter"),
        _get_metadata(registry=registry, name="ROT13Converter"),
    ]

    with (
        patch.object(registry, "get_all_registered_class_metadata", return_value=metadata_items),
        patch.object(
            registry,
            "create_instance",
            side_effect=[ValueError("missing configuration"), ROT13Converter()],
        ),
        caplog.at_level(logging.WARNING, logger="pyrit.setup.initializers.converters"),
    ):
        await ConverterInitializer().initialize_async()

    assert "base64" not in registry.instances
    assert isinstance(registry.instances.get("rot13"), ROT13Converter)
    assert "Skipping converter 'base64': missing configuration" in caplog.text


def test_initializer_is_discovered() -> None:
    registry = InitializerRegistry()
    assert "converter" in registry.get_class_names()
