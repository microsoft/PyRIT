# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real memory/storage coverage of multipart dataset creation and permanent deletion."""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, select, update
from sqlalchemy.engine import Connection, ExecutionContext
from starlette.requests import Request

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.main import app
from pyrit.backend.models.datasets import DatasetUploadLimits
from pyrit.backend.services.dataset_upload_service import _DatasetMultipartParser
from pyrit.memory import AzureBlobStorageIO, PromptMemoryEntry, SeedEntry, SQLiteMemory
from pyrit.memory.memory_models import (
    DatasetLifecycleEntry,
    DatasetMediaEntry,
    ScorableContentEntry,
)
from pyrit.models import AttackSeedGroup, MessagePiece, Seed, SeedObjective, SeedPrompt, SeedSimulatedConversation
from unit.async_utils import TEST_WATCHDOG_SECONDS

URL = "/api/datasets"


async def _editable_async(client: AsyncClient, name: str = "upload") -> dict[str, Any]:
    response = await client.get(f"{URL}/seeds", params={"selection_key": f"dataset:named:{name}", "editable": True})
    assert response.status_code == 200, response.text
    return response.json()


async def _update_async(
    client: AsyncClient,
    *,
    snapshot: dict[str, Any],
    seeds: list[dict[str, Any]] | None = None,
    deletes: list[str] | None = None,
    name: str = "upload",
    files: dict[str, tuple[str, bytes]] | None = None,
) -> Any:
    return await _upload(
        client,
        manifest={
            "name": name,
            "mode": "update",
            "expected_version": snapshot["version"],
            "seeds": seeds or [],
            "delete_seed_ids": deletes or [],
        },
        files=files,
    )


def _editable_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [seed for item in snapshot["items"] for seed in item["editable_seeds"]]


@pytest.fixture
async def client(patch_central_database: Any, compatibility_headers: dict[str, str]) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
    ) as client:
        yield client


def _manifest(*, name: str = "upload", seeds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"name": name, "seeds": seeds if seeds is not None else [{"data_type": "text", "value": "hello"}]}


async def _upload(
    client: AsyncClient, *, manifest: dict[str, Any] | None = None, files: dict[str, tuple[str, bytes]] | None = None
) -> Any:
    parts = {"manifest": (None, json.dumps(manifest if manifest is not None else _manifest()))}
    parts.update(files or {})
    return await client.post(URL, files=parts)


async def _seeds(memory: SQLiteMemory) -> list[SeedEntry]:
    async with await memory.get_session_async() as session:
        return list((await session.scalars(select(SeedEntry))).all())


async def test_text_upload_browsing_metadata_groups_and_exact_delete(
    client: AsyncClient, sqlite_instance: SQLiteMemory
):
    group = str(uuid4())
    seed_id = str(uuid4())
    seed = {
        "id": seed_id,
        "data_type": "text",
        "value": "{{ literal }}",
        "prompt_group_id": group,
        "role": "user",
        "sequence": 3,
        "metadata": {"source_detail": {"arbitrary": [1, 2]}},
        "authors": ["author"],
        "harm_categories": ["category"],
        "value_sha256": "forged",
        "added_by": "forged",
    }
    response = await _upload(
        client, manifest=_manifest(name="a_%", seeds=[seed, {**seed, "role": "assistant", "sequence": 4}])
    )
    assert response.status_code == 201, response.text
    assert response.json() == {
        "name": "a_%",
        "selection_key": "dataset:named:a_%",
        "seed_pieces": 2,
        "logical_examples": 1,
    }
    stored = await _seeds(sqlite_instance)
    assert len({seed.id for seed in stored}) == 2
    assert all(str(seed.id) != seed_id for seed in stored)
    assert stored[0].prompt_group_id == stored[1].prompt_group_id
    assert str(stored[0].prompt_group_id) != group
    assert {seed.sequence for seed in stored} == {3, 4}
    assert {seed.role for seed in stored} == {"user", "assistant"}
    assert stored[0].prompt_metadata == seed["metadata"]
    assert stored[0].value == "{{ literal }}"
    assert stored[0].value_sha256 == hashlib.sha256(b"{{ literal }}").hexdigest()
    assert stored[0].added_by == "dataset-api"
    with patch(
        "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async", return_value=[]
    ):
        listed = await client.get(URL, params={"loaded_only": True})
    assert listed.json()["items"][0]["seed_pieces"] == 2
    page = await client.get(f"{URL}/seeds", params={"selection_key": "dataset:named:a_%"})
    assert page.json()["total"] == 1
    detail = await client.get(
        f"{URL}/seeds/{page.json()['items'][0]['example_id']}", params={"selection_key": "dataset:named:a_%"}
    )
    assert len(detail.json()["members"]) == 2
    await _upload(client, manifest=_manifest(name="abother"))
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:a_%"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_seeds"] == 2
    assert [entry.dataset_name for entry in await _seeds(sqlite_instance)] == ["abother"]


@pytest.mark.parametrize(
    ("value", "search", "value_pattern", "expected"),
    [
        ("Stra\u00dfe", "STRASSE", None, 1),
        ("\u03bf\u03c2", "\u03bf\u03c3", None, 1),
        ("Stra\u00dfe", None, "STRASSE", 0),
        ("\u03bf\u03c2", None, "\u03bf\u03c3", 0),
    ],
)
async def test_editable_unicode_search_matches_ordinary_browsing(
    client: AsyncClient, value: str, search: str | None, value_pattern: str | None, expected: int
) -> None:
    created = await _upload(client, manifest=_manifest(seeds=[{"data_type": "text", "value": value}]))
    assert created.status_code == 201, created.text
    params: dict[str, str | bool] = {"selection_key": "dataset:named:upload"}
    if search is not None:
        params["search"] = search
    if value_pattern is not None:
        params["value_pattern"] = value_pattern
    ordinary = await client.get(f"{URL}/seeds", params=params)
    editable = await client.get(f"{URL}/seeds", params={**params, "editable": True})
    assert ordinary.status_code == editable.status_code == 200
    assert ordinary.json()["total"] == expected
    assert editable.json()["total"] == expected
    assert [item["example_id"] for item in ordinary.json()["items"]] == [
        item["example_id"] for item in editable.json()["items"]
    ]


async def test_atomic_mixed_batch_exact_ids_counts_provenance_and_omitted_rows(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    assert (
        await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "text", "value": value} for value in ("one", "two", "three")]),
        )
    ).status_code == 201
    originals = {str(row.id): row.get_seed_record().model_dump(mode="json") for row in await _seeds(sqlite_instance)}
    snapshot = await _editable_async(client)
    edit, deleted, omitted = _editable_rows(snapshot)
    edit["value"] = "changed"
    response = await _update_async(
        client,
        snapshot=snapshot,
        seeds=[edit, {"seed_type": "prompt", "data_type": "text", "value": "changed"}],
        deletes=[deleted["id"]],
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["added"], result["updated"], result["deleted"], result["seed_pieces"]) == (1, 1, 1, 3)
    assert result["seeds"][0] == {"index": 0, "id": edit["id"], "operation": "updated"}
    assert result["seeds"][1]["index"] == 1 and result["seeds"][1]["operation"] == "added"
    assert result["seeds"][1]["id"] not in originals
    assert result["delete_seed_ids"] == [deleted["id"]]
    assert result["dataset_exists"] and result["version"] != snapshot["version"]
    assert result["cleanup"]["status"] == "completed"
    persisted = {str(row.id): row.get_seed_record().model_dump(mode="json") for row in await _seeds(sqlite_instance)}
    assert persisted[omitted["id"]] == originals[omitted["id"]]
    assert persisted[edit["id"]]["value"] == "changed"
    for key in ("date_added", "added_by", "origin", "dataset_name", "prompt_group_id", "seed_type"):
        assert persisted[edit["id"]][key] == originals[edit["id"]][key]
    assert deleted["id"] not in persisted
    fresh = await _editable_async(client)
    assert fresh["version"] == result["version"]
    unchanged = await _update_async(client, snapshot=fresh, seeds=[_editable_rows(fresh)[0]])
    assert unchanged.status_code == 200 and unchanged.json()["updated"] == 1
    assert unchanged.json()["version"] == fresh["version"]


@pytest.mark.parametrize("loaded_sdk", [False, True])
async def test_objective_embedded_group_identity_round_trips_independently_of_record_group(
    client: AsyncClient, sqlite_instance: SQLiteMemory, loaded_sdk: bool
) -> None:
    outer_group, embedded_group = uuid4(), uuid4()
    embedded = SeedPrompt(
        value="guide",
        data_type="text",
        prompt_group_id=embedded_group,
        prompt_group_alias="embedded alias",
        dataset_name="embedded source" if loaded_sdk else "upload",
        added_by="original embedded author",
        origin="remote",
        date_added=datetime(2020, 1, 1, tzinfo=UTC),
        metadata={"descriptive": "retained"},
    )
    if loaded_sdk:
        objective = SeedObjective(
            value="goal",
            dataset_name="upload",
            prompt_group_id=outer_group,
            metadata={"adversarial_prompts": {"system_prompt": embedded.model_dump(mode="json")}},
        )
        await sqlite_instance.add_seeds_to_memory_async(seeds=[objective], added_by="sdk")
    else:
        created = await _upload(
            client,
            manifest=_manifest(
                seeds=[
                    {
                        "seed_type": "objective",
                        "value": "goal",
                        "prompt_group_id": str(outer_group),
                        "metadata": {
                            "adversarial_prompts": {
                                "system_prompt": embedded.model_dump(mode="json", exclude={"prompt_group_alias"})
                            }
                        },
                    }
                ]
            ),
        )
        assert created.status_code == 201, created.text
    original = (await _seeds(sqlite_instance))[0].get_seed_record().model_dump(mode="json")
    snapshot = await _editable_async(client)
    row = _editable_rows(snapshot)[0]
    saved_embedded = row["metadata"]["adversarial_prompts"]["system_prompt"].copy()
    assert saved_embedded["prompt_group_id"] != row["prompt_group_id"]
    unchanged = await _update_async(client, snapshot=snapshot, seeds=[row])
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["updated"] == 1 and unchanged.json()["added"] == unchanged.json()["deleted"] == 0
    assert unchanged.json()["version"] == snapshot["version"]
    persisted = (await _seeds(sqlite_instance))[0].get_seed_record().model_dump(mode="json")
    assert persisted == original
    fresh = await _editable_async(client)
    changed_row = _editable_rows(fresh)[0]
    changed_row["metadata"]["adversarial_prompts"]["system_prompt"]["value"] = "changed guide"
    changed = await _update_async(client, snapshot=fresh, seeds=[changed_row])
    assert changed.status_code == 200, changed.text
    assert changed.json()["seeds"] == [{"index": 0, "id": row["id"], "operation": "updated"}]
    persisted = (await _seeds(sqlite_instance))[0].get_seed_record().model_dump(mode="json")
    assert persisted["metadata"]["adversarial_prompts"]["system_prompt"] == {**saved_embedded, "value": "changed guide"}
    for field in ("id", "prompt_group_id", "dataset_name", "date_added", "added_by", "origin"):
        assert persisted[field] == original[field]


