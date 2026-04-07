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

import base64
import inspect
import mimetypes
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional, Union, get_args, get_origin
from urllib.parse import parse_qs, urlparse

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
from pyrit.models.data_type_serializer import data_serializer_factory
from pyrit.prompt_converter import PromptConverter
from pyrit.prompt_target import PromptChatTarget
from pyrit.registry.instance_registries import ConverterRegistry

_DATA_TYPE_EXTENSION: dict[str, str] = {
    "image_path": ".png",
    "audio_path": ".wav",
    "video_path": ".mp4",
    "binary_path": ".bin",
}


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
    """
    Convert a type annotation to a concise human-readable string.

    Returns:
        str: A human-readable representation of the type annotation.
    """
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
        return str(annotation.__name__)
    return str(annotation)


def _parse_arg_descriptions(converter_class: type) -> dict[str, str]:
    """
    Parse parameter descriptions from Google-style docstring Args section.

    Returns:
        dict[str, str]: Mapping of parameter names to their descriptions.
    """
    doc = (converter_class.__init__.__doc__ or converter_class.__doc__ or "").strip()  # type: ignore[misc]
    match = re.search(r"Args:\s*\n(.*?)(?:\n\s*\n|\n\s*Returns:|\n\s*Raises:|\Z)", doc, re.DOTALL)
    if not match:
        return {}
    args_block = match.group(1)
    # Detect indentation of first parameter line
    indent_match = re.match(r"^(\s+)", args_block)
    indent = indent_match.group(1) if indent_match else r"\s+"
    pattern = rf"^{indent}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+?)(?=\n{indent}\w|\Z)"
    descriptions: dict[str, str] = {}
    for m in re.finditer(pattern, args_block, re.DOTALL | re.MULTILINE):
        descriptions[m.group(1)] = " ".join(m.group(2).split())
    return descriptions


def _extract_parameters(converter_class: type) -> list[ConverterParameterSchema]:
    """
    Extract simple constructor parameters from a converter class.

    Returns:
        list[ConverterParameterSchema]: List of parameter schemas.
    """
    try:
        sig = inspect.signature(converter_class.__init__)  # type: ignore[misc]
    except (ValueError, TypeError):
        return []

    arg_descriptions = _parse_arg_descriptions(converter_class)

    params: list[ConverterParameterSchema] = []
    for name, p in sig.parameters.items():
        if name in ("self", "args", "kwargs"):
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
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
                description=arg_descriptions.get(name),
            )
        )

    return params


