# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.storage.blob import ContainerClient, StorageErrorCode

import pyrit.registry.file_document_storage as storage_module
from pyrit.registry.file_document_storage import (
    DocumentConflictError,
    DocumentListing,
    DocumentStorageUnavailableError,
    DocumentUnreadableError,
    FileDocumentStorage,
    UnreadableDocument,
    _read_regular_file,
    describe_storage_error,
)

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


def _blob_error(
    *, status_code: int, error_code: str, message: str = "Storage error.", missing: bool = False
) -> HttpResponseError:
    error_type = ResourceNotFoundError if missing else HttpResponseError
    error = error_type(message=message)
    error.status_code = status_code
    error.error_code = error_code
    return error


def _count_open_files() -> int | None:
    # Linux lists this process's open files here; elsewhere the count is skipped.
    descriptors = Path("/proc/self/fd")
    return len(list(descriptors.iterdir())) if descriptors.is_dir() else None


@contextmanager
def _change_after_listing(*, change: Callable[[], None], cache_types: bool = False) -> Generator[None, None, None]:
    # Runs `change` after the folder is listed and before its entries are read. With `cache_types`,
    # each entry's type is looked up first, so it is out of date once `change` runs.
    real_scandir = os.scandir

    @contextmanager
    def scandir(path: Path) -> Generator[Iterator[os.DirEntry[str]], None, None]:
        with real_scandir(path) as entries:
            listed = list(entries)
        if cache_types:
            for entry in listed:
                entry.is_file()
        yield iter(listed)
        change()

    with patch.object(storage_module, "_scandir", side_effect=scandir):
        yield