async def test_postcommit_native_lease_close_failure_returns_exact_success_and_pending_cleanup(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(client)
    snapshot = await _editable_async(client)
    committed = asyncio.Event()
    original_commit = sqlite_instance.commit_dataset_mutation_async
    original_release = sqlite_instance.release_dataset_upload_async

    async def commit_async(**kwargs: Any) -> dict[str, Any]:
        result = await original_commit(**kwargs)
        committed.set()
        return result

    async def release_async(operation_id: Any) -> None:
        assert committed.is_set()
        lease = sqlite_instance._dataset_upload_leases[operation_id]
        original_close = lease.close_async

        async def fail_close_async() -> None:
            await original_close()
            raise OSError("native close failure")

        with patch.object(lease, "close_async", side_effect=fail_close_async):
            await original_release(operation_id)

    with (
        patch.object(sqlite_instance, "commit_dataset_mutation_async", side_effect=commit_async),
        patch.object(sqlite_instance, "release_dataset_upload_async", side_effect=release_async),
    ):
        response = await _update_async(client, snapshot=snapshot, seeds=[{"data_type": "text", "value": "committed"}])
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["added"], result["updated"], result["deleted"], result["seed_pieces"]) == (1, 0, 0, 2)
    assert result["cleanup"]["status"] == "pending" and result["cleanup"]["pending_media"] is None
    assert response.headers["Location"] == result["cleanup_location"]
    entries = {str(entry.id): entry for entry in await _seeds(sqlite_instance)}
    assert entries[result["seeds"][0]["id"]].value == "committed"
    assert result["version"] == (await _editable_async(client))["version"]
    assert "native close failure" not in response.text
    assert (await client.get(response.headers["Location"])).json()["status"] == "completed"
    with patch.object(sqlite_instance, "commit_dataset_mutation_async", side_effect=RuntimeError("precommit failure")):
        failed = await _update_async(
            client, snapshot={"version": result["version"]}, seeds=[{"data_type": "text", "value": "not committed"}]
        )
    assert failed.status_code == 503
    assert {str(entry.id) for entry in await _seeds(sqlite_instance)} == set(entries)


@pytest.mark.parametrize("count", [1, 4])
async def test_update_one_and_n_additions_edits_and_deletion_only_final_removal(
    client: AsyncClient, sqlite_instance: SQLiteMemory, count: int
) -> None:
    await _upload(client)
    snapshot = await _editable_async(client)
    addition = await _update_async(
        client, snapshot=snapshot, seeds=[{"data_type": "text", "value": "duplicate"}] * count
    )
    assert addition.status_code == 200 and addition.json()["added"] == count
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    for row in rows[:count]:
        row["value"] = "edited"
    edited = await _update_async(client, snapshot=snapshot, seeds=rows[:count])
    assert edited.status_code == 200 and edited.json()["updated"] == count
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    deleted = await _update_async(client, snapshot=snapshot, deletes=[row["id"] for row in rows])
    assert deleted.status_code == 200, deleted.text
    result = deleted.json()
    assert result["deleted"] == count + 1 and result["seed_pieces"] == 0
    assert not result["dataset_exists"] and result["version"] is None
    assert not await _seeds(sqlite_instance)
    assert (
        await client.get(f"{URL}/seeds", params={"selection_key": "dataset:named:upload", "editable": True})
    ).status_code == 404
    assert (await _upload(client)).status_code == 201


@pytest.mark.parametrize(
    "kind,status",
    [
        ("unknown_edit", 404),
        ("foreign_edit", 404),
        ("unknown_delete", 404),
        ("foreign_delete", 404),
        ("duplicate_edit", 422),
        ("duplicate_delete", 422),
        ("overlap", 422),
        ("group_id_as_record", 404),
        ("missing_dataset", 404),
        ("stale", 409),
        ("no_op", 422),
        ("create_deletes", 422),
        ("missing_version", 422),
    ],
)
async def test_invalid_mutation_targets_are_atomic(
    client: AsyncClient, sqlite_instance: SQLiteMemory, kind: str, status: int
) -> None:
    await _upload(client, manifest=_manifest(seeds=[{"data_type": "text", "value": "same", "prompt_group_alias": "g"}]))
    await _upload(client, manifest=_manifest(name="other"))
    snapshot = await _editable_async(client)
    row = _editable_rows(snapshot)[0]
    foreign = _editable_rows(await _editable_async(client, "other"))[0]
    unknown = str(uuid4())
    before = {str(entry.id): entry.get_seed_record().model_dump(mode="json") for entry in await _seeds(sqlite_instance)}
    manifest: dict[str, Any] = {
        "name": "upload",
        "mode": "update",
        "expected_version": snapshot["version"],
        "seeds": [{"data_type": "text", "value": "must not be added"}, {**row, "value": "must not change"}],
        "delete_seed_ids": [],
    }
    if kind in {"unknown_edit", "foreign_edit"}:
        manifest["seeds"][1]["id"] = unknown if kind == "unknown_edit" else foreign["id"]
    elif kind in {"unknown_delete", "foreign_delete", "group_id_as_record"}:
        manifest["delete_seed_ids"] = [
            unknown
            if kind == "unknown_delete"
            else foreign["id"]
            if kind == "foreign_delete"
            else row["prompt_group_id"]
        ]
    elif kind == "duplicate_edit":
        manifest["seeds"].append(row)
    elif kind == "duplicate_delete":
        manifest["seeds"] = []
        manifest["delete_seed_ids"] = [row["id"], row["id"]]
    elif kind == "overlap":
        manifest["delete_seed_ids"] = [row["id"]]
    elif kind == "missing_dataset":
        manifest["name"] = "provider-only"
    elif kind == "stale":
        manifest["expected_version"] = "0" * 64
    elif kind == "no_op":
        manifest["seeds"] = []
    elif kind == "create_deletes":
        manifest["mode"] = "create"
        manifest.pop("expected_version")
        manifest["delete_seed_ids"] = [row["id"]]
    elif kind == "missing_version":
        manifest.pop("expected_version")
    response = await _upload(client, manifest=manifest)
    assert response.status_code == status, response.text
    after = {str(entry.id): entry.get_seed_record().model_dump(mode="json") for entry in await _seeds(sqlite_instance)}
    assert after == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed_type", "objective"),
        ("dataset_name", "other"),
        ("prompt_group_id", None),
        ("prompt_group_alias", "new"),
        ("added_by", "forged"),
        ("origin", "remote"),
        ("date_added", "2000-01-01T00:00:00Z"),
    ],
)
async def test_edited_identity_and_creation_attribution_are_immutable(
    client: AsyncClient, sqlite_instance: SQLiteMemory, field: str, value: Any
) -> None:
    await _upload(client, manifest=_manifest(seeds=[{"data_type": "text", "value": "old", "prompt_group_alias": "g"}]))
    snapshot = await _editable_async(client)
    row = {**_editable_rows(snapshot)[0], field: value}
    response = await _update_async(client, snapshot=snapshot, seeds=[row])
    assert response.status_code == 422, response.text
    assert (await _seeds(sqlite_instance))[0].value == "old"


async def test_apply_whole_batch_before_validating_surviving_group_and_preserve_siblings(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {"data_type": "text", "value": "one", "prompt_group_alias": "g", "role": "user", "sequence": 0},
                {"data_type": "text", "value": "two", "prompt_group_alias": "g", "role": "user", "sequence": 0},
                {"data_type": "text", "value": "sibling", "prompt_group_alias": "g", "role": None, "sequence": 0},
            ]
        ),
    )
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    first, second = [row for row in rows if row["value"] != "sibling"]
    sibling = next(row for row in rows if row["value"] == "sibling")
    # A single role change is invalid, but changing both sequence-zero members is valid.
    invalid = await _update_async(client, snapshot=snapshot, seeds=[{**first, "role": "assistant"}])
    assert invalid.status_code == 422
    valid = await _update_async(
        client, snapshot=snapshot, seeds=[{**first, "role": "assistant"}, {**second, "role": "assistant"}]
    )
    assert valid.status_code == 200, valid.text
    stored_sibling = next(entry for entry in await _seeds(sqlite_instance) if str(entry.id) == sibling["id"])
    assert stored_sibling.role is None
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    deleted = await _update_async(client, snapshot=snapshot, deletes=[sibling["id"]])
    assert deleted.status_code == 200 and deleted.json()["deleted"] == 1
    assert len(await _seeds(sqlite_instance)) == 2


@pytest.mark.parametrize(
    ("seed_type", "is_general_technique"),
    [("prompt", True), ("simulated_conversation", False)],
)
async def test_noop_edit_preserves_general_technique_flag_async(
    *, client: AsyncClient, sqlite_instance: SQLiteMemory, seed_type: str, is_general_technique: bool
) -> None:
    seed = (
        SeedPrompt(value="Technique prompt", is_general_technique=is_general_technique, dataset_name="upload")
        if seed_type == "prompt"
        else SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation."),
            is_general_technique=is_general_technique,
            dataset_name="upload",
        )
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="sdk")
    snapshot = await _editable_async(client)
    [row] = _editable_rows(snapshot)
    response = await _update_async(client, snapshot=snapshot, seeds=[row])
    assert response.status_code == 200, response.text
    assert row["is_general_technique"] is is_general_technique
    [stored] = await _seeds(sqlite_instance)
    assert stored.is_general_technique is is_general_technique


