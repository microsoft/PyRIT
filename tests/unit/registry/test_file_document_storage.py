# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the conditional writes and deletes of the shared document storage."""

import hashlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

from pyrit.registry.file_document_storage import (
    DocumentConflictError,
    FileDocumentStorage,
    _release_exclusive_lock,
    _try_acquire_exclusive_lock,
)

_BLOB_SOURCE = "https://account.blob.core.windows.net/documents/prefix?sp=rwd&sig=secret"


class _JsonDocuments(FileDocumentStorage):
    """Expose the protected document operations for testing."""

    def __init__(self, *, source: str) -> None:
        super().__init__(source=source, extension=".json", source_label="Test document")

    def list_documents(self) -> dict[str, bytes]:
        return self._list_documents()

    def write(self, *, name: str, content: bytes, expected_version: str | None) -> str:
        return self._save_document_conditional(name=name, content=content, expected_version=expected_version)

    def delete(self, *, name: str, expected_version: str) -> None:
        self._delete_document_conditional(name=name, expected_version=expected_version)


class _ImpatientJsonDocuments(_JsonDocuments):
    """Give up waiting for a held lock quickly."""

    LOCK_TIMEOUT_SECONDS = 0.1
    LOCK_POLL_SECONDS = 0.01


def _version(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@contextmanager
def _held_lock(path: Path) -> Iterator[bool]:
    """Take a document's lock through another open file, as a writer in another process would."""
    descriptor = os.open(path, os.O_RDWR)
    acquired = _try_acquire_exclusive_lock(descriptor)
    try:
        yield acquired
    finally:
        if acquired:
            _release_exclusive_lock(descriptor)
        os.close(descriptor)


@pytest.fixture
def local(tmp_path: Path) -> _JsonDocuments:
    return _JsonDocuments(source=str(tmp_path))


@pytest.fixture
def impatient(tmp_path: Path) -> _ImpatientJsonDocuments:
    return _ImpatientJsonDocuments(source=str(tmp_path))


def test_create_writes_document_and_returns_its_version(local: _JsonDocuments, tmp_path: Path) -> None:
    version = local.write(name="first", content=b'{"a": 1}', expected_version=None)

    assert version == _version(b'{"a": 1}')
    assert (tmp_path / "first.json").read_bytes() == b'{"a": 1}'


def test_create_conflicts_when_document_exists(local: _JsonDocuments, tmp_path: Path) -> None:
    existing = local.write(name="first", content=b"old", expected_version=None)

    with pytest.raises(DocumentConflictError, match="already exists") as raised:
        local.write(name="first", content=b"new", expected_version=None)

    assert raised.value.actual_version == existing
    assert (tmp_path / "first.json").read_bytes() == b"old"


def test_replace_with_current_version_writes_new_content(local: _JsonDocuments, tmp_path: Path) -> None:
    version = local.write(name="first", content=b"old", expected_version=None)

    new_version = local.write(name="first", content=b"new", expected_version=version)

    assert new_version == _version(b"new")
    assert (tmp_path / "first.json").read_bytes() == b"new"


def test_replace_with_stale_version_conflicts_and_keeps_document(local: _JsonDocuments, tmp_path: Path) -> None:
    stale = local.write(name="first", content=b"one", expected_version=None)
    current = local.write(name="first", content=b"two", expected_version=stale)

    with pytest.raises(DocumentConflictError, match="changed by someone else") as raised:
        local.write(name="first", content=b"three", expected_version=stale)

    assert raised.value.expected_version == stale
    assert raised.value.actual_version == current
    assert (tmp_path / "first.json").read_bytes() == b"two"


def test_replace_of_missing_document_conflicts(local: _JsonDocuments, tmp_path: Path) -> None:
    with pytest.raises(DocumentConflictError, match="no longer exists"):
        local.write(name="first", content=b"new", expected_version=_version(b"old"))

    assert not (tmp_path / "first.json").exists()


def test_hand_edit_is_detected_as_a_conflict(local: _JsonDocuments, tmp_path: Path) -> None:
    version = local.write(name="first", content=b"saved", expected_version=None)
    (tmp_path / "first.json").write_bytes(b"edited by hand")

    with pytest.raises(DocumentConflictError):
        local.write(name="first", content=b"new", expected_version=version)

    assert (tmp_path / "first.json").read_bytes() == b"edited by hand"


def test_conditional_delete_removes_document_with_current_version(local: _JsonDocuments, tmp_path: Path) -> None:
    version = local.write(name="first", content=b"saved", expected_version=None)

    local.delete(name="first", expected_version=version)

    assert not (tmp_path / "first.json").exists()


def test_conditional_delete_with_stale_version_keeps_document(local: _JsonDocuments, tmp_path: Path) -> None:
    stale = local.write(name="first", content=b"one", expected_version=None)
    current = local.write(name="first", content=b"two", expected_version=stale)

    with pytest.raises(DocumentConflictError) as raised:
        local.delete(name="first", expected_version=stale)

    assert raised.value.actual_version == current
    assert (tmp_path / "first.json").read_bytes() == b"two"


def test_conditional_delete_of_missing_document_conflicts(local: _JsonDocuments) -> None:
    with pytest.raises(DocumentConflictError, match="no longer exists"):
        local.delete(name="first", expected_version=_version(b"saved"))


@pytest.mark.parametrize("operation", ["write", "delete"])
def test_conditional_operations_reject_an_illegal_name(local: _JsonDocuments, operation: str) -> None:
    with pytest.raises(ValueError, match="Invalid registry name"):
        if operation == "write":
            local.write(name="../escape", content=b"x", expected_version=None)
        else:
            local.delete(name="../escape", expected_version=_version(b"x"))


def test_conditional_write_releases_its_lock(local: _JsonDocuments, tmp_path: Path) -> None:
    local.write(name="first", content=b"saved", expected_version=None)

    with _held_lock(tmp_path / ".first.json.lock") as acquired:
        assert acquired


def test_lock_files_are_never_listed_as_documents(local: _JsonDocuments, tmp_path: Path) -> None:
    local.write(name="first", content=b"saved", expected_version=None)

    assert (tmp_path / ".first.json.lock").is_file()
    assert local.list_documents() == {"first": b"saved"}


@pytest.mark.parametrize("operation", ["write", "delete"])
def test_conditional_operations_time_out_while_another_writer_holds_the_lock(
    impatient: _ImpatientJsonDocuments, tmp_path: Path, operation: str
) -> None:
    version = impatient.write(name="first", content=b"saved", expected_version=None)

    with _held_lock(tmp_path / ".first.json.lock") as acquired:
        assert acquired
        with pytest.raises(TimeoutError, match="another writer still holds it"):
            if operation == "write":
                impatient.write(name="first", content=b"new", expected_version=version)
            else:
                impatient.delete(name="first", expected_version=version)

    assert (tmp_path / "first.json").read_bytes() == b"saved"


def test_a_lock_file_left_by_a_writer_that_exited_does_not_block(
    impatient: _ImpatientJsonDocuments, tmp_path: Path
) -> None:
    version = impatient.write(name="first", content=b"saved", expected_version=None)
    # A writer that exited leaves the file behind, but the operating system released its lock.
    (tmp_path / ".first.json.lock").write_text("1")

    impatient.write(name="first", content=b"new", expected_version=version)

    assert (tmp_path / "first.json").read_bytes() == b"new"


def test_concurrent_replacements_from_one_version_allow_exactly_one_writer(local: _JsonDocuments) -> None:
    version = local.write(name="first", content=b"original", expected_version=None)
    start = threading.Barrier(8)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def replace(index: int) -> None:
        start.wait()
        try:
            local.write(name="first", content=f"writer {index}".encode(), expected_version=version)
            result = "written"
        except DocumentConflictError:
            result = "conflict"
        with outcomes_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=replace, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict"] * 7 + ["written"]


@pytest.fixture
def blob_client() -> Iterator[MagicMock]:
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
        yield client


def _stored_blob(client: MagicMock, *, content: bytes, etag: str) -> None:
    client.download_blob.return_value.readall.return_value = content
    client.download_blob.return_value.properties.etag = etag


def test_blob_create_refuses_to_overwrite(blob_client: MagicMock) -> None:
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    version = storage.write(name="first", content=b"new", expected_version=None)

    blob_client.upload_blob.assert_called_once_with(name="prefix/first.json", data=b"new", overwrite=False)
    assert version == _version(b"new")


def test_blob_create_of_existing_blob_is_a_conflict(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"old", etag="etag-1")
    blob_client.upload_blob.side_effect = ResourceExistsError("exists")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError, match="already exists") as raised:
        storage.write(name="first", content=b"new", expected_version=None)

    assert raised.value.actual_version == _version(b"old")


