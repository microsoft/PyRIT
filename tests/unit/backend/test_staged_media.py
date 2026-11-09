# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for expiring attachment staging and promotion."""

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from filelock import FileLock, Timeout

from pyrit.backend.services.media_persistence import MediaOrigin, persist_media_value_async
from pyrit.backend.services.staged_media import StagedMediaStore, staged_media_store
from pyrit.memory import DataTypeSerializer
from unit.async_utils import TEST_WATCHDOG_SECONDS


@pytest.mark.parametrize("operation", ["write", "read", "cleanup"])
def test_stores_share_the_lock_across_processes(tmp_path: Path, operation: str) -> None:
    store = StagedMediaStore(tmp_path)
    value = store._write(b"shared bytes")
    path = tmp_path / f"{value}.upload"
    if operation == "cleanup":
        expired_time = time.time() - store.RETENTION_SECONDS
        os.utime(path, (expired_time, expired_time))
    marker = tmp_path / "lock-attempt"
    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from uuid import UUID
        from filelock import FileLock
        from pyrit.backend.services.staged_media import StagedMediaStore
        root, operation, token = sys.argv[1:]
        directory = Path(root)
        acquire = FileLock.acquire
        def observed_acquire(self, *args, **kwargs):
            marker = directory / "lock-attempt.tmp"
            marker.write_text(str(self.lock_file))
            marker.replace(directory / "lock-attempt")
            return acquire(self, *args, **kwargs)
        with patch.object(FileLock, "acquire", observed_acquire):
            store = StagedMediaStore(directory)
            if operation == "write":
                assert store._write(b"shared bytes") == UUID(token)
            elif operation == "read":
                assert store._read(UUID(token)) == b"shared bytes"
            else:
                store._cleanup_expired()
    """)
    process: subprocess.Popen[str] | None = None
    try:
        with FileLock(tmp_path / ".staging.lock"):
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(tmp_path), operation, str(value)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + TEST_WATCHDOG_SECONDS
            while not marker.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    pytest.fail(f"Store did not acquire the shared lock: {stdout} {stderr}")
                if time.monotonic() >= deadline:
                    pytest.fail("Store did not attempt the shared lock before the watchdog expired")
                time.sleep(0.01)
            assert Path(marker.read_text()) == tmp_path / ".staging.lock"
        stdout, stderr = process.communicate(timeout=TEST_WATCHDOG_SECONDS)
        assert process.returncode == 0, f"{stdout} {stderr}"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=TEST_WATCHDOG_SECONDS)
    if operation == "cleanup":
        assert not path.exists()
    else:
        assert path.read_bytes() == b"shared bytes"


async def test_staging_mutations_hold_the_shared_directory_lock_async(tmp_path: Path) -> None:
    store = StagedMediaStore(tmp_path)
    original_replace = Path.replace
    original_stat = Path.stat
    original_unlink = Path.unlink
    checked: set[str] = set()

    def assert_locked(operation: str) -> None:
        with pytest.raises(Timeout):
            FileLock(tmp_path / ".staging.lock").acquire(timeout=0)
        checked.add(operation)

    def replace_locked(path: Path, target: str | Path) -> Path:
        if path.suffix == ".upload":
            assert_locked("replace")
        return original_replace(path, target)

    def stat_locked(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path.suffix == ".upload":
            assert_locked("stat")
        return original_stat(path, follow_symlinks=follow_symlinks)

    def unlink_locked(path: Path, *, missing_ok: bool = False) -> None:
        if path.suffix == ".upload":
            assert_locked("unlink")
        original_unlink(path, missing_ok=missing_ok)

    with patch.object(Path, "replace", replace_locked):
        value = await store.stage_async(b"same draft")
    path = tmp_path / f"{value.removeprefix(store.PREFIX)}.upload"
    with patch.object(Path, "stat", stat_locked):
        assert await StagedMediaStore(tmp_path).read_async(value) == b"same draft"
    expired_time = time.time() - store.RETENTION_SECONDS
    os.utime(path, (expired_time, expired_time))
    with patch.object(Path, "stat", stat_locked), patch.object(Path, "unlink", unlink_locked):
        await StagedMediaStore(tmp_path).cleanup_expired_async()
    assert checked == {"replace", "stat", "unlink"}
    assert not path.exists()
    assert await store.stage_async(b"same draft") == value
    assert await store.read_async(value) == b"same draft"


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


@pytest.mark.parametrize(
    "value", ["pyrit-upload:../../secret", "pyrit-upload:invalid", "122e8b99-d73e-47b9-a737-12c58fde5479"]
)
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
