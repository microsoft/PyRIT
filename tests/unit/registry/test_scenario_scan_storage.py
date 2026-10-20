# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario scan storage."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from pyrit.models.catalog.scenario_scan import SCAN_SCHEMA_VERSION, ScenarioScan, ScenarioScanEntry
from pyrit.registry.scenario_scan_storage import ScenarioScanConflictError, ScenarioScanStorage

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _scan(*, name: str = "weekly", description: str | None = None) -> ScenarioScan:
    """Build a scan with duplicate preset references in a known order."""
    return ScenarioScan(
        name=name,
        description=description,
        author="Ada Lovelace",
        entries=[
            ScenarioScanEntry(entry_id=ENTRY_TWO, preset_name="nightly"),
            ScenarioScanEntry(entry_id=ENTRY_ONE, preset_name="nightly"),
        ],
    )


def test_local_storage_round_trips_order_duplicates_and_author(tmp_path: Path) -> None:
    """Test the complete stored document contract."""
    storage = ScenarioScanStorage(source=str(tmp_path))

    saved = storage.save_scan(scan=_scan(), expected_version=None)
    loaded = storage.load_scan("weekly")

    assert loaded is not None
    assert loaded.version == saved.version
    assert loaded.scan.author == "Ada Lovelace"
    assert [entry.entry_id for entry in loaded.scan.entries] == [ENTRY_TWO, ENTRY_ONE]
    assert [entry.preset_name for entry in loaded.scan.entries] == ["nightly", "nightly"]