async def test_all_variants_edit_typed_fields_and_attach_new_or_existing_groups(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {
                    "seed_type": "objective",
                    "value": "goal",
                    "conditions": [{"condition_type": "matches_objective"}],
                    "prompt_group_alias": "g",
                    "metadata": {
                        "adversarial_prompts": {
                            "system_prompt": {"data_type": "text", "value": "guide", "parameters": ["objective"]}
                        }
                    },
                },
                {**_simulation(), "prompt_group_alias": "g"},
                {
                    "seed_type": "prompt",
                    "data_type": "text",
                    "value": "next",
                    "sequence": 4,
                    "role": "user",
                    "prompt_group_alias": "g",
                    "response_json_schema": {"type": "object"},
                },
            ]
        ),
    )
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    group = rows[0]["prompt_group_id"]
    objective = next(row for row in rows if row["seed_type"] == "objective")
    simulation = next(row for row in rows if row["seed_type"] == "simulated_conversation")
    prompt = next(row for row in rows if row["seed_type"] == "prompt")
    objective["value"] = "new goal"
    objective["conditions"] = []
    objective["metadata"]["adversarial_prompts"]["system_prompt"]["value"] = "new guide"
    simulation["num_turns"] = 3
    simulation["next_message_system_prompt"] = {"data_type": "text", "value": "next generator"}
    prompt["sequence"] = 7
    prompt["response_json_schema"] = {"type": "string"}
    response = await _update_async(
        client,
        snapshot=snapshot,
        seeds=[
            objective,
            simulation,
            prompt,
            {"data_type": "text", "value": "existing group", "prompt_group_id": group, "sequence": 8, "role": "user"},
            {"seed_type": "objective", "value": "fresh objective", "prompt_group_alias": "fresh"},
            {"data_type": "text", "value": "fresh prompt", "prompt_group_alias": "fresh", "role": "user"},
        ],
    )
    assert response.status_code == 200, response.text
    assert (response.json()["added"], response.json()["updated"], response.json()["logical_examples"]) == (3, 3, 2)
    entries = {str(row.id): row for row in await _seeds(sqlite_instance)}
    assert entries[objective["id"]].conditions is None
    assert entries[objective["id"]].get_seed().metadata["adversarial_prompts"]["system_prompt"]["value"] == "new guide"
    restored = entries[simulation["id"]].get_seed()
    assert restored.num_turns == 3 and restored.next_message_system_prompt.value == "next generator"
    assert entries[prompt["id"]].get_seed().response_json_schema == {"type": "string"}
    snapshot = await _editable_async(client)
    # Partial deletion may not remove the required objective from a surviving attack group.
    invalid = await _update_async(client, snapshot=snapshot, deletes=[objective["id"]])
    assert invalid.status_code == 422
    group_ids = [row["id"] for row in _editable_rows(snapshot) if row["prompt_group_id"] == group]
    deleted = await _update_async(client, snapshot=snapshot, deletes=group_ids)
    assert deleted.status_code == 200 and deleted.json()["logical_examples"] == 1


async def test_snapshot_token_notices_metadata_membership_conditions_and_sdk_writes(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="loaded", dataset_name="sdk")], added_by="sdk"
    )
    snapshot = await _editable_async(client, "sdk")
    row = _editable_rows(snapshot)[0]
    async with await sqlite_instance.get_session_async() as session:
        await session.execute(
            update(SeedEntry).values(
                description="SDK metadata edit", conditions=[{"condition_type": "matches_objective"}]
            )
        )
        await session.commit()
    response = await _update_async(client, name="sdk", snapshot=snapshot, seeds=[row])
    assert response.status_code == 409
    fresh = await _editable_async(client, "sdk")
    assert fresh["version"] != snapshot["version"]
    detail = await client.get(
        f"{URL}/seeds/{fresh['items'][0]['example_id']}",
        params={"selection_key": "dataset:named:sdk", "editable": True},
    )
    assert detail.json()["version"] == fresh["version"]
    assert detail.json()["editable_seeds"] == fresh["items"][0]["editable_seeds"]
    valid = await _update_async(client, name="sdk", snapshot=fresh, seeds=[_editable_rows(fresh)[0]])
    assert valid.status_code == 200


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("data_type", ["image_path", "audio_path", "video_path", "binary_path"])
async def test_retained_replaced_deleted_and_shared_owned_media(
    client: AsyncClient, sqlite_instance: SQLiteMemory, nested: bool, data_type: str
) -> None:
    raw = {"data_type": data_type, "value": "upload:a"}
    seed = _simulation(adversarial_chat_system_prompt=raw) if nested else raw
    await _upload(client, manifest=_manifest(seeds=[seed]), files={"a": ("first.bin", b"first")})
    old_entry = (await _seeds(sqlite_instance))[0]
    old_path = Path(
        json.loads(old_entry.value)["adversarial_chat_system_prompt"]["value"] if nested else old_entry.value
    )
    snapshot = await _editable_async(client)
    row = _editable_rows(snapshot)[0]
    handle = row["adversarial_chat_system_prompt"]["value"] if nested else row["value"]
    assert handle.startswith("retain:") and str(old_path) not in json.dumps(row)
    retained = await _update_async(client, snapshot=snapshot, seeds=[row])
    assert retained.status_code == 200, retained.text
    assert old_path.read_bytes() == b"first"
    assert retained.json()["version"] == snapshot["version"]
    # Sharing outside the dataset protects the old asset on replacement.
    async with await sqlite_instance.get_session_async() as session:
        session.add(
            SeedEntry(entry=SeedPrompt(value=str(old_path), data_type=data_type, dataset_name="shared", added_by="sdk"))
        )
        await session.commit()
    row = _editable_rows(await _editable_async(client))[0]
    if nested:
        row["adversarial_chat_system_prompt"]["value"] = "upload:b"
    else:
        row["value"] = "upload:b"
    replaced = await _update_async(client, snapshot=snapshot, seeds=[row], files={"b": ("second.bin", b"second")})
    assert replaced.status_code == 200, replaced.text
    assert old_path.exists()
    entry = next(entry for entry in await _seeds(sqlite_instance) if entry.dataset_name == "upload")
    new_path = Path(json.loads(entry.value)["adversarial_chat_system_prompt"]["value"] if nested else entry.value)
    assert new_path.read_bytes() == b"second" and new_path != old_path
    current = await _editable_async(client)
    deleted = await _update_async(client, snapshot=current, deletes=[row["id"]])
    assert deleted.status_code == 200 and not new_path.exists() and old_path.exists()


async def test_mutation_cleanup_pending_is_success_and_old_token_cannot_duplicate_additions(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:a"}]),
        files={"a": ("a.bin", b"old")},
    )
    snapshot = await _editable_async(client)
    row = _editable_rows(snapshot)[0]
    with patch.object(sqlite_instance.results_storage_io, "delete_file_async", side_effect=OSError("offline")):
        response = await _update_async(
            client,
            snapshot=snapshot,
            seeds=[{**row, "data_type": "text", "value": "replacement"}, {"data_type": "text", "value": "added"}],
        )
    assert response.status_code == 200, response.text
    assert response.json()["cleanup"]["status"] == "pending"
    assert response.json()["cleanup"]["pending_media"] == 1
    retry = await _update_async(client, snapshot=snapshot, seeds=[{"data_type": "text", "value": "added"}])
    assert retry.status_code == 409 and len(await _seeds(sqlite_instance)) == 2
    cleanup = await client.get(response.headers["Location"])
    assert cleanup.status_code == 200 and cleanup.json()["status"] == "completed"


async def test_retention_handles_cannot_move_between_record_dataset_or_nested_slots(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:a"}] * 2),
        files={"a": ("a.bin", b"a")},
    )
    snapshot = await _editable_async(client)
    first, second = _editable_rows(snapshot)
    assert first["value"] != second["value"]
    bad = await _update_async(client, snapshot=snapshot, seeds=[{**second, "value": first["value"]}])
    assert bad.status_code == 422
    bad = await _update_async(client, snapshot=snapshot, seeds=[{"data_type": "binary_path", "value": first["value"]}])
    assert bad.status_code == 422
    assert len(await _seeds(sqlite_instance)) == 2


async def test_mutation_failure_rolls_back_rows_and_only_new_staged_media(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:a"}]),
        files={"a": ("a.bin", b"old")},
    )
    old = (await _seeds(sqlite_instance))[0]
    snapshot = await _editable_async(client)
    row = {**_editable_rows(snapshot)[0], "value": "upload:b"}
    with patch.object(sqlite_instance.results_storage_io, "write_stream_async", side_effect=OSError("storage failure")):
        response = await _update_async(client, snapshot=snapshot, seeds=[row], files={"b": ("b.bin", b"new")})
    assert response.status_code == 503
    assert (await _seeds(sqlite_instance))[0].value == old.value and Path(old.value).read_bytes() == b"old"
    assert (await _editable_async(client))["version"] == snapshot["version"]


async def test_update_limits_count_deletes_and_affected_complete_groups(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {"data_type": "text", "value": str(index), "prompt_group_alias": "g", "role": "user"}
                for index in range(3)
            ]
        ),
    )
    snapshot = await _editable_async(client)
    rows = _editable_rows(snapshot)
    with patch.object(DatasetUploadLimits, "from_environment", return_value=DatasetUploadLimits(prompts=2)):
        response = await _update_async(client, snapshot=snapshot, seeds=[rows[0]])
        assert response.status_code == 413
        response = await _update_async(client, snapshot=snapshot, deletes=[row["id"] for row in rows])
        assert response.status_code == 413
    assert len(await _seeds(sqlite_instance)) == 3


@pytest.mark.parametrize(
    "slot",
    [
        "adversarial_chat_system_prompt",
        "simulated_target_system_prompt",
        "next_message_system_prompt",
    ],
)
async def test_update_recursive_trust_rejection_and_retention_slot_binding(
    client: AsyncClient, sqlite_instance: SQLiteMemory, slot: str
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                _simulation(
                    **{
                        slot: {"data_type": "binary_path", "value": "upload:a"},
                    }
                )
            ]
        ),
        files={"a": ("a.bin", b"bytes")},
    )
    snapshot = await _editable_async(client)
    row = _editable_rows(snapshot)[0]
    with (
        patch.object(Seed, "_render_trusted_template_value", side_effect=AssertionError("rendered")),
        patch.object(SeedPrompt, "from_yaml_file", side_effect=AssertionError("read client file")),
    ):
        for changes in (
            {slot + "_path": "C:\\secret.yaml"},
            {slot: {**row[slot], "is_jinja_template": True}},
            {slot: {"data_type": "binary_path", "value": "C:\\secret"}},
        ):
            invalid = await _update_async(client, snapshot=snapshot, seeds=[{**row, **changes}])
            assert invalid.status_code == 422, invalid.text
        other_slot = (
            "next_message_system_prompt" if slot != "next_message_system_prompt" else "adversarial_chat_system_prompt"
        )
        invalid = await _update_async(
            client,
            snapshot=snapshot,
            seeds=[
                {
                    **row,
                    other_slot: {"data_type": "binary_path", "value": row[slot]["value"]},
                }
            ],
        )
        assert invalid.status_code == 422
    valid = await _update_async(client, snapshot=snapshot, seeds=[row])
    assert valid.status_code == 200
    assert valid.json()["version"] == snapshot["version"]


