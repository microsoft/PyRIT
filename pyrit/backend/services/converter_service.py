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
import binascii
import uuid
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import aiofiles
import aiofiles.os

from pyrit.backend.mappers.converter_mappers import converter_object_to_instance
from pyrit.backend.models import DEFAULT_MEDIA_EXTENSIONS
from pyrit.backend.models.converters import (
    ConverterCatalogResponse,
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    ConverterTypeEntry,
    ConverterTypeResponse,
    CreateConverterRequest,
    PreviewStep,
)
from pyrit.common.path import DB_DATA_PATH
from pyrit.memory import data_serializer_factory
from pyrit.models import PromptDataType
from pyrit.registry.components import ConverterRegistry

if TYPE_CHECKING:
    from pyrit.converter import ConverterResult


@dataclass(frozen=True)
class _UploadFormat:
    """Server-owned format contract for one constructor upload."""

    extension: str
    signatures: tuple[bytes, ...]


_PATH_UPLOAD_FORMATS: dict[tuple[str, str], dict[str, _UploadFormat]] = {
    ("PDFConverter", "existing_pdf"): {
        "application/pdf": _UploadFormat(extension=".pdf", signatures=(b"%PDF-",)),
    },
    ("TransparencyAttackConverter", "benign_image_path"): {
        "image/jpeg": _UploadFormat(extension=".jpg", signatures=(b"\xff\xd8\xff",)),
    },
}
_ACTIVE_CONTENT_TYPES = frozenset({"application/xhtml+xml", "image/svg+xml", "text/html"})
_OWNED_ARTIFACT_PATHS_KEY = "owned_artifact_paths"
_REGISTRY_UPLOAD_DIRECTORY = DB_DATA_PATH / "registry-uploads"


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
        metadata = self._registry.get_registered_class_metadata(converter_obj.__class__.__name__)
        description = metadata.class_description or None if metadata else None
        return converter_object_to_instance(
            converter_id=converter_id,
            converter_obj=converter_obj,
            is_llm_based=metadata.is_llm_based if metadata else False,
            description=description,
        )

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

    async def list_converter_types_async(self) -> ConverterTypeResponse:
        """
        List all available converter types from the converter class registry.

        Returns every constructible converter. Deciding which entries to surface
        to a user is a presentation concern owned by the caller (e.g. the
        frontend), not this service.

        Returns:
            ConverterTypeResponse containing all available converter classes.
        """
        items: list[ConverterTypeEntry] = [
            ConverterTypeEntry(
                converter_type=metadata.class_name,
                supported_input_types=list(metadata.supported_input_types),
                supported_output_types=list(metadata.supported_output_types),
                parameters=[p for p in metadata.parameters if p.is_string_coercible or p.reference is not None],
                is_llm_based=metadata.is_llm_based,
                description=metadata.class_description or None,
            )
            for metadata in self._registry.get_all_registered_class_metadata()
        ]

        return ConverterTypeResponse(items=items)

    async def list_converter_catalog_async(self) -> ConverterCatalogResponse:
        """
        Return the legacy projection used by the current chat UI.

        Remove this method with the ``/catalog`` route when the chat-migration
        stack layer uses ``list_converter_types_async``.

        Returns:
            ConverterCatalogResponse: The scalar-only legacy projection.
        """
        types_response = await self.list_converter_types_async()
        items = [
            entry.model_copy(update={"parameters": [p for p in entry.parameters if p.is_string_coercible]})
            for entry in types_response.items
        ]
        return ConverterCatalogResponse(items=items)

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
            The Converter object if found, None otherwise.
        """
        return self._registry.instances.get(converter_id)

    async def delete_converter_async(self, *, converter_id: str) -> bool:
        """
        Delete a converter instance by registry name.

        Returns:
            bool: True when an instance was removed, otherwise False.
        """
        entry = self._registry.instances.get_entry(converter_id)
        if entry is None:
            return False

        owned_paths = self._get_owned_artifact_paths(entry.metadata)
        await self._remove_owned_artifacts_async(paths=owned_paths)
        return self._registry.instances.unregister(converter_id) is not None

    async def create_converter_async(self, *, request: CreateConverterRequest) -> ConverterInstance:
        """
        Create a new converter instance from API request.

        Instantiates the converter with the given type and params,
        then registers it in the registry.

        Args:
            request: The create converter request with type and params.

        Returns:
            ConverterInstance with the new converter's details.

        Raises:
            ValueError: If the converter type is not found or the registry name is
                unavailable.
        """
        if request.type not in self._registry:
            raise ValueError(f"Converter type '{request.type}' not found")
        # LEGACY COMPATIBILITY: The current chat UI omits the name. Remove this
        # generated fallback when that UI sends an explicit registry name.
        converter_id = request.name or f"compat_{uuid.uuid4().hex}"
        self._registry.instances.validate_name_available(converter_id)
        params, owned_paths = await self._persist_data_uri_params_async(
            converter_type=request.type,
            params=request.params,
        )
        try:
            converter_obj = self._registry.create_named_instance(
                name=converter_id,
                converter_type=request.type,
                registry_metadata={_OWNED_ARTIFACT_PATHS_KEY: [str(path) for path in owned_paths]},
                **params,
            )
        except Exception:
            await self._remove_owned_artifacts_async(paths=owned_paths)
            raise

        return self._build_instance_from_object(
            converter_id=converter_id,
            converter_obj=converter_obj,
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

                ext = DEFAULT_MEDIA_EXTENSIONS.get(str(data_type), ".bin")

                serializer = data_serializer_factory(
                    category="prompt-memory-entries",
                    data_type=data_type,
                    extension=ext,
                )
                await serializer.save_b64_image_async(data=value)
                original_value = str(serializer.value)
            else:
                try:
                    is_existing_file = Path(original_value).is_file()
                except (OSError, ValueError):
                    if not self._is_raw_base64(original_value):
                        raise
                    is_existing_file = False

                if not is_existing_file:
                    # Treat as raw base64
                    ext = DEFAULT_MEDIA_EXTENSIONS.get(str(data_type), ".bin")

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

    async def _persist_data_uri_params_async(
        self,
        *,
        converter_type: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Path]]:
        """
        Persist uploaded ``Path`` parameter values to managed local storage.

        The frontend file picker sends file contents as data URIs
        (e.g. ``data:image/png;base64,...``). Constructor parameters typed as
        ``Path`` receive the decoded file in a local working directory that is
        independent of the configured CentralMemory results store.

        The set of constructor parameters (and their types) is sourced from the
        registry's derived ``Parameter`` metadata rather than re-introspecting the
        constructor signature, so the registry stays the single source of truth.

        Args:
            converter_type (str): The registered converter class name.
            params (dict[str, Any]): The raw constructor params from the request.

        Returns:
            tuple[dict[str, Any], list[Path]]: Updated parameters and the explicit
                set of request-created files owned by the future registry entry.

        Raises:
            ValueError: If a ``Path`` value is not a valid, allowed data URI.
        """
        metadata = self._registry.get_registered_class_metadata(converter_type)
        param_types = {p.name: p.param_type for p in metadata.parameters} if metadata else {}

        result = dict(params)
        owned_paths: list[Path] = []
        try:
            for name, value in result.items():
                if param_types.get(name) is not Path:
                    continue
                if value is None:
                    continue
                if not isinstance(value, str) or not value.startswith("data:"):
                    raise ValueError(f"Path parameter '{name}' must be uploaded as a data URI")

                content, upload_format = self._validate_path_upload(
                    converter_type=converter_type,
                    parameter_name=name,
                    data_uri=value,
                )
                file_path = await self._save_owned_artifact_async(
                    content=content,
                    extension=upload_format.extension,
                )
                owned_paths.append(file_path)
                result[name] = file_path
        except Exception:
            await self._remove_owned_artifacts_async(paths=owned_paths)
            raise

        return result, owned_paths

    @staticmethod
    def _validate_path_upload(
        *,
        converter_type: str,
        parameter_name: str,
        data_uri: str,
    ) -> tuple[bytes, _UploadFormat]:
        """
        Validate a constructor upload against its server-owned format contract.

        Returns:
            tuple[bytes, _UploadFormat]: The decoded content and its trusted format.
        """
        header, separator, payload = data_uri.partition(",")
        header_parts = header[5:].split(";") if header.startswith("data:") else []
        if not separator or len(header_parts) != 2 or header_parts[1].lower() != "base64" or not payload:
            raise ValueError(f"Path parameter '{parameter_name}' must be a base64 data URI")

        mime_type = header_parts[0].lower()
        if mime_type in _ACTIVE_CONTENT_TYPES:
            raise ValueError(f"Active content type '{mime_type}' is not allowed for Path parameter '{parameter_name}'")

        allowed_formats = _PATH_UPLOAD_FORMATS.get((converter_type, parameter_name))
        if not allowed_formats:
            raise ValueError(
                f"Path parameter '{parameter_name}' on '{converter_type}' does not have an upload format contract"
            )
        upload_format = allowed_formats.get(mime_type)
        if upload_format is None:
            allowed_types = ", ".join(sorted(allowed_formats))
            raise ValueError(
                f"Content type '{mime_type}' is not allowed for Path parameter '{parameter_name}'; "
                f"expected one of: {allowed_types}"
            )

        try:
            content = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Path parameter '{parameter_name}' contains invalid base64 data") from exc
        if not any(content.startswith(signature) for signature in upload_format.signatures):
            raise ValueError(
                f"Uploaded content for Path parameter '{parameter_name}' does not match declared MIME type "
                f"'{mime_type}'"
            )
        return content, upload_format

    @staticmethod
    async def _save_owned_artifact_async(*, content: bytes, extension: str) -> Path:
        """
        Write one validated upload to the managed local registry directory.

        Returns:
            Path: The absolute path of the new local artifact.
        """
        await aiofiles.os.makedirs(_REGISTRY_UPLOAD_DIRECTORY, exist_ok=True)
        file_path = (_REGISTRY_UPLOAD_DIRECTORY / f"{uuid.uuid4().hex}{extension}").resolve()
        async with aiofiles.open(file_path, "xb") as file:
            await file.write(content)
        return file_path

    @staticmethod
    def _get_owned_artifact_paths(metadata: dict[str, Any]) -> list[Path]:
        """
        Read explicit artifact ownership from registry-entry metadata.

        Returns:
            list[Path]: Paths explicitly owned by the registry entry.
        """
        raw_paths = metadata.get(_OWNED_ARTIFACT_PATHS_KEY, [])
        if not isinstance(raw_paths, list) or not all(isinstance(path, str) for path in raw_paths):
            raise ValueError("Registry entry has invalid owned artifact metadata")
        return [Path(path) for path in raw_paths]

    @staticmethod
    async def _remove_owned_artifacts_async(*, paths: list[Path]) -> None:
        """Remove explicitly owned files, limited to the managed upload directory."""
        allowed_root = _REGISTRY_UPLOAD_DIRECTORY.resolve()
        for path in paths:
            resolved_path = path.resolve()
            try:
                resolved_path.relative_to(allowed_root)
            except ValueError as exc:
                raise ValueError(f"Owned artifact path is outside the managed upload directory: {path}") from exc
            with suppress(FileNotFoundError):
                await aiofiles.os.remove(resolved_path)

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
        current_value: str = initial_value
        current_type: PromptDataType = initial_type
        steps: list[PreviewStep] = []

        for conv_id, conv_type, conv_obj in converters:
            input_value, input_type = current_value, current_type
            result: ConverterResult = await conv_obj.convert_async(prompt=current_value, input_type=current_type)
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

    @staticmethod
    def _is_raw_base64(value: str) -> bool:
        """
        Determine whether a value is syntactically valid raw base64.

        Returns:
            True when the value is valid raw base64, otherwise False.
        """
        try:
            base64.b64decode(value, validate=True)
        except binascii.Error:
            return False
        return True


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
