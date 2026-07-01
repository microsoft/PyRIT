# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend converter service.
"""

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit import prompt_converter
from pyrit.backend.models.converters import (
    ConverterPreviewRequest,
    CreateConverterRequest,
)
from pyrit.backend.services.converter_service import (
    ConverterService,
    get_converter_service,
)
from pyrit.models import ComponentIdentifier
from pyrit.prompt_converter import (
    Base64Converter,
    CaesarConverter,
    RepeatTokenConverter,
    SuffixAppendConverter,
)
from pyrit.prompt_converter.prompt_converter import get_converter_modalities
from pyrit.registry.components import ConverterRegistry


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the converter registry before each test."""
    ConverterRegistry.reset_registry_singleton()
    yield
    ConverterRegistry.reset_registry_singleton()


class TestListConverters:
    """Tests for ConverterService.list_converters method."""

    async def test_list_converters_returns_empty_when_no_converters(self) -> None:
        """Test that list_converters returns empty list when no converters exist."""
        service = ConverterService()

        result = await service.list_converters_async()

        assert result.items == []

    async def test_list_converters_returns_converters_from_registry(self) -> None:
        """Test that list_converters returns converters from registry with full params."""
        service = ConverterService()

        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        mock_identifier = ComponentIdentifier(
            class_name="MockConverter",
            class_module="tests.unit.backend.test_converter_service",
            params={
                "supported_input_types": ("text",),
                "supported_output_types": ("text",),
                "param1": "value1",
                "param2": 42,
            },
        )
        mock_converter.get_identifier.return_value = mock_identifier
        service._registry.instances.register(mock_converter, name="conv-1")

        result = await service.list_converters_async()

        assert len(result.items) == 1
        assert result.items[0].converter_id == "conv-1"
        assert result.items[0].identifier.class_name == "MockConverter"
        assert result.items[0].identifier.supported_input_types == ["text"]
        assert result.items[0].identifier.supported_output_types == ["text"]
        assert result.items[0].identifier.params["param1"] == "value1"
        assert result.items[0].identifier.params["param2"] == 42


class TestListConverterCatalog:
    """Tests for ConverterService.list_converter_catalog_async method."""

    async def test_list_converter_catalog_returns_known_converter_types(self) -> None:
        """Test that the converter catalog exposes available converter classes."""
        service = ConverterService()

        result = await service.list_converter_catalog_async()

        converter_types = [item.converter_type for item in result.items]
        assert "Base64Converter" in converter_types
        assert "CaesarConverter" in converter_types

    async def test_list_converter_catalog_includes_supported_types(self) -> None:
        """Test that catalog entries include supported input and output types."""
        service = ConverterService()

        result = await service.list_converter_catalog_async()

        base64_entry = next(item for item in result.items if item.converter_type == "Base64Converter")
        assert "text" in base64_entry.supported_input_types
        assert "text" in base64_entry.supported_output_types

    async def test_catalog_includes_all_constructible_converters(self) -> None:
        """The catalog surfaces every constructible converter, including base/helper classes.

        Whether to display a given converter is left to the caller (e.g. the frontend),
        so the service no longer hides anything.
        """
        service = ConverterService()

        result = await service.list_converter_catalog_async()

        converter_types = [item.converter_type for item in result.items]
        assert "Base64Converter" in converter_types
        assert "SelectiveTextConverter" in converter_types

    async def test_catalog_serializes_parameter_type(self) -> None:
        """Catalog renders the raw annotation into a human-readable type_name."""
        service = ConverterService()

        result = await service.list_converter_catalog_async()

        caesar_entry = next(item for item in result.items if item.converter_type == "CaesarConverter")
        caesar_param = next(p for p in caesar_entry.parameters if p.name == "caesar_offset")
        assert caesar_param.type_name == "int"

    async def test_catalog_excludes_non_coercible_params(self) -> None:
        """Catalog only surfaces params that can be set from a string (e.g. not the LLM target)."""
        service = ConverterService()

        result = await service.list_converter_catalog_async()

        persuasion_entry = next(item for item in result.items if item.converter_type == "PersuasionConverter")
        assert persuasion_entry.is_llm_based is True
        assert all("Target" not in p.type_name for p in persuasion_entry.parameters)


