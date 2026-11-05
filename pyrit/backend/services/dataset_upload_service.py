# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded canonical seed ingestion, with no template execution or client path reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any, get_args
from uuid import UUID, uuid4

from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.middleware.request_size import RequestTooLargeError
from pyrit.backend.models.datasets import DatasetUploadLimits, DatasetUploadManifest, DatasetUploadResponse
from pyrit.exceptions.dataset_exception import DatasetAPIException
from pyrit.memory import CentralMemory, DataTypeSerializer, data_serializer_factory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    AttackSeedGroup,
    PromptDataType,
    SeedDataset,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
    SeedSimulatedConversation,
    SeedUnion,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import Request

logger = logging.getLogger(__name__)
_SEED_ADAPTER = TypeAdapter(SeedUnion)


def _invalid_manifest(*, field: str, message: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", "manifest", field), "msg": message, "type": "value_error"}])


class _DatasetMultipartParser(MultiPartParser):
    """Also bound a file-valued JSON part and reject truncated final multipart boundaries."""

    def __init__(self, *, request: Request, limits: DatasetUploadLimits) -> None:
        super().__init__(
            headers=request.headers,
            stream=self._bounded_stream_async(request=request, maximum=limits.request_bytes),
            max_fields=1,
            max_files=limits.prompts + 1,
            max_part_size=limits.manifest_bytes,
        )
        self._manifest_bytes = 0
        self._manifest_limit = limits.manifest_bytes
        self._ended = False

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.field_name == "manifest":
            self._manifest_bytes += end - start
            if self._manifest_bytes > self._manifest_limit:
                raise RequestTooLargeError
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self._ended = True
        super().on_end()

    async def parse(self) -> FormData:  # pyrit-async-suffix-exempt
        form = await super().parse()
        if not self._ended:
            await form.close()
            raise _invalid_manifest(field="multipart", message="Multipart body is incomplete.")
        return form

    @staticmethod
    async def _bounded_stream_async(*, request: Request, maximum: int) -> AsyncGenerator[bytes, None]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > maximum:
                raise RequestTooLargeError
            yield chunk


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


async def _manifest_and_files_async(
    *, form: FormData, limits: DatasetUploadLimits
) -> tuple[DatasetUploadManifest, dict[str, UploadFile]]:
    files: dict[str, UploadFile] = {}
    parts = form.multi_items()
    if len({name for name, _ in parts}) != len(parts):
        raise _invalid_manifest(field="multipart", message="Duplicate multipart part names are not allowed.")
    manifest_part = form.get("manifest")
    if manifest_part is None:
        raise _invalid_manifest(field="manifest", message="Exactly one JSON manifest part is required.")
    if isinstance(manifest_part, UploadFile):
        raw = await manifest_part.read(limits.manifest_bytes + 1)
    else:
        raw = manifest_part.encode("utf-8")
    if len(raw) > limits.manifest_bytes:
        raise RequestTooLargeError
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
        manifest = DatasetUploadManifest.model_validate(data)
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _invalid_manifest(field="manifest", message="Invalid dataset manifest JSON or fields.") from exc
    if len(manifest.seeds) > limits.prompts:
        raise RequestTooLargeError
    for name, part in parts:
        if name == "manifest":
            continue
        if not isinstance(part, UploadFile) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name):
            raise _invalid_manifest(field="multipart", message="Media parts require valid names and filenames.")
        files[name] = part
    return manifest, files


