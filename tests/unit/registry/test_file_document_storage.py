# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ServiceRequestError
from azure.storage.blob import ContainerClient, StorageErrorCode

from pyrit.registry.file_document_storage import DocumentListing, FileDocumentStorage, UnreadableDocument

_SAS_SOURCE = "https://account.blob.core.windows.net/documents/saved?sp=rl&sig=secret-signature"


def _local_storage(directory: Path) -> FileDocumentStorage:
    return FileDocumentStorage(source=str(directory), extension=".json", source_label="Test documents")


def _blob_storage() -> FileDocumentStorage:
    return FileDocumentStorage(source=_SAS_SOURCE, extension=".json", source_label="Test documents")


def _blob_client(*, names: list[str]) -> MagicMock:
    client = MagicMock(spec=ContainerClient)
    client.__enter__.return_value = client
    client.list_blobs.return_value = [SimpleNamespace(name=name) for name in names]
    return client


class TestLocalListingWithIssues:
    def test_reports_unreadable_document_and_keeps_the_others(self, tmp_path: Path) -> None:
        (tmp_path / "first.json").write_bytes(b"one")
        (tmp_path / "second.json").write_bytes(b"two")
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path.name == "second.json":
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
            listing = _local_storage(tmp_path)._list_documents_with_issues()
            legacy = _local_storage(tmp_path)._list_documents()

        assert listing == DocumentListing(
            documents={"first": b"one"},
            unreadable=(UnreadableDocument(name="second", message="Permission denied"),),
        )
        assert legacy == {"first": b"one"}

    def test_reports_badly_named_files_as_ignored_without_reading_them(self, tmp_path: Path) -> None:
        (tmp_path / "valid_name.json").write_bytes(b"ok")
        (tmp_path / "Bad-Name.json").write_bytes(b"never read")
        (tmp_path / "notes.txt").write_bytes(b"other extension")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.json").write_bytes(b"not a direct child")
        read_names: list[str] = []
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            read_names.append(path.name)
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.documents == {"valid_name": b"ok"}
        assert listing.unreadable == ()
        assert [issue.file_name for issue in listing.ignored] == ["Bad-Name.json"]
        assert "Bad-Name" in listing.ignored[0].message
        assert read_names == ["valid_name.json"]
        assert _local_storage(tmp_path)._list_documents() == listing.documents

    def test_skips_a_document_deleted_after_listing(self, tmp_path: Path) -> None:
        (tmp_path / "kept.json").write_bytes(b"kept")
        (tmp_path / "deleted.json").write_bytes(b"gone")
        original_read_bytes = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            if path.name == "deleted.json":
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(documents={"kept": b"kept"})

    def test_reports_entries_that_are_not_regular_files_without_reading_them(self, tmp_path: Path) -> None:
        (tmp_path / "folder.json").mkdir()
        try:
            (tmp_path / "broken_link.json").symlink_to(tmp_path / "missing_target.json")
        except OSError:
            pytest.skip("Creating symbolic links isn't permitted here")

        with patch.object(Path, "read_bytes", autospec=True) as read_bytes:
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.unreadable == (
            UnreadableDocument(name="broken_link", message="Not a regular file"),
            UnreadableDocument(name="folder", message="Not a regular file"),
        )
        read_bytes.assert_not_called()

    def test_reports_a_link_loop_and_keeps_the_others(self, tmp_path: Path) -> None:
        (tmp_path / "good.json").write_bytes(b"ok")
        try:
            (tmp_path / "loop.json").symlink_to(tmp_path / "loop.json")
        except OSError:
            pytest.skip("Creating symbolic links isn't permitted here")

        listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.documents == {"good": b"ok"}
        assert [issue.name for issue in listing.unreadable] == ["loop"]

    def test_matches_extensions_like_the_legacy_listing(self, tmp_path: Path) -> None:
        (tmp_path / "lower.json").write_bytes(b"one")
        (tmp_path / "upper.JSON").write_bytes(b"two")

        storage = _local_storage(tmp_path)

        assert storage._list_documents_with_issues().documents == storage._list_documents()

    def test_creates_missing_directory_and_lists_nothing(self, tmp_path: Path) -> None:
        listing = _local_storage(tmp_path / "missing")._list_documents_with_issues()

        assert listing == DocumentListing()
        assert (tmp_path / "missing").is_dir()

    def test_raises_when_the_directory_cannot_be_listed(self, tmp_path: Path) -> None:
        (tmp_path / "first.json").write_bytes(b"one")

        with patch("pyrit.registry.file_document_storage.os.scandir", side_effect=PermissionError(13, "denied")):
            with pytest.raises(PermissionError):
                _local_storage(tmp_path)._list_documents_with_issues()


