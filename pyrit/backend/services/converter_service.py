# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter service for managing converter instances.

Handles creation, replacement, deletion, retrieval, and preview of converters.
Uses ConverterRegistry as the source of truth for live instances.

Converters can be:
- Created via API request (built from request params, saved as a recipe, then registered)
- Restored from saved recipes when the backend starts or reinitializes
- Retrieved from registry (pre-registered at startup or created earlier)
"""

import asyncio
import base64
import binascii
import mimetypes
import uuid
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar

import aiofiles
import aiofiles.os

from pyrit.backend.mappers.converter_mappers import converter_object_to_instance
from pyrit.backend.models.converters import (
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    ConverterTypeEntry,
    ConverterTypeResponse,
    CreateConverterRequest,
    PreviewStep,
    UpdateConverterRequest,
)
from pyrit.backend.services.instance_persistence_service import (
    BuiltInstance,
    InstanceKindHandler,
    get_instance_persistence_service,
)
from pyrit.backend.services.media_persistence import persist_media_value_async
from pyrit.common.azure_storage import is_azure_blob_uri
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentType, MessagePiece, PromptDataType
from pyrit.models.catalog.instance_recipe import InstanceRecipe
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.registry.components import ConverterRegistry
from pyrit.registry.instance_registry import RegistryEntry

_OWNED_ARTIFACT_PATHS_KEY = "owned_artifact_paths"
_DEFAULT_UPLOAD_EXTENSION = ".bin"


class ConverterService(InstanceKindHandler[ConverterInstance]):
    """
    Service for managing converter instances.

    Uses ConverterRegistry as the sole source of truth.
    API metadata is derived from the converter objects. Converters created
    through the API are saved by the instance persistence service, which calls
    back into this service to build and map them.
    """

    kind: ClassVar[ComponentType] = ComponentType.CONVERTER

    def __init__(self) -> None:
        """Initialize the converter service."""
        self._registry = ConverterRegistry.get_registry_singleton()
        self._upload_directory = TemporaryDirectory(prefix="pyrit-registry-uploads-")
        self._upload_path = Path(self._upload_directory.name).resolve()

    def _build_instance_from_object(
        self, *, converter_id: str, converter_obj: Any, version: str | None = None
    ) -> ConverterInstance:
        """
        Build a ConverterInstance from a registry object.

        Uses the converter's identifier to extract all relevant metadata.

        Returns:
            ConverterInstance with metadata derived from the object's identifier.
        """
        metadata = self._registry.get_registered_class_metadata(converter_obj.__class__.__name__)
        description = metadata.class_description or None if metadata else None
        converter = converter_object_to_instance(
            converter_id=converter_id,
            converter_obj=converter_obj,
            is_llm_based=metadata.is_llm_based if metadata else False,
            description=description,
        )
        return converter.model_copy(update={"version": version})

    def _build_instance_from_entry(self, entry: RegistryEntry[Any]) -> ConverterInstance:
        """
        Build a ConverterInstance, with its saved version, from a registry entry.

        Returns:
            ConverterInstance with metadata derived from the entry's object.
        """
        version = get_instance_persistence_service().get_version(entry)
        return self._build_instance_from_object(converter_id=entry.name, converter_obj=entry.instance, version=version)

    # ========================================================================
    # Public API Methods
    # ========================================================================

    async def close_async(self) -> None:
        """Remove this backend's temporary inputs after requests have stopped."""
        owned_entries = [
            entry
            for entry in self._registry.instances.get_all_instances()
            if any(path.is_relative_to(self._upload_path) for path in self._get_owned_artifact_paths(entry.metadata))
        ]
        await asyncio.to_thread(self._upload_directory.cleanup)
        for entry in owned_entries:
            self._registry.instances.unregister(entry.name, expected_entry=entry)

    async def list_converters_async(self) -> ConverterInstanceListResponse:
        """
        List all converter instances.

        Returns:
            ConverterInstanceListResponse containing all registered converters and the
            saved converters that could not be restored.
        """
        items = [self._build_instance_from_entry(entry) for entry in self._registry.instances.get_all_instances()]
        persistence = get_instance_persistence_service()
        return ConverterInstanceListResponse(
            items=items,
            unrestorable=persistence.get_unrestorable(self.kind),
            restore_error=persistence.restore_error,
        )

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
                parameters=list(metadata.parameters),
                is_llm_based=metadata.is_llm_based,
                description=metadata.class_description or None,
            )
            for metadata in self._registry.get_all_registered_class_metadata()
        ]

        return ConverterTypeResponse(items=items)

    async def get_converter_async(self, *, converter_id: str) -> ConverterInstance | None:
        """
        Get a converter instance by ID.

        Returns:
            ConverterInstance if found, None otherwise.
        """
        entry = self._registry.instances.get_entry(converter_id)
        if entry is None:
            return None
        return self._build_instance_from_entry(entry)

    def describe_missing_converter(self, *, converter_id: str) -> str:
        """
        Explain why no converter is registered under a name.

        Returns:
            str: Why a saved converter was not restored, or that the name was not found.
        """
        return get_instance_persistence_service().describe_missing(kind=self.kind, name=converter_id)

    def get_converter_object(self, *, converter_id: str) -> Any | None:
        """
        Get the actual converter object.

        Returns:
            The Converter object if found, None otherwise.
        """
        return self._registry.instances.get(converter_id)

    async def delete_converter_async(
        self, *, converter_id: str, expected_version: str | None = None, is_admin: bool = False
    ) -> bool:
        """
        Delete a converter instance by registry name.

        A saved converter is deleted together with its saved recipe and requires the
        version it was read with. A converter that is not saved is only unregistered.

        Returns:
            bool: True when an instance was removed, otherwise False.
        """
        return await self.delete_saved_async(name=converter_id, expected_version=expected_version, is_admin=is_admin)

    async def create_converter_async(
        self, *, request: CreateConverterRequest, is_admin: bool = False
    ) -> ConverterInstance:
        """
        Create, save, and register a new converter instance from an API request.

        Instantiates the converter with the given type and params, saves its recipe,
        and only then registers it, so a request that fails at any step leaves no saved
        or registered converter and removes its uploaded files.

        Args:
            request: The create converter request with name, type, params, and credentials.
            is_admin: Whether the caller may reference server environment variables as credentials.

        Returns:
            ConverterInstance with the new converter's details and saved version.

        Raises:
            ValueError: If the converter type is not found or the registry name is
                unavailable.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            name=request.name,
            type=request.type,
            params=request.params,
            credentials=request.credentials,
        )
        return await self.create_saved_async(recipe=recipe, is_admin=is_admin)

    async def update_converter_async(
        self, *, converter_id: str, request: UpdateConverterRequest, is_admin: bool = False
    ) -> ConverterInstance:
        """
        Replace a saved converter with a new configuration.

        Returns:
            ConverterInstance: The replacement and its new saved version.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            name=converter_id,
            type=request.type,
            params=request.params,
            credentials=request.credentials,
        )
        return await self.update_saved_async(recipe=recipe, expected_version=request.version, is_admin=is_admin)

    def normalize_recipe(self, recipe: InstanceRecipe) -> InstanceRecipe:
        """
        Check that the converter type exists.

        Returns:
            InstanceRecipe: The recipe, unchanged.

        Raises:
            ValueError: If the converter type is not registered.
        """
        if recipe.type not in self._registry:
            raise ValueError(f"Converter type '{recipe.type}' not found")
        return recipe

    async def build_async(self, *, recipe: InstanceRecipe, credentials: dict[str, object]) -> BuiltInstance:
        """
        Store the recipe's uploads and construct a converter without registering it.

        Returns:
            BuiltInstance: The constructed converter, the uploads it owns, and how to remove them.
        """
        params, owned_paths = await self._persist_data_uri_params_async(
            converter_type=recipe.type, params=recipe.params
        )
        try:
            converter = self._registry.create_instance(recipe.type, **{**params, **credentials})
        except (Exception, asyncio.CancelledError):
            await self._remove_owned_artifacts_async(paths=owned_paths)
            raise
        return BuiltInstance(
            instance=converter,
            metadata={_OWNED_ARTIFACT_PATHS_KEY: [str(path) for path in owned_paths]},
            release=lambda: self._remove_owned_artifacts_async(paths=owned_paths),
        )

    def to_response(self, *, name: str, instance: Any) -> ConverterInstance:
        """
        Map a constructed converter to its API response.

        Returns:
            ConverterInstance: The response, without a version.
        """
        return self._build_instance_from_object(converter_id=name, converter_obj=instance)

    async def release_entry_async(self, entry: RegistryEntry[Any]) -> None:
        """Remove the uploads a replaced or deleted registry entry owns."""
        await self._remove_owned_artifacts_async(paths=self._get_owned_artifact_paths(entry.metadata))

    async def delete_unsaved_async(self, *, name: str) -> bool:
        """
        Unregister a converter that is not saved and remove the uploads it owns.

        Returns:
            bool: True when an instance was removed, otherwise False.
        """
        entry = self._registry.instances.get_entry(name)
        if entry is None:
            return False

        owned_paths = self._get_owned_artifact_paths(entry.metadata)
        await self._remove_owned_artifacts_async(paths=owned_paths)
        return self._registry.instances.unregister(name, expected_entry=entry) is not None

    async def preview_conversion_async(self, *, request: ConverterPreviewRequest) -> ConverterPreviewResponse:
        """
        Preview conversion through a converter pipeline.

        For non-text data types (image_path, audio_path, etc.), persists base64 data
        to a temporary file so converters can operate on file paths. Marked text
        regions use the request's delimiter settings for every stage.

        Returns:
            ConverterPreviewResponse with step-by-step conversion results.
        """
        original_value = request.original_value
        data_type = request.original_value_data_type

        # For path-based data types, resolve references or persist base64/data URIs.
        if str(data_type).endswith("_path"):
            result = await persist_media_value_async(
                value=original_value,
                data_type=data_type,
                # Preview historically derives data-URI extensions from the
                # declared prompt type; attack ingestion additionally accepts
                # explicit/data-URI MIME metadata.
                use_data_uri_mime_type=False,
                require_valid_base64_after_path_error=True,
                serializer_factory=data_serializer_factory,
            )
            original_value = result.value

        converters = self._gather_converters(converter_ids=request.converter_ids)
        steps, final_value, final_type = await self._apply_converters_async(
            converters=converters,
            initial_value=original_value,
            initial_type=data_type,
            start_token=request.start_token,
            end_token=request.end_token,
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
        (e.g. ``data:image/png;base64,...``). A constructor parameter typed as ``Path``
        is therefore an *upload*: the decoded file is written to a local working
        directory this service owns, and the client never names a server path. Every
        ``Path`` parameter is handled the same way, so a converter opts in simply by
        declaring the type; there is no per-converter or per-parameter table.
        ``Path | str`` parameters also accept Azure Blob URLs, which pass through
        unchanged. Their data-URI uploads use the same local storage.

        Inputs remain local until converter deletion or backend shutdown, even with
        Azure-backed memory. Converter outputs still use the configured result storage.

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
            ValueError: If a ``Path`` value is not a valid data URI.
        """
        metadata = self._registry.get_registered_class_metadata(converter_type)
        path_params = (
            {
                parameter.name: parameter
                for parameter in metadata.parameters
                if parameter.is_path or parameter.is_path_or_str
            }
            if metadata
            else {}
        )

        result = dict(params)
        owned_paths: list[Path] = []
        try:
            for name, value in result.items():
                if name not in path_params:
                    continue
                if value is None:
                    continue
                parameter = path_params[name]
                if not isinstance(value, str) or not value.startswith("data:"):
                    if parameter.is_path_or_str and isinstance(value, str) and is_azure_blob_uri(value):
                        continue
                    alternative = " or supplied as an Azure Blob URL" if parameter.is_path_or_str else ""
                    raise ValueError(f"Path parameter '{name}' must be uploaded as a data URI{alternative}")

                content, extension = self._decode_data_uri(parameter_name=name, data_uri=value)
                file_path = self._upload_path / f"{uuid.uuid4().hex}{extension}"
                async with aiofiles.open(file_path, "xb") as file:
                    owned_paths.append(file_path)
                    await file.write(content)
                result[name] = file_path
        except (Exception, asyncio.CancelledError):
            await self._remove_owned_artifacts_async(paths=owned_paths)
            raise

        return result, owned_paths

    @staticmethod
    def _decode_data_uri(*, parameter_name: str, data_uri: str) -> tuple[bytes, str]:
        """
        Decode one base64 data URI into raw content and the extension to store it under.

        Uploaded content is stored verbatim, whatever its type. PyRIT operators are
        trusted and every file type is a legitimate payload: uploading an HTML file so
        an attack can push it to a blob target is a valid operation. The only thing the
        server decides here is the file *name*, which is generated, so a declared MIME
        type can never influence where the upload lands. Restrictions on rendering
        untrusted content belong to the media route that serves it back, not to storage.

        Returns:
            tuple[bytes, str]: The decoded content and its file extension.

        Raises:
            ValueError: If the value is not a base64 data URI or its payload is not
                valid base64.
        """
        header, separator, payload = data_uri.partition(",")
        media_type, _, encoding = header.removeprefix("data:").partition(";")
        if not separator or not payload or not header.startswith("data:") or encoding.lower() != "base64":
            raise ValueError(f"Path parameter '{parameter_name}' must be a base64 data URI")

        try:
            content = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Path parameter '{parameter_name}' contains invalid base64 data") from exc

        media_type = media_type.strip().lower()
        extension = mimetypes.guess_extension(media_type) if media_type else None
        return content, extension or _DEFAULT_UPLOAD_EXTENSION

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

    async def _remove_owned_artifacts_async(self, *, paths: list[Path]) -> None:
        """Remove explicitly owned files, limited to the managed upload directory."""
        for path in paths:
            resolved_path = await asyncio.to_thread(path.resolve)
            try:
                resolved_path.relative_to(self._upload_path)
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
        start_token: str = "⟪",
        end_token: str = "⟫",
    ) -> tuple[list[PreviewStep], str, PromptDataType]:
        """
        Collect preview steps using the normalizer's conversion-only path.

        Returns:
            Tuple of (steps, final_value, final_type).
        """
        if not converters:
            return [], initial_value, initial_type

        piece = MessagePiece(
            role="user",
            original_value=initial_value,
            original_value_data_type=initial_type,
            not_in_memory=True,
        )
        message = piece.to_message()
        normalizer = PromptNormalizer(start_token=start_token, end_token=end_token)
        steps: list[PreviewStep] = []

        for conv_id, conv_type, conv_obj in converters:
            input_value, input_type = piece.converted_value, piece.converted_value_data_type
            await normalizer.convert_values_async(
                converter_configurations=[ConverterConfiguration(converters=[conv_obj])],
                message=message,
            )

            steps.append(
                PreviewStep(
                    converter_id=conv_id,
                    converter_type=conv_type,
                    input_value=input_value,
                    input_data_type=input_type,
                    output_value=piece.converted_value,
                    output_data_type=piece.converted_value_data_type,
                )
            )

        return steps, piece.converted_value, piece.converted_value_data_type


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
