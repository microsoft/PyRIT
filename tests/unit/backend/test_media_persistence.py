# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared backend media persistence."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode

import pytest

from pyrit.backend.models.attacks import MessagePieceRequest
from pyrit.backend.services.media_persistence import (
    MediaAccessDeniedError,
    MediaOrigin,
    persist_media_value_async,
    persist_message_pieces_async,
    validate_media_path,
)
from pyrit.memory import CentralMemory, SQLiteMemory


def _serializer(*, value: str = "/saved/media.bin") -> MagicMock:
    serializer = MagicMock()
    serializer.value = value
    serializer.save_b64_image_async = AsyncMock()
    return serializer


@pytest.mark.parametrize(
    ("value", "origin", "resolved_value", "resolved"),
    [
        ("https://example.test/media.png", MediaOrigin.REMOTE_URL, "https://example.test/media.png", True),
        ("http://example.test/media.png", MediaOrigin.REMOTE_URL, "http://example.test/media.png", True),
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


@pytest.mark.parametrize("value", ["/api/media", "/api/media?path=", "/api/media?other=image.png"])
async def test_media_reference_requires_path_async(value: str) -> None:
    factory = MagicMock()

    with pytest.raises(ValueError, match="Media reference must include a path"):
        await persist_media_value_async(value=value, data_type="image_path", serializer_factory=factory)

    factory.assert_not_called()


class TestValidateMediaPath:
    @pytest.mark.parametrize("folder", ["prompt-memory-entries", "seed-prompt-entries"])
    def test_accepts_allowed_media_folders(self, *, tmp_path: Path, folder: str) -> None:
        path = tmp_path / folder / "nested" / "image.png"

        assert validate_media_path(path=str(path), allowed_root=tmp_path) == path.resolve()

    @pytest.mark.parametrize("location", ["outside", "results-root", "other-directory", "sibling", "traversal"])
    def test_rejects_unmanaged_paths(self, *, tmp_path: Path, location: str) -> None:
        root = tmp_path / "results"
        paths = {
            "outside": tmp_path / "outside.png",
            "results-root": root / "image.png",
            "other-directory": root / "other" / "image.png",
            "sibling": root / "prompt-memory-entries-other" / "image.png",
            "traversal": root / "prompt-memory-entries" / ".." / "image.png",
        }

        with pytest.raises(MediaAccessDeniedError, match="Access denied"):
            validate_media_path(path=str(paths[location]), allowed_root=root)

    def test_rejects_results_directory_itself(self, *, tmp_path: Path) -> None:
        with pytest.raises(MediaAccessDeniedError, match="not in a media subdirectory"):
            validate_media_path(path=str(tmp_path), allowed_root=tmp_path)

    def test_returns_canonical_path(self, *, tmp_path: Path) -> None:
        path = tmp_path / "prompt-memory-entries" / "nested" / ".." / "image.png"
        root = tmp_path / "nested" / ".."

        assert validate_media_path(path=str(path), allowed_root=root) == path.resolve()

    def test_checks_resolved_path_not_input_path(self, *, tmp_path: Path) -> None:
        root = tmp_path / "results"
        path = root / "prompt-memory-entries" / "image.png"
        outside = tmp_path / "outside.png"
        original_resolve = Path.resolve

        def resolve(candidate: Path, *, strict: bool = False) -> Path:
            return outside if candidate == path else original_resolve(candidate, strict=strict)

        with (
            patch.object(Path, "resolve", new=resolve),
            pytest.raises(MediaAccessDeniedError, match="outside the allowed results directory"),
        ):
            validate_media_path(path=str(path), allowed_root=root)

    def test_rejects_symlink_escape(self, *, tmp_path: Path) -> None:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"image")
        root = tmp_path / "results"
        path = root / "prompt-memory-entries" / "link.png"
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Cannot create symlink in this environment: {exc}")

        with pytest.raises(MediaAccessDeniedError, match="outside the allowed results directory"):
            validate_media_path(path=str(path), allowed_root=root)


@pytest.mark.usefixtures("patch_central_database")
class TestLocalMediaPaths:
    async def test_media_prefix_does_not_bypass_local_path_check_async(self) -> None:
        with (
            patch.object(Path, "is_file", return_value=True),
            pytest.raises(MediaAccessDeniedError, match="outside the allowed results directory"),
        ):
            await persist_media_value_async(value="/api/media-other.png", data_type="image_path")

    @pytest.mark.parametrize("reference", [False, True])
    async def test_existing_media_is_not_persisted_async(self, *, managed_media_path: Path, reference: bool) -> None:
        path = managed_media_path
        value = f"/api/media?{urlencode({'path': str(path)})}" if reference else str(path)
        factory = MagicMock()

        result = await persist_media_value_async(value=value, data_type="audio_path", serializer_factory=factory)

        assert result.value == str(path.resolve())
        assert result.origin is (MediaOrigin.MEDIA_REFERENCE if reference else MediaOrigin.LOCAL_PATH)
        assert result.resolved is True
        assert result.persisted is False
        factory.assert_not_called()

    @pytest.mark.parametrize("reference", [False, True])
    async def test_rejects_unmanaged_file_without_persistence_async(self, *, tmp_path: Path, reference: bool) -> None:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"image")
        value = f"/api/media?{urlencode({'path': str(outside)})}" if reference else str(outside)
        factory = MagicMock()

        with pytest.raises(MediaAccessDeniedError, match="outside the allowed results directory"):
            await persist_media_value_async(value=value, data_type="image_path", serializer_factory=factory)

        factory.assert_not_called()

    @pytest.mark.parametrize("reference", [False, True])
    @pytest.mark.parametrize("field", ["original_value", "converted_value"])
    async def test_checks_both_message_values_async(
        self, *, managed_media_path: Path, tmp_path: Path, reference: bool, field: str
    ) -> None:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"image")
        value = f"/api/media?{urlencode({'path': str(outside)})}" if reference else str(outside)
        piece = MessagePieceRequest(
            data_type="image_path",
            original_value=value if field == "original_value" else str(managed_media_path),
            converted_value=value if field == "converted_value" else str(managed_media_path),
            converted_value_data_type="audio_path",
        )
        before = piece.model_dump()
        factory = MagicMock()

        with pytest.raises(MediaAccessDeniedError, match="outside the allowed results directory"):
            await persist_message_pieces_async(pieces=[piece], serializer_factory=factory)

        assert piece.model_dump() == before
        factory.assert_not_called()

    @pytest.mark.parametrize("reference", [False, True])
    async def test_requires_configured_results_path_async(
        self, *, managed_media_path: Path, sqlite_instance: SQLiteMemory, reference: bool
    ) -> None:
        value = f"/api/media?{urlencode({'path': str(managed_media_path)})}" if reference else str(managed_media_path)
        with (
            patch.object(sqlite_instance, "results_path", None),
            pytest.raises(RuntimeError, match="results_path is not configured"),
        ):
            await persist_media_value_async(value=value, data_type="image_path")

    async def test_requires_initialized_memory_async(self, *, managed_media_path: Path) -> None:
        with (
            patch.object(CentralMemory, "get_memory_instance", side_effect=ValueError("not initialized")),
            pytest.raises(RuntimeError, match="Memory not initialized"),
        ):
            await persist_media_value_async(value=str(managed_media_path), data_type="image_path")


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
