# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared backend media persistence."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.media_persistence import MediaOrigin, persist_media_value_async


def _serializer(*, value: str = "/saved/media.bin") -> MagicMock:
    serializer = MagicMock()
    serializer.value = value
    serializer.save_b64_image_async = AsyncMock()
    return serializer


@pytest.mark.parametrize(
    ("value", "origin", "resolved_value", "resolved"),
    [
        ("https://example.test/media.png", MediaOrigin.REMOTE_URL, "https://example.test/media.png", True),
        ("/api/media?path=%2Ftmp%2Fmedia.png", MediaOrigin.MEDIA_REFERENCE, "/tmp/media.png", True),
        ("/api/media", MediaOrigin.MEDIA_REFERENCE, "/api/media", False),
    ],
)
async def test_existing_references_are_not_persisted(
    value: str, origin: MediaOrigin, resolved_value: str, resolved: bool
) -> None:
    factory = MagicMock()

    result = await persist_media_value_async(value=value, data_type="image_path", serializer_factory=factory)

    assert result.origin is origin
    assert result.value == resolved_value
    assert result.resolved is resolved
    assert result.persisted is False
    factory.assert_not_called()


async def test_existing_local_path_is_not_persisted(tmp_path: Path) -> None:
    media_path = tmp_path / "audio.wav"
    media_path.write_bytes(b"RIFF")
    factory = MagicMock()

    result = await persist_media_value_async(value=str(media_path), data_type="audio_path", serializer_factory=factory)

    assert result.origin is MediaOrigin.LOCAL_PATH
    assert result.value == str(media_path)
    assert result.persisted is False
    factory.assert_not_called()


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
