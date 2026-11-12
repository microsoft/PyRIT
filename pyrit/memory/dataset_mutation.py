# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exact dataset snapshots and atomic targeted mutations, without executable reconstruction."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_args

from pydantic import TypeAdapter
from sqlalchemy import inspect, select

from pyrit.memory.dataset_lifecycle import (
    DatasetNameConflictError,
    _entry_media_values,
    _finish_storage_write_async,
    lock_dataset_lifecycle,
    media_key,
)
from pyrit.memory.memory_models import DatasetLifecycleEntry, DatasetMediaEntry, SeedEntry
from pyrit.models import MEDIA_PATH_DATA_TYPES, AttackSeedGroup, PromptDataType, SeedGroup, SeedObjective, SeedUnion

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from pyrit.memory.memory_interface import MemoryInterface
    from pyrit.memory.storage import StorageIO
    from pyrit.models import SeedRecord


class DatasetVersionConflictError(ValueError):
    """The expected complete persisted state no longer matches."""


class DatasetMutationLimitError(ValueError):
    """The affected complete groups exceed the configured work budget."""


@dataclass(frozen=True)
class DatasetEditingSnapshot:
    """Records and a content token read under one participating-writer lock."""

    records: tuple[SeedRecord, ...]
    version: str


class _SeedProjection:
    SHARED_FIELDS = (
        "id",
        "seed_type",
        "name",
        "dataset_name",
        "harm_categories",
        "description",
        "authors",
        "groups",
        "source",
        "metadata",
        "prompt_group_id",
        "is_general_technique",
    )
    SYSTEM_PROMPTS = (
        "adversarial_chat_system_prompt",
        "simulated_target_system_prompt",
        "next_message_system_prompt",
    )


def reject_unsafe_seed_input(raw: Any) -> None:
    """
    Reject template/path constructors recursively before validating any canonical model.

    Raises:
        ValueError: If a trusted template flag or legacy file constructor occurs.
    """
    if isinstance(raw, dict):
        if raw.get("is_jinja_template", False) is not False:
            raise ValueError("Trusted templates are not accepted.")
        if any(key.endswith("_system_prompt_path") for key in raw):
            raise ValueError("Client prompt-file paths are not accepted.")
        for field in _SeedProjection.SYSTEM_PROMPTS:
            if raw.get(field) is not None:
                reject_unsafe_seed_input(raw[field])
        metadata = raw.get("metadata")
        association = metadata.get("adversarial_prompts") if isinstance(metadata, dict) else None
        if isinstance(association, dict):
            for prompt in association.values():
                if prompt is not None:
                    reject_unsafe_seed_input(prompt)
    elif isinstance(raw, list):
        for value in raw:
            reject_unsafe_seed_input(value)


def seed_replacement(record: SeedRecord) -> dict[str, Any]:
    """
    Project typed editable fields inertly; never call SeedEntry.get_seed on stored config.

    Returns:
        dict[str, Any]: Complete client-editable subtype fields.

    Raises:
        ValueError: If stored configuration cannot be edited without file/template execution.
    """
    dumped = record.model_dump(mode="json")
    raw = {key: copy.deepcopy(dumped[key]) for key in _SeedProjection.SHARED_FIELDS}
    raw.update(data_type=record.data_type, is_jinja_template=False)
    if record.seed_type == "simulated_conversation":
        config = json.loads(record.value)
        if not isinstance(config, dict):
            raise ValueError("Stored simulation configuration must be an object.")
        reject_unsafe_seed_input(config)
        if set(config) - {*_SeedProjection.SYSTEM_PROMPTS, "num_turns", "sequence", "pyrit_version"}:
            raise ValueError("Stored simulation configuration is not submit-ready.")
        for field in _SeedProjection.SYSTEM_PROMPTS:
            if config.get(field) is not None:
                _validate_stored_prompt(config[field])
        raw.update(copy.deepcopy(config))
    else:
        raw["value"] = record.value
        if record.seed_type == "prompt":
            raw.update(
                parameters=record.parameters,
                response_json_schema=record.response_json_schema,
                role=record.role,
                sequence=record.sequence if record.sequence is not None else 0,
            )
        else:
            raw["conditions"] = dumped["conditions"] or []
            association = (record.metadata or {}).get("adversarial_prompts")
            if association is not None:
                if not isinstance(association, dict):
                    raise ValueError("Stored adversarial association must be an object.")
                for prompt in association.values():
                    if prompt is not None:
                        _validate_stored_prompt(prompt)
    reject_unsafe_seed_input(raw)
    return raw


