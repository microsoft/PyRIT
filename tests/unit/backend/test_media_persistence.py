# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared backend media persistence."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from pyrit.backend.services.media_persistence import (
    MEDIA_SUBDIRECTORIES,
    MediaOrigin,
    persist_media_value_async,
    require_managed_blob_url,
)
from pyrit.memory import CentralMemory
from pyrit.memory.storage.storage import AzureBlobStorageIO

_BLOB_ROOT = "https://account.blob.core.windows.net/results"


def _serializer(*, value: str = "/saved/media.bin") -> MagicMock:
    serializer = MagicMock()
    serializer.value = value
    serializer.save_b64_image_async = AsyncMock()
    return serializer


def _results_root(root: str):
    return patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock(results_path=root))


@pytest.fixture
def stored_image(tmp_path: Path) -> Path:
    image_path = tmp_path / "results" / "prompt-memory-entries" / "images" / "stored.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"PNG")
    return image_path


async def test_media_reference_inside_results_is_resolved(stored_image: Path) -> None:
    factory = MagicMock()
    root = stored_image.parents[2]

    with _results_root(str(root)):
        result = await persist_media_value_async(
            value=f"/api/media?path={quote(str(stored_image))}", data_type="image_path", serializer_factory=factory
        )

    assert result.origin is MediaOrigin.MEDIA_REFERENCE
    assert result.value == str(stored_image.resolve())
    assert result.resolved is True
    assert result.persisted is False
    factory.assert_not_called()


async def test_existing_local_path_inside_results_is_not_persisted(stored_image: Path) -> None:
    factory = MagicMock()

    with _results_root(str(stored_image.parents[2])):
        result = await persist_media_value_async(
            value=str(stored_image), data_type="image_path", serializer_factory=factory
        )

    assert result.origin is MediaOrigin.LOCAL_PATH
    assert result.value == str(stored_image.resolve())
    assert result.persisted is False
    factory.assert_not_called()


async def test_blob_url_inside_results_container_is_kept() -> None:
    value = f"{_BLOB_ROOT}/prompt-memory-entries/images/stored.png?sv=2024&sig=signature"

    with _results_root(_BLOB_ROOT):
        result = await persist_media_value_async(value=value, data_type="image_path", serializer_factory=MagicMock())

    assert result.origin is MediaOrigin.REMOTE_URL
    assert result.value == value
    assert result.persisted is False


@pytest.mark.parametrize(
    "value",
    [
        "https://localHoSt/",
        "https://127.0.1.2/",
        "https://0177.0.23.19/",
        "https://2130706433/",
        "https://0x7f.00331.0246.174/",
        "https://[::1]/",
        "https://[fc00::]/",
        "https://169.254.169.254/",
        "https://example.test/media.png",
        "https://account.blob.core.windows.net/results/prompt-memory-entries/images/stored.png",
    ],
)
async def test_url_is_rejected_when_results_are_local(stored_image: Path, value: str) -> None:
    factory = MagicMock()

    with _results_root(str(stored_image.parents[2])), pytest.raises(ValueError, match="result storage"):
        await persist_media_value_async(value=value, data_type="image_path", serializer_factory=factory)

    factory.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "https://other.blob.core.windows.net/results/prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}-other/prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}/other-folder/stored.png",
        f"{_BLOB_ROOT}/prompt-memory-entries/../other-folder/stored.png",
        f"{_BLOB_ROOT}/prompt-memory-entries/images/..\\..\\other-folder/stored.png",
        f"{_BLOB_ROOT}/prompt-memory-entries/images/%5C..%5C..%5Cother-folder/stored.png",
        "http://account.blob.core.windows.net/results/prompt-memory-entries/images/stored.png",
        "https://169.254.169.254/results/prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}%2Fprompt-memory-entries/other-folder/stored.png",
        f"{_BLOB_ROOT}%2fprompt-memory-entries/stored.png",
        f"{_BLOB_ROOT}%5Cprompt-memory-entries/stored.png",
        f"{_BLOB_ROOT}/./prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}//prompt-memory-entries/images/stored.png",
        "https://account.blob.core.windows.net//results/prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}/prompt-memory-entries",
    ],
)
async def test_url_outside_results_container_is_rejected(value: str) -> None:
    with _results_root(_BLOB_ROOT), pytest.raises(ValueError, match="result storage"):
        await persist_media_value_async(value=value, data_type="image_path", serializer_factory=MagicMock())


@pytest.mark.parametrize(
    "value",
    [
        f"{_BLOB_ROOT}/prompt-memory-entries/images/stored.png",
        f"{_BLOB_ROOT}/seed-prompt-entries/images/stored.png?sv=2024&sig=signature",
        f"{_BLOB_ROOT}/prompt-memory-entries\\images\\stored.png",
    ],
)
def test_accepted_blob_url_is_read_from_a_media_folder(value: str) -> None:
    with _results_root(_BLOB_ROOT):
        require_managed_blob_url(value)

    blob_segments = AzureBlobStorageIO(container_url=_BLOB_ROOT)._resolve_blob_name(value).split("/")
    assert blob_segments[0] in MEDIA_SUBDIRECTORIES
    assert ".." not in blob_segments


def test_blob_url_generated_under_trailing_slash_root_is_accepted() -> None:
    root = f"{_BLOB_ROOT}/"

    with _results_root(root):
        require_managed_blob_url(f"{root}/prompt-memory-entries/images/stored.png")
        for value in (f"{_BLOB_ROOT}/other-folder/stored.png", f"{root}/other-folder/stored.png"):
            with pytest.raises(ValueError, match="result storage"):
                require_managed_blob_url(value)