def test_blob_replace_sends_the_etag_of_the_compared_bytes(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"old", etag="etag-1")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    storage.write(name="first", content=b"new", expected_version=_version(b"old"))

    blob_client.upload_blob.assert_called_once_with(
        name="prefix/first.json",
        data=b"new",
        overwrite=True,
        etag="etag-1",
        match_condition=MatchConditions.IfNotModified,
    )


def test_blob_replace_with_stale_version_does_not_upload(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"newer", etag="etag-2")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError) as raised:
        storage.write(name="first", content=b"mine", expected_version=_version(b"old"))

    assert raised.value.actual_version == _version(b"newer")
    blob_client.upload_blob.assert_not_called()


def test_blob_replace_that_loses_a_race_is_a_conflict(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"old", etag="etag-1")
    blob_client.upload_blob.side_effect = ResourceModifiedError("precondition failed")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError):
        storage.write(name="first", content=b"new", expected_version=_version(b"old"))


def test_blob_replace_of_missing_blob_is_a_conflict(blob_client: MagicMock) -> None:
    blob_client.download_blob.side_effect = ResourceNotFoundError("missing")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError, match="no longer exists"):
        storage.write(name="first", content=b"new", expected_version=_version(b"old"))

    blob_client.upload_blob.assert_not_called()