class TestGetConverter:
    """Tests for ConverterService.get_converter method."""

    async def test_get_converter_returns_none_for_nonexistent(self) -> None:
        """Test that get_converter returns None for non-existent converter."""
        service = ConverterService()

        result = await service.get_converter_async(converter_id="nonexistent-id")

        assert result is None

    async def test_get_converter_returns_converter_from_registry(self) -> None:
        """Test that get_converter returns converter built from registry object."""
        service = ConverterService()

        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        mock_identifier = ComponentIdentifier(
            class_name="MockConverter",
            class_module="tests.unit.backend.test_converter_service",
            params={
                "supported_input_types": ("text",),
                "supported_output_types": ("text",),
                "param1": "value1",
            },
        )
        mock_converter.get_identifier.return_value = mock_identifier
        service._registry.instances.register(mock_converter, name="conv-1")

        result = await service.get_converter_async(converter_id="conv-1")

        assert result is not None
        assert result.converter_id == "conv-1"
        assert result.identifier.class_name == "MockConverter"


class TestGetConverterObject:
    """Tests for ConverterService.get_converter_object method."""

    def test_get_converter_object_returns_none_for_nonexistent(self) -> None:
        """Test that get_converter_object returns None for non-existent converter."""
        service = ConverterService()

        result = service.get_converter_object(converter_id="nonexistent-id")

        assert result is None

    def test_get_converter_object_returns_object_from_registry(self) -> None:
        """Test that get_converter_object returns the actual converter object."""
        service = ConverterService()
        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        service._registry.instances.register(mock_converter, name="conv-1")

        result = service.get_converter_object(converter_id="conv-1")

        assert result is mock_converter


class TestCreateConverter:
    """Tests for ConverterService.create_converter method."""

    async def test_create_converter_raises_for_invalid_type(self) -> None:
        """Test that create_converter raises for invalid converter type."""
        service = ConverterService()

        request = CreateConverterRequest(
            type="NonExistentConverter",
            params={},
        )

        with pytest.raises(ValueError, match="not found"):
            await service.create_converter_async(request=request)

    async def test_create_converter_success(self) -> None:
        """Test successful converter creation."""
        service = ConverterService()

        request = CreateConverterRequest(
            type="Base64Converter",
            display_name="My Base64",
            params={},
        )

        result = await service.create_converter_async(request=request)

        assert result.converter_id is not None
        assert result.converter_type == "Base64Converter"
        assert result.display_name == "My Base64"

    async def test_create_converter_registers_in_registry(self) -> None:
        """Test that create_converter registers object in registry."""
        service = ConverterService()

        request = CreateConverterRequest(
            type="Base64Converter",
            params={},
        )

        result = await service.create_converter_async(request=request)

        # Object should be retrievable from registry
        converter_obj = service.get_converter_object(converter_id=result.converter_id)
        assert converter_obj is not None


