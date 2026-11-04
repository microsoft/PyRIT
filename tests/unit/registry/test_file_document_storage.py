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
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ServiceRequestError
from azure.storage.blob import ContainerClient, StorageErrorCode

from pyrit.registry.file_document_storage import (
    DocumentListing,
    FileDocumentStorage,
    UnreadableDocument,
    _read_regular_file,
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

    with patch("pyrit.registry.file_document_storage.os.scandir", side_effect=scandir):
        yield


class TestLocalListingWithIssues:
    def test_reports_unreadable_document_and_keeps_the_others(self, tmp_path: Path) -> None:
        (tmp_path / "first.json").write_bytes(b"one")
        (tmp_path / "second.json").write_bytes(b"two")
        original_read_bytes = Path.read_bytes

        def read(path: str) -> bytes | None:
            if Path(path).name == "second.json":
                raise PermissionError(13, "Permission denied", path)
            return _read_regular_file(path)

        def read_bytes(path: Path) -> bytes:
            if path.name == "second.json":
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_bytes(path)

        with (
            patch("pyrit.registry.file_document_storage._read_regular_file", side_effect=read),
            patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes),
        ):
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
            unreadable=(UnreadableDocument(name="swapped", message="Not a regular file"),),
        )
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

        with patch("pyrit.registry.file_document_storage.os.open", side_effect=open_then_replace):
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

        with patch("pyrit.registry.file_document_storage.os.open", side_effect=replace_then_open):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(
            unreadable=(UnreadableDocument(name="document", message="Not a regular file"),)
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
            UnreadableDocument(name="broken_link", message="Not a regular file"),
            UnreadableDocument(name="folder", message="Not a regular file"),
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
            patch(
                "pyrit.registry.file_document_storage.os.lstat",
                side_effect=PermissionError(13, "Permission denied"),
            ),
        ):
            listing = _local_storage(tmp_path)._list_documents_with_issues()

        assert listing == DocumentListing(unreadable=(UnreadableDocument(name="deleted", message="Permission denied"),))

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