def _validated_seeds(
    *, manifest: DatasetUploadManifest, files: dict[str, UploadFile], added_by: str
) -> list[SeedUnion]:
    seeds: list[SeedUnion] = []
    groups: dict[UUID, UUID] = {}
    aliases: dict[str, UUID] = {}
    referenced: set[str] = set()

    def prepare(*, raw: Any, field: str, prompt_only: bool = False) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise _invalid_manifest(field=field, message="A seed must be an object.")
        if raw.get("is_jinja_template", False) is not False:
            raise _invalid_manifest(field=field, message="Trusted templates are not accepted.")
        seed_type = raw.get("seed_type", "prompt")
        if prompt_only and seed_type != "prompt":
            raise _invalid_manifest(field=field, message="A nested prompt must be a SeedPrompt.")
        if raw.get("dataset_name", manifest.name) not in (None, manifest.name):
            raise _invalid_manifest(field=field, message="Seeds must belong to the manifest dataset.")
        if seed_type == "prompt":
            if raw.get("data_type") not in get_args(PromptDataType) or not isinstance(raw.get("value"), str):
                raise _invalid_manifest(field=field, message="An explicit data_type and string value are required.")
            value = raw["value"]
            if raw["data_type"] in MEDIA_PATH_DATA_TYPES:
                part = value.removeprefix("upload:")
                if not value.startswith("upload:") or part not in files:
                    raise _invalid_manifest(
                        field=field, message="File-valued seeds must reference an attached upload part."
                    )
                referenced.add(part)
            elif value.startswith("upload:"):
                raise _invalid_manifest(field=field, message="Only file-valued prompts may reference upload parts.")
        data = {**raw, "seed_type": seed_type, "id": uuid4(), "dataset_name": manifest.name, "is_jinja_template": False}
        data.update(value_sha256=None, date_added=datetime.now(UTC), added_by=added_by, origin=SeedOrigin.LOCAL)
        if raw.get("prompt_group_id") is not None:
            original_group = UUID(str(raw["prompt_group_id"]))
            data["prompt_group_id"] = groups.setdefault(original_group, uuid4())
        elif isinstance(raw.get("prompt_group_alias"), str) and raw["prompt_group_alias"]:
            data["prompt_group_id"] = aliases.setdefault(raw["prompt_group_alias"], uuid4())
        if seed_type == "simulated_conversation":
            for name in (
                "adversarial_chat_system_prompt",
                "simulated_target_system_prompt",
                "next_message_system_prompt",
            ):
                if name + "_path" in raw:
                    raise _invalid_manifest(field=field, message="Client prompt-file paths are not accepted.")
                if raw.get(name) is not None:
                    data[name] = prepare(raw=raw[name], field=f"{field}.{name}", prompt_only=True)
        if seed_type == "objective" and isinstance(raw.get("metadata"), dict):
            association = raw["metadata"].get("adversarial_prompts")
            if association is not None:
                if not isinstance(association, dict):
                    raise _invalid_manifest(field=field, message="Invalid adversarial prompt association.")
                data["metadata"] = {
                    **raw["metadata"],
                    "adversarial_prompts": {
                        name: SeedPrompt.model_validate(
                            prepare(raw=prompt, field=f"{field}.metadata.adversarial_prompts.{name}", prompt_only=True)
                        ).model_dump(mode="json")
                        if prompt is not None
                        else None
                        for name, prompt in association.items()
                    },
                }
        return data

    for index, raw in enumerate(manifest.seeds):
        field = f"seeds.{index}"
        try:
            seeds.append(_SEED_ADAPTER.validate_python(prepare(raw=raw, field=field)))
        except (ValidationError, ValueError, TypeError) as exc:
            raise _invalid_manifest(field=field, message="Invalid seed fields.") from exc
    if referenced != set(files):
        raise _invalid_manifest(field="multipart", message="Every media part must be referenced by a seed.")
    try:
        grouped = SeedDataset.group_seed_prompts_by_prompt_group_id([seed.model_copy(deep=True) for seed in seeds])
        for group in grouped:
            if any(isinstance(seed, SeedObjective) for seed in group.seeds):
                AttackSeedGroup(seeds=group.seeds)
    except ValueError as exc:
        raise _invalid_manifest(field="seeds", message="Invalid seed grouping or conversation roles.") from exc
    return seeds


def _media_prompts(seeds: list[SeedUnion]) -> list[SeedPrompt]:
    return [
        prompt
        for seed in seeds
        for prompt in ([seed] if isinstance(seed, SeedPrompt) else vars(seed).values())
        if isinstance(prompt, SeedPrompt) and prompt.data_type in MEDIA_PATH_DATA_TYPES
    ]


async def _upload_chunks_async(*, part: UploadFile, digest: Any) -> AsyncGenerator[bytes, None]:
    while chunk := await part.read(1024 * 1024):
        digest.update(chunk)
        yield chunk


async def upload_dataset_async(request: Request) -> DatasetUploadResponse:
    """
    Validate all inputs, journal staged assets, then atomically create the dataset.

    Returns:
        DatasetUploadResponse: Committed population counts and the existing browsing key.
    """
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise DatasetAPIException(
            status_code=415, message="Use multipart/form-data with a JSON manifest and media parts."
        )
    limits = DatasetUploadLimits.from_environment()
    try:
        form = await _DatasetMultipartParser(request=request, limits=limits).parse()
    except MultiPartException as exc:
        raise _invalid_manifest(field="multipart", message="Invalid multipart upload.") from exc
    try:
        manifest, files = await _manifest_and_files_async(form=form, limits=limits)
        user = getattr(request.state, "user", None)
        added_by = user.oid if isinstance(user, AuthenticatedUser) else "dataset-api"
        seeds = await asyncio.to_thread(_validated_seeds, manifest=manifest, files=files, added_by=added_by)
        return await _persist_dataset_async(manifest=manifest, seeds=seeds, files=files)
    finally:
        await form.close()