async def test_historical_config_is_inert_on_editable_reads_and_affected_group_validation(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {**_simulation(), "prompt_group_alias": "g"},
                {"data_type": "text", "value": "next", "sequence": 4, "role": "user", "prompt_group_alias": "g"},
            ]
        ),
    )
    snapshot = await _editable_async(client)
    row = next(row for row in _editable_rows(snapshot) if row["seed_type"] == "prompt")
    simulation = next(entry for entry in await _seeds(sqlite_instance) if entry.seed_type == "simulated_conversation")
    async with await sqlite_instance.get_session_async() as session:
        await session.execute(
            update(SeedEntry)
            .where(SeedEntry.id == simulation.id)
            .values(value=json.dumps({"num_turns": 2, "adversarial_chat_system_prompt_path": "C:\\private.yaml"}))
        )
        await session.commit()
    with patch.object(SeedPrompt, "from_yaml_file", side_effect=AssertionError("loaded historical file")):
        ordinary = await client.get(
            f"{URL}/seeds/{simulation.prompt_group_id}", params={"selection_key": "dataset:named:upload"}
        )
        assert ordinary.status_code == 200
        editable = await client.get(f"{URL}/seeds", params={"selection_key": "dataset:named:upload", "editable": True})
        assert editable.status_code == 422
        current = await sqlite_instance.get_dataset_editing_snapshot_async("upload")
        response = await _update_async(client, snapshot={"version": current.version}, seeds=[{**row, "value": "new"}])
        assert response.status_code == 422
    assert next(entry for entry in await _seeds(sqlite_instance) if entry.seed_type == "prompt").value == "next"


async def test_cancelled_mutation_drains_new_storage_and_preserves_old_rows_and_media(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:a"}]),
        files={"a": ("a.bin", b"old")},
    )
    snapshot = await _editable_async(client)
    old = (await _seeds(sqlite_instance))[0]
    original = sqlite_instance.results_storage_io.write_stream_async
    written, release = asyncio.Event(), asyncio.Event()

    async def delayed_write_async(**kwargs: Any) -> None:
        await original(**kwargs)
        written.set()
        await release.wait()

    with patch.object(sqlite_instance.results_storage_io, "write_stream_async", side_effect=delayed_write_async):
        task = asyncio.create_task(
            _update_async(
                client,
                snapshot=snapshot,
                seeds=[{**_editable_rows(snapshot)[0], "value": "upload:b"}],
                files={"b": ("b.bin", b"cancelled")},
            )
        )
        try:
            await asyncio.wait_for(written.wait(), TEST_WATCHDOG_SECONDS)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, TEST_WATCHDOG_SECONDS)
    assert (await _seeds(sqlite_instance))[0].value == old.value
    assert Path(old.value).read_bytes() == b"old"
    assert list(Path(sqlite_instance.results_path).rglob("*.bin")) == [Path(old.value)]
    assert (await _editable_async(client))["version"] == snapshot["version"]


async def test_committed_cleanup_read_failure_still_returns_success_with_explicit_location(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(client)
    snapshot = await _editable_async(client)
    with patch.object(sqlite_instance, "cleanup_dataset_media_async", side_effect=OSError("database unavailable")):
        response = await _update_async(client, snapshot=snapshot, seeds=[{"data_type": "text", "value": "new"}])
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1 and response.json()["cleanup"]["status"] == "pending"
    assert response.json()["cleanup"]["pending_media"] is None
    assert response.headers["Location"] == response.json()["cleanup_location"]
    assert len(await _seeds(sqlite_instance)) == 2
    assert (await client.get(response.headers["Location"])).json()["status"] == "completed"


async def test_only_replaced_asset_is_cleaned_not_all_assets_from_original_upload(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {"data_type": "binary_path", "value": "upload:a"},
                {"data_type": "binary_path", "value": "upload:b"},
            ]
        ),
        files={"a": ("a.bin", b"first"), "b": ("b.bin", b"untouched")},
    )
    original = {str(entry.id): entry for entry in await _seeds(sqlite_instance)}
    snapshot = await _editable_async(client)
    edit, omitted = _editable_rows(snapshot)
    result = await _update_async(client, snapshot=snapshot, seeds=[{**edit, "data_type": "text", "value": "replaced"}])
    assert result.status_code == 200, result.text
    assert not Path(original[edit["id"]].value).exists()
    assert Path(original[omitted["id"]].value).exists()
    unowned = Path(sqlite_instance.results_path) / "external.bin"
    unowned.write_bytes(b"external")
    async with await sqlite_instance.get_session_async() as session:
        session.add(
            SeedEntry(
                entry=SeedPrompt(value=str(unowned), data_type="binary_path", dataset_name="external", added_by="sdk")
            )
        )
        await session.commit()
    external = await _editable_async(client, "external")
    result = await _update_async(
        client, name="external", snapshot=external, deletes=[_editable_rows(external)[0]["id"]]
    )
    assert result.status_code == 200 and unowned.exists()


async def test_database_failure_after_dirty_edit_and_delete_rolls_back_entire_batch_and_new_media(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {"data_type": "text", "value": "old"},
                {"data_type": "binary_path", "value": "upload:a"},
            ]
        ),
        files={"a": ("a.bin", b"old")},
    )
    snapshot = await _editable_async(client)
    before = {str(entry.id): entry.get_seed_record().model_dump(mode="json") for entry in await _seeds(sqlite_instance)}
    rows = _editable_rows(snapshot)
    edit = next(row for row in rows if row["data_type"] == "text")
    deleted = next(row for row in rows if row["data_type"] == "binary_path")
    async with await sqlite_instance.get_session_async() as session:
        engine = session.bind.sync_engine

    def fail_insert(*args: Any) -> None:
        if args[2].startswith('INSERT INTO "SeedPromptEntries"'):
            raise RuntimeError("injected database failure")

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        response = await _update_async(
            client,
            snapshot=snapshot,
            seeds=[
                {**edit, "value": "dirty replacement"},
                {"data_type": "binary_path", "value": "upload:b"},
            ],
            deletes=[deleted["id"]],
            files={"b": ("b.bin", b"new")},
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)
    assert response.status_code == 503
    after = {str(entry.id): entry.get_seed_record().model_dump(mode="json") for entry in await _seeds(sqlite_instance)}
    assert before == after
    assert list(Path(sqlite_instance.results_path).rglob("*.bin")) == [Path(before[deleted["id"]]["value"])]
    assert (await _editable_async(client))["version"] == snapshot["version"]


@pytest.mark.parametrize("data_type", ["image_path", "audio_path", "video_path", "binary_path"])
async def test_arbitrary_media_bytes_round_trip_and_owned_cleanup(
    client: AsyncClient, sqlite_instance: SQLiteMemory, data_type: str
):
    binary = b"\x00\xff<script>not decoded or executed</script>"
    response = await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": data_type, "value": "upload:document"}] * 2),
        files={"document": ("..\\..\\payload.html", binary)},
    )
    assert response.status_code == 201, response.text
    seeds = await _seeds(sqlite_instance)
    assert len(seeds) == 2 and seeds[0].value == seeds[1].value
    path = Path(seeds[0].value)
    assert path.is_relative_to(Path(sqlite_instance.results_path))
    assert path.read_bytes() == binary
    assert seeds[0].value_sha256 == hashlib.sha256(binary).hexdigest()
    downloaded = await client.get("/api/media", params={"path": str(path)})
    assert downloaded.content == binary
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment")
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 200, deleted.text
    assert not path.exists()
    assert not await _seeds(sqlite_instance)
    status = await client.get(deleted.headers["Location"])
    assert status.json()["status"] == "completed"
    assert (await client.delete(URL, params={"selection_key": "dataset:named:upload"})).status_code == 404


