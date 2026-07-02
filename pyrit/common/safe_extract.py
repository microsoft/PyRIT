# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Defensive ZIP extraction for untrusted remote archives.

Remote dataset loaders in PyRIT download ZIP archives from third-party sources
and feed them to ``zipfile.ZipFile.extractall()``. ``extractall`` does not
validate member paths, file sizes, or entry types, which leaves the loader
vulnerable to Zip Slip (CWE-22), zip bombs, and symlink-based path escape if
any upstream source is tampered with.

``safe_extract_zip`` validates every archive member before writing anything to
disk. If any member fails validation, no archive members are written from the
failing call (pre-existing contents of ``dest_dir`` are untouched).
"""

from __future__ import annotations

import io
import logging
import os
import stat
import zipfile
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

# 5 GiB cumulative uncompressed size across all members
DEFAULT_MAX_TOTAL_SIZE = 5 * 1024**3
# 1 GiB cap on any single member
DEFAULT_MAX_FILE_SIZE = 1 * 1024**3
# 50_000 entries: above legitimate dataset sizes, defeats inode DoS
DEFAULT_MAX_FILE_COUNT = 50_000
# Reject members whose uncompressed/compressed ratio exceeds this (zip bomb)
DEFAULT_MAX_COMPRESSION_RATIO = 100

# Sanitized permissions applied to extracted entries, stripping any setuid /
# setgid / sticky / world-write bits the archive may have requested.
_EXTRACTED_FILE_MODE = 0o644
_EXTRACTED_DIR_MODE = 0o755

# Predicates for entry types we refuse to extract.
_DISALLOWED_TYPE_PREDICATES = (
    stat.S_ISLNK,
    stat.S_ISBLK,
    stat.S_ISCHR,
    stat.S_ISFIFO,
    stat.S_ISSOCK,
)

ZipSource = str | os.PathLike | bytes | IO[bytes]


class UnsafeArchiveError(Exception):
    """Raised when an archive member fails a safe-extraction precondition."""


def safe_extract_zip(
    *,
    source: ZipSource,
    dest_dir: str | os.PathLike,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_file_count: int = DEFAULT_MAX_FILE_COUNT,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> Path:
    """
    Extract a ZIP archive after validating every member.

    Validation runs in a single pass over the archive's central directory
    before any bytes are written. If any check fails, ``UnsafeArchiveError`` is
    raised and no archive members are written from this call. After extraction
    each member's filesystem mode is replaced with a sanitized default so a
    tampered archive cannot set setuid/setgid/sticky/exec bits on the host.

    Args:
        source: Path, bytes, or file-like object accepted by ``zipfile.ZipFile``.
        dest_dir: Directory to extract into. Created if it does not exist.
        max_total_size: Cap on the sum of uncompressed member sizes.
        max_file_size: Cap on any single member's uncompressed size.
        max_file_count: Cap on the number of members in the archive.
        max_compression_ratio: Reject members whose uncompressed/compressed
            ratio exceeds this value (zip bomb defense).

    Returns:
        Resolved destination directory.

    Raises:
        UnsafeArchiveError: If any member fails validation.
    """
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)

    dest_real = Path(dest_dir).resolve()
    dest_real.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as zf:
        members = zf.infolist()
        try:
            _validate_members(
                members,
                dest_real=dest_real,
                max_total_size=max_total_size,
                max_file_size=max_file_size,
                max_file_count=max_file_count,
                max_compression_ratio=max_compression_ratio,
            )
        except UnsafeArchiveError as exc:
            logger.warning("safe_extract_zip rejected archive: %s", exc)
            raise
        for m in members:
            extracted = Path(zf.extract(m, dest_real))
            _sanitize_extracted_permissions(extracted)

    return dest_real


def _sanitize_extracted_permissions(path: Path) -> None:
    # zipfile.ZipFile.extract applies the archive's external_attr mode bits on
    # POSIX, so a tampered archive can request setuid/setgid/sticky or
    # executable bits on extracted entries. Replace with a sane default.
    try:
        if path.is_dir():
            os.chmod(path, _EXTRACTED_DIR_MODE)
        else:
            os.chmod(path, _EXTRACTED_FILE_MODE)
    except OSError as exc:
        logger.warning("safe_extract_zip could not chmod %s: %s", path, exc)


def _validate_members(
    members: list[zipfile.ZipInfo],
    *,
    dest_real: Path,
    max_total_size: int,
    max_file_size: int,
    max_file_count: int,
    max_compression_ratio: int,
) -> None:
    if len(members) > max_file_count:
        raise UnsafeArchiveError(f"archive contains {len(members)} entries (max {max_file_count})")

    total = 0
    for m in members:
        _reject_disallowed_entry_type(m)
        _reject_absolute_path(m)
        _reject_path_traversal(m, dest_real)
        _reject_oversized_member(m, max_file_size=max_file_size)
        _reject_compression_bomb(m, max_ratio=max_compression_ratio)

        total += m.file_size
        if total > max_total_size:
            raise UnsafeArchiveError(f"total uncompressed size exceeds {max_total_size} bytes")


def _reject_disallowed_entry_type(m: zipfile.ZipInfo) -> None:
    # The upper 16 bits of external_attr hold the Unix mode when the archive
    # was created on a Unix system. Check unconditionally because create_system
    # is attacker-controlled metadata: a zip crafted with create_system=0 (DOS)
    # but Unix-style mode bits set should still be rejected.
    mode = m.external_attr >> 16
    if any(predicate(mode) for predicate in _DISALLOWED_TYPE_PREDICATES):
        raise UnsafeArchiveError(f"disallowed entry type: {m.filename}")


def _reject_absolute_path(m: zipfile.ZipInfo) -> None:
    name = m.filename
    if name.startswith(("/", "\\")):
        raise UnsafeArchiveError(f"absolute path in archive: {name}")
    if len(name) >= 2 and name[1] == ":":
        raise UnsafeArchiveError(f"drive-letter path in archive: {name}")


def _reject_path_traversal(m: zipfile.ZipInfo, dest_real: Path) -> None:
    # Explicit null-byte check: Path.resolve() only raises ValueError for
    # embedded null bytes on POSIX. On Windows the path round-trips with the
    # null byte intact, so we need an OS-independent guard up front.
    if "\x00" in m.filename:
        raise UnsafeArchiveError(f"invalid characters in archive entry: {m.filename!r}")
    try:
        target = (dest_real / m.filename).resolve()
    except ValueError as exc:
        # Fallback for any other ValueError from Path construction or resolve.
        raise UnsafeArchiveError(f"invalid characters in archive entry: {m.filename!r}") from exc
    try:
        target.relative_to(dest_real)
    except ValueError as exc:
        raise UnsafeArchiveError(f"path traversal in archive: {m.filename!r} escapes {dest_real}") from exc


def _reject_oversized_member(m: zipfile.ZipInfo, *, max_file_size: int) -> None:
    if m.file_size > max_file_size:
        raise UnsafeArchiveError(f"member {m.filename!r} uncompressed size {m.file_size} exceeds cap {max_file_size}")


def _reject_compression_bomb(m: zipfile.ZipInfo, *, max_ratio: int) -> None:
    if m.file_size <= 0:
        return
    if m.compress_size <= 0:
        # Declared non-zero uncompressed size with zero compressed size is
        # malformed metadata, refuse rather than skip the ratio check.
        raise UnsafeArchiveError(
            f"member {m.filename!r} declares uncompressed size {m.file_size} but compressed size {m.compress_size}"
        )
    ratio = m.file_size / m.compress_size
    if ratio > max_ratio:
        raise UnsafeArchiveError(f"member {m.filename!r} compression ratio {ratio:.1f} exceeds cap {max_ratio}")