def _validate_stored_prompt(prompt: Any) -> None:
    if (
        not isinstance(prompt, dict)
        or prompt.get("seed_type", "prompt") != "prompt"
        or prompt.get("data_type") not in get_args(PromptDataType)
        or not isinstance(prompt.get("value"), str)
    ):
        raise ValueError("Stored nested prompts require explicit types and string values.")


def _canonical_state(*, entries: Sequence[SeedEntry], storage: StorageIO | None) -> list[dict[str, Any]]:
    states = []
    for entry in sorted(entries, key=lambda row: str(row.id)):
        state = {attr.key: copy.deepcopy(getattr(entry, attr.key)) for attr in inspect(SeedEntry).column_attrs}
        if entry.data_type in MEDIA_PATH_DATA_TYPES:
            state["value"] = media_key(entry.value, storage=storage)
        elif entry.seed_type == "simulated_conversation":
            try:
                config = json.loads(entry.value)
            except ValueError:
                config = None
            if isinstance(config, dict):
                for field in _SeedProjection.SYSTEM_PROMPTS:
                    prompt = config.get(field)
                    if isinstance(prompt, dict) and prompt.get("data_type") in MEDIA_PATH_DATA_TYPES:
                        value = prompt.get("value")
                        if isinstance(value, str):
                            prompt["value"] = media_key(value, storage=storage)
                state["value"] = config
        states.append(state)
    return states


