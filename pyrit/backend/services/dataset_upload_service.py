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
from pyrit.backend.models.datasets import (
    DatasetDeletionResponse,
    DatasetMutationResponse,
    DatasetUploadLimits,
    DatasetUploadManifest,
    DatasetUploadResponse,
)
from pyrit.exceptions.dataset_exception import DatasetAPIException
from pyrit.memory import CentralMemory, DataTypeSerializer, data_serializer_factory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.memory.dataset_mutation import (
    DatasetMutationLimitError,
    DatasetVersionConflictError,
    media_slots,
    reject_unsafe_seed_input,
    retained_media_handle,
    seed_replacement,
)
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    AttackSeedGroup,
    PromptDataType,
    SeedDataset,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
    SeedRecord,
    SeedSimulatedConversation,
    SeedUnion,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import Request

    from pyrit.memory import MemoryInterface

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
            max_files=3 * limits.prompts + 1,
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
    if len(manifest.seeds) + len(manifest.delete_seed_ids) > limits.prompts:
        raise RequestTooLargeError
    for name, part in parts:
        if name == "manifest":
            continue
        if not isinstance(part, UploadFile) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name):
            raise _invalid_manifest(field="multipart", message="Media parts require valid names and filenames.")
        files[name] = part
    return manifest, files