def test_blob_delete_sends_the_etag_of_the_compared_bytes(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"saved", etag="etag-1")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    storage.delete(name="first", expected_version=_version(b"saved"))

    blob_client.delete_blob.assert_called_once_with(
        "prefix/first.json", etag="etag-1", match_condition=MatchConditions.IfNotModified
    )


def test_blob_delete_with_stale_version_does_not_delete(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"newer", etag="etag-2")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError):
        storage.delete(name="first", expected_version=_version(b"saved"))

    blob_client.delete_blob.assert_not_called()


def test_blob_delete_that_loses_a_race_reports_the_stored_version(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"saved", etag="etag-1")
    blob_client.delete_blob.side_effect = ResourceModifiedError("precondition failed")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError) as raised:
        storage.delete(name="first", expected_version=_version(b"saved"))

    assert raised.value.actual_version == _version(b"saved")


def test_blob_delete_of_a_blob_removed_by_another_writer_is_a_conflict(blob_client: MagicMock) -> None:
    _stored_blob(blob_client, content=b"saved", etag="etag-1")
    blob_client.download_blob.side_effect = [blob_client.download_blob.return_value, ResourceNotFoundError("gone")]
    blob_client.delete_blob.side_effect = ResourceNotFoundError("deleted meanwhile")
    storage = _JsonDocuments(source=_BLOB_SOURCE)

    with pytest.raises(DocumentConflictError) as raised:
        storage.delete(name="first", expected_version=_version(b"saved"))

    assert raised.value.actual_version is None
