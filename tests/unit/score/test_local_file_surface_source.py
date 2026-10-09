# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import ctypes
import os
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import Acquisition, ContentWritten, Observation, SurfaceScorable
from pyrit.score import LocalFileSurfaceSource
from pyrit.score.observation import local_file_surface_source as source_module
from pyrit.score.true_false.file_write_scorer import match_content_written

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _assert_gap(observation: Observation, *, reason: str) -> None:
    assert observation.acquisition is Acquisition.PARTIAL
    assert observation.payload.entries == ()
    assert observation.payload.coverage.reasons == (reason,)
    scope = observation.payload.scope
    assert (
        match_content_written(
            condition=ContentWritten(uri=scope.uri, match=scope.match, contains="secret"),
            payload=observation.payload,
            acquisition=observation.acquisition,
        )
        is None
    )


def _listing_mock(tmp_path: Path) -> MagicMock:
    with os.scandir(tmp_path) as iterator:
        return MagicMock(spec=type(iterator))


@pytest.mark.parametrize("error", [NotADirectoryError, PermissionError])
async def test_exact_location_lookup_failure_async(*, tmp_path: Path, error: type[OSError]) -> None:
    source = LocalFileSurfaceSource(root=tmp_path)
    target = tmp_path.resolve() / "parent" / "out.txt"
    real_lstat = os.lstat

    def lstat(path: str | Path) -> os.stat_result:
        if Path(path) == target:
            raise error("cannot search parent")
        return real_lstat(path)

    with patch.object(source_module.os, "lstat", side_effect=lstat):
        observation = await source.acquire_async(scorable=SurfaceScorable(uri="/parent/out.txt"))

    if error is NotADirectoryError:
        assert observation.acquisition is Acquisition.COMPLETE
        assert observation.payload.entries == ()
    else:
        _assert_gap(observation, reason="listing_failed")