def _validated_seeds(
    *,
    manifest: DatasetUploadManifest,
    files: dict[str, UploadFile],
    added_by: str,
    records: dict[UUID, SeedRecord] | None = None,
) -> list[SeedUnion]:
    seeds: list[SeedUnion] = []
    groups: dict[UUID, UUID] = {}
    aliases: dict[str, UUID] = {}
    referenced: set[str] = set()
    for index, raw in enumerate(manifest.seeds):
        try:
            reject_unsafe_seed_input(raw)
        except ValueError as exc:
            raise _invalid_manifest(field=f"seeds.{index}", message=str(exc)) from exc
    targeted: set[UUID] = set()
    existing_groups = {record.prompt_group_id for record in (records or {}).values() if record.prompt_group_id}

    def prepare(
        *, raw: Any, field: str, prompt_only: bool = False, record: SeedRecord | None = None, slot: str = "value"
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise _invalid_manifest(field=field, message="A seed must be an object.")
        if raw.get("is_jinja_template", False) is not False:
            raise _invalid_manifest(field=field, message="Trusted templates are not accepted.")
        seed_type = raw.get("seed_type", record.seed_type if record is not None and not prompt_only else "prompt")
        if prompt_only and seed_type != "prompt":
            raise _invalid_manifest(field=field, message="A nested prompt must be a SeedPrompt.")
        if not (records is not None and prompt_only) and raw.get("dataset_name", manifest.name) not in (
            None,
            manifest.name,
        ):
            raise _invalid_manifest(field=field, message="Seeds must belong to the manifest dataset.")
        if seed_type == "prompt":
            if raw.get("data_type") not in get_args(PromptDataType) or not isinstance(raw.get("value"), str):
                raise _invalid_manifest(field=field, message="An explicit data_type and string value are required.")
            value = raw["value"]
            if raw["data_type"] in MEDIA_PATH_DATA_TYPES:
                part = value.removeprefix("upload:")
                if value.startswith("retain:") and record is not None:
                    saved_slots = dict(media_slots(seed_replacement(record)))
                    saved = saved_slots.get(slot)
                    if (
                        saved is None
                        or saved["data_type"] != raw["data_type"]
                        or value
                        != retained_media_handle(
                            record=record, version=manifest.expected_version or "", slot=slot, value=saved["value"]
                        )
                    ):
                        raise _invalid_manifest(field=field, message="Retained media does not match this record slot.")
                elif not value.startswith("upload:") or part not in files:
                    raise _invalid_manifest(
                        field=field, message="File-valued seeds must reference an attached upload part."
                    )
                else:
                    referenced.add(part)
            elif value.startswith(("upload:", "retain:")):
                raise _invalid_manifest(field=field, message="Only file-valued prompts may reference media.")
        data = {**raw, "seed_type": seed_type, "id": uuid4(), "dataset_name": manifest.name, "is_jinja_template": False}
        data.update(value_sha256=None, date_added=datetime.now(UTC), added_by=added_by, origin=SeedOrigin.LOCAL)
        if records is not None and prompt_only and raw.get("id") is not None:
            data["id"] = UUID(str(raw["id"]))
        if records is not None and prompt_only:
            for key in ("date_added", "added_by", "origin", "dataset_name"):
                if key in raw:
                    data[key] = raw[key]
        embedded_update = records is not None and prompt_only
        if not embedded_update and raw.get("prompt_group_id") is not None and raw.get("prompt_group_alias") is not None:
            raise _invalid_manifest(field=field, message="Choose an existing group ID or a fresh group alias.")
        if raw.get("prompt_group_id") is not None:
            original_group = UUID(str(raw["prompt_group_id"]))
            if records is not None:
                if not prompt_only and original_group not in existing_groups:
                    raise _invalid_manifest(field=field, message="The group ID does not belong to this dataset.")
                data["prompt_group_id"] = original_group
            else:
                data["prompt_group_id"] = groups.setdefault(original_group, uuid4())
        elif not embedded_update and isinstance(raw.get("prompt_group_alias"), str) and raw["prompt_group_alias"]:
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
                    data[name] = prepare(
                        raw=raw[name], field=f"{field}.{name}", prompt_only=True, record=record, slot=name + ".value"
                    )
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
        record = None
        if records is not None and raw.get("id") is not None:
            try:
                identifier = UUID(str(raw["id"]))
            except ValueError as exc:
                raise _invalid_manifest(field=field, message="Invalid record ID.") from exc
            if identifier in targeted or str(identifier) in manifest.delete_seed_ids:
                raise _invalid_manifest(field=field, message="Duplicate or overlapping edit/delete record IDs.")
            targeted.add(identifier)
            record = records.get(identifier)
            if record is None:
                raise LookupError("An edit record ID is not in this dataset.")
            immutable = {
                "dataset_name": record.dataset_name,
                "seed_type": record.seed_type,
                "prompt_group_id": str(record.prompt_group_id) if record.prompt_group_id else None,
                "date_added": record.model_dump(mode="json")["date_added"],
                "added_by": record.added_by,
                "origin": record.origin.value,
            }
            for key, expected in immutable.items():
                if key in raw and raw[key] != expected:
                    raise _invalid_manifest(field=field, message=f"{key} is immutable on an edit.")
            if raw.get("prompt_group_alias") is not None:
                raise _invalid_manifest(field=field, message="Edited seeds cannot change groups.")
        try:
            data = prepare(raw=raw, field=field, record=record)
            if record is not None:
                data.update(
                    id=record.id,
                    dataset_name=record.dataset_name,
                    seed_type=record.seed_type,
                    prompt_group_id=record.prompt_group_id,
                    date_added=record.date_added,
                    added_by=record.added_by,
                    origin=record.origin,
                )
            seeds.append(_SEED_ADAPTER.validate_python(data))
        except (ValidationError, ValueError, TypeError) as exc:
            raise _invalid_manifest(field=field, message="Invalid seed fields.") from exc
    if referenced != set(files):
        raise _invalid_manifest(field="multipart", message="Every media part must be referenced by a seed.")
    if records is not None:
        return seeds
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


async def upload_dataset_async(request: Request) -> DatasetUploadResponse | DatasetMutationResponse:
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
        if manifest.mode == "update":
            return await _persist_mutation_async(manifest=manifest, files=files, added_by=added_by, limits=limits)
        seeds = await asyncio.to_thread(_validated_seeds, manifest=manifest, files=files, added_by=added_by)
        return await _persist_dataset_async(manifest=manifest, seeds=seeds, files=files)
    finally:
        await form.close()


async def _persist_dataset_async(
    *, manifest: DatasetUploadManifest, seeds: list[SeedUnion], files: dict[str, UploadFile]
) -> DatasetUploadResponse:
    memory = CentralMemory.get_memory_instance()
    serializers, paths = await _stage_media_async(memory=memory, files=files)
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
        hashes = await _write_parts_async(files=files, serializers=serializers)
        for prompt in _media_prompts(seeds):
            if prompt.value.startswith("upload:"):
                _bind_uploaded_prompt(prompt=prompt, files=files, paths=paths, hashes=hashes)
        _refresh_seed_values(seeds)

    committed = False
    release_failed = False
    try:
        if cancelled:
            raise asyncio.CancelledError
        release_failed = not await memory.commit_dataset_upload_async(
            upload_id=upload_id, seeds=seeds, write_media=write_media_async
        )
        committed = True
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
        try:
            await memory.release_dataset_upload_async(upload_id)
        except Exception:
            if not committed:
                raise
            logger.exception("Committed upload native lease release failed: %s", upload_id)
            release_failed = True
    return DatasetUploadResponse(
        name=manifest.name,
        selection_key=f"dataset:named:{manifest.name}",
        seed_pieces=len(seeds),
        logical_examples=len({seed.prompt_group_id or seed.id for seed in seeds}),
        cleanup=DatasetDeletionResponse(
            deletion_id=upload_id,
            status="pending",
            deleted_seeds=0,
            pending_media=None,
            error="Upload committed; retry cleanup at the status resource.",
        )
        if release_failed
        else None,
        cleanup_location=f"/api/datasets/deletions/{upload_id}" if release_failed else None,
    )


async def _stage_media_async(
    *, memory: MemoryInterface, files: dict[str, UploadFile]
) -> tuple[dict[str, DataTypeSerializer], dict[str, str]]:
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
    return serializers, paths


async def _write_parts_async(
    *, files: dict[str, UploadFile], serializers: dict[str, DataTypeSerializer]
) -> dict[str, str]:
    hashes = {}
    for name, part in files.items():
        digest = hashlib.sha256()
        await serializers[name].save_stream_async(_upload_chunks_async(part=part, digest=digest))
        hashes[name] = digest.hexdigest()
    return hashes


def _bind_uploaded_prompt(
    *, prompt: SeedPrompt, files: dict[str, UploadFile], paths: dict[str, str], hashes: dict[str, str]
) -> None:
    name = prompt.value.removeprefix("upload:")
    prompt.value, prompt.value_sha256 = paths[name], hashes[name]
    prompt.metadata = {
        **(prompt.metadata or {}),
        "upload_filename": files[name].filename,
        "upload_content_type": files[name].content_type,
    }


def _refresh_seed_values(seeds: list[SeedUnion]) -> None:
    for seed in seeds:
        if isinstance(seed, SeedSimulatedConversation):
            seed.value = SeedSimulatedConversation.model_validate(seed).value
        if seed.data_type not in MEDIA_PATH_DATA_TYPES:
            seed.value_sha256 = hashlib.sha256(seed.value.encode("utf-8")).hexdigest()


async def _persist_mutation_async(
    *, manifest: DatasetUploadManifest, files: dict[str, UploadFile], added_by: str, limits: DatasetUploadLimits
) -> DatasetMutationResponse:
    memory = CentralMemory.get_memory_instance()
    serializers, paths = await _stage_media_async(memory=memory, files=files)
    reservation = asyncio.create_task(
        memory.reserve_dataset_mutation_async(dataset_name=manifest.name, paths=list(paths.values()))
    )
    cancelled = False
    while not reservation.done():
        try:
            await asyncio.shield(reservation)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        upload_id = reservation.result()
    except LookupError as exc:
        raise DatasetAPIException(status_code=404, message="No stored dataset has this exact name.") from exc
    except DatasetNameConflictError as exc:
        raise DatasetAPIException(status_code=409, message="The dataset is reserved by another writer.") from exc

    saved: dict[UUID, SeedRecord] = {}

    def prepare(records: dict[UUID, SeedRecord]) -> list[SeedUnion]:
        saved.update(records)
        return _validated_seeds(manifest=manifest, files=files, added_by=added_by, records=records)

    async def write_media_async(seeds: list[SeedUnion]) -> None:
        hashes = await _write_parts_async(files=files, serializers=serializers)
        for seed in seeds:
            original = saved.get(seed.id)
            prompts = (
                [seed]
                if isinstance(seed, SeedPrompt)
                else [prompt for prompt in vars(seed).values() if isinstance(prompt, SeedPrompt)]
            )
            for prompt in prompts:
                if prompt.data_type not in MEDIA_PATH_DATA_TYPES:
                    continue
                if prompt.value.startswith("upload:"):
                    _bind_uploaded_prompt(prompt=prompt, files=files, paths=paths, hashes=hashes)
                elif original is not None:
                    slots = dict(media_slots(seed_replacement(original)))
                    slot = (
                        "value"
                        if prompt is seed
                        else next(name + ".value" for name, value in vars(seed).items() if value is prompt)
                    )
                    prompt.value = slots[slot]["value"]
                    prompt.value_sha256 = original.value_sha256 if prompt is seed else None
        _refresh_seed_values(seeds)

    result: dict[str, Any] | None = None
    release_failed = False
    try:
        if cancelled:
            raise asyncio.CancelledError
        result = await memory.commit_dataset_mutation_async(
            upload_id=upload_id,
            expected_version=manifest.expected_version or "",
            delete_ids=[UUID(value) for value in manifest.delete_seed_ids],
            prepare_seeds=prepare,
            write_media=write_media_async,
            max_records=limits.prompts,
            max_bytes=limits.manifest_bytes,
        )
    except BaseException as error:
        cleanup = asyncio.create_task(memory.cleanup_dataset_media_async(operation_id=upload_id, abandon_upload=True))
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            cleanup_result = cleanup.result()
        except Exception as exc:
            logger.exception("Mutation rollback cleanup failed: %s", upload_id)
            raise DatasetAPIException(
                status_code=503, message="Mutation failed; cleanup is pending. Inspect Location.", cleanup_id=upload_id
            ) from exc
        if cleanup_result["status"] == "pending":
            raise DatasetAPIException(
                status_code=503, message="Mutation failed; cleanup is pending. Inspect Location.", cleanup_id=upload_id
            ) from error
        if not isinstance(error, Exception):
            raise
        if isinstance(error, RequestValidationError):
            raise
        if isinstance(error, LookupError):
            raise DatasetAPIException(
                status_code=404, message="Dataset or record ID not found in this dataset."
            ) from error
        if isinstance(error, (DatasetVersionConflictError, DatasetNameConflictError)):
            raise DatasetAPIException(status_code=409, message=str(error)) from error
        if isinstance(error, DatasetMutationLimitError):
            raise RequestTooLargeError from error
        if isinstance(error, (ValueError, TypeError)):
            logger.debug("Dataset mutation validation rejected for operation %s", upload_id, exc_info=True)
            raise _invalid_manifest(field="seeds", message="Invalid final seed fields or grouping.") from error
        logger.exception("Dataset mutation failed for operation %s", upload_id)
        raise DatasetAPIException(status_code=503, message="Dataset mutation could not be stored.") from error
    finally:
        try:
            await memory.release_dataset_upload_async(upload_id)
        except Exception:
            if result is None:
                raise
            logger.exception("Committed mutation native lease release failed: %s", upload_id)
            release_failed = True
    if result is None:
        raise RuntimeError("Dataset mutation did not return a committed result.")
    cleanup_status = None
    if not release_failed:
        try:
            cleanup_result = await memory.cleanup_dataset_media_async(operation_id=upload_id)
            cleanup_status = DatasetDeletionResponse.model_validate(cleanup_result)
        except Exception:
            logger.exception("Committed mutation cleanup pending: %s", upload_id)
    if cleanup_status is None:
        cleanup_status = DatasetDeletionResponse(
            deletion_id=upload_id,
            status="pending",
            deleted_seeds=result["deleted"],
            pending_media=None,
            error="Mutation committed; retry cleanup at the status resource.",
        )
    return DatasetMutationResponse(
        name=manifest.name,
        selection_key=f"dataset:named:{manifest.name}",
        **result,
        cleanup=cleanup_status,
        cleanup_location=f"/api/datasets/deletions/{upload_id}",
    )
