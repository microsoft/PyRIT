# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the merged ``ConverterRegistry`` (buildable catalog + instance container)
and its introspection helpers.
"""

from typing import Literal

import pytest

from pyrit.common import REQUIRED_VALUE
from pyrit.models import ComponentIdentifier, Message, MessagePiece, PromptDataType
from pyrit.models.parameter import ComponentType
from pyrit.prompt_converter import (
    Base64Converter,
    CaesarConverter,
    ConverterResult,
    LLMGenericTextConverter,
    NoiseConverter,
    PersuasionConverter,
    PromptConverter,
    TenseConverter,
    ToneConverter,
    TranslationConverter,
    VariationConverter,
)
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry.components import (
    ConverterMetadata,
    ConverterRegistry,
)
from pyrit.registry.object_registries import (
    TargetRegistry,
)
from pyrit.registry.resolution import derive_parameters


class MockPromptTarget(PromptTarget):
    """Minimal PromptTarget (with LLM-converter capabilities) for resolution tests."""

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    def __init__(self, *, model_name: str = "mock_model") -> None:
        super().__init__(model_name=model_name)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [MessagePiece(role="assistant", original_value="mock response").to_message()]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass


class MockTextConverter(PromptConverter):
    """Mock text-to-text converter for testing."""

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """Convert prompt (no-op for testing).

        Returns:
            ConverterResult: The unchanged prompt.
        """
        return ConverterResult(output_text=prompt, output_type="text")


class MockImageConverter(PromptConverter):
    """Mock image-to-text converter for testing."""

    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """Convert prompt (no-op for testing).

        Returns:
            ConverterResult: The unchanged prompt.
        """
        return ConverterResult(output_text=prompt, output_type="text")


class MockMultiModalConverter(PromptConverter):
    """Mock multi-modal converter accepting text and image input for testing."""

    SUPPORTED_INPUT_TYPES = ("text", "image_path")
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """Convert prompt (no-op for testing).

        Returns:
            ConverterResult: The unchanged prompt.
        """
        return ConverterResult(output_text=prompt, output_type="text")


@pytest.fixture
def registry():
    """Provide a fresh ``ConverterRegistry`` singleton, reset around each test."""
    ConverterRegistry.reset_registry_singleton()
    instance = ConverterRegistry.get_registry_singleton()
    yield instance
    ConverterRegistry.reset_registry_singleton()


# ---------------------------------------------------------------------------
# Instance container (reached via the ``instances`` property)
# ---------------------------------------------------------------------------


class TestConverterRegistrySingleton:
    """Tests for the singleton pattern in ConverterRegistry."""

    def setup_method(self):
        ConverterRegistry.reset_registry_singleton()

    def teardown_method(self):
        ConverterRegistry.reset_registry_singleton()

    def test_get_registry_singleton_returns_same_instance(self):
        assert ConverterRegistry.get_registry_singleton() is ConverterRegistry.get_registry_singleton()

    def test_get_registry_singleton_returns_converter_registry_type(self):
        assert isinstance(ConverterRegistry.get_registry_singleton(), ConverterRegistry)

    def test_reset_registry_singleton_clears_singleton(self):
        instance1 = ConverterRegistry.get_registry_singleton()
        ConverterRegistry.reset_registry_singleton()
        assert ConverterRegistry.get_registry_singleton() is not instance1


class TestConverterRegistryRegisterInstance:
    """Tests for instance registration via the ``instances`` property."""

    def test_register_instance_with_custom_name(self, registry: ConverterRegistry):
        converter = MockTextConverter()
        registry.instances.register(converter, name="custom_converter")

        assert "custom_converter" in registry.instances
        assert registry.instances.get("custom_converter") is converter

    def test_register_instance_generates_name_from_class(self, registry: ConverterRegistry):
        converter = MockTextConverter()
        registry.instances.register(converter)

        names = registry.instances.get_names()
        assert len(names) == 1
        assert names[0].startswith("MockTextConverter::")

    def test_register_instance_multiple_converters_unique_names(self, registry: ConverterRegistry):
        registry.instances.register(MockTextConverter())
        registry.instances.register(MockImageConverter())

        assert len(registry.instances) == 2

    def test_register_instance_duplicate_name_overwrites(self, registry: ConverterRegistry):
        converter1 = MockTextConverter()
        converter2 = MockImageConverter()

        registry.instances.register(converter1, name="shared_name")
        registry.instances.register(converter2, name="shared_name")

        assert len(registry.instances) == 1
        assert registry.instances.get("shared_name") is converter2

    def test_register_instance_rejects_non_converter(self, registry: ConverterRegistry):
        class NotAConverter:
            pass

        with pytest.raises(TypeError, match="PromptConverter"):
            registry.instances.register(NotAConverter())  # type: ignore[arg-type]

        assert len(registry.instances) == 0


class TestConverterRegistryGetInstanceByName:
    """Tests for instance lookup via ``instances.get``."""

    def test_get_instance_by_name_returns_converter(self, registry: ConverterRegistry):
        converter = MockTextConverter()
        registry.instances.register(converter, name="test_converter")
        assert registry.instances.get("test_converter") is converter

    def test_get_instance_by_name_nonexistent_returns_none(self, registry: ConverterRegistry):
        assert registry.instances.get("nonexistent") is None


class TestConverterRegistryInstanceMetadata:
    """Tests for instance-level metadata (``instances.list_metadata``)."""

    def test_instance_metadata_is_component_identifier(self, registry: ConverterRegistry):
        converter = MockTextConverter()
        registry.instances.register(converter, name="text_converter")

        metadata = registry.instances.list_metadata()
        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0] == converter.get_identifier()

    def test_instance_metadata_filter_by_class_name(self, registry: ConverterRegistry):
        registry.instances.register(MockTextConverter(), name="t1")
        registry.instances.register(MockTextConverter(), name="t2")
        registry.instances.register(MockImageConverter(), name="i1")

        metadata = registry.instances.list_metadata(include_filters={"class_name": "MockTextConverter"})
        assert len(metadata) == 2
        assert all(m.class_name == "MockTextConverter" for m in metadata)


class TestConverterRegistryContainerProtocol:
    """Tests for the ``instances`` container protocol surface."""

    def test_contains_and_len_and_iter(self, registry: ConverterRegistry):
        registry.instances.register(MockTextConverter(), name="test_converter")
        assert "test_converter" in registry.instances
        assert "unknown_converter" not in registry.instances
        assert len(registry.instances) == 1
        assert "test_converter" in list(registry.instances)

    def test_get_names_returns_sorted_list(self, registry: ConverterRegistry):
        registry.instances.register(MockImageConverter(), name="zeta_converter")
        registry.instances.register(MockImageConverter(), name="alpha_converter")
        assert registry.instances.get_names() == ["alpha_converter", "zeta_converter"]

    def test_get_all_instances_returns_all(self, registry: ConverterRegistry):
        text = MockTextConverter()
        image = MockImageConverter()
        registry.instances.register(text, name="text_converter")
        registry.instances.register(image, name="image_converter")

        entry_map = {e.name: e for e in registry.instances.get_all_instances()}
        assert entry_map["text_converter"].instance is text
        assert entry_map["image_converter"].instance is image


# ---------------------------------------------------------------------------
# Buildable class catalog (discovery + introspection + build)
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for converter class discovery."""

    def test_discovers_known_converters(self, registry: ConverterRegistry):
        names = registry.get_class_names()
        assert "Base64Converter" in names
        assert "CaesarConverter" in names

    def test_discovers_non_catalog_converters(self, registry: ConverterRegistry):
        # SelectiveTextConverter is hidden from the user-facing catalog (a frontend
        # concern) but must remain discoverable/buildable so agents can use it.
        assert "SelectiveTextConverter" in registry.get_class_names()

    def test_does_not_register_base_class(self, registry: ConverterRegistry):
        assert "PromptConverter" not in registry.get_class_names()

    def test_keyed_by_exact_class_name(self, registry: ConverterRegistry):
        names = registry.get_class_names()
        assert "Base64Converter" in names
        assert "base64_converter" not in names