async def test_inert_metadata_and_schema_fields_are_not_template_constructor_inputs(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    raw = {
        "data_type": "text",
        "value": "literal",
        "metadata": {"is_jinja_template": True, "next_message_system_prompt_path": "inert source metadata"},
        "response_json_schema": {"type": "object", "properties": {"is_jinja_template": {"type": "boolean"}}},
    }
    with patch.object(Seed, "_render_trusted_template_value", side_effect=AssertionError("rendered")):
        created = await _upload(client, manifest=_manifest(seeds=[raw]))
        assert created.status_code == 201
        snapshot = await _editable_async(client)
        result = await _update_async(client, snapshot=snapshot, seeds=[_editable_rows(snapshot)[0]])
        assert result.status_code == 200, result.text
    assert (await _seeds(sqlite_instance))[0].prompt_metadata["is_jinja_template"] is True


async def test_whole_dataset_delete_includes_assets_owned_by_later_mutations(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    await _upload(client)
    snapshot = await _editable_async(client)
    result = await _update_async(
        client,
        snapshot=snapshot,
        seeds=[{"data_type": "binary_path", "value": "upload:a"}],
        files={"a": ("a.bin", b"mutation-owned")},
    )
    assert result.status_code == 200
    path = Path(next(entry.value for entry in await _seeds(sqlite_instance) if entry.data_type == "binary_path"))
    assert path.exists()
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 200 and deleted.json()["deleted_seeds"] == 2
    assert not path.exists() and not await _seeds(sqlite_instance)


@pytest.mark.parametrize(
    "invalid", ["objective_cardinality", "simulation_cardinality", "sequence_overlap", "foreign_group"]
)
async def test_invalid_additions_validate_complete_existing_groups_atomically(
    client: AsyncClient, sqlite_instance: SQLiteMemory, invalid: str
) -> None:
    created = await _upload(
        client,
        manifest=_manifest(
            seeds=[
                {"seed_type": "objective", "value": "goal", "prompt_group_alias": "g"},
                {**_simulation(), "prompt_group_alias": "g"},
            ]
        ),
    )
    assert created.status_code == 201
    snapshot = await _editable_async(client)
    group = _editable_rows(snapshot)[0]["prompt_group_id"]
    if invalid == "objective_cardinality":
        addition = {"seed_type": "objective", "value": "second goal", "prompt_group_id": group}
    elif invalid == "simulation_cardinality":
        addition = {**_simulation(), "prompt_group_id": group}
    elif invalid == "sequence_overlap":
        addition = {"data_type": "text", "value": "overlap", "prompt_group_id": group, "sequence": 1}
    else:
        addition = {"data_type": "text", "value": "foreign", "prompt_group_id": str(uuid4())}
    result = await _update_async(
        client, snapshot=snapshot, seeds=[{"data_type": "text", "value": "must rollback"}, addition]
    )
    assert result.status_code == 422
    assert len(await _seeds(sqlite_instance)) == 2
    assert (await _editable_async(client))["version"] == snapshot["version"]


@pytest.mark.parametrize(
    "seed",
    [
        {"value": "text"},
        {"data_type": None, "value": "text"},
        {"data_type": "image_path", "value": "C:\\Windows\\secret"},
        {"data_type": "binary_path", "value": "../../secret"},
        {"data_type": "text", "value": "upload:document"},
        {"data_type": "text", "value": "text", "is_jinja_template": True},
        {"data_type": "text", "value": "text", "is_jinja_template": "false"},
        {"data_type": "text", "value": "text", "dataset_name": "other"},
        {"data_type": "binary_path", "value": "text", "seed_type": "objective"},
        {"data_type": "text", "value": "text", "prompt_group_id": "bad"},
        {"data_type": "text", "value": "text", "unknown": "field"},
    ],
)
async def test_invalid_prompts_never_construct_trusted_templates_or_read_client_paths(
    client: AsyncClient, sqlite_instance: SQLiteMemory, seed: dict[str, Any]
):
    with (
        patch.object(SeedPrompt, "_render_trusted_template_value", side_effect=AssertionError("rendered")),
        patch("pyrit.models.seeds.seed_prompt.Path.is_file", side_effect=AssertionError("inferred filesystem type")),
    ):
        response = await _upload(client, manifest=_manifest(seeds=[seed]))
    assert response.status_code == 422, response.text
    assert not await _seeds(sqlite_instance)
    assert "seeds.0" in str(response.json())


async def test_duplicate_names_and_concurrent_uploads(client: AsyncClient, sqlite_instance: SQLiteMemory):
    first, second = await asyncio.gather(_upload(client), _upload(client))
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    assert len(await _seeds(sqlite_instance)) == 1
    assert (await _upload(client)).status_code == 409


@pytest.mark.parametrize("seed_type", ["prompt", "objective", "simulated_conversation"])
async def test_edit_omitted_seed_type_uses_stored_variant(
    client: AsyncClient, sqlite_instance: SQLiteMemory, seed_type: str
) -> None:
    raw = (
        _simulation()
        if seed_type == "simulated_conversation"
        else {"seed_type": seed_type, "data_type": "text", "value": "original"}
    )
    assert (await _upload(client, manifest=_manifest(seeds=[raw]))).status_code == 201
    snapshot = await _editable_async(client)
    edit = _editable_rows(snapshot)[0]
    original_id = edit["id"]
    edit.pop("seed_type")
    if seed_type == "simulated_conversation":
        edit["num_turns"] = 3
    else:
        edit["value"] = "replacement"
    response = await _update_async(client, snapshot=snapshot, seeds=[edit])
    assert response.status_code == 200, response.text
    stored = (await _seeds(sqlite_instance))[0]
    assert str(stored.id) == original_id and stored.seed_type == seed_type
    assert response.json()["version"] != snapshot["version"]
    restored = stored.get_seed()
    if seed_type == "simulated_conversation":
        assert restored.num_turns == 3
    else:
        assert restored.value == "replacement"


@pytest.mark.parametrize("mode", ["create", "update"])
@pytest.mark.parametrize("file_manifest", [False, True])
async def test_one_simulation_budget_allows_three_media_slots(
    client: AsyncClient, sqlite_instance: SQLiteMemory, mode: str, file_manifest: bool
) -> None:
    snapshot = None
    if mode == "update":
        assert (await _upload(client, manifest=_manifest(seeds=[_simulation()]))).status_code == 201
        snapshot = await _editable_async(client)
    raw = _simulation(
        **{
            field: {"data_type": "binary_path", "value": f"upload:media{index}"}
            for index, field in enumerate(
                ("adversarial_chat_system_prompt", "simulated_target_system_prompt", "next_message_system_prompt")
            )
        }
    )
    manifest = _manifest(seeds=[raw])
    if snapshot is not None:
        raw["id"] = _editable_rows(snapshot)[0]["id"]
        manifest.update(mode="update", expected_version=snapshot["version"])
    files = {f"media{index}": (f"{index}.bin", bytes([index])) for index in range(3)}
    if file_manifest:
        files["manifest"] = ("manifest.json", json.dumps(manifest).encode())
    with patch.object(DatasetUploadLimits, "from_environment", return_value=DatasetUploadLimits(prompts=1)):
        response = await _upload(client, manifest=manifest, files=files)
    assert response.status_code == (201 if mode == "create" else 200), response.text
    stored = (await _seeds(sqlite_instance))[0].get_seed()
    for index, field in enumerate(
        ("adversarial_chat_system_prompt", "simulated_target_system_prompt", "next_message_system_prompt")
    ):
        assert Path(getattr(stored, field).value).read_bytes() == bytes([index])


@pytest.mark.parametrize("failed_release", [1, 2])
async def test_create_reports_committed_success_after_native_release_error(
    client: AsyncClient, sqlite_instance: SQLiteMemory, failed_release: int
) -> None:
    original_release = sqlite_instance.release_dataset_upload_async
    calls = 0

    async def release_async(operation_id: Any) -> None:
        nonlocal calls
        calls += 1
        await original_release(operation_id)
        if calls == failed_release:
            raise OSError("private native close failure")

    with patch.object(sqlite_instance, "release_dataset_upload_async", side_effect=release_async):
        response = await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:media"}]),
            files={"media": ("media.bin", b"committed")},
        )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["cleanup"]["status"] == "pending" and result["cleanup"]["pending_media"] is None
    assert response.headers["Location"] == result["cleanup_location"]
    assert "private native close failure" not in response.text
    stored = await _seeds(sqlite_instance)
    assert len(stored) == 1 and Path(stored[0].value).read_bytes() == b"committed"
    assert (await client.get(result["cleanup_location"])).json()["status"] == "completed"
    assert (await _upload(client)).status_code == 409


def _simulation(**changes: Any) -> dict[str, Any]:
    return {
        "seed_type": "simulated_conversation",
        "data_type": "text",
        "num_turns": 2,
        "sequence": 0,
        "adversarial_chat_system_prompt": {"data_type": "text", "value": "literal {{ objective }}"},
        "simulated_target_system_prompt": {"data_type": "text", "value": "literal target"},
        **changes,
    }


@pytest.mark.parametrize(
    "raw,model",
    [
        ({"seed_type": "prompt", "data_type": "text", "value": "literal"}, SeedPrompt),
        (
            {
                "seed_type": "objective",
                "data_type": "text",
                "value": "literal {{ objective }}",
                "conditions": [{"condition_type": "matches_objective"}],
            },
            SeedObjective,
        ),
        (
            {key: value for key, value in _simulation(value="ignored").items() if key != "data_type"},
            SeedSimulatedConversation,
        ),
    ],
)
async def test_all_seed_variants_upload_round_trip(
    client: AsyncClient, sqlite_instance: SQLiteMemory, raw: dict[str, Any], model: type
) -> None:
    response = await _upload(client, manifest=_manifest(seeds=[raw]))
    assert response.status_code == 201, response.text
    entry = (await _seeds(sqlite_instance))[0]
    restored = entry.get_seed()
    assert isinstance(restored, model)
    assert restored.seed_type == raw["seed_type"]
    assert entry.value_sha256 == hashlib.sha256(entry.value.encode("utf-8")).hexdigest()
    if isinstance(restored, SeedObjective):
        assert restored.conditions[0].condition_type == "matches_objective"
        assert restored.value == raw["value"]
    if isinstance(restored, SeedSimulatedConversation):
        assert restored.num_turns == 2
        assert restored.adversarial_chat_system_prompt.value == "literal {{ objective }}"
        assert restored.value != "ignored"
    page = await client.get(f"{URL}/seeds", params={"selection_key": "dataset:named:upload"})
    assert page.status_code == 200 and page.json()["total"] == 1
    detail = await client.get(
        f"{URL}/seeds/{page.json()['items'][0]['example_id']}",
        params={"selection_key": "dataset:named:upload"},
    )
    assert detail.status_code == 200
    assert detail.json()["members"][0]["seed_type"] == raw["seed_type"]
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 200 and deleted.json()["deleted_seeds"] == 1


async def test_all_seed_mixed_group_preserves_conditions_associations_and_config(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    alias = "shared"
    association = {
        "system_prompt": {
            "data_type": "text",
            "value": "literal {{ objective }}",
            "parameters": ["objective"],
            "response_json_schema": {"type": "object"},
        }
    }
    raw = [
        {
            "seed_type": "objective",
            "value": "goal",
            "conditions": [{"condition_type": "matches_objective"}],
            "metadata": {"adversarial_prompts": association, "custom": {"typed": [1, True]}},
        },
        _simulation(sequence=2, next_message_system_prompt={"data_type": "text", "value": "next"}),
        {"data_type": "text", "value": "follow up", "sequence": 7, "role": "user"},
    ]
    response = await _upload(
        client, manifest=_manifest(seeds=[{**seed, "prompt_group_alias": alias, "id": str(uuid4())} for seed in raw])
    )
    assert response.status_code == 201, response.text
    assert response.json()["seed_pieces"] == 3 and response.json()["logical_examples"] == 1
    stored = await _seeds(sqlite_instance)
    assert len({seed.prompt_group_id for seed in stored}) == 1
    group = AttackSeedGroup(seeds=[seed.get_seed() for seed in stored])
    assert group.objective.conditions[0].condition_type == "matches_objective"
    assert group.objective.metadata["custom"] == {"typed": [1, True]}
    assert group.adversarial_prompts.system_prompt.response_json_schema == {"type": "object"}
    assert group.adversarial_prompts.system_prompt.value == "literal {{ objective }}"
    assert group.simulated_conversation_config.sequence == 2
    assert group.simulated_conversation_config.next_message_system_prompt.value == "next"
    page = await client.get(f"{URL}/seeds", params={"selection_key": "dataset:named:upload"})
    assert page.json()["total"] == 1
    detail = await client.get(
        f"{URL}/seeds/{page.json()['items'][0]['example_id']}",
        params={"selection_key": "dataset:named:upload"},
    )
    assert {member["seed_type"] for member in detail.json()["members"]} == {
        "prompt",
        "objective",
        "simulated_conversation",
    }
    assert (await _upload(client, manifest=_manifest(seeds=raw))).status_code == 409


@pytest.mark.parametrize(
    "seeds",
    [
        [{"seed_type": "unknown", "value": "private"}],
        [{"seed_type": "objective", "value": "private", "data_type": "image_path"}],
        [{"seed_type": "objective", "value": "private", "is_general_technique": True}],
        [{"seed_type": "objective", "value": "private", "conditions": [{"condition_type": "unknown"}]}],
        [{"seed_type": "objective", "value": "private", "role": "user"}],
        [_simulation(num_turns=0)],
        [_simulation(sequence=-1)],
        [_simulation(data_type="binary_path")],
        [_simulation(unknown="field")],
        [
            {"seed_type": "objective", "value": "a", "prompt_group_alias": "same"},
            {"seed_type": "objective", "value": "b", "prompt_group_alias": "same"},
        ],
        [_simulation(prompt_group_alias="same"), _simulation(prompt_group_alias="same")],
        [
            _simulation(prompt_group_alias="same"),
            {"data_type": "text", "value": "overlap", "sequence": 1, "prompt_group_alias": "same"},
        ],
        [
            {
                "seed_type": "objective",
                "value": "private",
                "metadata": {
                    "adversarial_prompts": {"system_prompt": {"data_type": "binary_path", "value": "upload:part"}}
                },
            }
        ],
        [
            {
                "seed_type": "objective",
                "value": "private",
                "metadata": {"adversarial_prompts": {"unknown": {"data_type": "text", "value": "bad"}}},
            }
        ],
    ],
)
async def test_all_seed_invalid_subtypes_and_groups_leave_no_reservation(
    client: AsyncClient, sqlite_instance: SQLiteMemory, seeds: list[dict[str, Any]]
) -> None:
    response = await _upload(client, manifest=_manifest(seeds=seeds))
    assert response.status_code == 422, response.text
    assert "private" not in response.text
    async with await sqlite_instance.get_session_async() as session:
        assert not (await session.scalars(select(SeedEntry))).all()
        assert not (await session.scalars(select(DatasetLifecycleEntry))).all()


@pytest.mark.parametrize(
    "raw",
    [
        {"seed_type": "objective", "value": "{{ secret }}", "is_jinja_template": True},
        _simulation(is_jinja_template=True),
        *[
            _simulation(**{name: {"value": "{{ secret }}", "data_type": "text", "is_jinja_template": True}})
            for name in (
                "adversarial_chat_system_prompt",
                "simulated_target_system_prompt",
                "next_message_system_prompt",
            )
        ],
        *[
            _simulation(**{name + "_path": "C:\\client\\secret.yaml"})
            for name in (
                "adversarial_chat_system_prompt",
                "simulated_target_system_prompt",
                "next_message_system_prompt",
            )
        ],
        _simulation(adversarial_chat_system_prompt={"value": "C:\\client\\secret.yaml"}),
        _simulation(adversarial_chat_system_prompt={"value": "C:\\client\\secret.yaml", "data_type": "binary_path"}),
        {
            "seed_type": "objective",
            "value": "goal",
            "metadata": {
                "adversarial_prompts": {
                    "system_prompt": {"value": "{{ secret }}", "data_type": "text", "is_jinja_template": True}
                }
            },
        },
    ],
)
async def test_all_seed_trust_is_checked_before_template_and_file_constructors(
    client: AsyncClient, sqlite_instance: SQLiteMemory, raw: dict[str, Any]
) -> None:
    with (
        patch.object(Seed, "_render_trusted_template_value", side_effect=AssertionError("rendered")),
        patch.object(SeedPrompt, "from_yaml_file", side_effect=AssertionError("loaded client file")),
        patch("pyrit.models.seeds.seed_prompt.Path.is_file", side_effect=AssertionError("filesystem inference")),
    ):
        response = await _upload(client, manifest=_manifest(seeds=[raw]))
    assert response.status_code == 422, response.text
    assert not await _seeds(sqlite_instance)


@pytest.mark.parametrize(
    "field", ["adversarial_chat_system_prompt", "simulated_target_system_prompt", "next_message_system_prompt"]
)
@pytest.mark.parametrize("data_type", ["image_path", "audio_path", "video_path", "binary_path"])
async def test_all_seed_nested_media_is_owned_and_shared_references_are_guarded(
    client: AsyncClient, sqlite_instance: SQLiteMemory, field: str, data_type: str
) -> None:
    response = await _upload(
        client,
        manifest=_manifest(seeds=[_simulation(**{field: {"value": "upload:part", "data_type": data_type}})]),
        files={"part": ("inert.bin", b"\x00\xff")},
    )
    assert response.status_code == 201, response.text
    entry = (await _seeds(sqlite_instance))[0]
    restored = entry.get_seed()
    path = Path(getattr(restored, field).value)
    assert path.read_bytes() == b"\x00\xff"
    assert (await client.get("/api/media", params={"path": str(path)})).content == b"\x00\xff"
    shared = SeedSimulatedConversation(
        **{**_simulation(), field: {"value": str(path), "data_type": data_type}},
        dataset_name="shared",
        added_by="sdk",
    )
    async with await sqlite_instance.get_session_async() as session:
        session.add(SeedEntry(entry=shared))
        await session.commit()
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 200 and path.exists()
    # A separate, unshared nested asset is removed and cannot be reattached by Core DML.
    response = await _upload(
        client,
        manifest=_manifest(
            name="unshared", seeds=[_simulation(**{field: {"value": "upload:part", "data_type": data_type}})]
        ),
        files={"part": ("inert.bin", b"unshared")},
    )
    assert response.status_code == 201, response.text
    owned = next(seed for seed in await _seeds(sqlite_instance) if seed.dataset_name == "unshared")
    owned_path = Path(getattr(owned.get_seed(), field).value)
    await client.delete(URL, params={"selection_key": "dataset:named:unshared"})
    assert not owned_path.exists()
    async with await sqlite_instance.get_session_async() as session:
        with pytest.raises(ValueError, match="removed"):
            await session.execute(
                update(SeedEntry.__table__).where(SeedEntry.id == shared.id).values(value=owned.value)
            )
        await session.commit()
    async with await sqlite_instance.get_session_async() as session:
        assert (await session.get(SeedEntry, shared.id)).value == shared.value


@pytest.mark.parametrize(
    "value",
    ["not-json", "[]", '{"adversarial_chat_system_prompt": {"data_type": "binary_path"}}'],
)
async def test_all_seed_corrupt_legacy_config_remains_inspectable_and_cleanup_fails_closed(
    client: AsyncClient, sqlite_instance: SQLiteMemory, value: str
) -> None:
    corrupt = SeedEntry(entry=SeedPrompt(value=value, dataset_name="legacy", added_by="sdk"))
    corrupt.seed_type = "simulated_conversation"
    async with await sqlite_instance.get_session_async() as session:
        session.add(corrupt)
        await session.commit()
    response = await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:part"}]),
        files={"part": ("inert.bin", b"owned")},
    )
    assert response.status_code == 201, response.text
    path = Path(next(seed for seed in await _seeds(sqlite_instance) if seed.dataset_name == "upload").value)
    deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 202
    assert deleted.json()["pending_media"] == 1 and path.exists()


@pytest.mark.parametrize("roles,sequences", [(["user", "assistant"], [0, 0]), ([None, None], [0, 1])])
async def test_invalid_complete_groups_are_rejected_before_reservation(
    client: AsyncClient, sqlite_instance: SQLiteMemory, roles: list[str | None], sequences: list[int]
) -> None:
    group = str(uuid4())
    prompts = [
        {"data_type": "text", "value": "private prompt", "prompt_group_id": group, "role": role, "sequence": sequence}
        for role, sequence in zip(roles, sequences, strict=True)
    ]
    response = await _upload(client, manifest=_manifest(seeds=prompts))
    assert response.status_code == 422, response.text
    assert "private prompt" not in response.text
    async with await sqlite_instance.get_session_async() as session:
        assert not (await session.scalars(select(DatasetLifecycleEntry))).all()
        assert not (await session.scalars(select(SeedEntry))).all()


async def test_complete_group_validation_preserves_uploaded_roles_and_unicode_identity(
    client: AsyncClient, sqlite_instance: SQLiteMemory
) -> None:
    group = str(uuid4())
    name = "\u6570\u636e-\U0001f600"
    response = await _upload(
        client,
        manifest=_manifest(
            name=name,
            seeds=[
                {"data_type": "text", "value": "explicit", "role": "user", "prompt_group_id": group},
                {"data_type": "text", "value": "inferred on consumption", "role": None, "prompt_group_id": group},
            ],
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == name
    stored = await _seeds(sqlite_instance)
    assert {seed.value: seed.role for seed in stored} == {"explicit": "user", "inferred on consumption": None}
    page = await client.get(f"{URL}/seeds", params={"selection_key": f"dataset:named:{name}"})
    assert page.status_code == 200 and page.json()["total"] == 1
    assert (await client.delete(URL, params={"selection_key": f"dataset:named:{name}"})).status_code == 200
    assert not await _seeds(sqlite_instance)


async def test_cleanup_failure_is_durable_and_retryable(client: AsyncClient, sqlite_instance: SQLiteMemory):
    response = await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
        files={"file": ("file.bin", b"data")},
    )
    assert response.status_code == 201
    path = Path((await _seeds(sqlite_instance))[0].value)
    with patch.object(
        sqlite_instance.results_storage_io, "delete_file_async", side_effect=OSError("storage unavailable")
    ):
        deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["pending_media"] == 1
    assert deleted.json()["error"]
    assert path.exists() and not await _seeds(sqlite_instance)
    # No in-memory job registry is required to resume: a new request reads the journal.
    result = await client.get(deleted.headers["Location"])
    assert result.json()["status"] == "completed"
    assert not path.exists()


async def test_storage_failure_rolls_back_all_seeds_and_partial_files(
    client: AsyncClient, sqlite_instance: SQLiteMemory
):
    storage = sqlite_instance.results_storage_io
    original = storage.write_stream_async

    async def fail_after_write(**kwargs: Any) -> None:
        await original(**kwargs)
        raise OSError("partial storage failure")

    with patch.object(storage, "write_stream_async", side_effect=fail_after_write):
        response = await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
            files={"file": ("file.bin", b"data")},
        )
    assert response.status_code == 503
    assert "partial storage failure" not in response.text
    assert not await _seeds(sqlite_instance)
    assert not list(Path(sqlite_instance.results_path).rglob("*.bin"))
    async with await sqlite_instance.get_session_async() as session:
        assert (await session.scalars(select(DatasetMediaEntry.status))).all() == ["deleted"]


@pytest.mark.parametrize("reference", ["seed", "original", "converted"])
async def test_shared_media_is_retained(client: AsyncClient, sqlite_instance: SQLiteMemory, reference: str):
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
        files={"file": ("file.bin", b"shared")},
    )
    path = (await _seeds(sqlite_instance))[0].value
    async with await sqlite_instance.get_session_async() as session:
        if reference == "seed":
            session.add(
                SeedEntry(entry=SeedPrompt(value=path, data_type="binary_path", dataset_name="shared", added_by="test"))
            )
        else:
            piece = MessagePiece(
                role="user",
                conversation_id=str(uuid4()),
                sequence=0,
                original_value=path if reference == "original" else "text",
                original_value_data_type="binary_path" if reference == "original" else "text",
                converted_value=path if reference == "converted" else "text",
                converted_value_data_type="binary_path" if reference == "converted" else "text",
            )
            session.add(PromptMemoryEntry(entry=piece))
        await session.commit()
    response = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
    assert response.status_code == 200, response.text
    assert Path(path).read_bytes() == b"shared"