class TestPersistDataUriParams:
    """Tests for ConverterService._persist_data_uri_params_async (registry-metadata driven)."""

    async def test_persist_data_uri_wraps_path_param(self) -> None:
        """A data-URI value for a ``Path``-typed constructor param is persisted and wrapped in Path."""
        service = ConverterService()

        mock_serializer = MagicMock()
        mock_serializer.value = "/tmp/persisted.pdf"
        mock_serializer.save_data_async = AsyncMock()

        params = {"existing_pdf": "data:application/pdf;base64,iVBORw0KGgo="}

        with patch(
            "pyrit.backend.services.converter_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            result = await service._persist_data_uri_params_async(converter_type="PDFConverter", params=params)

        assert result["existing_pdf"] == Path("/tmp/persisted.pdf")
        mock_serializer.save_data_async.assert_awaited_once_with(data=base64.b64decode("iVBORw0KGgo="))

    async def test_persist_data_uri_keeps_str_param_as_string(self) -> None:
        """A data-URI value for a ``str``-typed constructor param is persisted but left as a string."""
        service = ConverterService()

        mock_serializer = MagicMock()
        mock_serializer.value = "/tmp/words.yaml"
        mock_serializer.save_data_async = AsyncMock()

        params = {"wordswap_path": "data:text/yaml;base64,aGVsbG8="}

        with patch(
            "pyrit.backend.services.converter_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            result = await service._persist_data_uri_params_async(
                converter_type="ColloquialWordswapConverter", params=params
            )

        assert result["wordswap_path"] == "/tmp/words.yaml"
        assert not isinstance(result["wordswap_path"], Path)

    async def test_persist_data_uri_ignores_param_not_on_converter(self) -> None:
        """A data-URI value under a name that is not a constructor param is left unchanged."""
        service = ConverterService()

        with patch("pyrit.backend.services.converter_service.data_serializer_factory") as mock_factory:
            result = await service._persist_data_uri_params_async(
                converter_type="PDFConverter",
                params={"not_a_param": "data:application/pdf;base64,iVBORw0KGgo="},
            )

        assert result == {"not_a_param": "data:application/pdf;base64,iVBORw0KGgo="}
        mock_factory.assert_not_called()

    async def test_persist_data_uri_noop_for_unregistered_type(self) -> None:
        """When the converter type has no registry metadata, params pass through untouched."""
        service = ConverterService()

        params = {"existing_pdf": "data:application/pdf;base64,iVBORw0KGgo="}

        with patch("pyrit.backend.services.converter_service.data_serializer_factory") as mock_factory:
            result = await service._persist_data_uri_params_async(converter_type="NonExistentConverter", params=params)

        assert result == params
        mock_factory.assert_not_called()

    async def test_persist_data_uri_ignores_non_data_uri_values(self) -> None:
        """Values that are not data URIs are left unchanged."""
        service = ConverterService()

        params = {"existing_pdf": "/already/a/path.pdf", "font_size": 12}

        with patch("pyrit.backend.services.converter_service.data_serializer_factory") as mock_factory:
            result = await service._persist_data_uri_params_async(converter_type="PDFConverter", params=params)

        assert result == params
        mock_factory.assert_not_called()


class TestPreviewConversion:
    """Tests for ConverterService.preview_conversion method."""

    async def test_preview_conversion_raises_for_nonexistent_converter(self) -> None:
        """Test that preview raises ValueError for non-existent converter ID."""
        service = ConverterService()

        request = ConverterPreviewRequest(
            original_value="test",
            original_value_data_type="text",
            converter_ids=["nonexistent"],
        )

        with pytest.raises(ValueError, match="not found"):
            await service.preview_conversion_async(request=request)

    async def test_preview_conversion_with_converter_ids(self) -> None:
        """Test preview with converter IDs."""
        service = ConverterService()

        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        mock_result = MagicMock()
        mock_result.output_text = "encoded_value"
        mock_result.output_type = "text"
        mock_converter.convert_async = AsyncMock(return_value=mock_result)
        service._registry.instances.register(mock_converter, name="conv-1")

        request = ConverterPreviewRequest(
            original_value="test",
            original_value_data_type="text",
            converter_ids=["conv-1"],
        )

        result = await service.preview_conversion_async(request=request)

        assert result.original_value == "test"
        assert result.converted_value == "encoded_value"
        assert len(result.steps) == 1
        assert result.steps[0].converter_id == "conv-1"

    async def test_preview_conversion_chains_multiple_converters(self) -> None:
        """Test that preview chains multiple converters."""
        service = ConverterService()

        mock_converter1 = MagicMock(spec=prompt_converter.PromptConverter)
        mock_result1 = MagicMock()
        mock_result1.output_text = "step1_output"
        mock_result1.output_type = "text"
        mock_converter1.convert_async = AsyncMock(return_value=mock_result1)

        mock_converter2 = MagicMock(spec=prompt_converter.PromptConverter)
        mock_result2 = MagicMock()
        mock_result2.output_text = "step2_output"
        mock_result2.output_type = "text"
        mock_converter2.convert_async = AsyncMock(return_value=mock_result2)

        service._registry.instances.register(mock_converter1, name="conv-1")
        service._registry.instances.register(mock_converter2, name="conv-2")

        request = ConverterPreviewRequest(
            original_value="input",
            original_value_data_type="text",
            converter_ids=["conv-1", "conv-2"],
        )

        result = await service.preview_conversion_async(request=request)

        assert result.converted_value == "step2_output"
        assert len(result.steps) == 2
        mock_converter2.convert_async.assert_called_with(prompt="step1_output", input_type="text")

    async def test_preview_conversion_persists_data_uri_for_image_path(self) -> None:
        """Data URIs on *_path types are decoded via the DEFAULT_MEDIA_EXTENSIONS map and persisted."""
        service = ConverterService()

        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        mock_result = MagicMock()
        mock_result.output_text = "/tmp/persisted.png"
        mock_result.output_type = "image_path"
        mock_converter.convert_async = AsyncMock(return_value=mock_result)
        service._registry.instances.register(mock_converter, name="conv-1")

        mock_serializer = MagicMock()
        mock_serializer.value = "/tmp/persisted.png"
        mock_serializer.save_b64_image_async = AsyncMock()

        request = ConverterPreviewRequest(
            original_value="data:image/png;base64,iVBORw0KGgo=",
            original_value_data_type="image_path",
            converter_ids=["conv-1"],
        )

        with patch(
            "pyrit.backend.services.converter_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as mock_factory:
            await service.preview_conversion_async(request=request)

        mock_factory.assert_called_once()
        # ext is the image_path mapping from DEFAULT_MEDIA_EXTENSIONS
        assert mock_factory.call_args.kwargs["extension"] == ".png"
        assert mock_factory.call_args.kwargs["data_type"] == "image_path"
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="iVBORw0KGgo=")

    async def test_preview_conversion_persists_raw_base64_for_audio_path(self) -> None:
        """Values that aren't URLs/data URIs/existing files are treated as raw base64 and persisted."""
        service = ConverterService()

        mock_converter = MagicMock(spec=prompt_converter.PromptConverter)
        mock_result = MagicMock()
        mock_result.output_text = "/tmp/persisted.wav"
        mock_result.output_type = "audio_path"
        mock_converter.convert_async = AsyncMock(return_value=mock_result)
        service._registry.instances.register(mock_converter, name="conv-1")

        mock_serializer = MagicMock()
        mock_serializer.value = "/tmp/persisted.wav"
        mock_serializer.save_b64_image_async = AsyncMock()

        raw_b64 = "UklGRiQAAABXQVZF"
        request = ConverterPreviewRequest(
            original_value=raw_b64,
            original_value_data_type="audio_path",
            converter_ids=["conv-1"],
        )

        with patch(
            "pyrit.backend.services.converter_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as mock_factory:
            await service.preview_conversion_async(request=request)

        mock_factory.assert_called_once()
        # ext is the audio_path mapping from DEFAULT_MEDIA_EXTENSIONS
        assert mock_factory.call_args.kwargs["extension"] == ".wav"
        assert mock_factory.call_args.kwargs["data_type"] == "audio_path"
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data=raw_b64)


class TestGetConverterObjectsForIds:
    """Tests for ConverterService.get_converter_objects_for_ids method."""

    def test_get_converter_objects_for_ids_raises_for_nonexistent(self) -> None:
        """Test that method raises ValueError for non-existent ID."""
        service = ConverterService()

        with pytest.raises(ValueError, match="not found"):
            service.get_converter_objects_for_ids(converter_ids=["nonexistent"])

    def test_get_converter_objects_for_ids_returns_objects(self) -> None:
        """Test that method returns converter objects in order."""
        service = ConverterService()

        mock1 = MagicMock(spec=prompt_converter.PromptConverter)
        mock2 = MagicMock(spec=prompt_converter.PromptConverter)
        service._registry.instances.register(mock1, name="conv-1")
        service._registry.instances.register(mock2, name="conv-2")

        result = service.get_converter_objects_for_ids(converter_ids=["conv-1", "conv-2"])

        assert result == [mock1, mock2]


class TestConverterServiceSingleton:
    """Tests for get_converter_service singleton function."""

    def test_get_converter_service_returns_converter_service(self) -> None:
        """Test that get_converter_service returns a ConverterService instance."""
        get_converter_service.cache_clear()

        service = get_converter_service()
        assert isinstance(service, ConverterService)

    def test_get_converter_service_returns_same_instance(self) -> None:
        """Test that get_converter_service returns the same instance."""
        get_converter_service.cache_clear()

        service1 = get_converter_service()
        service2 = get_converter_service()
        assert service1 is service2


# ============================================================================
# Real Converter Integration Tests
# ============================================================================


def _get_all_converter_names() -> list[str]:
    """
    Dynamically collect all converter class names from the codebase.

    Uses get_converter_modalities() which reads from prompt_converter.__all__
    and filters to only actual PromptConverter subclasses.
    """
    return [name for name, _, _ in get_converter_modalities()]


def _try_instantiate_converter(converter_name: str):
    """
    Try to instantiate a converter with minimal representative arguments.

    Uses mock objects for complex dependencies (PromptTarget, PromptConverter)
    and provides minimal valid values for simple required parameters so that the
    identifier extraction test covers ALL converters without skipping.

    Returns:
        Tuple of (converter_instance, error_message).
        If successful, error_message is None.
        If failed, converter_instance is None and error_message explains why.
    """
    import inspect
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from pyrit.common.apply_defaults import _RequiredValueSentinel
    from pyrit.prompt_target import PromptTarget

    # Converters requiring external credentials or resources that can't be mocked
    # at the constructor level — these validate env vars / files in __init__ body
    skip_converters = {
        "AddImageTextConverter",  # requires a real image file on disk (loaded eagerly in __init__)
        "AzureSpeechAudioToTextConverter",  # requires AZURE_SPEECH_REGION env var
        "AzureSpeechTextToAudioConverter",  # requires AZURE_SPEECH_REGION env var
        "TransparencyAttackConverter",  # requires a real JPEG image file on disk
    }

    # Converter-specific overrides for params with validation
    overrides: dict = {
        "AddTextImageConverter": {"text_to_add": "test text"},
        "CodeChameleonConverter": {"encrypt_type": "reverse"},
        "SearchReplaceConverter": {"pattern": "foo", "replace": "bar"},
        "PersuasionConverter": {"persuasion_technique": "logical_appeal"},
        "ImagePromptStyleConverter": {"filter_name": "gritty_documentary"},
    }

    converter_cls = getattr(prompt_converter, converter_name, None)
    if converter_cls is None:
        return None, f"Converter {converter_name} not found in prompt_converter module"

    if converter_name in skip_converters:
        return None, None  # Signal to skip without failure

    # Build minimal kwargs based on constructor signature
    sig = inspect.signature(converter_cls.__init__)
    kwargs: dict = {}

    for pname, param in sig.parameters.items():
        if pname in ("self", "args", "kwargs"):
            continue

        # Check if this param has a REQUIRED_VALUE sentinel as its default
        is_required_value = isinstance(param.default, _RequiredValueSentinel)
        has_no_default = param.default is inspect.Parameter.empty

        if not has_no_default and not is_required_value:
            continue  # Has a real default — skip

        # Check overrides first
        if converter_name in overrides and pname in overrides[converter_name]:
            kwargs[pname] = overrides[converter_name][pname]
            continue

        ann = param.annotation
        ann_str = str(ann) if ann is not inspect.Parameter.empty else ""

        # PromptTarget — mock it with a proper identifier
        if ann is not inspect.Parameter.empty and (
            (isinstance(ann, type) and issubclass(ann, PromptTarget)) or "PromptTarget" in ann_str
        ):
            mock_target = MagicMock(spec=PromptTarget)
            # Configure get_identifier() to return a real identifier so that
            # _create_identifier can promote it into the typed child slot.
            mock_id = ComponentIdentifier(
                class_name="MockChatTarget",
                class_module="mock",
                params={"model_name": "test-model"},
            )
            mock_target.get_identifier.return_value = mock_id
            kwargs[pname] = mock_target
        # PromptConverter — use a real simple converter to avoid JSON serialization issues
        elif "PromptConverter" in ann_str:
            kwargs[pname] = Base64Converter()
        # TextSelectionStrategy — use a real concrete strategy
        elif "TextSelectionStrategy" in ann_str:
            from pyrit.prompt_converter.text_selection_strategy import AllWordsSelectionStrategy

            kwargs[pname] = AllWordsSelectionStrategy()
        # TextJailBreak — use string template
        elif "TextJailBreak" in ann_str:
            from pyrit.datasets.jailbreak.text_jailbreak import TextJailBreak

            kwargs[pname] = TextJailBreak(string_template="Test {{ prompt }}")
        # Path — use a temp JPEG file
        elif ann is Path or "Path" in ann_str:
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)  # noqa: SIM115
            # Minimal valid JPEG header
            tmp.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00")
            tmp.close()
            kwargs[pname] = Path(tmp.name)
        # str
        elif ann is str or ann_str == "<class 'str'>":
            kwargs[pname] = "test_value"
        # int
        elif ann is int or ann_str == "<class 'int'>":
            kwargs[pname] = 1
        # float
        elif ann is float or ann_str == "<class 'float'>":
            kwargs[pname] = 0.5
        else:
            kwargs[pname] = "test_value"

    # Apply converter-specific overrides (may override defaults or add params with
    # default values that fail validation, e.g. img_to_add="" in AddImageTextConverter)
    if converter_name in overrides:
        kwargs.update(overrides[converter_name])

    try:
        instance = converter_cls(**kwargs)
        return instance, None
    except Exception as e:
        return None, f"Could not instantiate {converter_name}: {e}"


