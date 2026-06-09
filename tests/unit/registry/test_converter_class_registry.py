# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ConverterClassRegistry and its introspection/coercion helpers.
"""

from typing import Literal

import pytest

from pyrit.prompt_converter import (
    Base64Converter,
    CaesarConverter,
    LLMGenericTextConverter,
    NoiseConverter,
    PersuasionConverter,
    PromptConverter,
    TenseConverter,
    ToneConverter,
    TranslationConverter,
    VariationConverter,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry.class_registries import (
    ConverterClassRegistry,
    ConverterMetadata,
)
from pyrit.registry.class_registries.converter_class_registry import (
    _coerce_params,
    _extract_parameters,
    _is_simple_type,
)
from pyrit.registry.object_registries import ConverterRegistry


@pytest.fixture
def registry():
    """Provide a fresh ConverterClassRegistry and reset both registries."""
    ConverterClassRegistry.reset_instance()
    ConverterRegistry.reset_instance()
    instance = ConverterClassRegistry.get_registry_singleton()
    yield instance
    ConverterClassRegistry.reset_instance()
    ConverterRegistry.reset_instance()


class TestDiscovery:
    """Tests for converter class discovery."""

    def test_discovers_known_converters(self, registry: ConverterClassRegistry) -> None:
        names = registry.get_names()
        assert "Base64Converter" in names
        assert "CaesarConverter" in names

    def test_discovers_non_catalog_converters(self, registry: ConverterClassRegistry) -> None:
        # SelectiveTextConverter is hidden from the catalog but must remain
        # discoverable/buildable so agents can use it.
        assert "SelectiveTextConverter" in registry

    def test_does_not_register_base_class(self, registry: ConverterClassRegistry) -> None:
        assert "PromptConverter" not in registry

    def test_keyed_by_exact_class_name(self, registry: ConverterClassRegistry) -> None:
        # Exact class name, not snake_case.
        assert "Base64Converter" in registry
        assert "base64_converter" not in registry


class TestGetConverterClass:
    """Tests for get_converter_class."""

    def test_returns_class(self, registry: ConverterClassRegistry) -> None:
        assert registry.get_converter_class(converter_type="Base64Converter") is Base64Converter

    def test_unknown_type_raises(self, registry: ConverterClassRegistry) -> None:
        with pytest.raises(ValueError, match="not found"):
            registry.get_converter_class(converter_type="NotARealConverter")


class TestCreateConverter:
    """Tests for create_instance (construction with coercion)."""

    def test_creates_instance(self, registry: ConverterClassRegistry) -> None:
        converter = registry.create_instance("Base64Converter")
        assert isinstance(converter, Base64Converter)

    def test_coerces_string_params(self, registry: ConverterClassRegistry) -> None:
        converter = registry.create_instance("CaesarConverter", caesar_offset="13")
        assert isinstance(converter, CaesarConverter)
        assert converter.get_identifier().params.get("caesar_offset") == 13

    def test_unknown_type_raises(self, registry: ConverterClassRegistry) -> None:
        with pytest.raises(ValueError, match="not found"):
            registry.create_instance("NotARealConverter")

    def test_does_not_register_into_instance_registry(self, registry: ConverterClassRegistry) -> None:
        registry.create_instance("Base64Converter")
        assert len(ConverterRegistry.get_registry_singleton()) == 0

    def test_honors_registered_default_kwargs(self, registry: ConverterClassRegistry) -> None:
        registry.register(CaesarConverter, name="CaesarDefault", default_kwargs={"caesar_offset": 5})
        converter = registry.create_instance("CaesarDefault")
        assert converter.get_identifier().params.get("caesar_offset") == 5

    def test_uses_registered_factory(self, registry: ConverterClassRegistry) -> None:
        sentinel = Base64Converter()
        registry.register(Base64Converter, name="B64Factory", factory=lambda **kwargs: sentinel)
        assert registry.create_instance("B64Factory") is sentinel


class TestMetadata:
    """Tests for converter metadata building."""

    def _metadata_for(self, registry: ConverterClassRegistry, name: str) -> ConverterMetadata:
        return next(m for m in registry.list_metadata() if m.class_name == name)

    def test_metadata_includes_supported_types(self, registry: ConverterClassRegistry) -> None:
        meta = self._metadata_for(registry, "Base64Converter")
        assert "text" in meta.supported_input_types
        assert "text" in meta.supported_output_types

    def test_metadata_has_no_catalog_visible_field(self, registry: ConverterClassRegistry) -> None:
        # catalog_visible is a presentation concern owned by the backend service.
        assert not hasattr(self._metadata_for(registry, "Base64Converter"), "catalog_visible")

    def test_is_llm_based_flag(self, registry: ConverterClassRegistry) -> None:
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

    def test_parameters_extracted(self, registry: ConverterClassRegistry) -> None:
        meta = self._metadata_for(registry, "CaesarConverter")
        caesar_param = next(p for p in meta.parameters if p.name == "caesar_offset")
        assert caesar_param.required is True
        assert caesar_param.annotation is int
        assert caesar_param.coercible_from_string is True

    def test_surfaces_non_coercible_params(self, registry: ConverterClassRegistry) -> None:
        # An LLM-based converter exposes its target parameter for dynamic
        # construction even though it cannot be coerced from a string.
        meta = self._metadata_for(registry, "PersuasionConverter")
        non_coercible = [p for p in meta.parameters if not p.coercible_from_string]
        assert non_coercible, "expected at least one non-coercible parameter (the LLM target)"


class _UnionTargetConverter:
    """Helper with a PEP 604 unioned target parameter for introspection tests."""

    def __init__(self, *, target: PromptTarget | None = None, offset: int | None = None) -> None:
        self.target = target
        self.offset = offset


class _BoolConverter:
    """Helper with a bool parameter for coercion tests."""

    def __init__(self, *, flag: bool = False) -> None:
        self.flag = flag


class _OptionalLiteralConverter:
    """Helper with an optional Literal parameter for choices extraction tests."""

    def __init__(self, *, fmt: Literal["A", "B"] | None = None) -> None:
        self.fmt = fmt


class TestHelpers:
    """Tests for the introspection/coercion helpers."""

    def test_is_simple_type(self) -> None:
        assert _is_simple_type(str) is True
        assert _is_simple_type(int | None) is True
        assert _is_simple_type(Literal["a", "b"]) is True
        assert _is_simple_type(PromptTarget) is False

    def test_coerce_params_pep604_union(self) -> None:
        coerced = _coerce_params(converter_class=_UnionTargetConverter, params={"offset": "42"})
        assert coerced["offset"] == 42

    def test_coerce_params_bool_false(self) -> None:
        coerced = _coerce_params(converter_class=_BoolConverter, params={"flag": "false"})
        assert coerced["flag"] is False

    def test_coerce_params_bool_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="expects bool"):
            _coerce_params(converter_class=_BoolConverter, params={"flag": "maybe"})

    def test_extract_parameters_exposes_raw_annotation(self) -> None:
        params = _extract_parameters(_UnionTargetConverter)
        offset_param = next(p for p in params if p.name == "offset")
        assert offset_param.annotation == (int | None)
        assert offset_param.coercible_from_string is True

    def test_extract_parameters_includes_non_coercible(self) -> None:
        params = _extract_parameters(_UnionTargetConverter)
        target_param = next(p for p in params if p.name == "target")
        assert target_param.coercible_from_string is False

    def test_extract_parameters_optional_literal_choices(self) -> None:
        params = _extract_parameters(_OptionalLiteralConverter)
        fmt_param = next(p for p in params if p.name == "fmt")
        assert fmt_param.choices == ("A", "B")

    def test_extract_parameters_sets_requires_llm(self) -> None:
        params = _extract_parameters(_UnionTargetConverter)
        target_param = next(p for p in params if p.name == "target")
        offset_param = next(p for p in params if p.name == "offset")
        assert target_param.requires_llm is True
        assert offset_param.requires_llm is False


class TestSingleton:
    """Tests for the registry singleton lifecycle."""

    def test_singleton_identity(self) -> None:
        ConverterClassRegistry.reset_instance()
        first = ConverterClassRegistry.get_registry_singleton()
        second = ConverterClassRegistry.get_registry_singleton()
        assert first is second
        ConverterClassRegistry.reset_instance()

    def test_is_subclass_relationship(self, registry: ConverterClassRegistry) -> None:
        assert issubclass(registry.get_converter_class(converter_type="Base64Converter"), PromptConverter)

    def test_module_has_no_backend_dependency(self) -> None:
        # The central goal of this refactor is reuse without depending on pyrit.backend.
        import ast
        import inspect

        import pyrit.registry.class_registries.converter_class_registry as module

        tree = ast.parse(inspect.getsource(module))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        assert not any(name.startswith("pyrit.backend") for name in imported_modules)