async def test_late_reference_to_cleanup_claim_is_rejected(client: AsyncClient, sqlite_instance: SQLiteMemory):
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
        files={"file": ("file.bin", b"removed")},
    )
    path = (await _seeds(sqlite_instance))[0].value
    deletion_id = await sqlite_instance.delete_dataset_async(dataset_name="upload")
    for bulk in (False, True):
        async with await sqlite_instance.get_session_async() as session:
            seed = SeedEntry(entry=SeedPrompt(value=path, data_type="binary_path", added_by="test"))
            if not bulk:
                session.add(seed)
                with pytest.raises(ValueError, match="reserved for dataset cleanup"):
                    await session.commit()
            else:
                seed.value, seed.data_type = "text", "text"
                session.add(seed)
                await session.commit()
                with pytest.raises(ValueError, match="reserved for dataset cleanup"):
                    await session.execute(update(SeedEntry).values(value=path, data_type="binary_path"))
                await session.rollback()
    await sqlite_instance.cleanup_dataset_media_async(operation_id=deletion_id)
    assert not Path(path).exists()


@pytest.mark.parametrize("surface", ["seed", "original", "converted", "scorable"])
@pytest.mark.parametrize("operation", ["insert", "update"])
async def test_rejected_core_media_dml_is_not_committable(
    client: AsyncClient, sqlite_instance: SQLiteMemory, surface: str, operation: str
) -> None:
    await _upload(
        client,
        manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
        files={"file": ("file.bin", b"removed")},
    )
    path = (await _seeds(sqlite_instance))[0].value
    deletion_id = await sqlite_instance.delete_dataset_async(dataset_name="upload")
    entry: SeedEntry | ScorableContentEntry | PromptMemoryEntry
    if surface == "seed":
        entry = SeedEntry(entry=SeedPrompt(value="original", dataset_name="other", added_by="test"))
        changes = {"value": path, "data_type": "binary_path"}
    elif surface == "scorable":
        entry = ScorableContentEntry(
            id=uuid4(),
            value="original",
            value_sha256=hashlib.sha256(b"original").hexdigest(),
            data_type="text",
            timestamp=datetime.now(UTC),
        )
        changes = {"value": path, "data_type": "binary_path"}
    else:
        entry = PromptMemoryEntry(
            entry=MessagePiece(role="user", original_value="original", conversation_id=str(uuid4()), sequence=0)
        )
        changes = {f"{surface}_value": path, f"{surface}_value_data_type": "binary_path"}
    model = type(entry)
    entry_id = entry.id
    async with await sqlite_instance.get_session_async() as session:
        if operation == "update":
            session.add(entry)
            await session.commit()
            statement = update(model.__table__).where(model.id == entry_id).values(**changes)
        else:
            values = {column.name: getattr(entry, column.name) for column in model.__table__.columns}
            statement = insert(model.__table__).values(**(values | changes))
        with pytest.raises(ValueError, match="reserved for dataset cleanup"):
            await session.execute(statement)
        await session.commit()
    async with await sqlite_instance.get_session_async() as session:
        stored = await session.get(model, entry_id)
        if operation == "insert":
            assert stored is None
        else:
            column = f"{surface}_value" if surface in {"original", "converted"} else "value"
            assert await session.scalar(select(model.__table__.c[column]).where(model.id == entry_id)) == "original"
    await sqlite_instance.cleanup_dataset_media_async(operation_id=deletion_id)
    assert not Path(path).exists()