class TestLocalListingWithIssues:
    def test_reports_unreadable_document_and_keeps_the_others(self, tmp_path: Path) -> None:
        (tmp_path / "first.json").write_bytes(b"one")
        (tmp_path / "second.json").write_bytes(b"two")

        def read(path: str) -> bytes | None:
            if Path(path).name == "second.json":
                raise PermissionError(13, "Permission denied", path)
            return _read_regular_file(path)

        with patch("pyrit.registry.file_document_storage._read_regular_file", side_effect=read):
            listing = _local_storage(tmp_path)._list_documents_with_issues()
            documents = _local_storage(tmp_path)._list_documents()

        assert listing == DocumentListing(
            documents={"first": b"one"},
            unreadable=(UnreadableDocument(name="second", file_name="second.json", message="Permission denied"),),
        )
        assert isinstance(listing.unreadable[0].error, PermissionError)
        assert documents == {"first": b"one"}

    def test_reports_badly_named_files_as_ignored_without_reading_them(self, tmp_path: Path) -> None:
        (tmp_path / "valid_name.json").write_bytes(b"ok")
        (tmp_path / "Bad-Name.json").write_bytes(b"never read")
        (tmp_path / "notes.txt").write_bytes(b"other extension")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.json").write_bytes(b"not a direct child")
        read_names: list[str] = []

        def read(path: str) -> bytes | None:
            read_names.append(Path(path).name)
            return _read_regular_file(path)

        with patch("pyrit.registry.file_document_storage._read_regular_file", side_effect=read):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.documents == {"valid_name": b"ok"}
        assert listing.unreadable == ()
        assert [issue.file_name for issue in listing.ignored] == ["Bad-Name.json"]
        assert "Bad-Name" in listing.ignored[0].message
        assert read_names == ["valid_name.json"]
        assert _local_storage(tmp_path)._list_documents() == listing.documents

    def test_skips_a_document_deleted_after_listing(self, tmp_path: Path) -> None:
        (tmp_path / "kept.json").write_bytes(b"{}\r\n")
        (tmp_path / "empty.json").write_bytes(b"")
        deleted = tmp_path / "deleted.json"
        deleted.write_bytes(b"gone")

        with _change_after_listing(change=deleted.unlink, cache_types=True):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(documents={"empty": b"", "kept": b"{}\r\n"})

    def test_skips_a_link_deleted_after_listing(self, tmp_path: Path) -> None:
        (tmp_path / "kept.json").write_bytes(b"kept")
        (tmp_path / "target.txt").write_bytes(b"linked")
        link = tmp_path / "linked.json"
        try:
            link.symlink_to(tmp_path / "target.txt")
        except OSError:
            pytest.skip("Creating symbolic links isn't permitted here")

        with _change_after_listing(change=link.unlink):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(documents={"kept": b"kept"})

    @pytest.mark.parametrize("replacement", ["pipe", "folder", "dangling link"])
    @pytest.mark.timeout(60, method="thread")
    def test_reports_a_non_file_put_in_place_of_a_listed_document_without_reading_it(
        self, tmp_path: Path, replacement: str
    ) -> None:
        if replacement == "pipe" and not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes need os.mkfifo")
        if replacement == "folder" and sys.platform == "win32":
            pytest.skip("Windows can't open a folder as a file")
        if replacement == "dangling link" and sys.platform == "win32":
            pytest.skip("Creating symbolic links may not be permitted on Windows")
        (tmp_path / "kept.json").write_bytes(b"kept")
        swapped = tmp_path / "swapped.json"
        swapped.write_bytes(b"replaced")

        def replace() -> None:
            swapped.unlink()
            if replacement == "pipe":
                os.mkfifo(swapped)
            elif replacement == "folder":
                swapped.mkdir()
            else:
                swapped.symlink_to(tmp_path / "missing_target.json")

        open_files = _count_open_files()
        with _change_after_listing(change=replace, cache_types=True):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(
            documents={"kept": b"kept"},
            unreadable=(UnreadableDocument(name="swapped", file_name="swapped.json", message="Not a regular file"),),
        )
        assert listing.unreadable[0].error is None
        assert _count_open_files() == open_files

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes need os.mkfifo")
    @pytest.mark.timeout(60, method="thread")
    def test_checks_the_opened_file_rather_than_the_path(self, tmp_path: Path) -> None:
        document = tmp_path / "document.json"
        document.write_bytes(b"original")
        real_open = os.open

        def open_then_replace(path: str, flags: int, *args: int) -> int:
            descriptor = real_open(path, flags, *args)
            os.unlink(path)
            os.mkfifo(path)
            return descriptor

        with patch.object(storage_module, "_open", side_effect=open_then_replace):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(documents={"document": b"original"})
        assert document.is_fifo()

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes need os.mkfifo")
    @pytest.mark.timeout(60, method="thread")
    def test_checks_a_file_replaced_just_before_it_is_opened(self, tmp_path: Path) -> None:
        (tmp_path / "document.json").write_bytes(b"original")
        real_open = os.open

        def replace_then_open(path: str, flags: int, *args: int) -> int:
            os.unlink(path)
            os.mkfifo(path)
            return real_open(path, flags, *args)

        with patch.object(storage_module, "_open", side_effect=replace_then_open):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(
            unreadable=(UnreadableDocument(name="document", file_name="document.json", message="Not a regular file"),)
        )

    def test_reports_entries_that_are_not_regular_files_without_reading_them(self, tmp_path: Path) -> None:
        (tmp_path / "folder.json").mkdir()
        try:
            (tmp_path / "broken_link.json").symlink_to(tmp_path / "missing_target.json")
        except OSError:
            pytest.skip("Creating symbolic links isn't permitted here")

        with patch("pyrit.registry.file_document_storage._read_regular_file") as read:
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.unreadable == (
            UnreadableDocument(name="broken_link", file_name="broken_link.json", message="Not a regular file"),
            UnreadableDocument(name="folder", file_name="folder.json", message="Not a regular file"),
        )
        read.assert_not_called()

    @pytest.mark.skipif(sys.platform == "win32", reason="The null device is a POSIX character device here")
    def test_reads_only_regular_files(self) -> None:
        assert _read_regular_file(os.devnull) is None

    def test_reports_an_entry_that_cannot_be_checked_after_listing(self, tmp_path: Path) -> None:
        deleted = tmp_path / "deleted.json"
        deleted.write_bytes(b"gone")

        with (
            _change_after_listing(change=deleted.unlink, cache_types=True),
            patch.object(storage_module, "_lstat", side_effect=PermissionError(13, "Permission denied")),
        ):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(
            unreadable=(UnreadableDocument(name="deleted", file_name="deleted.json", message="Permission denied"),)
        )

    def test_reports_a_link_loop_and_keeps_the_others(self, tmp_path: Path) -> None:
        (tmp_path / "good.json").write_bytes(b"ok")
        try:
            (tmp_path / "loop.json").symlink_to(tmp_path / "loop.json")
        except OSError:
            pytest.skip("Creating symbolic links isn't permitted here")

        listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing.documents == {"good": b"ok"}
        assert [issue.name for issue in listing.unreadable] == ["loop"]

    def test_matches_extensions_with_the_platform_case_rule(self, tmp_path: Path) -> None:
        (tmp_path / "lower.json").write_bytes(b"one")
        (tmp_path / "upper.JSON").write_bytes(b"two")

        listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert sorted(listing.documents) == sorted(path.stem for path in tmp_path.glob("*.json"))

    def test_creates_missing_directory_and_lists_nothing(self, tmp_path: Path) -> None:
        listing = _local_storage(tmp_path / "missing")._list_documents_with_issues()

        assert listing == DocumentListing()
        assert (tmp_path / "missing").is_dir()

    def test_raises_when_the_directory_cannot_be_listed(self, tmp_path: Path) -> None:
        (tmp_path / "first.json").write_bytes(b"one")

        with patch.object(storage_module, "_scandir", side_effect=PermissionError(13, "denied")):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _local_storage(tmp_path)._list_documents_with_issues()

        assert isinstance(raised.value.__cause__, PermissionError)
        assert (raised.value.label, raised.value.source, raised.value.reason) == (
            "Test documents",
            str(tmp_path),
            "denied",
        )
        assert str(raised.value) == f"Test documents storage at {tmp_path} is unavailable: denied"


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
                raise _blob_error(status_code=409, error_code="BlobArchived", message=f"Archived: {_SAS_SOURCE}")
            return SimpleNamespace(readall=lambda: b"content")

        client.download_blob.side_effect = download_blob

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            listing = _blob_storage()._list_documents_with_issues()

        assert listing.documents == {"good": b"content"}
        assert listing.unreadable == (
            UnreadableDocument(name="broken", file_name="broken.json", message="HttpResponseError (409, BlobArchived)"),
        )
        assert isinstance(listing.unreadable[0].error, HttpResponseError)
        assert [issue.file_name for issue in listing.ignored] == ["Bad-Name.json"]
        assert all("secret-signature" not in issue.message for issue in (*listing.unreadable, *listing.ignored))
        client.list_blobs.assert_called_once_with(name_starts_with="saved/")
        downloaded = [call.args[0] for call in client.download_blob.call_args_list]
        assert downloaded == ["saved/broken.json", "saved/good.json"]

    def test_skips_only_a_blob_deleted_after_listing(self) -> None:
        client = _blob_client(names=["saved/kept.json", "saved/deleted.json"])

        def download_blob(blob_name: str) -> SimpleNamespace:
            if blob_name == "saved/deleted.json":
                raise _blob_error(status_code=404, error_code=StorageErrorCode.BLOB_NOT_FOUND, missing=True)
            return SimpleNamespace(readall=lambda: b"kept")

        client.download_blob.side_effect = download_blob

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            listing = _blob_storage()._list_documents_with_issues()

        assert listing == DocumentListing(documents={"kept": b"kept"})

    @pytest.mark.parametrize(
        "error",
        [
            _blob_error(status_code=404, error_code=StorageErrorCode.CONTAINER_NOT_FOUND, missing=True),
            _blob_error(status_code=403, error_code=StorageErrorCode.AUTHORIZATION_FAILURE),
            _blob_error(status_code=401, error_code="InvalidAuthenticationInfo"),
            ClientAuthenticationError(message="no token"),
            ServiceRequestError(message="connection refused"),
            ServiceResponseError(message="connection reset"),
        ],
    )
    def test_raises_when_an_error_affects_the_whole_container(self, error: Exception) -> None:
        client = _blob_client(names=["saved/first.json", "saved/second.json"])
        client.download_blob.side_effect = error

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _blob_storage()._list_documents_with_issues()

        assert raised.value.__cause__ is error
        assert "secret-signature" not in str(raised.value)

    def test_raises_when_the_container_cannot_be_listed(self) -> None:
        def failing_pages() -> Iterator[SimpleNamespace]:
            raise HttpResponseError(message="listing failed")
            yield SimpleNamespace(name="saved/never.json")

        client = _blob_client(names=[])
        client.list_blobs.return_value = failing_pages()

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _blob_storage()._list_documents_with_issues()

        assert isinstance(raised.value.__cause__, HttpResponseError)