@pytest.mark.parametrize("root", ["https://account.blob.core.windows.net", "https://account.blob.core.windows.net/"])
def test_blob_url_is_rejected_when_results_root_has_no_container(root: str) -> None:
    with _results_root(root), pytest.raises(ValueError, match="result storage"):
        require_managed_blob_url("https://account.blob.core.windows.net/prompt-memory-entries/images/stored.png")


@pytest.mark.parametrize("path_suffix", ["/../../OtherPath/", "/..//OtherPath/", "/%2E%2E%2f/OtherPath/"])
async def test_media_reference_traversal_is_rejected(stored_image: Path, path_suffix: str) -> None:
    root = stored_image.parents[2]
    value = f"/api/media?path={quote(str(root / 'prompt-memory-entries'))}{path_suffix}"

    with _results_root(str(root)), pytest.raises(ValueError, match="results directory"):
        await persist_media_value_async(value=value, data_type="image_path", serializer_factory=MagicMock())


async def test_local_path_outside_results_is_rejected(stored_image: Path, tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside")
    factory = MagicMock()

    with _results_root(str(stored_image.parents[2])), pytest.raises(ValueError, match="outside the allowed results"):
        await persist_media_value_async(value=str(outside_file), data_type="binary_path", serializer_factory=factory)

    factory.assert_not_called()


async def test_local_path_outside_media_folders_is_rejected(stored_image: Path) -> None:
    root = stored_image.parents[2]
    database_file = root / "pyrit.db"
    database_file.write_bytes(b"db")

    with _results_root(str(root)), pytest.raises(ValueError, match="not in a media folder"):
        await persist_media_value_async(
            value=str(database_file), data_type="binary_path", serializer_factory=MagicMock()
        )


async def test_symlink_escaping_results_is_rejected(stored_image: Path, tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"PNG")
    link = stored_image.parent / "link.png"
    link.symlink_to(outside_file)

    with _results_root(str(stored_image.parents[2])), pytest.raises(ValueError, match="outside the allowed results"):
        await persist_media_value_async(value=str(link), data_type="image_path", serializer_factory=MagicMock())


async def test_local_path_is_rejected_when_results_are_remote(stored_image: Path) -> None:
    with _results_root(_BLOB_ROOT), pytest.raises(ValueError, match="Azure Blob Storage"):
        await persist_media_value_async(value=str(stored_image), data_type="image_path", serializer_factory=MagicMock())


@pytest.mark.parametrize("value", ["/api/media", "/api/media?path=", "/api/media?path=a.png&path=b.png"])
async def test_malformed_media_reference_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="exactly one path"):
        await persist_media_value_async(value=value, data_type="image_path", serializer_factory=MagicMock())


async def test_data_uri_uses_explicit_mime_before_uri_mime() -> None:
    serializer = _serializer(value="/saved/media.jpg")
    factory = MagicMock(return_value=serializer)

    result = await persist_media_value_async(
        value="data:image/png;base64,UklGRg==",
        data_type="audio_path",
        mime_type="image/jpeg",
        serializer_factory=factory,
    )

    assert result.origin is MediaOrigin.DATA_URI
    assert result.extension == ".jpg"
    factory.assert_called_once_with(category="prompt-memory-entries", data_type="audio_path", extension=".jpg")
    serializer.save_b64_image_async.assert_awaited_once_with(data="UklGRg==")


async def test_data_uri_mime_policy_can_preserve_data_type_extension() -> None:
    serializer = _serializer(value="/saved/media.wav")
    factory = MagicMock(return_value=serializer)

    result = await persist_media_value_async(
        value="data:image/png;base64,UklGRg==",
        data_type="audio_path",
        use_data_uri_mime_type=False,
        serializer_factory=factory,
    )

    assert result.extension == ".wav"


async def test_malformed_data_uri_preserves_empty_payload_behavior() -> None:
    serializer = _serializer(value="/saved/media.png")

    result = await persist_media_value_async(
        value="data:image/png;base64",
        data_type="image_path",
        serializer_factory=MagicMock(return_value=serializer),
    )

    assert result.origin is MediaOrigin.DATA_URI
    assert result.extension == ".png"
    serializer.save_b64_image_async.assert_awaited_once_with(data="")


@pytest.mark.parametrize("strict", [False, True])
async def test_path_inspection_error_accepts_valid_raw_base64(strict: bool) -> None:
    serializer = _serializer()
    factory = MagicMock(return_value=serializer)

    with patch.object(Path, "is_file", side_effect=OSError("path inspection failed")):
        result = await persist_media_value_async(
            value="UklGRg==",
            data_type="audio_path",
            require_valid_base64_after_path_error=strict,
            serializer_factory=factory,
        )

    assert result.origin is MediaOrigin.RAW_BASE64
    assert result.persisted is True


async def test_strict_path_error_policy_rejects_non_base64() -> None:
    factory = MagicMock()

    with (
        patch.object(Path, "is_file", side_effect=PermissionError("permission denied")),
        pytest.raises(PermissionError, match="permission denied"),
    ):
        await persist_media_value_async(
            value="not raw base64!",
            data_type="audio_path",
            require_valid_base64_after_path_error=True,
            serializer_factory=factory,
        )

    factory.assert_not_called()


async def test_persistence_failure_returns_no_partial_result() -> None:
    serializer = _serializer()
    serializer.save_b64_image_async.side_effect = ValueError("invalid base64")

    with pytest.raises(ValueError, match="invalid base64"):
        await persist_media_value_async(
            value="not-base64",
            data_type="binary_path",
            serializer_factory=MagicMock(return_value=serializer),
        )