class TestGetClass:
    """Tests for get_class (the inherited class-catalog accessor)."""

    def test_returns_class(self, registry: ConverterRegistry):
        assert registry.get_class("Base64Converter") is Base64Converter

    def test_unknown_type_raises(self, registry: ConverterRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.get_class("NotARealConverter")

    def test_is_subclass_relationship(self, registry: ConverterRegistry):
        assert issubclass(registry.get_class("Base64Converter"), PromptConverter)


class TestCreateInstance:
    """Tests for create_instance (build via the shared resolver)."""

    def test_creates_instance(self, registry: ConverterRegistry):
        assert isinstance(registry.create_instance("Base64Converter"), Base64Converter)

    def test_coerces_string_params(self, registry: ConverterRegistry):
        converter = registry.create_instance("CaesarConverter", caesar_offset="13")
        assert isinstance(converter, CaesarConverter)
        assert converter.get_identifier().params.get("caesar_offset") == 13

    def test_unknown_type_raises(self, registry: ConverterRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.create_instance("NotARealConverter")

    def test_unknown_param_raises(self, registry: ConverterRegistry):
        with pytest.raises(ValueError, match="Unknown parameter"):
            registry.create_instance("Base64Converter", not_a_param="x")

    def test_build_does_not_register_instance(self, registry: ConverterRegistry):
        registry.create_instance("Base64Converter")
        assert len(registry.instances) == 0


@pytest.mark.usefixtures("patch_central_database")
class TestCreateLLMConverter:
    """Tests that LLM converters are buildable by resolving a target by name."""

    def test_build_llm_converter_resolves_target_by_name(self, registry: ConverterRegistry):
        target = MockPromptTarget()
        TargetRegistry.reset_instance()
        TargetRegistry.get_registry_singleton().register_instance(target, name="my_target")
        try:
            converter = registry.create_instance("TenseConverter", converter_target="my_target", tense="past")
            assert isinstance(converter, TenseConverter)
            assert converter._converter_target is target
        finally:
            TargetRegistry.reset_instance()

    def test_build_llm_converter_unknown_target_raises(self, registry: ConverterRegistry):
        TargetRegistry.reset_instance()
        try:
            with pytest.raises(ValueError, match="not found"):
                registry.create_instance("TenseConverter", converter_target="missing", tense="past")
        finally:
            TargetRegistry.reset_instance()


class TestClassMetadata:
    """Tests for converter class-catalog metadata building."""

    def _metadata_for(self, registry: ConverterRegistry, name: str) -> ConverterMetadata:
        return next(m for m in registry.get_all_registered_class_metadata() if m.class_name == name)

    def test_metadata_includes_supported_types(self, registry: ConverterRegistry):
        meta = self._metadata_for(registry, "Base64Converter")
        assert "text" in meta.supported_input_types
        assert "text" in meta.supported_output_types

    def test_metadata_carries_class_attributes(self, registry: ConverterRegistry):
        meta = self._metadata_for(registry, "Base64Converter")
        # Supported types are sourced from class attributes via Param.ClassAttr,
        # not from a fabricated instance identifier.
        assert "supported_input_types" in meta.class_attributes
        assert "text" in [str(dt) for dt in meta.class_attributes["supported_input_types"]]

    def test_metadata_has_no_catalog_visible_field(self, registry: ConverterRegistry):
        # catalog_visible is a presentation concern owned by the backend/frontend.
        assert not hasattr(self._metadata_for(registry, "Base64Converter"), "catalog_visible")

    def test_is_llm_based_flag(self, registry: ConverterRegistry):
        llm_based = (
            LLMGenericTextConverter,
            NoiseConverter,
            PersuasionConverter,
            ToneConverter,
            TenseConverter,
            TranslationConverter,
            VariationConverter,
        )
        for cls in llm_based:
            meta = self._metadata_for(registry, cls.__name__)
            assert meta.is_llm_based is True, f"{cls.__name__} should be LLM-based"
        assert self._metadata_for(registry, "Base64Converter").is_llm_based is False
        assert self._metadata_for(registry, "CaesarConverter").is_llm_based is False

    def test_parameters_extracted(self, registry: ConverterRegistry):
        meta = self._metadata_for(registry, "CaesarConverter")
        caesar_param = next(p for p in meta.parameters if p.name == "caesar_offset")
        assert caesar_param.default is REQUIRED_VALUE
        assert caesar_param.param_type is int
        assert caesar_param.reference is None
        assert caesar_param.is_string_coercible is True

    def test_surfaces_non_coercible_params(self, registry: ConverterRegistry):
        # An LLM-based converter exposes its target parameter (a registry reference)
        # for dynamic construction even though it cannot be coerced from a string.
        meta = self._metadata_for(registry, "PersuasionConverter")
        references = [p for p in meta.parameters if p.reference is not None]
        assert references, "expected at least one reference parameter (the LLM target)"
        assert any(p.is_reference_to(ComponentType.TARGET) for p in meta.parameters)


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


class _UnionTargetConverter:
    """Helper with a PEP 604 unioned target parameter for introspection tests."""

    def __init__(self, *, converter_target: PromptTarget | None = None, offset: int | None = None) -> None:
        self.converter_target = converter_target
        self.offset = offset


class _OptionalLiteralConverter:
    """Helper with an optional Literal parameter for choices extraction tests."""

    def __init__(self, *, fmt: Literal["A", "B"] | None = None) -> None:
        self.fmt = fmt


class TestDeriveParameters:
    """Tests for the converter-parameter derivation into the ``Parameter`` contract."""

    def test_unwraps_optional_into_param_type(self) -> None:
        from pyrit.models.identifiers import ConverterIdentifier

        params = derive_parameters(cls=_UnionTargetConverter, identifier_type=ConverterIdentifier)
        offset_param = next(p for p in params if p.name == "offset")
        assert offset_param.param_type is int
        assert offset_param.reference is None
        assert offset_param.is_string_coercible is True

    def test_target_becomes_reference(self) -> None:
        from pyrit.models.identifiers import ConverterIdentifier

        params = derive_parameters(cls=_UnionTargetConverter, identifier_type=ConverterIdentifier)
        target_param = next(p for p in params if p.name == "converter_target")
        assert target_param.reference is not None
        assert target_param.reference.component_type is ComponentType.TARGET
        assert target_param.param_type is None

    def test_optional_literal_choices(self) -> None:
        from pyrit.registry.resolution import display_choices

        fmt_param = next(p for p in derive_parameters(cls=_OptionalLiteralConverter) if p.name == "fmt")
        assert display_choices(fmt_param.param_type) == ("A", "B")


class TestNoBackendDependency:
    """The registry must be reusable without depending on pyrit.backend."""

    def test_module_has_no_backend_dependency(self) -> None:
        import ast
        import inspect

        import pyrit.registry.components.converter_registry as module

        tree = ast.parse(inspect.getsource(module))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(name.startswith("pyrit.backend") for name in imported_modules)


class TestRegistrationGate:
    """The identifier blueprint must line up with a resolvable contract for every converter."""

    def test_discovery_validates_all_converters(self, registry: ConverterRegistry) -> None:
        # Discovery registers every converter through ``register_class``, which
        # validates each class. Accessing the catalog therefore proves every
        # discovered converter is describable and buildable (all reference params
        # map to a wired registry); otherwise discovery would have raised.
        names = registry.get_class_names()
        assert names
        assert "Base64Converter" in names

    def test_every_converter_derives_a_contract(self, registry: ConverterRegistry) -> None:
        from pyrit.models.identifiers import ConverterIdentifier

        for name in registry.get_class_names():
            cls = registry.get_class(name)
            parameters = derive_parameters(cls=cls, identifier_type=ConverterIdentifier)
            # Reference params only ever carry a component type the resolver can map.
            for param in parameters:
                if param.reference is not None:
                    assert param.reference.component_type in (
                        ComponentType.TARGET,
                        ComponentType.CONVERTER,
                        ComponentType.SCORER,
                    )

    def test_is_llm_based_matches_target_reference(self, registry: ConverterRegistry) -> None:
        from pyrit.models.identifiers import ConverterIdentifier

        for meta in registry.get_all_registered_class_metadata():
            parameters = derive_parameters(cls=registry.get_class(meta.class_name), identifier_type=ConverterIdentifier)
            has_target = any(p.is_reference_to(ComponentType.TARGET) for p in parameters)
            assert meta.is_llm_based is has_target, f"is_llm_based mismatch for {meta.class_name}"

    def test_register_class_raises_for_unresolvable_reference(self, registry: ConverterRegistry) -> None:
        from unittest.mock import patch

        from pyrit.models.parameter import Parameter, RegistryReference

        target_ref = Parameter(
            name="converter_target",
            description="",
            reference=RegistryReference(component_type=ComponentType.TARGET, annotation=object),
        )
        # A class whose reference parameter has no wired registry must fail the
        # registration gate (validation runs at register_class time).
        with (
            patch("pyrit.registry.registry.derive_parameters", return_value=[target_ref]),
            patch("pyrit.registry.resolution._registry_getter_for_component_type", return_value=None),
        ):
            with pytest.raises(ValueError, match="no registry wired"):
                registry.register_class(Base64Converter)