class TestReadDocument:
    def test_reads_a_document_and_treats_a_missing_one_as_missing(self, tmp_path: Path) -> None:
        (tmp_path / "present.json").write_bytes(b"content")
        storage = _local_storage(tmp_path)

        assert storage._read_document_bytes("present") == b"content"
        assert storage._read_document_bytes("absent") is None
        assert _local_storage(tmp_path / "missing_folder")._read_document_bytes("absent") is None

    @pytest.mark.parametrize("entry", ["folder", "broken link", "pipe"])
    @pytest.mark.timeout(60, method="thread")
    def test_reports_what_the_listing_reports_as_unreadable(self, tmp_path: Path, entry: str) -> None:
        path = tmp_path / "odd.json"
        if entry == "folder":
            path.mkdir()
        elif entry == "broken link":
            try:
                path.symlink_to(tmp_path / "missing_target.json")
            except OSError:
                pytest.skip("Creating symbolic links isn't permitted here")
        elif not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes need os.mkfifo")
        else:
            os.mkfifo(path)
        storage = _local_storage(tmp_path)

        with pytest.raises(DocumentUnreadableError) as raised:
            storage._read_document_bytes("odd")

        assert (raised.value.name, raised.value.file_name, raised.value.reason) == (
            "odd",
            "odd.json",
            "Not a regular file",
        )
        assert [issue.name for issue in storage._list_documents_with_issues().unreadable] == ["odd"]

    def test_reports_a_file_it_cannot_open(self, tmp_path: Path) -> None:
        (tmp_path / "locked.json").write_bytes(b"secret")

        with patch.object(storage_module, "_open", side_effect=PermissionError(13, "Permission denied")):
            with pytest.raises(DocumentUnreadableError) as raised:
                _local_storage(tmp_path)._read_document_bytes("locked")

        assert raised.value.reason == "Permission denied"
        assert isinstance(raised.value.__cause__, PermissionError)
        assert str(raised.value) == f"Test documents 'locked.json' in {tmp_path} can't be read: Permission denied"

    def test_blob_treats_only_a_missing_blob_as_missing(self) -> None:
        client = _blob_client(names=[])
        client.download_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.BLOB_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            assert _blob_storage()._read_document_bytes("absent") is None

    def test_blob_raises_when_the_container_is_gone(self) -> None:
        client = _blob_client(names=[])
        client.download_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.CONTAINER_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _blob_storage()._read_document_bytes("present")

        assert not isinstance(raised.value, DocumentUnreadableError)

    def test_blob_reports_a_blob_it_cannot_read(self) -> None:
        client = _blob_client(names=[])
        client.download_blob.side_effect = _blob_error(status_code=409, error_code="BlobArchived")

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentUnreadableError) as raised:
                _blob_storage()._read_document_bytes("archived")

        assert raised.value.reason == "HttpResponseError (409, BlobArchived)"