async def test_manifest_and_prompt_count_limits(client: AsyncClient):
    manifest = json.dumps(_manifest())
    with patch.dict("os.environ", {"PYRIT_DATASET_MAX_MANIFEST_BYTES": str(len(manifest.encode()))}):
        assert (await client.post(URL, files={"manifest": (None, manifest)})).status_code == 201
        assert (await client.post(URL, files={"manifest": ("m.json", manifest + " ")})).status_code == 413
    with patch.dict("os.environ", {"PYRIT_DATASET_MAX_PROMPTS": "1"}):
        assert (
            await _upload(client, manifest=_manifest(name="limit", seeds=[{"data_type": "text", "value": "x"}]))
        ).status_code == 201
        assert (
            await _upload(client, manifest=_manifest(name="over", seeds=[{"data_type": "text", "value": "x"}] * 2))
        ).status_code == 413
        assert (
            await _upload(
                client, manifest=_manifest(name="objective-limit", seeds=[{"seed_type": "objective", "value": "x"}])
            )
        ).status_code == 201
        assert (
            await _upload(
                client,
                manifest=_manifest(name="all-over", seeds=[{"seed_type": "objective", "value": "x"}, _simulation()]),
            )
        ).status_code == 413


@pytest.mark.parametrize("with_length", [True, False])
async def test_actual_multipart_request_byte_limit(client: AsyncClient, with_length: bool):
    body = (
        b'--limit\r\nContent-Disposition: form-data; name="manifest"\r\n\r\n'
        + json.dumps(_manifest()).encode()
        + b"\r\n--limit--\r\n"
    )
    headers = {"Content-Type": "multipart/form-data; boundary=limit"}
    if with_length:
        headers["Content-Length"] = str(len(body))

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 13):
            yield body[start : start + 13]

    with patch.dict("os.environ", {"PYRIT_DATASET_MAX_REQUEST_BYTES": str(len(body))}):
        response = await client.post(URL, content=chunks(), headers=headers)
        assert response.status_code == 201, response.text
    with patch.dict("os.environ", {"PYRIT_DATASET_MAX_REQUEST_BYTES": str(len(body) - 1)}):
        assert (await client.post(URL, content=chunks(), headers=headers)).status_code == 413


async def test_restart_recovers_journaled_upload(client: AsyncClient, sqlite_instance: SQLiteMemory):
    path = Path(sqlite_instance.results_path) / "seed-prompt-entries" / "abandoned.bin"
    path.parent.mkdir(parents=True)
    operation_id = await sqlite_instance.reserve_dataset_upload_async(dataset_name="abandoned", paths=[str(path)])
    path.write_bytes(b"partial")
    assert (await client.get(f"{URL}/deletions/{operation_id}")).status_code == 409
    # A runtime restart releases native ownership; recovery must not assume a live
    # reservation is abandoned merely because its transaction already committed.
    await sqlite_instance.release_dataset_upload_async(operation_id)
    await sqlite_instance.recover_dataset_cleanup_async()
    assert not path.exists()
    status = await client.get(f"{URL}/deletions/{operation_id}")
    assert status.json()["status"] == "completed"
    assert (await _upload(client, manifest=_manifest(name="abandoned"))).status_code == 201


@pytest.mark.parametrize("selection_key", ["dataset:unnamed", "invalid", "dataset:named:"])
async def test_delete_rejects_non_named_selections(client: AsyncClient, selection_key: str):
    assert (await client.delete(URL, params={"selection_key": selection_key})).status_code == 400


async def test_multipart_parts_and_manifest_errors(client: AsyncClient):
    for parts in (
        [("manifest", (None, json.dumps(_manifest())))] * 2,
        [("document", ("d.bin", b"unreferenced")), ("manifest", (None, json.dumps(_manifest())))],
        [("manifest", (None, '{"name":"x","name":"y","seeds":[]}'))],
        [("document", ("d.bin", b"missing manifest"))],
    ):
        assert (await client.post(URL, files=parts)).status_code == 422
    assert (await client.post(URL, json=_manifest())).status_code == 415
    assert (await client.get(f"{URL}/deletions/{uuid4()}")).status_code == 404


def test_upload_limits_configuration_is_explicit():
    assert DatasetUploadLimits.from_environment() == DatasetUploadLimits()
    with patch.dict("os.environ", {"PYRIT_DATASET_MAX_PROMPTS": "0"}):
        with pytest.raises(ValueError, match="positive"):
            DatasetUploadLimits.from_environment()


@pytest.mark.parametrize("mixed", [False, True])
async def test_db_failure_rolls_back_seed_batch_and_owned_media(
    client: AsyncClient, sqlite_instance: SQLiteMemory, mixed: bool
):
    async with await sqlite_instance.get_session_async() as session:
        engine = session.bind.sync_engine

    def fail_seed_insert(
        conn: Connection, cursor: Any, statement: str, parameters: Any, context: ExecutionContext, executemany: bool
    ) -> None:
        if statement.startswith('INSERT INTO "SeedPromptEntries"'):
            raise RuntimeError("injected database failure")

    event.listen(engine, "before_cursor_execute", fail_seed_insert)
    try:
        response = await _upload(
            client,
            manifest=_manifest(
                seeds=[
                    {"data_type": "text", "value": "first"},
                    {"data_type": "binary_path", "value": "upload:file"},
                    *([{"seed_type": "objective", "value": "goal"}, _simulation()] if mixed else []),
                ]
            ),
            files={"file": ("file.bin", b"rollback")},
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_seed_insert)
    assert response.status_code == 503
    assert not await _seeds(sqlite_instance)
    assert not list(Path(sqlite_instance.results_path).rglob("*.bin"))


