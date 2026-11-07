# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario suite storage."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from pyrit.models.catalog.scenario_suite import SUITE_SCHEMA_VERSION, ScenarioSuite, ScenarioSuiteEntry
from pyrit.registry.scenario_suite_storage import ScenarioSuiteConflictError, ScenarioSuiteStorage

ENTRY_ONE = UUID("11111111-1111-4111-8111-111111111111")
ENTRY_TWO = UUID("22222222-2222-4222-8222-222222222222")


def _suite(*, name: str = "weekly", description: str | None = None) -> ScenarioSuite:
    """Build a suite with duplicate preset references in a known order."""
    return ScenarioSuite(
        name=name,
        description=description,
        author="Ada Lovelace",
        entries=[
            ScenarioSuiteEntry(entry_id=ENTRY_TWO, preset_name="nightly"),
            ScenarioSuiteEntry(entry_id=ENTRY_ONE, preset_name="nightly"),
        ],
    )


def test_local_storage_round_trips_order_duplicates_and_author(tmp_path: Path) -> None:
    """Test the complete stored document contract."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))

    saved = storage.save_suite(suite=_suite(), expected_version=None)
    loaded = storage.load_suite("weekly")

    assert loaded is not None
    assert loaded.version == saved.version
    assert loaded.suite.author == "Ada Lovelace"
    assert [entry.entry_id for entry in loaded.suite.entries] == [ENTRY_TWO, ENTRY_ONE]
    assert [entry.preset_name for entry in loaded.suite.entries] == ["nightly", "nightly"]


def test_stored_shape_omits_name_and_version_but_records_schema(tmp_path: Path) -> None:
    """Test that the document key and concurrency token stay outside the payload."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))

    storage.save_suite(suite=_suite(), expected_version=None)
    payload = json.loads((tmp_path / "weekly.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == SUITE_SCHEMA_VERSION
    assert "name" not in payload
    assert "version" not in payload


def test_stale_update_is_rejected_without_overwriting_winner(tmp_path: Path) -> None:
    """Test optimistic concurrency for suite edits."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    created = storage.save_suite(suite=_suite(description="original"), expected_version=None)
    winner = storage.save_suite(suite=_suite(description="winner"), expected_version=created.version)

    with pytest.raises(ScenarioSuiteConflictError) as caught:
        storage.save_suite(suite=_suite(description="stale"), expected_version=created.version)

    assert caught.value.actual_version == winner.version
    loaded = storage.load_suite("weekly")
    assert loaded is not None
    assert loaded.suite.description == "winner"


def test_create_over_malformed_document_conflicts_and_recovery_uses_version(tmp_path: Path) -> None:
    """Test that malformed user content is never discarded without a version check."""
    path = tmp_path / "weekly.json"
    path.write_text("{not json", encoding="utf-8")
    storage = ScenarioSuiteStorage(source=str(tmp_path))

    with pytest.raises(ScenarioSuiteConflictError):
        storage.save_suite(suite=_suite(), expected_version=None)

    version = storage.get_suite_version("weekly")
    assert version is not None
    storage.save_suite(suite=_suite(), expected_version=version)
    assert storage.load_suite("weekly") is not None


def test_failed_atomic_replace_preserves_previous_suite(tmp_path: Path) -> None:
    """Test that a failed write leaves the previous complete document readable."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    created = storage.save_suite(suite=_suite(description="original"), expected_version=None)

    with patch("os.replace", side_effect=OSError(28, "No space left on device")):
        with pytest.raises(OSError):
            storage.save_suite(suite=_suite(description="replacement"), expected_version=created.version)

    loaded = storage.load_suite("weekly")
    assert loaded is not None
    assert loaded.suite.description == "original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_local_lock_excludes_a_second_writer(tmp_path: Path) -> None:
    """Test that the version check and replacement remain one locked operation."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    created = storage.save_suite(suite=_suite(description="original"), expected_version=None)
    competitor = ScenarioSuiteStorage(source=str(tmp_path))
    real_replace = ScenarioSuiteStorage._replace_file

    def replace_after_competitor_times_out(*, path: Path, content: bytes) -> None:
        with pytest.raises(TimeoutError):
            competitor.save_suite(suite=_suite(description="racer"), expected_version=created.version)
        real_replace(path=path, content=content)

    with (
        patch.object(ScenarioSuiteStorage, "LOCK_TIMEOUT_SECONDS", 0.1),
        patch.object(ScenarioSuiteStorage, "_replace_file", staticmethod(replace_after_competitor_times_out)),
    ):
        storage.save_suite(suite=_suite(description="winner"), expected_version=created.version)

    loaded = storage.load_suite("weekly")
    assert loaded is not None
    assert loaded.suite.description == "winner"


def test_list_and_delete_suites(tmp_path: Path) -> None:
    """Test the storage lifecycle for multiple suites."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    storage.save_suite(suite=_suite(name="weekly"), expected_version=None)
    storage.save_suite(suite=_suite(name="nightly_suite"), expected_version=None)

    assert sorted(storage.list_suites()) == ["nightly_suite", "weekly"]
    assert storage.delete_suite("weekly") is True
    assert storage.delete_suite("weekly") is False


def test_default_source_is_under_configuration_directory(tmp_path: Path) -> None:
    """Test the standard ``~/.pyrit/scenario_suites`` location."""
    with patch("pyrit.common.path.CONFIGURATION_DIRECTORY_PATH", tmp_path):
        storage = ScenarioSuiteStorage()
        storage.save_suite(suite=_suite(), expected_version=None)

    assert (tmp_path / "scenario_suites" / "weekly.json").is_file()


def test_blob_storage_lists_and_updates_suite_documents() -> None:
    """Test that the adapter uses the shared Azure Blob document backend."""
    content = ScenarioSuiteStorage._serialize_suite(_suite())
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
    storage = ScenarioSuiteStorage(source="https://account.blob.core.windows.net/suites?sp=rwd&sig=secret")

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        suites = storage.list_suites()
        storage.save_suite(suite=_suite(description="updated"), expected_version=suites["weekly"].version)

    assert list(suites) == ["weekly"]
    assert storage.display_source == "https://account.blob.core.windows.net/suites"
    assert client.upload_blob.call_args.kwargs["name"] == "weekly.json"


@pytest.mark.parametrize("name", ["../victim", "..\\victim", "nested/victim", "Weekly", ""])
def test_document_operations_reject_illegal_names(tmp_path: Path, name: str) -> None:
    """Test that suite names cannot escape the configured source."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))

    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.load_suite(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.delete_suite(name)
    with pytest.raises(ValueError, match="Invalid registry name"):
        storage.get_suite_source(name)


def test_document_name_is_authoritative(tmp_path: Path) -> None:
    """Test that a payload cannot redirect a later save to another key."""
    payload = json.loads(ScenarioSuiteStorage._serialize_suite(_suite()).decode("utf-8"))
    payload["name"] = "different"
    (tmp_path / "actual.json").write_text(json.dumps(payload), encoding="utf-8")
    storage = ScenarioSuiteStorage(source=str(tmp_path))

    loaded = storage.load_suite("actual")

    assert loaded is not None
    assert loaded.suite.name == "actual"


def test_writer_does_not_leave_temporary_files(tmp_path: Path) -> None:
    """Test successful replacement cleanup on the local backend."""
    storage = ScenarioSuiteStorage(source=str(tmp_path))
    created = storage.save_suite(suite=_suite(), expected_version=None)

    storage.save_suite(suite=_suite(description="updated"), expected_version=created.version)

    assert not any(path.suffix == ".tmp" for path in tmp_path.iterdir())
    assert os.path.isfile(tmp_path / "weekly.json")
