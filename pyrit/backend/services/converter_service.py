# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter service for managing converter instances.

Handles creation, retrieval, and preview of converters.
Uses ConverterRegistry as the source of truth for instances.

Converters can be:
- Created via API request (instantiated from request params, then registered)
- Retrieved from registry (pre-registered at startup or created earlier)
"""

import inspect
import uuid
from functools import lru_cache
from typing import Any, Literal, Optional, Union, get_args, get_origin

from pyrit import prompt_converter
from pyrit.backend.mappers.converter_mappers import converter_object_to_instance
from pyrit.backend.models.converters import (
    ConverterCatalogEntry,
    ConverterCatalogResponse,
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterParameterSchema,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    CreateConverterRequest,
    CreateConverterResponse,
    PreviewStep,
)
from pyrit.models import PromptDataType
from pyrit.prompt_converter import PromptConverter
from pyrit.registry.instance_registries import ConverterRegistry


def _build_converter_class_registry() -> dict[str, type]:
    """
    Build a registry mapping converter class names to their classes.

    Uses the prompt_converter module's __all__ to discover all available converters.

    Returns:
        Dict mapping class name (str) to class (type).
    """
    registry: dict[str, type] = {}
    for name in prompt_converter.__all__:
        cls = getattr(prompt_converter, name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, PromptConverter):
            registry[name] = cls
    return registry


# Module-level class registry (built once on import)
_CONVERTER_CLASS_REGISTRY: dict[str, type] = _build_converter_class_registry()

# Types that can be rendered as simple form fields
_SIMPLE_TYPES: set[type] = {str, int, float, bool}


def _is_simple_type(annotation: Any) -> bool:
    """Return True if the annotation represents a type renderable in a form field."""
    if annotation in _SIMPLE_TYPES:
        return True
    origin = get_origin(annotation)
    if origin is Literal:
        return True
    if origin is Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        return len(non_none) == 1 and _is_simple_type(non_none[0])
    return False


def _serialize_type(annotation: Any) -> str:
    """Convert a type annotation to a concise human-readable string."""
    if annotation is inspect.Parameter.empty:
        return "Any"
    origin = get_origin(annotation)
    if origin is Literal:
        args = get_args(annotation)
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    if origin is Union:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner = _serialize_type(non_none[0])
            return f"Optional[{inner}]" if len(args) > len(non_none) else inner
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _extract_parameters(converter_class: type) -> list[ConverterParameterSchema]:
    """Extract simple constructor parameters from a converter class."""
    try:
        sig = inspect.signature(converter_class.__init__)
    except (ValueError, TypeError):
        return []

    params: list[ConverterParameterSchema] = []
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        if not _is_simple_type(p.annotation):
            continue

        no_default = p.default is inspect.Parameter.empty
        is_sentinel = hasattr(p.default, "__class__") and "Sentinel" in type(p.default).__name__
        required = no_default or is_sentinel

        default_value: Optional[str] = None
        if not required and p.default is not None:
            default_value = str(p.default)

        choices: Optional[list[str]] = None
        if get_origin(p.annotation) is Literal:
            choices = [str(a) for a in get_args(p.annotation)]

        params.append(
            ConverterParameterSchema(
                name=name,
                type_name=_serialize_type(p.annotation),
                required=required,
                default_value=default_value,
                choices=choices,
            )
        )

    return params


class ConverterService:
    """
    Service for managing converter instances.

    Uses ConverterRegistry as the sole source of truth.
    API metadata is derived from the converter objects.
    """

    def __init__(self) -> None:
        """Initialize the converter service."""
        self._registry = ConverterRegistry.get_registry_singleton()

    def _build_instance_from_object(self, *, converter_id: str, converter_obj: Any) -> ConverterInstance:
        """
        Build a ConverterInstance from a registry object.

        Uses the converter's identifier to extract all relevant metadata.

        Returns:
            ConverterInstance with metadata derived from the object's identifier.
        """
        return converter_object_to_instance(converter_id, converter_obj)

    # ========================================================================
    # Public API Methods
    # ========================================================================

    async def list_converters_async(self) -> ConverterInstanceListResponse:
        """
        List all converter instances.

        Returns:
            ConverterInstanceListResponse containing all registered converters.
        """
        items = [
            self._build_instance_from_object(converter_id=entry.name, converter_obj=entry.instance)
            for entry in self._registry.get_all_instances()
        ]
        return ConverterInstanceListResponse(items=items)

    async def list_converter_catalog_async(self) -> ConverterCatalogResponse:
        """
        List all available converter types from the backend converter registry.

        Returns:
            ConverterCatalogResponse containing all available converter classes.
        """
        items: list[ConverterCatalogEntry] = []
        for converter_type, converter_class in sorted(_CONVERTER_CLASS_REGISTRY.items()):
            if converter_type in ("PromptConverter", "ConverterResult") or "Strategy" in converter_type:
                continue

            supported_input_types = [str(data_type) for data_type in getattr(converter_class, "SUPPORTED_INPUT_TYPES", ())]
            supported_output_types = [str(data_type) for data_type in getattr(converter_class, "SUPPORTED_OUTPUT_TYPES", ())]

            items.append(
                ConverterCatalogEntry(
                    converter_type=converter_type,
                    supported_input_types=supported_input_types,
                    supported_output_types=supported_output_types,
                    parameters=_extract_parameters(converter_class),
                )
            )

        return ConverterCatalogResponse(items=items)

    async def get_converter_async(self, *, converter_id: str) -> Optional[ConverterInstance]:
        """
        Get a converter instance by ID.

        Returns:
            ConverterInstance if found, None otherwise.
        """
        obj = self._registry.get_instance_by_name(converter_id)
        if obj is None:
            return None
        return self._build_instance_from_object(converter_id=converter_id, converter_obj=obj)

    def get_converter_object(self, *, converter_id: str) -> Optional[Any]:
        """
        Get the actual converter object.

        Returns:
            The PromptConverter object if found, None otherwise.
        """
        return self._registry.get_instance_by_name(converter_id)

    async def create_converter_async(self, *, request: CreateConverterRequest) -> CreateConverterResponse:
        """
        Create a new converter instance from API request.

        Instantiates the converter with the given type and params,
        then registers it in the registry.

        Args:
            request: The create converter request with type and params.

        Returns:
            CreateConverterResponse with the new converter's details.

        Raises:
            ValueError: If the converter type is not found.
        """
        converter_id = str(uuid.uuid4())

        # Resolve any converter references in params and instantiate
        params = self._resolve_converter_params(params=request.params)
        converter_class = self._get_converter_class(converter_type=request.type)
        converter_obj = converter_class(**params)
        self._registry.register_instance(converter_obj, name=converter_id)

        return CreateConverterResponse(
            converter_id=converter_id,
            converter_type=request.type,
            display_name=request.display_name,
        )

    async def preview_conversion_async(self, *, request: ConverterPreviewRequest) -> ConverterPreviewResponse:
        """
        Preview conversion through a converter pipeline.

        Returns:
            ConverterPreviewResponse with step-by-step conversion results.
        """
        converters = self._gather_converters(converter_ids=request.converter_ids)
        steps, final_value, final_type = await self._apply_converters(
            converters=converters, initial_value=request.original_value, initial_type=request.original_value_data_type
        )

        return ConverterPreviewResponse(
            original_value=request.original_value,
            original_value_data_type=request.original_value_data_type,
            converted_value=final_value,
            converted_value_data_type=final_type,
            steps=steps,
        )

    def get_converter_objects_for_ids(self, *, converter_ids: list[str]) -> list[Any]:
        """
        Get converter objects for a list of IDs.

        Returns:
            List of converter objects in the same order as the input IDs.
        """
        converters = []
        for conv_id in converter_ids:
            conv_obj = self.get_converter_object(converter_id=conv_id)
            if conv_obj is None:
                raise ValueError(f"Converter instance '{conv_id}' not found")
            converters.append(conv_obj)
        return converters

    # ========================================================================
    # Private Helper Methods
    # ========================================================================

    def _get_converter_class(self, *, converter_type: str) -> type:
        """
        Get the converter class for a given type name.

        Looks up the class in the module-level converter class registry.

        Args:
            converter_type: The exact class name of the converter (e.g., 'Base64Converter').

        Returns:
            The converter class.

        Raises:
            ValueError: If the converter type is not found.
        """
        cls = _CONVERTER_CLASS_REGISTRY.get(converter_type)
        if cls is None:
            raise ValueError(
                f"Converter type '{converter_type}' not found. "
                f"Available types: {sorted(_CONVERTER_CLASS_REGISTRY.keys())}"
            )
        return cls

    def _resolve_converter_params(self, *, params: dict[str, Any]) -> dict[str, Any]:
        """
        Resolve converter references in params.

        If params contains a 'converter' key with a converter_id reference,
        resolve it to the actual converter object from the registry.

        Returns:
            Params dict with converter_id references replaced by actual objects.
        """
        resolved = dict(params)
        if "converter" in resolved and isinstance(resolved["converter"], dict):
            ref = resolved["converter"]
            if "converter_id" in ref:
                conv_obj = self.get_converter_object(converter_id=ref["converter_id"])
                if conv_obj is None:
                    raise ValueError(f"Referenced converter '{ref['converter_id']}' not found")
                resolved["converter"] = conv_obj
        return resolved

    def _gather_converters(self, *, converter_ids: list[str]) -> list[tuple[str, str, Any]]:
        """
        Gather converters to apply from IDs.

        Returns:
            List of tuples (converter_id, converter_type, converter_obj).
        """
        converters: list[tuple[str, str, Any]] = []
        for conv_id in converter_ids:
            conv_obj = self.get_converter_object(converter_id=conv_id)
            if conv_obj is None:
                raise ValueError(f"Converter instance '{conv_id}' not found")
            conv_type = conv_obj.__class__.__name__
            converters.append((conv_id, conv_type, conv_obj))
        return converters

    async def _apply_converters(
        self,
        *,
        converters: list[tuple[str, str, Any]],
        initial_value: str,
        initial_type: PromptDataType,
    ) -> tuple[list[PreviewStep], str, PromptDataType]:
        """
        Apply converters and collect steps.

        Returns:
            Tuple of (steps, final_value, final_type).
        """
        current_value = initial_value
        current_type = initial_type
        steps: list[PreviewStep] = []

        for conv_id, conv_type, conv_obj in converters:
            input_value, input_type = current_value, current_type
            result = await conv_obj.convert_async(prompt=current_value)
            current_value, current_type = result.output_text, result.output_type

            steps.append(
                PreviewStep(
                    converter_id=conv_id,
                    converter_type=conv_type,
                    input_value=input_value,
                    input_data_type=input_type,
                    output_value=current_value,
                    output_data_type=current_type,
                )
            )

        return steps, current_value, current_type


# ============================================================================
# Singleton
# ============================================================================


@lru_cache(maxsize=1)
def get_converter_service() -> ConverterService:
    """
    Get the global converter service instance.

    Returns:
        The singleton ConverterService instance.
    """
    return ConverterService()
