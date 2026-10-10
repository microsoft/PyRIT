# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Acquire what a local directory holds, as condition-independent surface evidence."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import os
import stat
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from pyrit.models import (
    Acquisition,
    ComponentIdentifier,
    Observation,
    SurfaceCoverage,
    SurfaceEntry,
    SurfaceMatch,
    SurfaceObservationPayload,
)

if TYPE_CHECKING:
    from pyrit.models import SurfaceScorable

logger = logging.getLogger(__name__)


class _FileLimitReachedError(Exception):
    """Unwinds enumeration once more candidates exist than ``max_files`` allows."""


class _AcquisitionCancelledError(Exception):
    """Raised inside the worker thread once the awaiting coroutine was cancelled."""


@dataclass
class _Budget:
    """Work one acquisition may still perform, plus the cancellation signal."""

    cancel: threading.Event
    listed_entries_left: int
    read_bytes_left: int
    reasons: list[str] = field(default_factory=list)

    def check(self) -> None:
        if self.cancel.is_set():
            raise _AcquisitionCancelledError


class LocalFileSurfaceSource:
    """
    Read the files a ``SurfaceScorable`` names under one caller-owned root directory.

    The root is the directory the system under test writes into, such as a mounted sandbox
    workspace, and a scorable's ``uri`` is read relative to it: ``/data/out.txt`` is
    ``<root>/data/out.txt``. The system under test controls that workspace, so every file
    is confined by the handle actually opened: the source opens the file, asks the
    operating system where that open handle lives, refuses it unless it is inside the root,
    and reads size, timestamp and content from that same handle. Links or directories
    swapped between enumeration and the read therefore cannot redirect the read outside
    the root. A hard link to an outside file on the same volume is a genuine entry of the
    root and is read as one.

    A negative is only reported when it is proven. A directory that cannot be listed, a
    budget that runs out, or a file that cannot be read in full becomes a coverage gap, so
    the observation is partial and a missing match stays undetermined.

    Work is bounded: ``max_listed_entries`` caps directory entries examined, ``max_files``
    caps candidate files, and ``max_read_bytes`` caps bytes read and hashed across the
    whole acquisition. Cancelling the awaiting coroutine stops the worker thread at its
    next entry or chunk.

    The snapshot shows what the root holds when it is read, not which run wrote it. A caller
    that needs attribution gives each attempt its own root.
    """

    _READ_CHUNK_BYTES = 1 << 16
    # Nonblocking open lets the file type check reject a FIFO without waiting for a writer.
    _OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)

    def __init__(
        self,
        *,
        root: str | Path,
        max_files: int = 1000,
        max_content_bytes: int = 1_000_000,
        max_read_bytes: int = 16_000_000,
        max_listed_entries: int = 100_000,
    ) -> None:
        """
        Initialize bounded acquisition under one root directory.

        Args:
            root (str | Path): The directory scorable locations are read relative to.
            max_files (int): The most locations one glob scorable may cover.
            max_content_bytes (int): The most bytes of each file retained as text evidence.
            max_read_bytes (int): The most bytes read and hashed across one acquisition. A
                file cut short by this budget has no digest and leaves coverage incomplete.
            max_listed_entries (int): The most directory entries one acquisition examines.

        Raises:
            ValueError: If a limit is not positive.
        """
        if min(max_files, max_content_bytes, max_read_bytes, max_listed_entries) < 1:
            raise ValueError("max_files, max_content_bytes, max_read_bytes and max_listed_entries must be positive.")
        self._root = Path(root)
        self._max_files = max_files
        self._max_content_bytes = max_content_bytes
        self._max_read_bytes = max_read_bytes
        self._max_listed_entries = max_listed_entries

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the reader version, root and limits.

        Returns:
            ComponentIdentifier: The nonsecret source configuration.
        """
        return ComponentIdentifier.of(
            self,
            params={
                "acquisition_version": 4,
                "root": str(self._root),
                "max_files": self._max_files,
                "max_content_bytes": self._max_content_bytes,
                "max_read_bytes": self._max_read_bytes,
                "max_listed_entries": self._max_listed_entries,
            },
        )

    async def acquire_async(self, *, scorable: SurfaceScorable) -> Observation:
        """
        Acquire one bounded snapshot of the named locations.

        Args:
            scorable (SurfaceScorable): The locations to read.

        Returns:
            Observation: The snapshot, including acquisition and coverage state.

        Raises:
            ValueError: If the scorable leaves the root.
            asyncio.CancelledError: If the awaiting task is cancelled; the worker stops too.
        """
        relative = PurePosixPath(scorable.uri.lstrip("/"))
        if ".." in relative.parts or not relative.parts:
            raise ValueError("A file surface locator must name a location inside the root.")
        cancel = threading.Event()
        try:
            return await asyncio.to_thread(self._acquire, scorable=scorable, relative=relative, cancel=cancel)
        except asyncio.CancelledError:
            # to_thread cannot interrupt its worker; the flag stops it at the next checkpoint.
            cancel.set()
            raise

    def _acquire(self, *, scorable: SurfaceScorable, relative: PurePosixPath, cancel: threading.Event) -> Observation:
        try:
            root = Path(os.path.realpath(self._root, strict=True))
        except OSError:
            root = None
        if root is None or not root.is_dir():
            return self._observation(
                scorable=scorable, acquisition=Acquisition.UNAVAILABLE, reasons=("surface_root_unavailable",)
            )

        budget = _Budget(
            cancel=cancel, listed_entries_left=self._max_listed_entries, read_bytes_left=self._max_read_bytes
        )
        try:
            if scorable.match is SurfaceMatch.EXACT:
                candidates = self._exact_candidate(root=root, relative=relative, budget=budget)
            else:
                candidates = self._glob_candidates(root=root, parts=relative.parts, budget=budget)

            entries: list[SurfaceEntry] = []
            for parts in candidates:
                budget.check()
                location = scorable.uri if scorable.match is SurfaceMatch.EXACT else "/" + "/".join(parts)
                entry = self._read_confined(root=root, parts=parts, location=location, budget=budget)
                if entry is not None:
                    entries.append(entry)
        except _AcquisitionCancelledError:
            logger.info("Surface acquisition stopped after cancellation.")
            raise

        reasons = tuple(dict.fromkeys(budget.reasons))
        return self._observation(
            scorable=scorable,
            acquisition=Acquisition.PARTIAL if reasons else Acquisition.COMPLETE,
            reasons=reasons,
            entries=tuple(entries),
        )

    # --- enumeration ---------------------------------------------------------------------

    def _exact_candidate(self, *, root: Path, relative: PurePosixPath, budget: _Budget) -> list[tuple[str, ...]]:
        """
        Decide whether the named location exists, without following it.

        Returns:
            list[tuple[str, ...]]: The location's parts, or nothing when it is proven absent.
        """
        budget.check()
        try:
            info = os.lstat(root.joinpath(*relative.parts))
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError:
            # A parent that cannot be searched does not prove the file is absent.
            budget.reasons.append("listing_failed")
            return []
        if stat.S_ISDIR(info.st_mode):
            budget.reasons.append("not_a_file")
            return []
        return [relative.parts]

    def _glob_candidates(self, *, root: Path, parts: tuple[str, ...], budget: _Budget) -> list[tuple[str, ...]]:
        """
        Enumerate files matching a glob pattern, recording every directory not fully searched.

        ``**`` matches zero or more directories and, as the final segment, all files beneath
        them. Directory links are not descended into, since their contents are outside this
        walk's confinement; they are reported as gaps.

        Returns:
            list[tuple[str, ...]]: Matching file locations, at most ``max_files``, sorted.
        """
        found: set[tuple[str, ...]] = set()
        listings: dict[tuple[str, ...], list[tuple[str, str]] | None] = {}
        visited: set[tuple[tuple[str, ...], int]] = set()

        def add_candidate(candidate: tuple[str, ...]) -> None:
            if candidate not in found:
                if len(found) >= self._max_files:
                    raise _FileLimitReachedError
                found.add(candidate)

        def walk(*, prefix: tuple[str, ...], index: int) -> None:
            budget.check()
            if index >= len(parts) or (prefix, index) in visited:
                return
            visited.add((prefix, index))
            pattern = parts[index]
            if pattern == "**":
                walk(prefix=prefix, index=index + 1)
            if prefix not in listings:
                listings[prefix] = self._list_directory(root=root, prefix=prefix, budget=budget)
            listing = listings[prefix]
            if listing is None:
                return
            last = index == len(parts) - 1
            for name, kind in listing:
                if pattern == "**":
                    if kind == "dir":
                        walk(prefix=(*prefix, name), index=index)
                    elif last:
                        add_candidate((*prefix, name))
                    continue
                if not fnmatch.fnmatch(name, pattern):
                    continue
                if last:
                    if kind == "dir":
                        continue
                    add_candidate((*prefix, name))
                elif kind == "dir":
                    walk(prefix=(*prefix, name), index=index + 1)

        try:
            walk(prefix=(), index=0)
        except _FileLimitReachedError:
            # Stop enumerating as soon as one candidate more than the limit is seen.
            budget.reasons.append("file_limit_exceeded")
        return sorted(found)

    def _list_directory(self, *, root: Path, prefix: tuple[str, ...], budget: _Budget) -> list[tuple[str, str]] | None:
        """
        List one directory as (name, kind) pairs, where kind is "dir", "file" or "link".

        Returns:
            list[tuple[str, str]] | None: Sorted entries, or None when the directory could not
            be listed in full; that gap is recorded on the budget.
        """
        budget.check()
        try:
            iterator = _scandir(root.joinpath(*prefix))
        except FileNotFoundError:
            return []
        except OSError:
            budget.reasons.append("listing_failed")
            return None
        listing: list[tuple[str, str]] = []
        try:
            with iterator:
                for entry in iterator:
                    budget.check()
                    if budget.listed_entries_left <= 0:
                        budget.reasons.append("listing_limit_exceeded")
                        return None
                    budget.listed_entries_left -= 1
                    if entry.is_symlink():
                        if _link_is_directory(entry):
                            budget.reasons.append("link_not_followed")
                            continue
                        kind = "link"
                    else:
                        kind = "dir" if entry.is_dir(follow_symlinks=False) else "file"
                    listing.append((entry.name, kind))
        except OSError:
            budget.reasons.append("listing_failed")
            return None
        return sorted(listing)

    # --- reading -------------------------------------------------------------------------

    def _read_confined(
        self,
        *,
        root: Path,
        parts: tuple[str, ...],
        location: str,
        budget: _Budget,
    ) -> SurfaceEntry | None:
        """
        Open one location, prove the open handle is inside the root, then read through it.

        Returns:
            SurfaceEntry | None: The entry, or None when it was not read; the reason is
            recorded on the budget.

        Raises:
            _AcquisitionCancelledError: If the awaiting coroutine was cancelled.
        """
        path = root.joinpath(*parts)
        try:
            fd = os.open(path, self._OPEN_FLAGS)
        except FileNotFoundError:
            budget.reasons.append("dangling_link" if os.path.islink(path) else "read_failed")
            return None
        except OSError:
            budget.reasons.append("read_failed")
            return None
        try:
            try:
                opened = _final_path(fd)
            except OSError:
                budget.reasons.append("confinement_unverified")
                return None
            if not _is_within(opened=opened, root=root):
                budget.reasons.append("link_outside_root")
                return None
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                budget.reasons.append("not_a_file")
                return None
            modified_at = datetime.fromtimestamp(info.st_mtime, tz=UTC)
            return self._read_entry(fd=fd, location=location, info=info, modified_at=modified_at, budget=budget)
        except _AcquisitionCancelledError:
            raise
        except OSError as error:
            logger.warning("Reading a surface location failed (%s).", type(error).__name__)
            budget.reasons.append("read_failed")
            return None
        finally:
            os.close(fd)

    def _read_entry(
        self, *, fd: int, location: str, info: os.stat_result, modified_at: datetime, budget: _Budget
    ) -> SurfaceEntry:
        digest = hashlib.sha256()
        retained = bytearray()
        read = 0
        complete = False
        while True:
            budget.check()
            if budget.read_bytes_left <= 0:
                # The budget ran out exactly at the recorded size: one byte confirms the end.
                complete = read >= info.st_size and not _read_chunk(fd=fd, size=1)
                break
            chunk = _read_chunk(fd=fd, size=min(self._READ_CHUNK_BYTES, budget.read_bytes_left))
            if not chunk:
                complete = True
                break
            budget.read_bytes_left -= len(chunk)
            digest.update(chunk)
            read += len(chunk)
            if len(retained) < self._max_content_bytes:
                retained.extend(chunk[: self._max_content_bytes - len(retained)])
        if not complete:
            budget.reasons.append("read_limit_exceeded")
        size = read if complete else max(info.st_size, read)
        # A read stopped by the budget never proves the retained text is all there is.
        truncated = not complete or size > len(retained)
        content = _decode_text(bytes(retained), truncated=truncated)
        return SurfaceEntry(
            uri=location,
            size_bytes=size,
            sha256=digest.hexdigest() if complete else None,
            modified_at=modified_at,
            content=content,
            content_truncated=truncated and content is not None,
        )

    def _observation(
        self,
        *,
        scorable: SurfaceScorable,
        acquisition: Acquisition,
        reasons: tuple[str, ...],
        entries: tuple[SurfaceEntry, ...] = (),
    ) -> Observation:
        return Observation(
            source_identifier=self.get_identifier(),
            acquisition=acquisition,
            scorable=scorable,
            payload=SurfaceObservationPayload(
                scope=scorable,
                entries=entries,
                coverage=SurfaceCoverage(complete=acquisition is Acquisition.COMPLETE, reasons=reasons),
            ),
        )


# --- platform seams (module-level so tests can count or fault them) ---------------------------


def _scandir(path: Path) -> os._ScandirIterator[str]:  # noqa: SLF001
    return os.scandir(path)


def _read_chunk(*, fd: int, size: int) -> bytes:
    return os.read(fd, size)


def _link_is_directory(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=True)
    except OSError:
        return False


def _is_within(*, opened: str, root: Path) -> bool:
    """
    Compare normalized absolute paths; the root was resolved by the same operating system.

    Returns:
        bool: True when ``opened`` lies strictly inside ``root``.
    """
    opened_norm = os.path.normcase(os.path.normpath(opened))
    root_norm = os.path.normcase(os.path.normpath(str(root)))
    try:
        return os.path.commonpath([opened_norm, root_norm]) == root_norm and opened_norm != root_norm
    except ValueError:
        # Different drives on Windows.
        return False


def _final_path(fd: int) -> str:
    """
    Return where an open file actually lives, as the operating system resolved it.

    Returns:
        str: The absolute path of the open handle.

    Raises:
        OSError: If the platform cannot report the path of an open handle.
    """
    if sys.platform == "win32":
        return _final_path_windows(fd)
    if sys.platform == "darwin":
        import fcntl

        buffer = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        return os.fsdecode(buffer.split(b"\0", 1)[0])
    proc = f"/proc/self/fd/{fd}"
    if not os.path.exists(proc):
        raise OSError("This platform cannot report the path of an open file.")
    return os.readlink(proc)


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GetFinalPathNameByHandleW = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    _GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _GetFinalPathNameByHandleW.restype = wintypes.DWORD

    def _final_path_windows(fd: int) -> str:
        handle = msvcrt.get_osfhandle(fd)
        size = 32768
        buffer: ctypes.Array[ctypes.c_wchar] = ctypes.create_unicode_buffer(size)
        length = _GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if length == 0 or length >= size:
            raise ctypes.WinError()
        path = ctypes.wstring_at(ctypes.addressof(buffer), length)
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
        return path


def _decode_text(data: bytes, *, truncated: bool) -> str | None:
    """
    Decode retained bytes as UTF-8 text, or return None for content that is not text.

    A retention cut can split one multi-byte character at the end, so a truncated prefix may
    drop up to three trailing bytes. Anything else that fails to decode is not text.

    Returns:
        str | None: The text, or None when the bytes are not UTF-8.
    """
    for trim in range(4 if truncated else 1):
        try:
            return data[: len(data) - trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None