class TestDeleteDocument:
    def test_blob_reports_a_missing_blob_as_already_gone(self) -> None:
        client = _blob_client(names=[])
        client.delete_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.BLOB_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            assert _blob_storage()._delete_document("absent") is False

    def test_blob_raises_when_the_container_is_gone(self) -> None:
        client = _blob_client(names=[])
        client.delete_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.CONTAINER_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError):
                _blob_storage()._delete_document("present")


class TestConditionalSave:
    def test_local_refuses_to_replace_something_it_cannot_read(self, tmp_path: Path) -> None:
        (tmp_path / "odd.json").mkdir()

        with pytest.raises(DocumentUnreadableError):
            _local_storage(tmp_path)._save_document_conditional(name="odd", content=b"new", expected_version=None)

        assert (tmp_path / "odd.json").is_dir()

    def test_blob_update_of_a_deleted_blob_is_a_conflict(self) -> None:
        client = _blob_client(names=[])
        client.download_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.BLOB_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentConflictError) as raised:
                _blob_storage()._save_document_conditional(name="gone", content=b"new", expected_version="v1")

        assert raised.value.actual_version is None

    def test_blob_update_raises_when_the_container_is_gone(self) -> None:
        client = _blob_client(names=[])
        client.download_blob.side_effect = _blob_error(
            status_code=404, error_code=StorageErrorCode.CONTAINER_NOT_FOUND, missing=True
        )

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _blob_storage()._save_document_conditional(name="present", content=b"new", expected_version="v1")

        assert not isinstance(raised.value, DocumentUnreadableError)
        client.upload_blob.assert_not_called()

    @pytest.mark.parametrize("error_type", [ServiceRequestError, ClientAuthenticationError])
    @pytest.mark.parametrize("updating", [False, True])
    def test_blob_upload_failure_is_storage_unavailable(
        self, error_type: type[ServiceRequestError | ClientAuthenticationError], updating: bool
    ) -> None:
        client = _blob_client(names=[])
        client.download_blob.return_value.readall.return_value = b"old"
        client.download_blob.return_value.properties.etag = "etag-1"
        error = error_type(message=f"Upload failed: {_SAS_SOURCE}")
        client.upload_blob.side_effect = error
        expected_version = FileDocumentStorage._compute_version(b"old") if updating else None

        with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client):
            with pytest.raises(DocumentStorageUnavailableError) as raised:
                _blob_storage()._save_document_conditional(
                    name="present", content=b"new", expected_version=expected_version
                )

        client.upload_blob.assert_called_once()
        assert type(raised.value) is DocumentStorageUnavailableError
        assert raised.value.__cause__ is error
        assert str(raised.value) == (
            "Test documents storage at https://account.blob.core.windows.net/documents/saved "
            f"is unavailable: {error_type.__name__}"
        )


