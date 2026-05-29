# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType
from pyrit.prompt_converter import ConverterResult, PromptConverter
from pyrit.registry.object_registries.converter_registry import ConverterRegistry


class MockTextConverter(PromptConverter):
    """Mock text-to-text converter for testing."""

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """Convert prompt (no-op for testing).

        Args:
            prompt (str): The prompt to convert.
            input_type (PromptDataType): The input type. Defaults to "text".

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

        Args:
            prompt (str): The prompt to convert.
            input_type (PromptDataType): The input type. Defaults to "image_path".

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

        Args:
            prompt (str): The prompt to convert.
            input_type (PromptDataType): The input type. Defaults to "text".

        Returns:
            ConverterResult: The unchanged prompt.
        """
        return ConverterResult(output_text=prompt, output_type="text")


class TestConverterRegistrySingleton:
    """Tests for the singleton pattern in ConverterRegistry."""

    def setup_method(self):
        """Reset the singleton before each test."""
        ConverterRegistry.reset_instance()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_get_registry_singleton_returns_same_instance(self):
        """Test that get_registry_singleton returns the same singleton each time."""
        instance1 = ConverterRegistry.get_registry_singleton()
        instance2 = ConverterRegistry.get_registry_singleton()

        assert instance1 is instance2

    def test_get_registry_singleton_returns_converter_registry_type(self):
        """Test that get_registry_singleton returns a ConverterRegistry instance."""
        instance = ConverterRegistry.get_registry_singleton()
        assert isinstance(instance, ConverterRegistry)

    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        instance1 = ConverterRegistry.get_registry_singleton()
        ConverterRegistry.reset_instance()
        instance2 = ConverterRegistry.get_registry_singleton()

        assert instance1 is not instance2


class TestConverterRegistryRegisterInstance:
    """Tests for register_instance functionality in ConverterRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConverterRegistry.reset_instance()
        self.registry = ConverterRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_register_instance_with_custom_name(self):
        """Test registering a converter with a custom name."""
        converter = MockTextConverter()
        self.registry.register_instance(converter, name="custom_converter")

        assert "custom_converter" in self.registry
        assert self.registry.get("custom_converter") is converter

    def test_register_instance_generates_name_from_class(self):
        """Test that register_instance generates a name from class name when not provided."""
        converter = MockTextConverter()
        self.registry.register_instance(converter)

        # Name should be derived from class name with hash suffix
        names = self.registry.get_names()
        assert len(names) == 1
        assert names[0].startswith("MockTextConverter::")

    def test_register_instance_multiple_converters_unique_names(self):
        """Test registering multiple converters generates unique names."""
        converter1 = MockTextConverter()
        converter2 = MockImageConverter()

        self.registry.register_instance(converter1)
        self.registry.register_instance(converter2)

        assert len(self.registry) == 2

    def test_register_instance_same_converter_type_different_names(self):
        """Test that same converter class can be registered with different names."""
        converter1 = MockTextConverter()
        converter2 = MockTextConverter()

        self.registry.register_instance(converter1, name="converter_1")
        self.registry.register_instance(converter2, name="converter_2")

        assert len(self.registry) == 2

    def test_register_instance_duplicate_name_overwrites(self):
        """Test that registering with a duplicate name silently overwrites the previous instance."""
        converter1 = MockTextConverter()
        converter2 = MockImageConverter()

        self.registry.register_instance(converter1, name="shared_name")
        self.registry.register_instance(converter2, name="shared_name")

        assert len(self.registry) == 1
        assert self.registry.get("shared_name") is converter2


class TestConverterRegistryGetInstanceByName:
    """Tests for get_instance_by_name functionality in ConverterRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConverterRegistry.reset_instance()
        self.registry = ConverterRegistry.get_registry_singleton()
        self.converter = MockTextConverter()
        self.registry.register_instance(self.converter, name="test_converter")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_get_instance_by_name_returns_converter(self):
        """Test getting a registered converter by name."""
        result = self.registry.get_instance_by_name("test_converter")
        assert result is self.converter

    def test_get_instance_by_name_nonexistent_returns_none(self):
        """Test that getting a non-existent converter returns None."""
        result = self.registry.get_instance_by_name("nonexistent")
        assert result is None