def dataset_version(*, entries: Sequence[SeedEntry], storage: StorageIO | None) -> str:
    """
    Hash every persisted column and membership, excluding media URL signatures.

    Returns:
        str: Deterministic complete-state SHA-256 token.
    """
    encoded = json.dumps(
        _canonical_state(entries=entries, storage=storage),
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def retained_media_handle(*, record: SeedRecord, version: str, slot: str, value: str) -> str:
    """
    Bind a retained reference to the exact dataset, record, nested slot, and snapshot.

    Returns:
        str: Opaque handle valid only for this saved slot and expected state.
    """
    encoded = json.dumps(
        [record.dataset_name, str(record.id), version, slot, media_key(value)], ensure_ascii=False
    ).encode("utf-8")
    return "retain:" + hashlib.sha256(encoded).hexdigest()


def editable_seed(*, record: SeedRecord, version: str) -> dict[str, Any]:
    """
    Return a submit-ready replacement with handles, not filesystem path authority.

    Returns:
        dict[str, Any]: Subtype replacement with current-slot media handles.
    """
    raw = seed_replacement(record)
    validated = TypeAdapter(SeedUnion).validate_python(copy.deepcopy(raw))
    if isinstance(validated, SeedObjective):
        AttackSeedGroup(seeds=[validated])
    for slot, prompt in media_slots(raw):
        prompt["value"] = retained_media_handle(record=record, version=version, slot=slot, value=prompt["value"])
    return raw


def media_slots(raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """
    Identify only canonical media slots, not arbitrary metadata strings.

    Returns:
        list[tuple[str, dict[str, Any]]]: Exact slot names and their prompt objects.
    """
    if raw.get("seed_type", "prompt") == "prompt":
        return [("value", raw)] if raw.get("data_type") in MEDIA_PATH_DATA_TYPES else []
    if raw.get("seed_type") != "simulated_conversation":
        return []
    return [
        (field + ".value", prompt)
        for field in _SeedProjection.SYSTEM_PROMPTS
        if isinstance(prompt := raw.get(field), dict) and prompt.get("data_type") in MEDIA_PATH_DATA_TYPES
    ]


async def editing_snapshot_async(*, memory: MemoryInterface, dataset_name: str) -> DatasetEditingSnapshot:
    """
    Read complete records and their version from one locked database snapshot.

    Returns:
        DatasetEditingSnapshot: Inert records and version from one snapshot.

    Raises:
        LookupError: If the exact named dataset is not stored.
    """
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        entries = (await session.scalars(select(SeedEntry).where(SeedEntry.dataset_name == dataset_name))).all()
        entries = [entry for entry in entries if entry.dataset_name == dataset_name]
        if not entries:
            raise LookupError("No stored dataset has this exact name.")
        version = await asyncio.to_thread(dataset_version, entries=entries, storage=memory.results_storage_io)
        records = tuple(entry.get_seed_record() for entry in entries)
        return DatasetEditingSnapshot(records=records, version=version)


def _validate_final_groups(
    *,
    original: Sequence[SeedEntry],
    seeds: Sequence[SeedUnion],
    delete_ids: set[UUID],
    max_records: int,
    max_bytes: int,
) -> None:
    affected = {seed.prompt_group_id or seed.id for seed in seeds}
    affected.update(entry.prompt_group_id or entry.id for entry in original if entry.id in delete_ids)
    replaced = {seed.id for seed in seeds} | delete_ids
    attack_groups = {entry.prompt_group_id or entry.id for entry in original if entry.seed_type == "objective"}
    groups: dict[UUID, list[SeedUnion]] = defaultdict(list)
    count = len(seeds) + len(delete_ids)
    size = sum(len(seed.model_dump_json().encode("utf-8")) for seed in seeds)
    adapter = TypeAdapter(SeedUnion)
    for entry in original:
        group_id = entry.prompt_group_id or entry.id
        if group_id not in affected:
            continue
        size += len(json.dumps(_canonical_state(entries=[entry], storage=None), default=str).encode("utf-8"))
        if entry.id not in replaced:
            count += 1
        if count > max_records or size > max_bytes:
            raise DatasetMutationLimitError("Affected complete groups exceed the configured work budget.")
        if entry.id in replaced:
            continue
        groups[group_id].append(adapter.validate_python(seed_replacement(entry.get_seed_record())))
    if count > max_records or size > max_bytes:
        raise DatasetMutationLimitError("Affected complete groups exceed the configured work budget.")
    for seed in seeds:
        group_id = seed.prompt_group_id or seed.id
        if group_id is None:
            raise ValueError("Mutation seeds require server-assigned record IDs.")
        groups[group_id].append(seed.model_copy(deep=True))
    for group_id, members in groups.items():
        # Preserve an existing attack group's objective requirement after partial deletion.
        was_attack = group_id in attack_groups
        group_type = (
            AttackSeedGroup if was_attack or any(isinstance(seed, SeedObjective) for seed in members) else SeedGroup
        )
        group_type(seeds=members)


async def commit_mutation_async(
    *,
    memory: MemoryInterface,
    upload_id: UUID,
    expected_version: str,
    delete_ids: Sequence[UUID],
    prepare_seeds: Callable[[dict[UUID, SeedRecord]], list[SeedUnion]],
    write_media: Callable[[list[SeedUnion]], Awaitable[None]],
    max_records: int,
    max_bytes: int,
) -> dict[str, Any]:
    """
    Apply a complete batch under the lifecycle lock and queue only removed owned assets.

    Returns:
        dict[str, Any]: Exact committed counts, indexed IDs, final population and token.

    Raises:
        DatasetNameConflictError: If the native reservation is no longer active.
        DatasetVersionConflictError: If complete persisted state changed.
        LookupError: If the dataset or a delete target no longer exists.
        ValueError: If IDs overlap or final copied groups fail canonical validation.
    """
    async with await memory.get_session_async() as session:
        await session.run_sync(lock_dataset_lifecycle)
        lease = memory._dataset_upload_leases.get(upload_id)
        operation = await session.get(DatasetLifecycleEntry, upload_id)
        if lease is None or not await lease.active_async() or operation is None or operation.status != "staging":
            raise DatasetNameConflictError("The mutation reservation is no longer active.")
        entries = (
            await session.scalars(select(SeedEntry).where(SeedEntry.dataset_name == operation.dataset_name))
        ).all()
        entries = [entry for entry in entries if entry.dataset_name == operation.dataset_name]
        if not entries:
            raise LookupError("No stored dataset has this exact name.")
        current = await asyncio.to_thread(dataset_version, entries=entries, storage=memory.results_storage_io)
        if current != expected_version:
            raise DatasetVersionConflictError("The dataset state changed.")
        by_id = {entry.id: entry for entry in entries}
        if len(set(delete_ids)) != len(delete_ids):
            raise ValueError("Duplicate delete record IDs.")
        if any(identifier not in by_id for identifier in delete_ids):
            raise LookupError("A delete record ID is not in this dataset.")
        seeds = await asyncio.to_thread(prepare_seeds, {entry.id: entry.get_seed_record() for entry in entries})
        if not seeds and not delete_ids:
            raise ValueError("A mutation requires at least one operation.")
        if len({seed.id for seed in seeds}) != len(seeds) or set(delete_ids) & {seed.id for seed in seeds}:
            raise ValueError("Duplicate or overlapping edit/delete record IDs.")
        await asyncio.to_thread(
            _validate_final_groups,
            original=entries,
            seeds=seeds,
            delete_ids=set(delete_ids),
            max_records=max_records,
            max_bytes=max_bytes,
        )
        session.info["dataset_upload_id"] = upload_id
        for seed in seeds:
            if seed.dataset_name != operation.dataset_name or not seed.id or not seed.added_by:
                raise ValueError("Mutation seeds must belong to the reserved dataset and have assigned identity.")
            old = by_id.get(seed.id)
            if old is not None and (
                seed.seed_type != old.seed_type
                or seed.prompt_group_id != old.prompt_group_id
                or seed.date_added != old.date_added
                or seed.added_by != old.added_by
                or seed.origin.value != old.origin
            ):
                raise ValueError("An edit cannot change identity, type, group or original creation provenance.")
        await _finish_storage_write_async(write_media(seeds))
        if any(not seed.value_sha256 for seed in seeds):
            raise ValueError("Mutation checksums must be computed before persistence.")
        targeted_ids = set(delete_ids) | {seed.id for seed in seeds}
        removed_values = [
            value for entry in entries if entry.id in targeted_ids for value in _entry_media_values(entry)
        ]
        results = []
        for index, seed in enumerate(seeds):
            replacement = SeedEntry(entry=seed)
            old = by_id.get(seed.id)
            results.append({"index": index, "id": seed.id, "operation": "updated" if old is not None else "added"})
            if old is None:
                session.add(replacement)
            else:
                for attr in inspect(SeedEntry).column_attrs:
                    setattr(old, attr.key, getattr(replacement, attr.key))
        for identifier in delete_ids:
            await session.delete(by_id[identifier])
        await session.flush()
        remaining = (
            await session.scalars(select(SeedEntry).where(SeedEntry.dataset_name == operation.dataset_name))
        ).all()
        remaining = [entry for entry in remaining if entry.dataset_name == operation.dataset_name]
        removed_keys = await asyncio.to_thread(
            lambda: {media_key(value, storage=memory.results_storage_io) for value in removed_values}
        )
        # Keep retained slots in this dataset live; global shared references are checked by cleanup.
        kept_keys = await asyncio.to_thread(
            lambda: {
                media_key(value, storage=memory.results_storage_io)
                for entry in remaining
                for value in _entry_media_values(entry)
            }
        )
        eligible_keys = sorted(removed_keys - kept_keys)
        for start in range(0, len(eligible_keys), 500):
            assets = (
                await session.scalars(
                    select(DatasetMediaEntry).where(
                        DatasetMediaEntry.status.in_(["live", "retained"]),
                        DatasetMediaEntry.key.in_(eligible_keys[start : start + 500]),
                    )
                )
            ).all()
            for asset in assets:
                asset.cleanup_id, asset.status = upload_id, "pending"
        fresh = (await session.scalars(select(DatasetMediaEntry).where(DatasetMediaEntry.upload_id == upload_id))).all()
        for asset in fresh:
            asset.status = "live"
        operation.status, operation.deleted_seeds = "pending", len(delete_ids)
        version = (
            await asyncio.to_thread(dataset_version, entries=remaining, storage=memory.results_storage_io)
            if remaining
            else None
        )
        result = {
            "dataset_exists": bool(remaining),
            "version": version,
            "seed_pieces": len(remaining),
            "logical_examples": len({entry.prompt_group_id or entry.id for entry in remaining}),
            "seeds": results,
            "delete_seed_ids": list(delete_ids),
            "added": sum(row["operation"] == "added" for row in results),
            "updated": sum(row["operation"] == "updated" for row in results),
            "deleted": len(delete_ids),
        }
        await session.commit()
        return result