class TestBlobListingWithIssues:
    def test_reports_unreadable_and_ignored_blobs_without_exposing_the_url(self) -> None:
        client = _blob_client(
            names=[
                "saved/good.json",
                "saved/broken.json",
                "saved/Bad-Name.json",
                "saved/notes.txt",
                "saved/deeper/inner.json",
            ]
        )

        def download_blob(blob_name: str) -> SimpleNamespace:
            if blob_name == "saved/broken.json":
                error = HttpResponseError(message=f"Forbidden: {_SAS_SOURCE}")
                error.status_code = 403
                raise error
            return SimpleNamespace(readall=lambda: b"content")

        client.download_blob.side_effect = download_blob

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            listing = _blob_storage()._list_documents_with_issues()

        assert listing.documents == {"good": b"content"}
        assert listing.unreadable == (UnreadableDocument(name="broken", message="HttpResponseError (403)"),)
        assert [issue.file_name for issue in listing.ignored] == ["Bad-Name.json"]
        assert all("secret-signature" not in issue.message for issue in (*listing.unreadable, *listing.ignored))
        client.list_blobs.assert_called_once_with(name_starts_with="saved/")
        downloaded = [call.args[0] for call in client.download_blob.call_args_list]
        assert downloaded == ["saved/broken.json", "saved/good.json"]

    def test_skips_only_a_blob_deleted_after_listing(self) -> None:
        client = _blob_client(names=["saved/kept.json", "saved/deleted.json", "saved/container_gone.json"])
        error_codes = {
            "saved/deleted.json": StorageErrorCode.BLOB_NOT_FOUND,
            "saved/container_gone.json": StorageErrorCode.CONTAINER_NOT_FOUND,
        }

        def download_blob(blob_name: str) -> SimpleNamespace:
            if blob_name in error_codes:
                error = ResourceNotFoundError(message="Not found.")
                error.status_code = 404
                error.error_code = error_codes[blob_name]
                raise error
            return SimpleNamespace(readall=lambda: b"kept")

        client.download_blob.side_effect = download_blob

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            listing = _blob_storage()._list_documents_with_issues()

        assert listing == DocumentListing(
            documents={"kept": b"kept"},
            unreadable=(
                UnreadableDocument(name="container_gone", message="ResourceNotFoundError (404, ContainerNotFound)"),
            ),
        )

    def test_raises_when_the_container_cannot_be_listed(self) -> None:
        def failing_pages() -> Iterator[SimpleNamespace]:
            raise HttpResponseError(message="listing failed")
            yield SimpleNamespace(name="saved/never.json")

        client = _blob_client(names=[])
        client.list_blobs.return_value = failing_pages()

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(HttpResponseError):
                _blob_storage()._list_documents_with_issues()


class TestDescribeReadError:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (PermissionError(13, "Permission denied", "/server/path/secret.json"), "Permission denied"),
            (OSError(), "OSError"),
            (ServiceRequestError(message="https://account.blob.core.windows.net/x?sig=secret"), "ServiceRequestError"),
        ],
    )
    def test_describes_errors_without_their_details(self, error: Exception, expected: str) -> None:
        assert FileDocumentStorage._describe_read_error(error) == expected

    @pytest.mark.parametrize(
        ("error_code", "expected"),
        [
            (StorageErrorCode.AUTHORIZATION_FAILURE, "HttpResponseError (403, AuthorizationFailure)"),
            ("AuthorizationPermissionMismatch", "HttpResponseError (403, AuthorizationPermissionMismatch)"),
        ],
    )
    def test_includes_the_azure_status_and_error_code(self, error_code: str, expected: str) -> None:
        error = HttpResponseError(message="https://account.blob.core.windows.net/x?sig=secret")
        error.status_code = 403
        error.error_code = error_code

        assert FileDocumentStorage._describe_read_error(error) == expected
