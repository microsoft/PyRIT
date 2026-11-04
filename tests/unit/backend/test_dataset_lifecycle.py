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
from pyrit.memory import AzureBlobStorageIO, SQLiteMemory
from pyrit.memory.memory_models import (
    DatasetLifecycleEntry,
    DatasetMediaEntry,
    PromptMemoryEntry,
    ScorableContentEntry,
    SeedEntry,
)
from pyrit.models import MessagePiece, SeedPrompt
from unit.async_utils import TEST_WATCHDOG_SECONDS

URL = "/api/datasets"


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
        {"data_type": "text", "value": "text", "seed_type": "objective"},
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


async def test_db_failure_rolls_back_seed_batch_and_owned_media(client: AsyncClient, sqlite_instance: SQLiteMemory):
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
                seeds=[{"data_type": "text", "value": "first"}, {"data_type": "binary_path", "value": "upload:file"}]
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