# Get all converter names dynamically
ALL_CONVERTERS = _get_all_converter_names()


class TestBuildInstanceFromObjectWithRealConverters:
    """
    Integration tests that verify _build_instance_from_object works with real converters.

    These tests ensure the identifier extraction works correctly across all converter types.
    Uses dynamic discovery to test ALL converters in the codebase.
    """

    @pytest.mark.parametrize("converter_name", ALL_CONVERTERS)
    def test_build_instance_from_converter(self, converter_name: str) -> None:
        """
        Test that _build_instance_from_object works with each converter.

        Instantiates every converter with minimal representative arguments
        (using mocks for complex dependencies like PromptTarget) and verifies:
        - converter_id is set correctly
        - identifier.class_name matches the class name
        - identifier supported input/output types are lists or None
        """
        # Try to instantiate the converter
        converter_instance, error = _try_instantiate_converter(converter_name)

        if converter_instance is None and error is None:
            pytest.skip(f"{converter_name} requires external credentials/resources")
        if error:
            pytest.fail(error)

        # Build the instance using the service method
        service = ConverterService()
        result = service._build_instance_from_object(converter_id="test-id", converter_obj=converter_instance)

        # Verify the result
        assert result.converter_id == "test-id"
        assert result.identifier.class_name == converter_name
        assert result.identifier.supported_input_types is None or isinstance(
            result.identifier.supported_input_types, list
        )
        assert result.identifier.supported_output_types is None or isinstance(
            result.identifier.supported_output_types, list
        )