async def test_cancelled_upload_drains_storage_and_rolls_back(client: AsyncClient, sqlite_instance: SQLiteMemory):
    storage = sqlite_instance.results_storage_io
    original = storage.write_stream_async
    written, release = asyncio.Event(), asyncio.Event()

    async def delayed_write(**kwargs: Any) -> None:
        await original(**kwargs)
        written.set()
        await release.wait()

    with patch.object(storage, "write_stream_async", side_effect=delayed_write):
        task = asyncio.create_task(
            _upload(
                client,
                manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
                files={"file": ("file.bin", b"cancelled")},
            )
        )
        try:
            await asyncio.wait_for(written.wait(), TEST_WATCHDOG_SECONDS)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, TEST_WATCHDOG_SECONDS)
    assert not await _seeds(sqlite_instance)
    assert not list(Path(sqlite_instance.results_path).rglob("*.bin"))
    await sqlite_instance.recover_dataset_cleanup_async()
    assert (await _upload(client)).status_code == 201


async def test_failed_rollback_cleanup_survives_for_later_retry(client: AsyncClient, sqlite_instance: SQLiteMemory):
    storage = sqlite_instance.results_storage_io
    original = storage.write_stream_async

    async def fail_write(**kwargs: Any) -> None:
        await original(**kwargs)
        raise OSError("private server path or credential")

    with (
        patch.object(storage, "write_stream_async", side_effect=fail_write),
        patch.object(storage, "delete_file_async", side_effect=OSError("cleanup unavailable")),
    ):
        response = await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
            files={"file": ("file.bin", b"pending")},
        )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "private server path" not in response.text
    assert not await _seeds(sqlite_instance)
    assert list(Path(sqlite_instance.results_path).rglob("*.bin"))
    result = await client.get(response.headers["Location"])
    assert result.json()["status"] == "completed"
    assert not list(Path(sqlite_instance.results_path).rglob("*.bin"))


async def test_sdk_dataset_conflict_and_unowned_media_preservation(client: AsyncClient, sqlite_instance: SQLiteMemory):
    external = Path(sqlite_instance.results_path) / "external.bin"
    external.write_bytes(b"not API-owned")
    seed = SeedPrompt(value=str(external), data_type="binary_path", dataset_name="upload", added_by="sdk")
    async with await sqlite_instance.get_session_async() as session:
        session.add(SeedEntry(entry=seed))
        await session.commit()
    response = await _upload(client)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert len(await _seeds(sqlite_instance)) == 1
    assert (await client.delete(URL, params={"selection_key": "dataset:named:upload"})).status_code == 200
    assert external.read_bytes() == b"not API-owned"


async def test_provider_remains_reloadable_after_stored_seed_deletion(
    client: AsyncClient, sqlite_instance: SQLiteMemory
):
    with patch(
        "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
        return_value=["provider"],
    ):
        assert (await client.delete(URL, params={"selection_key": "dataset:named:provider"})).status_code == 404
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="provider seed", data_type="text", dataset_name="provider")], added_by="provider"
        )
        assert (await client.delete(URL, params={"selection_key": "dataset:named:provider"})).status_code == 200
        listed = (await client.get(URL)).json()
    assert listed["items"][0]["provider_available"] is True
    assert listed["items"][0]["loaded"] is False


async def test_truncated_multipart_and_invalid_json_values(client: AsyncClient, sqlite_instance: SQLiteMemory):
    body = (
        b'--cut\r\nContent-Disposition: form-data; name="manifest"\r\n\r\n'
        + json.dumps(_manifest()).encode()
        + b"\r\n--cut\r\n"
    )
    assert (
        await client.post(URL, content=body, headers={"Content-Type": "multipart/form-data; boundary=cut"})
    ).status_code == 422
    for seed in [
        {"data_type": "text", "value": "value", "dataset_name": []},
        {"data_type": "text", "value": "value", "metadata": {"invalid": float("nan")}},
        {"data_type": "text", "value": "\ud800"},
    ]:
        assert (await _upload(client, manifest=_manifest(seeds=[seed]))).status_code == 422
    assert not await _seeds(sqlite_instance)


@pytest.mark.parametrize("reference", ["signed-url", "relative", "windows-relative"])
async def test_blob_upload_streams_inert_bytes_and_recognizes_signed_shared_references(
    client: AsyncClient, sqlite_instance: SQLiteMemory, reference: str
):
    blob = AzureBlobStorageIO(container_url="https://account.blob.core.windows.net/results")
    uploaded: dict[str, bytes] = {}

    async def write_async(*, path: str, chunks: AsyncIterator[bytes]) -> None:
        uploaded[str(path)] = b"".join([chunk async for chunk in chunks])

    with (
        patch.object(sqlite_instance, "results_path", "https://account.blob.core.windows.net/results"),
        patch.object(sqlite_instance, "results_storage_io", blob),
        patch.object(blob, "path_exists_async", return_value=False),
        patch.object(blob, "write_stream_async", side_effect=write_async),
        patch.object(blob, "delete_file_async", new_callable=AsyncMock) as delete,
    ):
        response = await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "audio_path", "value": "upload:file"}]),
            files={"file": ("file.mp3", b"arbitrary inert bytes")},
        )
        assert response.status_code == 201, response.text
        path = (await _seeds(sqlite_instance))[0].value
        assert uploaded == {path: b"arbitrary inert bytes"}
        # StorageIO resolves this alias to the same configured container/blob.
        alias = path.replace(".net/results/", ".net:443/other/")
        if reference != "signed-url":
            alias = blob._resolve_blob_name(path)
            if reference == "windows-relative":
                alias = alias.replace("/", "\\")
        else:
            alias += "?sig=signed"
        async with await sqlite_instance.get_session_async() as session:
            session.add(
                SeedEntry(entry=SeedPrompt(value=alias, data_type="audio_path", dataset_name="shared", added_by="sdk"))
            )
            await session.commit()
        deleted = await client.delete(URL, params={"selection_key": "dataset:named:upload"})
        assert deleted.status_code == 200
        delete.assert_not_awaited()


async def test_db_failure_during_rollback_returns_durable_cleanup_location(
    client: AsyncClient, sqlite_instance: SQLiteMemory
):
    with (
        patch.object(sqlite_instance, "commit_dataset_upload_async", side_effect=RuntimeError("database down")),
        patch.object(sqlite_instance, "cleanup_dataset_media_async", side_effect=RuntimeError("cleanup DB down")),
    ):
        response = await _upload(client)
    assert response.status_code == 503
    assert response.headers["Location"].startswith("/api/datasets/deletions/")
    assert "database down" not in response.text
    assert (await client.get(response.headers["Location"])).json()["status"] == "completed"
    assert (await _upload(client)).status_code == 201


async def test_generated_path_collision_preserves_unowned_bytes(client: AsyncClient, sqlite_instance: SQLiteMemory):
    path = Path(sqlite_instance.results_path) / "existing.bin"
    path.write_bytes(b"unowned")
    with patch("pyrit.memory.storage.serializers.DataTypeSerializer.get_data_filename_async", return_value=path):
        response = await _upload(
            client,
            manifest=_manifest(seeds=[{"data_type": "binary_path", "value": "upload:file"}]),
            files={"file": ("file.bin", b"new")},
        )
    assert response.status_code == 503
    await sqlite_instance.recover_dataset_cleanup_async()
    assert path.read_bytes() == b"unowned"
    assert not await _seeds(sqlite_instance)


async def test_cancelled_reservation_releases_name_without_restart(client: AsyncClient, sqlite_instance: SQLiteMemory):
    original = sqlite_instance.reserve_dataset_upload_async
    reserved, release = asyncio.Event(), asyncio.Event()

    async def delayed_reservation_async(**kwargs: Any) -> Any:
        upload_id = await original(**kwargs)
        reserved.set()
        await release.wait()
        return upload_id

    with patch.object(sqlite_instance, "reserve_dataset_upload_async", side_effect=delayed_reservation_async):
        task = asyncio.create_task(_upload(client))
        try:
            await asyncio.wait_for(reserved.wait(), TEST_WATCHDOG_SECONDS)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, TEST_WATCHDOG_SECONDS)
    assert not await _seeds(sqlite_instance)
    assert (await _upload(client)).status_code == 201


@pytest.mark.parametrize("method", ["POST", "DELETE", "GET"])
async def test_dataset_lifecycle_routes_require_authentication(client: AsyncClient, method: str):
    policy = AuthenticationPolicy(mode="entra", tenant_id="tenant", client_id="client", allowed_group_ids=("group",))
    with patch.object(app.state, "authentication_policy", policy):
        response = await client.request(method, URL + (f"/deletions/{uuid4()}" if method == "GET" else ""))
    assert response.status_code == 401


@pytest.mark.parametrize("interrupt", ["limit", "cancel"])
async def test_interrupted_multipart_closes_spooled_files(
    client: AsyncClient, sqlite_instance: SQLiteMemory, interrupt: str
):
    opened: list[Any] = []
    factory = tempfile.SpooledTemporaryFile

    def spool(**kwargs: Any) -> Any:
        file = factory(**kwargs)
        opened.append(file)
        return file

    prefix = b'--spool\r\nContent-Disposition: form-data; name="file"; filename="f.bin"\r\n\r\n'

    async def chunks() -> AsyncIterator[bytes]:
        yield prefix
        yield b"x" * 2048
        if interrupt == "cancel":
            raise asyncio.CancelledError
        yield b"x" * 2048

    with (
        patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool),
        patch.dict("os.environ", {"PYRIT_DATASET_MAX_REQUEST_BYTES": str(len(prefix) + 3000)}),
    ):
        if interrupt == "cancel":
            stream = chunks()

            async def receive_async() -> dict[str, Any]:
                return {"type": "http.request", "body": await anext(stream), "more_body": True}

            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": URL,
                    "headers": [(b"content-type", b"multipart/form-data; boundary=spool")],
                },
                receive=receive_async,
            )
            with pytest.raises(asyncio.CancelledError):
                await _DatasetMultipartParser(request=request, limits=DatasetUploadLimits()).parse()
        else:
            response = await client.post(
                URL, content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=spool"}
            )
            assert response.status_code == 413
    assert opened and all(file.closed for file in opened)
    assert not await _seeds(sqlite_instance)
