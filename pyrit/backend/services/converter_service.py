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
import types
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal, Union, get_args, get_origin
from urllib.parse import parse_qs, urlparse

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
from pyrit.common import REQUIRED_VALUE
from pyrit.memory import data_serializer_factory
from pyrit.models import PromptDataType
from pyrit.models.parameter import Parameter
from pyrit.registry.components import ConverterRegistry
from pyrit.registry.resolution import display_choices


def _serialize_type(annotation: Any) -> str:
    """
    Render a parameter's type annotation as a concise human-readable string.

    Used to populate the catalog DTO consumed by the frontend (e.g. ``"str"``,
    ``"Optional[int]"``, ``"Literal['a', 'b']"``).

    Returns:
        str: A human-readable representation of the type annotation.
    """
    if annotation is inspect.Parameter.empty:
        return "Any"
    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner = _serialize_type(non_none[0])
            has_none = type(None) in args
            return f"Optional[{inner}]" if has_none else inner
    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    return str(annotation)


class ConverterService:
    """
    Service for managing converter instances.

    Uses ConverterRegistry as the sole source of truth.
    API metadata is derived from the converter objects.
    """

    _DATA_TYPE_EXTENSION: ClassVar[dict[str, str]] = {
        "image_path": ".png",
        "audio_path": ".wav",
        "video_path": ".mp4",
        "binary_path": ".bin",
    }

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
            for entry in self._registry.instances.get_all_instances()
        ]
        return ConverterInstanceListResponse(items=items)

    async def list_converter_catalog_async(self) -> ConverterCatalogResponse:
        """
        List all available converter types from the converter class registry.

        Returns every constructible converter. Deciding which entries to surface
        to a user is a presentation concern owned by the caller (e.g. the
        frontend), not this service.

        Returns:
            ConverterCatalogResponse containing all available converter classes.
        """
        items: list[ConverterCatalogEntry] = [
            ConverterCatalogEntry(
                converter_type=metadata.class_name,
                supported_input_types=list(metadata.supported_input_types),
                supported_output_types=list(metadata.supported_output_types),
                parameters=[self._build_parameter_schema(p) for p in metadata.parameters if p.is_string_coercible],
                is_llm_based=metadata.is_llm_based,
                description=metadata.class_description or None,
            )
            for metadata in self._registry.get_all_registered_class_metadata()
        ]

        return ConverterCatalogResponse(items=items)

    @staticmethod
    def _build_parameter_schema(parameter: Parameter) -> ConverterParameterSchema:
        """
        Map a derived ``Parameter`` to the catalog DTO.

        Renders the parameter's ``param_type`` to a human-readable ``type_name`` and
        projects its allowed values (presentation concerns owned by this service).
        Required-ness is read from the ``REQUIRED_VALUE`` sentinel default.

        Returns:
            ConverterParameterSchema: The parameter schema for the catalog entry.
        """
        required = parameter.default is REQUIRED_VALUE
        default_value = None if required or parameter.default is None else str(parameter.default)
        choices = display_choices(parameter.param_type)
        return ConverterParameterSchema(
            name=parameter.name,
            type_name=_serialize_type(parameter.param_type),
            required=required,
            default_value=default_value,
            choices=[str(c) for c in choices] if choices is not None else None,
            description=parameter.description or None,
        )

    async def get_converter_async(self, *, converter_id: str) -> ConverterInstance | None:
        """
        Get a converter instance by ID.

        Returns:
            ConverterInstance if found, None otherwise.
        """
        obj = self._registry.instances.get(converter_id)
        if obj is None:
            return None
        return self._build_instance_from_object(converter_id=converter_id, converter_obj=obj)

    def get_converter_object(self, *, converter_id: str) -> Any | None:
        """
        Get the actual converter object.

        Returns:
            The PromptConverter object if found, None otherwise.
        """
        return self._registry.instances.get(converter_id)

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

        # Resolve any converter references in params, persist data-URI params to
        # disk (frontend concern), then delegate construction (incl. param
        # coercion) to the converter registry.
        params = self._resolve_converter_params(params=request.params)
        try:
            converter_class = self._registry.get_class(request.type)
        except KeyError as e:
            raise ValueError(f"Converter type '{request.type}' not found") from e
        params = await self._persist_data_uri_params_async(converter_class=converter_class, params=params)
        converter_obj = self._registry.create_instance(request.type, **params)
        self._registry.instances.register(converter_obj, name=converter_id)

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

                ext = self._DATA_TYPE_EXTENSION.get(str(data_type), ".bin")

                serializer = data_serializer_factory(
                    category="prompt-memory-entries",
                    data_type=data_type,
                    extension=ext,
                )
                await serializer.save_b64_image_async(data=value)
                original_value = str(serializer.value)
            # Already an existing file on disk — keep as-is
            elif Path(original_value).is_file():
                pass
            else:
                # Treat as raw base64
                ext = self._DATA_TYPE_EXTENSION.get(str(data_type), ".bin")

                serializer = data_serializer_factory(
                    category="prompt-memory-entries",
                    data_type=data_type,
                    extension=ext,
                )
                await serializer.save_b64_image_async(data=original_value)
                original_value = str(serializer.value)

        converters = self._gather_converters(converter_ids=request.converter_ids)
        steps, final_value, final_type = await self._apply_converters_async(
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
            sig = inspect.signature(converter_class.__init__)
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
            await serializer.save_data_async(data=base64.b64decode(payload))
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

    async def _apply_converters_async(
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