async def test_exact_directory_is_not_file_evidence_async(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()

    observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(scorable=SurfaceScorable(uri="/data"))

    _assert_gap(observation, reason="not_a_file")


async def test_absent_glob_directory_is_complete_async(tmp_path: Path) -> None:
    with patch.object(source_module, "_scandir", side_effect=FileNotFoundError("gone")):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/missing/**", match="glob")
        )

    assert observation.acquisition is Acquisition.COMPLETE
    assert observation.payload.entries == ()


async def test_glob_listing_error_after_an_entry_discards_incomplete_listing_async(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    with os.scandir(tmp_path) as listing:
        entry = next(listing)

    def entries() -> Iterator[os.DirEntry[str]]:
        yield entry
        raise PermissionError("listing interrupted")

    iterator = _listing_mock(tmp_path)
    iterator.__enter__.return_value = iterator
    iterator.__iter__.side_effect = entries
    with patch.object(source_module, "_scandir", return_value=iterator):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/**", match="glob")
        )

    _assert_gap(observation, reason="listing_failed")
    iterator.__exit__.assert_called_once()


@pytest.mark.parametrize("is_directory", [True, False])
async def test_glob_link_selection_async(*, tmp_path: Path, is_directory: bool) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    entry = MagicMock(spec=os.DirEntry)
    entry.name = "out.txt"
    entry.is_symlink.return_value = True
    entry.is_dir.return_value = is_directory
    iterator = _listing_mock(tmp_path)
    iterator.__enter__.return_value = iterator
    iterator.__iter__.return_value = iter([entry])
    with patch.object(source_module, "_scandir", return_value=iterator):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/*", match="glob")
        )

    if is_directory:
        _assert_gap(observation, reason="link_not_followed")
    else:
        assert observation.acquisition is Acquisition.COMPLETE
        assert observation.payload.entries[0].content == "secret"


def test_directory_link_stat_error_does_not_invent_a_directory() -> None:
    entry = MagicMock(spec=os.DirEntry)
    entry.is_dir.side_effect = PermissionError("cannot stat link")

    assert source_module._link_is_directory(entry) is False


@pytest.mark.parametrize(
    ("error", "is_link", "reason"),
    [
        (FileNotFoundError, True, "dangling_link"),
        (FileNotFoundError, False, "read_failed"),
        (PermissionError, False, "read_failed"),
    ],
)
async def test_open_failure_is_incomplete_async(
    *, tmp_path: Path, error: type[OSError], is_link: bool, reason: str
) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    with (
        patch.object(source_module.os, "open", side_effect=error("cannot open")),
        patch.object(source_module.os.path, "islink", return_value=is_link),
    ):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/out.txt")
        )

    _assert_gap(observation, reason=reason)


@pytest.mark.parametrize(
    ("operation", "reason"),
    [("_final_path", "confinement_unverified"), ("_read_chunk", "read_failed")],
)
async def test_open_handle_is_closed_after_failure_async(*, tmp_path: Path, operation: str, reason: str) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    with (
        patch.object(source_module, operation, side_effect=OSError("controlled failure")),
        patch.object(source_module.os, "close", wraps=os.close) as close,
    ):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/out.txt")
        )

    _assert_gap(observation, reason=reason)
    close.assert_called_once()
    with pytest.raises(OSError):
        os.fstat(close.call_args.args[0])


async def test_nonregular_open_handle_is_not_read_async(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    info = os.stat_result((stat.S_IFIFO, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    with (
        patch.object(source_module.os, "fstat", return_value=info),
        patch.object(source_module, "_read_chunk") as read,
    ):
        observation = await LocalFileSurfaceSource(root=tmp_path).acquire_async(
            scorable=SurfaceScorable(uri="/out.txt")
        )

    _assert_gap(observation, reason="not_a_file")
    read.assert_not_called()


async def test_cancellation_closes_open_handle_async(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("secret", encoding="utf-8")
    source = LocalFileSurfaceSource(root=tmp_path)
    with (
        patch.object(source_module, "_read_chunk", side_effect=source_module._AcquisitionCancelledError),
        patch.object(source_module.os, "close", wraps=os.close) as close,
    ):
        with pytest.raises(source_module._AcquisitionCancelledError):
            await source.acquire_async(scorable=SurfaceScorable(uri="/out.txt"))

    close.assert_called_once()


async def test_directory_swap_before_open_is_rejected_async(tmp_path: Path) -> None:
    root = tmp_path / "root"
    data = root / "data"
    outside = tmp_path / "outside"
    data.mkdir(parents=True)
    outside.mkdir()
    (data / "out.txt").write_text("inside", encoding="utf-8")
    (outside / "out.txt").write_text("outside-synthetic-secret", encoding="utf-8")
    moved = root / "data-original"
    real_open = os.open

    def swap_then_open(path: Path, flags: int) -> int:
        if path == data / "out.txt":
            data.rename(moved)
            if os.name == "nt":
                subprocess.run(
                    [os.environ["COMSPEC"], "/c", "mklink", "/J", str(data), str(outside)],
                    capture_output=True,
                    check=True,
                )
            else:
                data.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags)

    try:
        with (
            patch.object(source_module.os, "open", side_effect=swap_then_open),
            patch.object(source_module, "_read_chunk") as read,
        ):
            observation = await LocalFileSurfaceSource(root=root).acquire_async(
                scorable=SurfaceScorable(uri="/data/out.txt")
            )
        _assert_gap(observation, reason="link_outside_root")
        read.assert_not_called()
    finally:
        if moved.exists():
            if data.is_symlink():
                data.unlink()
            elif data.exists():
                data.rmdir()
            moved.rename(data)


@pytest.mark.parametrize("opened", ["", "outside"])
def test_path_containment_rejects_the_root_and_other_locations(*, tmp_path: Path, opened: str) -> None:
    root = tmp_path / "root"
    candidate = root if not opened else tmp_path / opened
    assert source_module._is_within(str(candidate), root) is False


def test_path_containment_rejects_different_drives(tmp_path: Path) -> None:
    with patch.object(source_module.os.path, "commonpath", side_effect=ValueError("different drives")):
        assert source_module._is_within(str(tmp_path / "out.txt"), tmp_path) is False


def test_linux_final_path_requires_proc_handle() -> None:
    with (
        patch.object(source_module.sys, "platform", "linux"),
        patch.object(source_module.os.path, "exists", return_value=False),
    ):
        with pytest.raises(OSError, match="cannot report the path"):
            source_module._final_path(42)


def test_linux_final_path_uses_the_open_handle() -> None:
    with (
        patch.object(source_module.sys, "platform", "linux"),
        patch.object(source_module.os.path, "exists", return_value=True),
        patch.object(source_module.os, "readlink", return_value="/workspace/out.txt") as readlink,
    ):
        assert source_module._final_path(42) == "/workspace/out.txt"

    readlink.assert_called_once_with("/proc/self/fd/42")


def test_macos_final_path_decodes_the_handle_path() -> None:
    fcntl = ModuleType("fcntl")
    fcntl.F_GETPATH = 50
    fcntl.fcntl = MagicMock(return_value=b"/workspace/out.txt\0" + bytes(1005))
    with patch.dict(sys.modules, {"fcntl": fcntl}), patch.object(source_module.sys, "platform", "darwin"):
        assert source_module._final_path(42) == "/workspace/out.txt"

    fcntl.fcntl.assert_called_once_with(42, 50, bytes(1024))


@pytest.fixture
def windows_source() -> tuple[dict[str, Any], MagicMock]:
    api = MagicMock()
    library = MagicMock()
    library.kernel32.GetFinalPathNameByHandleW = api
    msvcrt = ModuleType("msvcrt")
    msvcrt.get_osfhandle = MagicMock(return_value=1234)
    with (
        patch.object(ctypes, "windll", library, create=True),
        patch.object(ctypes, "WinError", return_value=OSError("final path unavailable"), create=True),
        patch.dict(sys.modules, {"msvcrt": msvcrt}),
        patch.object(source_module.sys, "platform", "win32"),
    ):
        namespace = runpy.run_path(source_module.__file__)
    return namespace, api


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"\\?\C:\workspace\out.txt", r"C:\workspace\out.txt"),
        (r"\\?\UNC\server\share\out.txt", r"\\server\share\out.txt"),
        (r"C:\workspace\out.txt", r"C:\workspace\out.txt"),
    ],
)
def test_windows_final_path_normalization(
    *, windows_source: tuple[dict[str, Any], MagicMock], path: str, expected: str
) -> None:
    namespace, api = windows_source

    def get_path(handle: int, buffer: ctypes.Array[ctypes.c_wchar], size: int, flags: int) -> int:
        assert (handle, size, flags) == (1234, 32768, 0)
        buffer.value = path
        return len(path)

    api.side_effect = get_path
    with patch.object(source_module.sys, "platform", "win32"):
        assert namespace["_final_path"](42) == expected
    api.assert_called_once()


@pytest.mark.parametrize("length", [0, 32768])
def test_windows_final_path_errors_are_explicit(
    *, windows_source: tuple[dict[str, Any], MagicMock], length: int
) -> None:
    namespace, api = windows_source
    api.return_value = length
    with patch.object(ctypes, "WinError", return_value=OSError("final path unavailable"), create=True):
        with pytest.raises(OSError, match="final path unavailable"):
            namespace["_final_path_windows"](42)
