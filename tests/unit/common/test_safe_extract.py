# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import stat
import zipfile

import pytest

from pyrit.common.safe_extract import (
    DEFAULT_MAX_COMPRESSION_RATIO,
    UnsafeArchiveError,
    safe_extract_zip,
)


def _zip_with(entries):
    """
    Build an in-memory zip.

    entries: list of (filename, data, external_attr_mode_or_None)
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3  # unix, so external_attr is interpreted as mode
            if mode is not None:
                info.external_attr = mode << 16
            zf.writestr(info, data)
    buf.seek(0)
    return buf


def test_happy_path_extracts_files(tmp_path):
    archive = _zip_with(
        [
            ("a.txt", b"hello", None),
            ("nested/b.txt", b"world", None),
        ]
    )
    out = safe_extract_zip(source=archive, dest_dir=tmp_path / "out")

    assert (out / "a.txt").read_bytes() == b"hello"
    assert (out / "nested" / "b.txt").read_bytes() == b"world"


def test_rejects_dotdot_traversal(tmp_path):
    archive = _zip_with([("../escape.txt", b"x", None)])
    with pytest.raises(UnsafeArchiveError, match="path traversal"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")
    # destination should be created but empty
    assert list((tmp_path / "out").iterdir()) == []


def test_rejects_absolute_unix_path(tmp_path):
    archive = _zip_with([("/etc/passwd", b"x", None)])
    with pytest.raises(UnsafeArchiveError, match="absolute path"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")


def test_rejects_drive_letter_path(tmp_path):
    archive = _zip_with([("C:/windows/system32/x.dll", b"x", None)])
    with pytest.raises(UnsafeArchiveError, match="drive-letter"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")


def test_rejects_symlink_entry(tmp_path):
    archive = _zip_with([("link", b"../target", stat.S_IFLNK | 0o777)])
    with pytest.raises(UnsafeArchiveError, match="disallowed entry type"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")


def test_rejects_device_entry(tmp_path):
    archive = _zip_with([("dev", b"", stat.S_IFBLK | 0o600)])
    with pytest.raises(UnsafeArchiveError, match="disallowed entry type"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")


def test_rejects_fifo_entry(tmp_path):
    archive = _zip_with([("pipe", b"", stat.S_IFIFO | 0o600)])
    with pytest.raises(UnsafeArchiveError, match="disallowed entry type"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out")


def test_rejects_total_size_bomb(tmp_path):
    archive = _zip_with([(f"f{i}.txt", b"A" * 1000, None) for i in range(5)])
    with pytest.raises(UnsafeArchiveError, match="total uncompressed size"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out", max_total_size=2000)


def test_rejects_single_file_bomb(tmp_path):
    archive = _zip_with([("big.bin", b"A" * 1000, None)])
    with pytest.raises(UnsafeArchiveError, match="exceeds cap"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out", max_file_size=500)


def test_rejects_compression_ratio_bomb(tmp_path):
    # DEFLATE 1 MiB of zeros into a few hundred bytes, classic ratio bomb.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        info = zipfile.ZipInfo("bomb.bin")
        info.create_system = 3
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, b"\x00" * (1024 * 1024))
    buf.seek(0)

    with pytest.raises(UnsafeArchiveError, match="compression ratio"):
        safe_extract_zip(
            source=buf,
            dest_dir=tmp_path / "out",
            max_compression_ratio=DEFAULT_MAX_COMPRESSION_RATIO,
            max_file_size=10 * 1024 * 1024,
        )


def test_rejects_excessive_file_count(tmp_path):
    archive = _zip_with([(f"f{i}.txt", b"x", None) for i in range(10)])
    with pytest.raises(UnsafeArchiveError, match="entries"):
        safe_extract_zip(source=archive, dest_dir=tmp_path / "out", max_file_count=5)


def test_no_partial_write_when_one_member_invalid(tmp_path):
    # First 2 entries are valid, third escapes, nothing should be written.
    archive = _zip_with(
        [
            ("ok1.txt", b"one", None),
            ("ok2.txt", b"two", None),
            ("../escape.txt", b"bad", None),
        ]
    )
    out = tmp_path / "out"
    with pytest.raises(UnsafeArchiveError):
        safe_extract_zip(source=archive, dest_dir=out)

    assert list(out.iterdir()) == []


def test_accepts_bytes_source(tmp_path):
    buf = _zip_with([("a.txt", b"hi", None)])
    out = safe_extract_zip(source=buf.getvalue(), dest_dir=tmp_path / "out")
    assert (out / "a.txt").read_bytes() == b"hi"


def test_accepts_path_source(tmp_path):
    zip_path = tmp_path / "src.zip"
    zip_path.write_bytes(_zip_with([("a.txt", b"hi", None)]).getvalue())

    out = safe_extract_zip(source=zip_path, dest_dir=tmp_path / "out")
    assert (out / "a.txt").read_bytes() == b"hi"


def test_destination_dir_is_created(tmp_path):
    archive = _zip_with([("a.txt", b"hi", None)])
    target = tmp_path / "does" / "not" / "exist"

    out = safe_extract_zip(source=archive, dest_dir=target)
    assert out.is_dir()
    assert (out / "a.txt").read_bytes() == b"hi"


def test_returns_resolved_destination(tmp_path):
    archive = _zip_with([("a.txt", b"hi", None)])
    out = safe_extract_zip(source=archive, dest_dir=tmp_path / "out")
    assert out == (tmp_path / "out").resolve()
    assert out.is_absolute()


def test_path_traversal_check_handles_invalid_chars(tmp_path):
    # Python's zipfile reader truncates filenames at null bytes, so this can't
    # be triggered through a real archive — but the validator should still
    # surface UnsafeArchiveError rather than leak a ValueError if a future
    # caller hands us a manually-built ZipInfo with such a name.
    from pyrit.common.safe_extract import _reject_path_traversal

    info = zipfile.ZipInfo()
    info.filename = "foo\x00.txt"

    with pytest.raises(UnsafeArchiveError, match="invalid characters"):
        _reject_path_traversal(info, tmp_path.resolve())


def test_rejects_symlink_when_create_system_is_dos(tmp_path):
    # Adversary sets create_system=0 (DOS) but with Unix S_IFLNK upper bits.
    # Helper must still reject because create_system is attacker-controlled.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 0
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"/etc/passwd")
    buf.seek(0)

    with pytest.raises(UnsafeArchiveError, match="disallowed entry type"):
        safe_extract_zip(source=buf, dest_dir=tmp_path / "out")


def test_directory_entry_happy_path(tmp_path):
    # Explicit directory entry (filename ending in '/') plus a regular file inside it.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        dir_info = zipfile.ZipInfo("subdir/")
        dir_info.create_system = 3
        zf.writestr(dir_info, b"")
        file_info = zipfile.ZipInfo("subdir/file.txt")
        file_info.create_system = 3
        zf.writestr(file_info, b"hi")
    buf.seek(0)

    out = safe_extract_zip(source=buf, dest_dir=tmp_path / "out")
    assert (out / "subdir").is_dir()
    assert (out / "subdir" / "file.txt").read_bytes() == b"hi"


def test_rejects_directory_entry_with_traversal(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("../escape/")
        info.create_system = 3
        zf.writestr(info, b"")
    buf.seek(0)

    with pytest.raises(UnsafeArchiveError, match="path traversal"):
        safe_extract_zip(source=buf, dest_dir=tmp_path / "out")


def test_rejects_zero_compress_size_with_nonzero_file_size(tmp_path):
    # Malformed metadata: declared compress_size=0 but file_size>0.
    # This is the bypass path previously short-circuited by the ratio check.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("malformed.bin")
        info.create_system = 3
        zf.writestr(info, b"X" * 100)
    raw = bytearray(buf.getvalue())
    # Patch the central-directory entry: set compress_size to 0, leave file_size.
    import struct

    idx = raw.rfind(b"PK\x01\x02")
    assert idx != -1, "central directory signature missing, zip layout assumption is stale"
    struct.pack_into("<I", raw, idx + 20, 0)
    patched = io.BytesIO(bytes(raw))

    with pytest.raises(UnsafeArchiveError, match="compressed size"):
        safe_extract_zip(source=patched, dest_dir=tmp_path / "out")
