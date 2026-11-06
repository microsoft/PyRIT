# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for expiring attachment staging and promotion."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from pyrit.backend.services.media_persistence import MediaOrigin, persist_media_value_async
from pyrit.backend.services.staged_media import StagedMediaStore, staged_media_store
from pyrit.memory import DataTypeSerializer
from unit.async_utils import TEST_WATCHDOG_SECONDS


async def test_abandoned_uploads_expire_across_store_restarts_async(tmp_path: Path) -> None:
    store = StagedMediaStore(tmp_path)
    expired = await store.stage_async(b"abandoned")
    live = await store.stage_async(b"retained draft")
    old_path = tmp_path / f"{expired.removeprefix(store.PREFIX)}.upload"
    expired_time = time.time() - store.RETENTION_SECONDS
    os.utime(old_path, (expired_time, expired_time))
    unrelated = tmp_path / "unrelated.upload"
    unrelated.write_bytes(b"not owned")

    restarted = StagedMediaStore(tmp_path)
    await restarted.cleanup_expired_async()

    assert not old_path.exists()
    assert unrelated.read_bytes() == b"not owned"
    assert await restarted.read_async(live) == b"retained draft"
    with pytest.raises(ValueError, match="expired"):
        await restarted.read_async(expired)


async def test_expired_upload_cannot_be_read_before_sweep_async(tmp_path: Path) -> None:
    store = StagedMediaStore(tmp_path)
    value = await store.stage_async(b"old")
    with patch("pyrit.backend.services.staged_media.time.time", return_value=time.time() + store.RETENTION_SECONDS + 1):
        with pytest.raises(ValueError, match="expired"):
            await store.read_async(value)


async def test_refresh_keeps_reference_stable_for_save_idempotency_async(tmp_path: Path) -> None:
    store = StagedMediaStore(tmp_path)
    original = await store.stage_async(b"same draft file")
    path = tmp_path / f"{original.removeprefix(store.PREFIX)}.upload"
    expired_time = time.time() - store.RETENTION_SECONDS
    os.utime(path, (expired_time, expired_time))
    await store.cleanup_expired_async()

    refreshed = await StagedMediaStore(tmp_path).stage_async(b"same draft file")

    assert refreshed == original
    assert await store.read_async(refreshed) == b"same draft file"
    assert len(list(tmp_path.glob("*.upload"))) == 1


@pytest.mark.parametrize("value", ["pyrit-upload:../../secret", "pyrit-upload:invalid", str(uuid4())])
async def test_upload_references_cannot_name_arbitrary_files_async(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="Invalid attachment upload"):
        await StagedMediaStore(tmp_path).read_async(value)


async def test_sweep_runs_without_further_uploads_and_stops_on_cancel_async(tmp_path: Path) -> None:
    store = StagedMediaStore(tmp_path)
    swept = asyncio.Event()

    async def sweep_async() -> None:
        swept.set()

    with patch.object(store, "cleanup_expired_async", side_effect=sweep_async) as sweep:
        task = asyncio.create_task(store.run_cleanup_async())
        try:
            await asyncio.wait_for(swept.wait(), timeout=TEST_WATCHDOG_SECONDS)
            sweep.assert_called_once()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_sweep_reports_storage_errors_and_keeps_running_async(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = StagedMediaStore(tmp_path)
    with (
        patch.object(store, "cleanup_expired_async", AsyncMock(side_effect=OSError("permission denied"))),
        patch("pyrit.backend.services.staged_media.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        pytest.raises(asyncio.CancelledError),
    ):
        await store.run_cleanup_async()
    assert "Could not clean up expired attachment uploads" in caplog.text


async def test_staged_upload_promotes_through_configured_results_serializer_async(tmp_path: Path) -> None:
    serializer = MagicMock(spec=DataTypeSerializer)
    serializer.value = "https://account.blob.core.windows.net/results/prompt-memory-entries/claimed.png"
    serializer.get_data_filename_async = AsyncMock(return_value=serializer.value)
    serializer.save_b64_image_async = AsyncMock()
    factory = MagicMock(return_value=serializer)
    with patch.object(staged_media_store, "_directory", tmp_path):
        value = await staged_media_store.stage_async(b"image bytes")
        created: list[str] = []
        result = await persist_media_value_async(
            value=value,
            data_type="image_path",
            mime_type="image/png",
            serializer_factory=factory,
            created_paths=created,
        )
        assert result.origin is MediaOrigin.STAGED_UPLOAD
        assert result.value == serializer.value
        assert created == [serializer.value]
        assert serializer.save_b64_image_async.call_args.kwargs["data"] == "aW1hZ2UgYnl0ZXM="
        # A failed message save can remove the promoted copy and still retry the upload.
        assert await staged_media_store.read_async(value) == b"image bytes"
        upload_path = next(tmp_path.glob("*.upload"))
        expired_time = time.time() - staged_media_store.RETENTION_SECONDS
        os.utime(upload_path, (expired_time, expired_time))
        await staged_media_store.cleanup_expired_async()
        serializer.save_b64_image_async.assert_awaited_once()
        assert not upload_path.exists()