def test_stored_shape_omits_name_and_version_but_records_schema(tmp_path: Path) -> None:
    """Test that the document key and concurrency token stay outside the payload."""
    storage = ScenarioScanStorage(source=str(tmp_path))

    storage.save_scan(scan=_scan(), expected_version=None)
    payload = json.loads((tmp_path / "weekly.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == SCAN_SCHEMA_VERSION
    assert "name" not in payload
    assert "version" not in payload


def test_stale_update_is_rejected_without_overwriting_winner(tmp_path: Path) -> None:
    """Test optimistic concurrency for scan edits."""
    storage = ScenarioScanStorage(source=str(tmp_path))
    created = storage.save_scan(scan=_scan(description="original"), expected_version=None)
    winner = storage.save_scan(scan=_scan(description="winner"), expected_version=created.version)

    with pytest.raises(ScenarioScanConflictError) as caught:
        storage.save_scan(scan=_scan(description="stale"), expected_version=created.version)

    assert caught.value.actual_version == winner.version
    loaded = storage.load_scan("weekly")
    assert loaded is not None
    assert loaded.scan.description == "winner"


def test_create_over_malformed_document_conflicts_and_recovery_uses_version(tmp_path: Path) -> None:
    """Test that malformed user content is never discarded without a version check."""
    path = tmp_path / "weekly.json"
    path.write_text("{not json", encoding="utf-8")
    storage = ScenarioScanStorage(source=str(tmp_path))

    with pytest.raises(ScenarioScanConflictError):
        storage.save_scan(scan=_scan(), expected_version=None)

    version = storage.get_scan_version("weekly")
    assert version is not None
    storage.save_scan(scan=_scan(), expected_version=version)
    assert storage.load_scan("weekly") is not None


def test_failed_atomic_replace_preserves_previous_scan(tmp_path: Path) -> None:
    """Test that a failed write leaves the previous complete document readable."""
    storage = ScenarioScanStorage(source=str(tmp_path))
    created = storage.save_scan(scan=_scan(description="original"), expected_version=None)

    with patch("os.replace", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(OSError):
            storage.save_scan(scan=_scan(description="replacement"), expected_version=created.version)

    loaded = storage.load_scan("weekly")
    assert loaded is not None
    assert loaded.scan.description == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_local_lock_excludes_a_second_writer(tmp_path: Path) -> None:
    """Test that the version check and replacement remain one locked operation."""
    storage = ScenarioScanStorage(source=str(tmp_path))
    created = storage.save_scan(scan=_scan(description="original"), expected_version=None)
    competitor = ScenarioScanStorage(source=str(tmp_path))
    real_replace = ScenarioScanStorage._replace_file

    def replace_after_competitor_times_out(*, path: Path, content: bytes) -> None:
        with pytest.raises(TimeoutError):
            competitor.save_scan(scan=_scan(description="racer"), expected_version=created.version)
        real_replace(path=path, content=content)

    with (
        patch.object(ScenarioScanStorage, "LOCK_TIMEOUT_SECONDS", 0.1),
        patch.object(ScenarioScanStorage, "_replace_file", staticmethod(replace_after_competitor_times_out)),
    ):
        storage.save_scan(scan=_scan(description="winner"), expected_version=created.version)

    loaded = storage.load_scan("weekly")
    assert loaded is not None
    assert loaded.scan.description == "winner"


def test_list_and_delete_scans(tmp_path: Path) -> None:
    """Test the storage lifecycle for multiple scans."""
    storage = ScenarioScanStorage(source=str(tmp_path))
    storage.save_scan(scan=_scan(name="weekly"), expected_version=None)
    storage.save_scan(scan=_scan(name="nightly_scan"), expected_version=None)

    assert sorted(storage.list_scans()) == ["nightly_scan", "weekly"]
    assert storage.delete_scan("weekly") is True
    assert storage.delete_scan("weekly") is False


def test_default_source_is_under_configuration_directory(tmp_path: Path) -> None:
    """Test the standard ``~/.pyrit/scenario_scans`` location."""
    with patch("pyrit.common.path.CONFIGURATION_DIRECTORY_PATH", tmp_path):
        storage = ScenarioScanStorage()
        storage.save_scan(scan=_scan(), expected_version=None)

    assert (tmp_path / "scenario_scans" / "weekly.json").is_file()


def test_blob_storage_lists_and_updates_scan_documents() -> None:
    """Test that the adapter uses the shared Azure Blob document backend."""
    content = ScenarioScanStorage._serialize_scan(_scan())
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [
        SimpleNamespace(name="weekly.json"),
        SimpleNamespace(name="notes.txt"),
        SimpleNamespace(name="archive/ignored.json"),
    ]
    downloader = MagicMock()
    downloader.readall.return_value = content
    downloader.properties.etag = "etag"
    client.download_blob.return_value = downloader
    storage = ScenarioScanStorage(source="https://account.blob.core.windows.net/scans?sp=rwd&sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        scans = storage.list_scans()
        storage.save_scan(scan=_scan(description="updated"), expected_version=scans["weekly"].version)

    assert list(scans) == ["weekly"]
    assert storage.display_source == "https://account.blob.core.windows.net/scans"
    assert client.upload_blob.call_args.kwargs["name"] == "weekly.json"


@pytest.mark.parametrize("name", ["../victim", "..\\victim", "nested/victim", "Weekly", ""])
def test_document_operations_reject_illegal_names(tmp_path: Path, name: str) -> None:
    """Test that scan names cannot escape the configured source."""
    storage = ScenarioScanStorage(source=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.load_scan(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.delete_scan(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.get_scan_source(name)


def test_document_name_is_authoritative(tmp_path: Path) -> None:
    """Test that a payload cannot redirect a later save to another key."""
    payload = json.loads(ScenarioScanStorage._serialize_scan(_scan()).decode("utf-8"))
    payload["name"] = "different"
    (tmp_path / "actual.json").write_text(json.dumps(payload), encoding="utf-8")
    storage = ScenarioScanStorage(source=str(tmp_path))

    loaded = storage.load_scan("actual")

    assert loaded is not None
    assert loaded.scan.name == "actual"


def test_writer_does_not_leave_temporary_files(tmp_path: Path) -> None:
    """Test successful replacement cleanup on the local backend."""
    storage = ScenarioScanStorage(source=str(tmp_path))
    created = storage.save_scan(scan=_scan(), expected_version=None)

    storage.save_scan(scan=_scan(description="updated"), expected_version=created.version)

    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())
    assert os.path.isfile(tmp_path / "weekly.json")