def _is_llm_based(converter_class: type) -> bool:
    """Return True if the converter requires an LLM target parameter."""
    try:
        sig = inspect.signature(converter_class.__init__)  # type: ignore[misc]
    except (ValueError, TypeError):
        return False

    for name, p in sig.parameters.items():
        if name == "self":
            continue
        ann = p.annotation
        if ann is inspect.Parameter.empty:
            continue
        try:
            if isinstance(ann, type) and issubclass(ann, PromptChatTarget):
                return True
        except TypeError:
            continue
    return False


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
            if (
                converter_type
                in ("PromptConverter", "ConverterResult", "HumanInTheLoopConverter", "SelectiveTextConverter")
                or "Strategy" in converter_type
            ):
                continue

            supported_input_types = [
                str(data_type) for data_type in getattr(converter_class, "SUPPORTED_INPUT_TYPES", ())
            ]
            supported_output_types = [
                str(data_type) for data_type in getattr(converter_class, "SUPPORTED_OUTPUT_TYPES", ())
            ]

            # Extract first paragraph of docstring as description
            raw_doc = (converter_class.__doc__ or "").strip()
            description = raw_doc.split("\n\n")[0].replace("\n", " ").strip() or None

            items.append(
                ConverterCatalogEntry(
                    converter_type=converter_type,
                    supported_input_types=supported_input_types,
                    supported_output_types=supported_output_types,
                    parameters=_extract_parameters(converter_class),
                    is_llm_based=_is_llm_based(converter_class),
                    description=description,
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
        params = self._coerce_params(converter_class=converter_class, params=params)
        params = await self._persist_data_uri_params_async(converter_class=converter_class, params=params)
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

        For non-text data types (image_path, audio_path, etc.), persists base64 data
        to a temporary file so converters can operate on file paths.

        Returns:
            ConverterPreviewResponse with step-by-step conversion results.
        """
        original_value = request.original_value
        data_type = request.original_value_data_type

        # For path-based data types, persist base64/data-uri to a file.
        # Reuse the same detection logic as AttackService._persist_base64_pieces_async
        # to correctly distinguish file paths / URLs from raw base64 payloads.
        if str(data_type).endswith("_path"):
            # Already a remote URL — keep as-is
            if original_value.startswith(("http://", "https://")):
                pass
            # Already a local media URL (e.g. /api/media?path=...) — extract the file path
            elif original_value.startswith("/api/media"):
                parsed = urlparse(original_value)
                file_path = parse_qs(parsed.query).get("path", [None])[0]
                if file_path:
                    original_value = file_path
            # Data URI from the frontend (e.g. "data:image/png;base64,...") — decode and persist
            elif original_value.startswith("data:"):
                _, _, value = original_value.partition(",")

                ext = _DATA_TYPE_EXTENSION.get(str(data_type), ".bin")

                serializer = data_serializer_factory(
                    category="prompt-memory-entries",
                    data_type=data_type,
                    extension=ext,
                )
                await serializer.save_b64_image(data=value)
                original_value = str(serializer.value)
            # Already an existing file on disk — keep as-is
            elif Path(original_value).is_file():
                pass
            else:
                # Treat as raw base64
                ext = _DATA_TYPE_EXTENSION.get(str(data_type), ".bin")

                serializer = data_serializer_factory(
                    category="prompt-memory-entries",
                    data_type=data_type,
                    extension=ext,
                )
                await serializer.save_b64_image(data=original_value)
                original_value = str(serializer.value)

        converters = self._gather_converters(converter_ids=request.converter_ids)
        steps, final_value, final_type = await self._apply_converters(
            converters=converters, initial_value=original_value, initial_type=data_type
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

    @staticmethod
    def _coerce_params(*, converter_class: type, params: dict[str, Any]) -> dict[str, Any]:
        """
        Coerce parameter values to match the converter's __init__ type annotations.

        The frontend sends all values as strings; this converts them to int, float,
        or bool as needed based on the constructor signature.

        Returns:
            Params dict with values coerced to the expected types.
        """
        try:
            sig = inspect.signature(converter_class.__init__)  # type: ignore[misc]
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Failed to inspect __init__ signature for converter '{converter_class.__name__}': {e}"
            ) from e

        coerced = dict(params)
        for name, value in coerced.items():
            if name not in sig.parameters or not isinstance(value, str):
                continue
            annotation = sig.parameters[name].annotation
            if annotation is inspect.Parameter.empty:
                continue

            origin = get_origin(annotation)
            # Unwrap Optional[X] to X
            if origin is Union:
                args = get_args(annotation)
                non_none = [a for a in args if a is not type(None)]
                if len(non_none) == 1:
                    annotation = non_none[0]
                    origin = get_origin(annotation)

            try:
                if annotation is int:
                    coerced[name] = int(value)
                elif annotation is float:
                    coerced[name] = float(value)
                elif annotation is bool:
                    coerced[name] = value.lower() in ("true", "1", "yes")
            except (ValueError, TypeError) as e:
                raise ValueError(f"Parameter '{name}' expects {annotation.__name__}, got {value!r}") from e

        return coerced

    @staticmethod
    async def _persist_data_uri_params_async(
        *,
        converter_class: type,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Persist data-URI parameter values to disk.

        The frontend file picker sends file contents as data URIs
        (e.g. ``data:image/png;base64,...``). Constructor parameters typed as
        ``Path`` or ``str`` params whose names suggest a file path receive the
        decoded file persisted to the results store, with the value replaced
        by the resulting file path.

        Returns:
            Params dict with data-URI values replaced by file paths.
        """
        try:
            sig = inspect.signature(converter_class.__init__)  # type: ignore[misc]
        except (ValueError, TypeError):
            return params

        result = dict(params)
        for name, value in result.items():
            if not isinstance(value, str) or not value.startswith("data:"):
                continue
            if name not in sig.parameters:
                continue

            # Parse data URI: data:[<mediatype>][;base64],<data>
            header, _, payload = value.partition(",")
            if not payload:
                continue

            # Derive extension from the MIME type in the header
            mime_type = header.split(":")[1].split(";")[0] if ":" in header else ""
            ext = mimetypes.guess_extension(mime_type, strict=False) if mime_type else None
            if not ext:
                ext = ".bin"

            serializer = data_serializer_factory(
                category="prompt-memory-entries",
                data_type="binary_path",
                extension=ext,
            )
            await serializer.save_data(data=base64.b64decode(payload))
            file_path = str(serializer.value)

            # Coerce to Path if the constructor expects it
            annotation = sig.parameters[name].annotation
            origin = get_origin(annotation)
            if origin is Union:
                args = get_args(annotation)
                non_none = [a for a in args if a is not type(None)]
                if len(non_none) == 1:
                    annotation = non_none[0]

            if annotation is Path:
                result[name] = Path(file_path)
            else:
                result[name] = file_path

        return result

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
            result = await conv_obj.convert_async(prompt=current_value, input_type=current_type)
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
