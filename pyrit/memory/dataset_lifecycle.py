# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Create-only dataset transactions and durable, reference-safe owned-media cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import select
from sqlalchemy.util.concurrency import await_only

from pyrit.memory.dataset_upload_lease import try_upload_lease_async
from pyrit.memory.memory_models import (
    DatasetLifecycleEntry,
    DatasetLifecycleLock,
    DatasetMediaEntry,
    PromptMemoryEntry,
    ScorableContentEntry,
    SeedEntry,
)
from pyrit.models import MEDIA_PATH_DATA_TYPES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.orm import Session

    from pyrit.memory.memory_interface import MemoryInterface
    from pyrit.memory.storage import StorageIO
    from pyrit.models import SeedUnion

logger = logging.getLogger(__name__)


class DatasetNameConflictError(ValueError):
    """A stored dataset or another import already uses this exact name."""


def media_key(value: str, *, storage: StorageIO | None = None) -> str:
    """
    Canonicalize local aliases and blob SAS URLs.

    Returns:
        str: A bounded storage identity, independent of signed query parameters.
    """
    if value.startswith("/api/media?"):
        value = parse_qs(urlparse(value).query).get("path", [value])[0]
    parsed = urlparse(value)
    if storage is not None:
        canonical = storage.canonical_path(value)
    elif parsed.scheme in {"http", "https"} and (parsed.hostname or "").endswith(".blob.core.windows.net"):
        # AzureBlobStorageIO resolves the URL's blob name against its configured container,
        # not against the supplied hostname/container. Match that effective storage identity.
        canonical = "blob:" + "/".join(parsed.path.split("/")[2:])
    elif parsed.scheme in {"http", "https"}:
        canonical = parsed._replace(
            netloc=parsed.netloc.lower(), path=unquote(parsed.path), query="", fragment=""
        ).geturl()
    else:
        canonical = os.path.normcase(str(Path(value).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _media_keys(session: Session, values: Sequence[str]) -> set[str]:
    def canonicalize() -> set[str]:
        return {media_key(value, storage=session.info.get("dataset_storage_io")) for value in values}

    if session.get_bind().dialect.is_async:
        return await_only(asyncio.to_thread(canonicalize))
    return canonicalize()


def lock_dataset_lifecycle(session: Session) -> None:
    """
    Hold the same database lock for lifecycle decisions and every reference change.

    Raises:
        RuntimeError: If schema migration has not installed the singleton lock row.
    """
    if session.info.get("dataset_lifecycle_locked"):
        return
    from pyrit.memory.memory_session import _begin_sqlite_write

    _begin_sqlite_write(session)
    statement = select(DatasetLifecycleLock.id).where(DatasetLifecycleLock.id == 1)
    if session.get_bind().dialect.name == "mssql":
        statement = statement.with_hint(DatasetLifecycleLock, "WITH (XLOCK, HOLDLOCK)", dialect_name="mssql")
    with session.no_autoflush:
        if session.scalar(statement) is None:
            raise RuntimeError("Dataset lifecycle lock is missing; apply memory schema migrations.")
    session.info["dataset_lifecycle_locked"] = True


def entry_references_dataset_assets(entry: object) -> bool:
    """
    Return whether writing ``entry`` can add or change a dataset reference.

    Seeds always can, through dataset names and reservations. Message pieces and scorable
    content reference dataset assets only through media-path values; cleanup never inspects
    text rows, so text-only writes do not need the lifecycle lock.

    Args:
        entry (object): A pending ORM entry.

    Returns:
        bool: True when writing the entry must hold the dataset lifecycle lock.
    """
    if isinstance(entry, SeedEntry):
        return True
    return bool(_entry_media_values(entry))


def _entry_media_values(entry: object) -> list[str]:
    if isinstance(entry, SeedEntry):
        try:
            return _seed_media_values(seed_type=entry.seed_type, value=entry.value, data_type=entry.data_type)
        except ValueError:
            # Legacy raw configurations remain inspectable; cleanup still fails closed.
            logger.warning("Seed %s has an invalid configuration; media cleanup requires repair.", entry.id)
            return []
    if isinstance(entry, ScorableContentEntry):
        return [entry.value] if entry.data_type in MEDIA_PATH_DATA_TYPES else []
    if isinstance(entry, PromptMemoryEntry):
        return [
            value
            for value, data_type in (
                (entry.original_value, entry.original_value_data_type),
                (entry.converted_value, entry.converted_value_data_type),
            )
            if value and data_type in MEDIA_PATH_DATA_TYPES
        ]
    return []


def _seed_media_values(*, seed_type: str, value: str, data_type: str) -> list[str]:
    if data_type in MEDIA_PATH_DATA_TYPES:
        return [value]
    if seed_type != "simulated_conversation":
        return []
    config = json.loads(value)
    if not isinstance(config, dict):
        raise ValueError("A simulated conversation requires a stored configuration object.")
    values = []
    for field in ("adversarial_chat_system_prompt", "simulated_target_system_prompt", "next_message_system_prompt"):
        prompt = config.get(field)
        if isinstance(prompt, dict) and prompt.get("data_type") in MEDIA_PATH_DATA_TYPES:
            path = prompt.get("value")
            if not isinstance(path, str):
                raise ValueError(f"Stored {field} requires a media path.")
            values.append(path)
    return values


def validate_dataset_reference_writes(*, session: Session, entries: Sequence[object]) -> None:
    """
    Reject late references to claimed/deleted assets and writes into reserved imports.

    Raises:
        DatasetNameConflictError: If another upload reserved this dataset.
        ValueError: If media is staged by another upload or claimed for cleanup.
    """
    values = [value for entry in entries for value in _entry_media_values(entry)]
    _validate_dataset_references(
        session=session,
        dataset_names={entry.dataset_name for entry in entries if isinstance(entry, SeedEntry)},
        keys=_media_keys(session, values) if values else set(),
    )


def _validate_dataset_references(*, session: Session, dataset_names: set[str | None], keys: set[str]) -> None:
    with session.no_autoflush:
        own_upload = session.info.get("dataset_upload_id")
        names = sorted(name for name in dataset_names if name is not None)
        for start in range(0, len(names), 500):
            reservation = select(DatasetLifecycleEntry.id).where(
                DatasetLifecycleEntry.status == "staging",
                DatasetLifecycleEntry.dataset_name.in_(names[start : start + 500]),
            )
            if own_upload is not None:
                reservation = reservation.where(DatasetLifecycleEntry.id != own_upload)
            if session.scalar(reservation.limit(1)) is not None:
                raise DatasetNameConflictError("The dataset name is reserved by an upload.")
        # Chunk for SQL Server's parameter limit.
        ordered = sorted(keys)
        for start in range(0, len(ordered), 500):
            assets = session.execute(
                select(DatasetMediaEntry.status, DatasetMediaEntry.upload_id).where(
                    DatasetMediaEntry.key.in_(ordered[start : start + 500])
                )
            ).all()
            for asset_status, upload_id in assets:
                if asset_status in {"pending", "deleted"} or (asset_status == "staged" and upload_id != own_upload):
                    raise ValueError("Media has been removed or is reserved for dataset cleanup.")


def stored_media_keys(session: Session) -> set[str]:
    """
    Read all live seed, original/converted message, and loose scorer-media references.

    Returns:
        set[str]: Canonical identities of the referenced media.
    """
    values: list[str] = []
    for seed_type, value, data_type in session.execute(
        select(SeedEntry.seed_type, SeedEntry.value, SeedEntry.data_type).where(
            (SeedEntry.data_type.in_(MEDIA_PATH_DATA_TYPES)) | (SeedEntry.seed_type == "simulated_conversation")
        )
    ):
        values.extend(_seed_media_values(seed_type=seed_type, value=value, data_type=data_type))
    values.extend(
        session.scalars(
            select(ScorableContentEntry.value).where(ScorableContentEntry.data_type.in_(MEDIA_PATH_DATA_TYPES))
        ).all()
    )
    for value_column, type_column in (
        (PromptMemoryEntry.original_value, PromptMemoryEntry.original_value_data_type),
        (PromptMemoryEntry.converted_value, PromptMemoryEntry.converted_value_data_type),
    ):
        values.extend(session.scalars(select(value_column).where(type_column.in_(MEDIA_PATH_DATA_TYPES))).all())
    return _media_keys(session, [value for value in values if value])


def reserved_seed_state(session: Session) -> dict[uuid.UUID, tuple[object, ...]]:
    """
    Snapshot only seed rows protected by another staging reservation.

    Returns:
        dict[uuid.UUID, tuple[object, ...]]: Protected row values keyed by seed identity.
    """
    reservations = select(DatasetLifecycleEntry.dataset_name).where(DatasetLifecycleEntry.status == "staging")
    own_upload = session.info.get("dataset_upload_id")
    if own_upload is not None:
        reservations = reservations.where(DatasetLifecycleEntry.id != own_upload)
    with session.no_autoflush:
        rows = session.execute(
            select(SeedEntry.id, *SeedEntry.__table__.columns).where(SeedEntry.dataset_name.in_(reservations))
        )
        return {row[0]: tuple(row[1:]) for row in rows}


def validate_stored_dataset_references(
    session: Session, *, reserved_seeds: dict[uuid.UUID, tuple[object, ...]] | None = None
) -> None:
    """
    Validate bulk writes without refreshing or discarding pending ORM changes.

    Raises:
        DatasetNameConflictError: If bulk DML changed a protected seed population.
    """
    with session.no_autoflush:
        if reserved_seeds is not None and reserved_seed_state(session) != reserved_seeds:
            raise DatasetNameConflictError("The dataset name is reserved by an upload.")
        _validate_dataset_references(
            session=session,
            dataset_names=set(),
            keys=stored_media_keys(session),
        )


async def _finish_storage_write_async(write: Awaitable[None]) -> None:
    task = asyncio.ensure_future(write)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # A partial/late write must finish before compensating deletion starts.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


async def reserve_upload_async(
    *, memory: MemoryInterface, dataset_name: str, paths: Sequence[str], mutation: bool = False
) -> uuid.UUID:
    """
    Durably journal every generated asset path before any bytes are written.

    Returns:
        uuid.UUID: The durable upload/cleanup identifier.

    Raises:
        DatasetNameConflictError: If this exact dataset name is already in use.
    """
    upload_id = uuid.uuid4()
    lease = await try_upload_lease_async(memory=memory, upload_id=upload_id)
    if lease is None:
        raise DatasetNameConflictError("The upload identifier is already active.")
    memory._dataset_upload_leases[upload_id] = lease
    try:
        return await _reserve_upload_async(
            memory=memory, upload_id=upload_id, dataset_name=dataset_name, paths=paths, mutation=mutation
        )
    except BaseException:
        await memory.release_dataset_upload_async(upload_id)
        raise


async def _reserve_upload_async(
    *, memory: MemoryInterface, upload_id: uuid.UUID, dataset_name: str, paths: Sequence[str], mutation: bool = False
) -> uuid.UUID:
    keys = await asyncio.to_thread(lambda: [media_key(path, storage=memory.results_storage_io) for path in paths])
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        stored = await session.scalar(select(SeedEntry.id).where(SeedEntry.dataset_name == dataset_name).limit(1))
        staging = await session.scalar(
            select(DatasetLifecycleEntry.id)
            .where(DatasetLifecycleEntry.status == "staging", DatasetLifecycleEntry.dataset_name == dataset_name)
            .limit(1)
        )
        if staging is not None or (stored is not None and not mutation):
            raise DatasetNameConflictError("A stored dataset or upload already uses this name.")
        if mutation:
            names = (
                await session.scalars(select(SeedEntry.dataset_name).where(SeedEntry.dataset_name == dataset_name))
            ).all()
            if dataset_name not in names:
                raise LookupError("No stored dataset has this exact name.")
        session.add(
            DatasetLifecycleEntry(
                id=upload_id,
                dataset_name=dataset_name,
                kind="mutation" if mutation else "upload",
                status="staging",
                deleted_seeds=0,
            )
        )
        await session.flush()
        session.add_all(
            DatasetMediaEntry(key=key, path=path, upload_id=upload_id, status="staged")
            for key, path in zip(keys, paths, strict=True)
        )
        await session.commit()
    return upload_id


async def commit_upload_async(
    *,
    memory: MemoryInterface,
    upload_id: uuid.UUID,
    seeds: Sequence[SeedUnion],
    write_media: Callable[[], Awaitable[None]],
) -> bool:
    """
    Write staged bytes and all seeds under one reference lock; never deduplicate.

    Returns:
        bool: Whether native ownership was released after the successful commit.

    Raises:
        DatasetNameConflictError: If recovery already released the reservation.
        ValueError: If the batch is empty, belongs to another dataset, or lacks storage checksums.
    """
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        lease = memory._dataset_upload_leases.get(upload_id)
        if lease is None or not await lease.active_async():
            raise DatasetNameConflictError("The upload owner is no longer active.")
        operation = await session.get(DatasetLifecycleEntry, upload_id)
        if operation is None or operation.status != "staging":
            raise DatasetNameConflictError("The upload reservation is no longer active.")
        if not seeds or any(seed.dataset_name != operation.dataset_name or not seed.added_by for seed in seeds):
            raise ValueError("An upload requires a nonempty, storage-ready batch belonging to its reserved dataset.")
        # The durable journal survives a rollback or process exit, whereas these seeds do not.
        session.info["dataset_upload_id"] = upload_id
        await _finish_storage_write_async(write_media())
        if any(not seed.value_sha256 for seed in seeds):
            raise ValueError("Uploaded seed checksums must be computed before persistence.")
        session.add_all(SeedEntry(entry=seed) for seed in seeds)
        assets = (
            await session.scalars(select(DatasetMediaEntry).where(DatasetMediaEntry.upload_id == upload_id))
        ).all()
        for asset in assets:
            asset.status = "live"
        operation.status = "completed"
        await session.commit()
    try:
        await memory.release_dataset_upload_async(upload_id)
    except Exception:
        logger.exception("Committed upload native lease release failed: %s", upload_id)
        return False
    return True


async def delete_dataset_async(*, memory: MemoryInterface, dataset_name: str) -> uuid.UUID:
    """
    Remove only exact-name seeds and durably queue only their recorded owned media.

    Returns:
        uuid.UUID: The durable deletion identifier.

    Raises:
        LookupError: If no seeds have this exact dataset name.
    """
    deletion_id = uuid.uuid4()
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        entries = (await session.scalars(select(SeedEntry).where(SeedEntry.dataset_name == dataset_name))).all()
        # SQL Server collations can match case variants and trailing spaces; the API is exact.
        entries = [entry for entry in entries if entry.dataset_name == dataset_name]
        if not entries:
            raise LookupError("No stored dataset has this exact name.")
        session.add(
            DatasetLifecycleEntry(
                id=deletion_id, dataset_name=dataset_name, kind="delete", status="pending", deleted_seeds=len(entries)
            )
        )
        await session.flush()
        for entry in entries:
            await session.delete(entry)
        uploads = (
            await session.scalars(
                select(DatasetLifecycleEntry).where(DatasetLifecycleEntry.kind.in_(["upload", "mutation"]))
            )
        ).all()
        upload_ids = {upload.id for upload in uploads if upload.dataset_name == dataset_name}
        assets = (
            await session.scalars(select(DatasetMediaEntry).where(DatasetMediaEntry.status.in_(["live", "retained"])))
        ).all()
        for asset in assets:
            if asset.upload_id in upload_ids:
                asset.cleanup_id = deletion_id
                asset.status = "pending"
        await session.commit()
    return deletion_id


async def cleanup_dataset_media_async(
    *, memory: MemoryInterface, operation_id: uuid.UUID, abandon_upload: bool = False
) -> dict[str, object]:
    """
    Retry cleanup inline; a committed tombstone blocks all new memory references.

    Returns:
        dict[str, object]: Durable counts, completion state, and a sanitized cleanup error.

    Raises:
        DatasetNameConflictError: If a live upload still owns the reservation.
        LookupError: If the operation identifier is unknown.
        RuntimeError: If results storage is unavailable during cleanup.
    """
    lease = memory._dataset_upload_leases.pop(operation_id, None) if abandon_upload else None
    if lease is None:
        lease = await try_upload_lease_async(memory=memory, upload_id=operation_id)
    if lease is None:
        raise DatasetNameConflictError("The upload is still active; cleanup cannot claim its reservation.")
    try:
        return await _cleanup_dataset_media_async(memory=memory, operation_id=operation_id)
    finally:
        await _finish_storage_write_async(lease.close_async())


async def _cleanup_dataset_media_async(*, memory: MemoryInterface, operation_id: uuid.UUID) -> dict[str, object]:
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        operation = await session.get(DatasetLifecycleEntry, operation_id)
        if operation is None:
            raise LookupError("Dataset deletion not found.")
        if operation.status == "staging":
            operation.status = "pending"
            assets = (
                await session.scalars(select(DatasetMediaEntry).where(DatasetMediaEntry.upload_id == operation_id))
            ).all()
            for asset in assets:
                asset.cleanup_id = operation_id
                asset.status = "pending"
        assets = (
            await session.scalars(select(DatasetMediaEntry).where(DatasetMediaEntry.cleanup_id == operation_id))
        ).all()
        try:
            references = await session.run_sync(stored_media_keys) if assets else set()
        except (OSError, ValueError):
            logger.exception("Could not establish media reference safety for cleanup %s", operation_id)
            references = None
        for asset in assets:
            if asset.status == "pending" and references is not None and asset.key in references:
                asset.status = "retained"
        candidates = (
            [(asset.key, asset.path) for asset in assets if asset.status == "pending"] if references is not None else []
        )
        # Claiming precedes external deletion. This state survives a crash after unlinking.
        await session.commit()

    failed = False
    for key, path in candidates:
        try:
            if memory.results_storage_io is None:
                raise RuntimeError("Results storage is not configured.")
            await memory.results_storage_io.delete_file_async(path)
        except Exception:
            logger.exception("Dataset media cleanup failed for operation %s", operation_id)
            failed = True
            continue
        async with await memory.get_session_async() as session:
            current = await session.get(DatasetMediaEntry, key)
            if current is not None:
                current.status = "deleted"
                await session.commit()
    async with await memory.get_session_async() as session:
        operation = await session.get(DatasetLifecycleEntry, operation_id)
        if operation is None:
            raise LookupError("Dataset deletion not found.")
        remaining = (
            await session.scalars(
                select(DatasetMediaEntry.key).where(
                    DatasetMediaEntry.cleanup_id == operation_id, DatasetMediaEntry.status == "pending"
                )
            )
        ).all()
        if operation.status != "completed":
            operation.status = "pending" if remaining else "completed"
        operation.error = "Media cleanup failed; retry this status resource." if failed or remaining else None
        result: dict[str, object] = {
            "deletion_id": str(operation_id),
            "status": operation.status,
            "deleted_seeds": operation.deleted_seeds,
            "pending_media": len(remaining),
            "error": operation.error,
        }
        await session.commit()
        return result


async def recover_dataset_cleanup_async(memory: MemoryInterface) -> None:
    """Recover interrupted imports/deletions when a backend runtime starts."""
    async with await memory.get_session_async() as session:
        ids = (
            await session.scalars(
                select(DatasetLifecycleEntry.id).where(DatasetLifecycleEntry.status.in_(["staging", "pending"]))
            )
        ).all()
    for operation_id in ids:
        try:
            await cleanup_dataset_media_async(memory=memory, operation_id=operation_id)
        except DatasetNameConflictError:
            logger.info("Skipping dataset recovery for active upload %s", operation_id)