def test_a_listing_cannot_be_changed_by_its_reader(tmp_path: Path) -> None:
    (tmp_path / "first.json").write_bytes(b"one")

    listing = _local_storage(tmp_path)._list_documents_with_issues()

    with pytest.raises(TypeError):
        listing.documents["second"] = b"two"  # type: ignore[index]


class TestListDocuments:
    def test_logs_what_it_leaves_out_and_returns_the_rest(self, tmp_path: Path) -> None:
        (tmp_path / "good.json").write_bytes(b"ok")
        (tmp_path / "Bad-Name.json").write_bytes(b"never read")
        (tmp_path / "folder.json").mkdir()

        with patch("pyrit.registry.file_document_storage.logger") as log:
            documents = _local_storage(tmp_path)._list_documents()

        assert documents == {"good": b"ok"}
        messages = [call.args[0] for call in log.warning.call_args_list]
        assert len(messages) == 2
        assert messages[0].startswith(f"Ignoring stored document 'Bad-Name.json' in {tmp_path}: ")
        assert messages[1] == f"Skipping unreadable document 'folder.json' in {tmp_path}: Not a regular file"

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Named pipes need os.mkfifo")
    @pytest.mark.timeout(60, method="thread")
    def test_leaves_out_a_pipe_named_like_a_document_without_blocking(self, tmp_path: Path) -> None:
        (tmp_path / "good.json").write_bytes(b"ok")
        os.mkfifo(tmp_path / "pipe.json")

        assert _local_storage(tmp_path)._list_documents() == {"good": b"ok"}

    def test_raises_when_the_directory_cannot_be_listed(self, tmp_path: Path) -> None:
        with patch.object(storage_module, "_scandir", side_effect=PermissionError(13, "denied")):
            with pytest.raises(DocumentStorageUnavailableError):
                _local_storage(tmp_path)._list_documents()

    def test_logs_an_unreadable_blob_without_exposing_the_url(self) -> None:
        client = _blob_client(names=["saved/good.json", "saved/broken.json"])

        def download_blob(blob_name: str) -> SimpleNamespace:
            if blob_name == "saved/broken.json":
                raise HttpResponseError(message=f"Forbidden: {_SAS_SOURCE}")
            return SimpleNamespace(readall=lambda: b"content")

        client.download_blob.side_effect = download_blob

        with (
            patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client),
            patch("pyrit.registry.file_document_storage.logger") as log,
        ):
            documents = _blob_storage()._list_documents()

        assert documents == {"good": b"content"}
        message = log.warning.call_args.args[0]
        assert message.startswith(
            "Skipping unreadable document 'broken.json' in https://account.blob.core.windows.net/"
        )
        assert message.endswith(": HttpResponseError")
        assert "secret-signature" not in message


class TestDescribeStorageError:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (PermissionError(13, "Permission denied", "/server/path/secret.json"), "Permission denied"),
            (OSError(), "OSError"),
            (ServiceRequestError(message="https://account.blob.core.windows.net/x?sig=secret"), "ServiceRequestError"),
        ],
    )
    def test_describes_errors_without_their_details(self, error: Exception, expected: str) -> None:
        assert describe_storage_error(error) == expected

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

        assert describe_storage_error(error) == expected