async def _persist_dataset_async(
    *, manifest: DatasetUploadManifest, seeds: list[SeedUnion], files: dict[str, UploadFile]
) -> DatasetUploadResponse:
    memory = CentralMemory.get_memory_instance()
    serializers: dict[str, DataTypeSerializer] = {}
    paths: dict[str, str] = {}
    for name, part in files.items():
        extension = PureWindowsPath(part.filename or "").suffix.lstrip(".")
        if not re.fullmatch(r"[A-Za-z0-9]{1,16}", extension):
            extension = "bin"
        serializers[name] = data_serializer_factory(
            category="seed-prompt-entries", data_type="binary_path", extension=extension
        )
        paths[name] = str(await serializers[name].get_data_filename_async())
        if memory.results_storage_io is None or await memory.results_storage_io.path_exists_async(paths[name]):
            raise DatasetAPIException(status_code=503, message="A fresh managed upload path is unavailable.")
    reservation = asyncio.create_task(
        memory.reserve_dataset_upload_async(dataset_name=manifest.name, paths=list(paths.values()))
    )
    cancelled = False
    try:
        while not reservation.done():
            try:
                await asyncio.shield(reservation)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        upload_id = reservation.result()
    except DatasetNameConflictError as exc:
        raise DatasetAPIException(
            status_code=409, message="A stored dataset or upload already uses this name."
        ) from exc

    async def write_media_async() -> None:
        hashes: dict[str, str] = {}
        for name, part in files.items():
            digest = hashlib.sha256()
            await serializers[name].save_stream_async(_upload_chunks_async(part=part, digest=digest))
            hashes[name] = digest.hexdigest()
        for prompt in _media_prompts(seeds):
            name = prompt.value.removeprefix("upload:")
            prompt.value = paths[name]
            prompt.value_sha256 = hashes[name]
            prompt.metadata = {
                **(prompt.metadata or {}),
                "upload_filename": files[name].filename,
                "upload_content_type": files[name].content_type,
            }
        for seed in seeds:
            if isinstance(seed, SeedSimulatedConversation):
                seed.value = SeedSimulatedConversation.model_validate(seed).value
            if seed.data_type not in MEDIA_PATH_DATA_TYPES:
                seed.value_sha256 = hashlib.sha256(seed.value.encode("utf-8")).hexdigest()

    try:
        if cancelled:
            raise asyncio.CancelledError
        await memory.commit_dataset_upload_async(upload_id=upload_id, seeds=seeds, write_media=write_media_async)
    except BaseException as error:
        # Cancellation and storage/DB errors take the same compensating cleanup path.
        cleanup = asyncio.create_task(memory.cleanup_dataset_media_async(operation_id=upload_id, abandon_upload=True))
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            result = cleanup.result()
        except Exception as exc:
            logger.exception("Dataset upload rollback cleanup failed: %s", upload_id)
            raise DatasetAPIException(
                status_code=503,
                message="Upload failed; cleanup is pending. Inspect the resource in Location.",
                cleanup_id=upload_id,
            ) from exc
        if result["status"] == "pending":
            raise DatasetAPIException(
                status_code=503,
                message="Upload failed; media cleanup is pending. Inspect the resource in Location.",
                cleanup_id=upload_id,
            ) from None
        if not isinstance(error, Exception):
            raise
        if isinstance(error, DatasetNameConflictError):
            raise DatasetAPIException(status_code=409, message="The upload reservation is no longer active.") from error
        logger.exception("Dataset upload failed for operation %s", upload_id)
        raise DatasetAPIException(status_code=503, message="Dataset upload could not be stored.") from error
    finally:
        await memory.release_dataset_upload_async(upload_id)
    return DatasetUploadResponse(
        name=manifest.name,
        selection_key=f"dataset:named:{manifest.name}",
        seed_pieces=len(seeds),
        logical_examples=len({seed.prompt_group_id or seed.id for seed in seeds}),
    )