class TestConverterParamsExtraction:
    """
    Tests that verify converter-specific params are correctly extracted onto the
    identifier.

    Uses converters with known parameters to verify the params are properly
    captured from the identifier.
    """

    def test_caesar_converter_params(self) -> None:
        """Test that CaesarConverter params are extracted correctly."""
        converter = CaesarConverter(caesar_offset=13)
        service = ConverterService()
        result = service._build_instance_from_object(converter_id="test-id", converter_obj=converter)

        assert result.identifier.class_name == "CaesarConverter"
        assert result.identifier.params.get("caesar_offset") == 13

    def test_suffix_append_converter_params(self) -> None:
        """Test that SuffixAppendConverter params are extracted correctly."""
        converter = SuffixAppendConverter(suffix="test suffix")
        service = ConverterService()
        result = service._build_instance_from_object(converter_id="test-id", converter_obj=converter)

        assert result.identifier.class_name == "SuffixAppendConverter"
        assert result.identifier.params.get("suffix") == "test suffix"

    def test_repeat_token_converter_params(self) -> None:
        """Test that RepeatTokenConverter params are extracted correctly."""
        converter = RepeatTokenConverter(token_to_repeat="x", times_to_repeat=5)
        service = ConverterService()
        result = service._build_instance_from_object(converter_id="test-id", converter_obj=converter)

        assert result.identifier.class_name == "RepeatTokenConverter"
        assert result.identifier.params.get("token_to_repeat") == "x"
        assert result.identifier.params.get("times_to_repeat") == 5

    def test_base64_converter_default_params(self) -> None:
        """Test that Base64Converter default params are captured."""
        converter = Base64Converter()
        service = ConverterService()
        result = service._build_instance_from_object(converter_id="test-id", converter_obj=converter)

        assert result.identifier.class_name == "Base64Converter"
        # Verify type info is populated from identifier
        assert isinstance(result.identifier.supported_input_types, list)
        assert isinstance(result.identifier.supported_output_types, list)