class TestConverterRegistryBuildMetadata:
    """Tests for _build_metadata functionality in ConverterRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ConverterRegistry.reset_instance()
        self.registry = ConverterRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_build_metadata_includes_class_name(self):
        """Test that metadata includes the converter class name."""
        converter = MockTextConverter()
        self.registry.register_instance(converter, name="text_converter")

        metadata = self.registry.list_metadata()
        assert len(metadata) == 1
        assert metadata[0].class_name == "MockTextConverter"

    def test_build_metadata_includes_supported_input_types(self):
        """Test that metadata includes supported_input_types in params."""
        converter = MockTextConverter()
        self.registry.register_instance(converter, name="text_converter")

        metadata = self.registry.list_metadata()
        assert metadata[0].params["supported_input_types"] == ("text",)

    def test_build_metadata_includes_supported_output_types(self):
        """Test that metadata includes supported_output_types in params."""
        converter = MockTextConverter()
        self.registry.register_instance(converter, name="text_converter")

        metadata = self.registry.list_metadata()
        assert metadata[0].params["supported_output_types"] == ("text",)

    def test_build_metadata_is_component_identifier(self):
        """Test that metadata is the converter's ComponentIdentifier."""
        converter = MockTextConverter()
        self.registry.register_instance(converter, name="text_converter")

        metadata = self.registry.list_metadata()
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0] == converter.get_identifier()

    def test_build_metadata_different_modalities(self):
        """Test that metadata reflects converter-specific modalities."""
        converter = MockImageConverter()
        self.registry.register_instance(converter, name="image_converter")

        metadata = self.registry.list_metadata()
        assert metadata[0].params["supported_input_types"] == ("image_path",)
        assert metadata[0].params["supported_output_types"] == ("text",)
        assert metadata[0].class_name == "MockImageConverter"


class TestConverterRegistryListMetadataFiltering:
    """Tests for list_metadata filtering in ConverterRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry with multiple converters."""
        ConverterRegistry.reset_instance()
        self.registry = ConverterRegistry.get_registry_singleton()

        self.text_converter1 = MockTextConverter()
        self.text_converter2 = MockTextConverter()
        self.image_converter = MockImageConverter()
        self.multi_modal_converter = MockMultiModalConverter()

        self.registry.register_instance(self.text_converter1, name="text_converter_1")
        self.registry.register_instance(self.text_converter2, name="text_converter_2")
        self.registry.register_instance(self.image_converter, name="image_converter")
        self.registry.register_instance(self.multi_modal_converter, name="multi_modal_converter")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_list_metadata_no_filter_returns_all(self):
        """Test that list_metadata without filters returns all items."""
        metadata = self.registry.list_metadata()
        assert len(metadata) == 4

    def test_list_metadata_filter_by_class_name(self):
        """Test filtering metadata by class_name."""
        metadata = self.registry.list_metadata(include_filters={"class_name": "MockTextConverter"})
        assert len(metadata) == 2
        assert all(m.class_name == "MockTextConverter" for m in metadata)

    def test_list_metadata_filter_by_supported_input_type(self):
        """Test filtering metadata by supported_input_types (containment check)."""
        # "text" is in supported_input_types for MockTextConverter and MockMultiModalConverter
        metadata = self.registry.list_metadata(include_filters={"supported_input_types": "text"})
        assert len(metadata) == 3  # 2 text converters + 1 multi-modal
        class_names = {m.class_name for m in metadata}
        assert "MockTextConverter" in class_names
        assert "MockMultiModalConverter" in class_names

    def test_list_metadata_exclude_by_class_name(self):
        """Test excluding metadata by class_name."""
        metadata = self.registry.list_metadata(exclude_filters={"class_name": "MockTextConverter"})
        assert len(metadata) == 2
        assert all(m.class_name != "MockTextConverter" for m in metadata)

    def test_list_metadata_combined_include_and_exclude(self):
        """Test combined include and exclude filters."""
        # Include converters that accept text, exclude MockMultiModalConverter
        metadata = self.registry.list_metadata(
            include_filters={"supported_input_types": "text"},
            exclude_filters={"class_name": "MockMultiModalConverter"},
        )
        assert len(metadata) == 2
        assert all(m.class_name == "MockTextConverter" for m in metadata)


class TestConverterRegistryInheritedMethods:
    """Tests for inherited methods from RetrievableInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry."""
        ConverterRegistry.reset_instance()
        self.registry = ConverterRegistry.get_registry_singleton()
        self.converter = MockTextConverter()
        self.registry.register_instance(self.converter, name="test_converter")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ConverterRegistry.reset_instance()

    def test_contains_registered_name(self):
        """Test __contains__ for registered name."""
        assert "test_converter" in self.registry

    def test_contains_unregistered_name(self):
        """Test __contains__ for unregistered name."""
        assert "unknown_converter" not in self.registry

    def test_len_returns_count(self):
        """Test __len__ returns correct count."""
        assert len(self.registry) == 1

    def test_iter_yields_names(self):
        """Test __iter__ yields registered names."""
        names = list(self.registry)
        assert "test_converter" in names

    def test_get_names_returns_sorted_list(self):
        """Test get_names returns sorted list of names."""
        self.registry.register_instance(MockImageConverter(), name="alpha_converter")
        self.registry.register_instance(MockImageConverter(), name="zeta_converter")

        names = self.registry.get_names()
        assert names == ["alpha_converter", "test_converter", "zeta_converter"]

    def test_get_all_instances_returns_all(self):
        """Test get_all_instances returns list of all registered entries."""
        image_converter = MockImageConverter()
        self.registry.register_instance(image_converter, name="image_converter")

        all_entries = self.registry.get_all_instances()
        assert len(all_entries) == 2
        entry_map = {e.name: e for e in all_entries}
        assert entry_map["test_converter"].instance is self.converter
        assert entry_map["image_converter"].instance is image_converter
