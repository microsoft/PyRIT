# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote._audio_cache import cache_audio_bytes_async
from pyrit.memory import MemoryInterface

_FACTORY_PATH = "pyrit.datasets.seed_datasets.remote._audio_cache.data_serializer_factory"


def _make_mock_serializer(*, exists: bool = False) -> MagicMock:
    """Build a MagicMock serializer with memory configured."""
    mock_serializer = MagicMock()
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.results_path = "/results"
    mock_storage_io = AsyncMock()
    mock_storage_io.path_exists_async = AsyncMock(return_value=exists)
    mock_memory.results_storage_io = mock_storage_io
    mock_serializer._memory = mock_memory
    mock_serializer.data_sub_directory = "/seed-prompt-entries/audio"
    mock_serializer.save_data_async = AsyncMock()
    return mock_serializer


async def test_returns_cached_path_when_file_exists_and_skips_write() -> None:
    mock_serializer = _make_mock_serializer(exists=True)

    with patch(_FACTORY_PATH, return_value=mock_serializer):
        result = await cache_audio_bytes_async(filename="clip.wav", audio_bytes=b"audio", log_prefix="TestLoader")

    expected_path = str(Path("/results") / "seed-prompt-entries" / "audio" / "clip.wav")
    assert result == expected_path
    assert mock_serializer.value == expected_path
    mock_serializer.save_data_async.assert_not_called()


async def test_writes_bytes_on_cache_miss() -> None:
    mock_serializer = _make_mock_serializer(exists=False)

    with patch(_FACTORY_PATH, return_value=mock_serializer):
        result = await cache_audio_bytes_async(filename="clip.wav", audio_bytes=b"audio-bytes")

    mock_serializer.save_data_async.assert_called_once_with(data=b"audio-bytes", output_filename="clip")
    assert result == str(Path("/results") / "seed-prompt-entries" / "audio" / "clip.wav")


@pytest.mark.parametrize(
    ("filename", "expected_extension"),
    [("clip.wav", "wav"), ("clip.mp3", "mp3"), ("clip", None)],
)
async def test_passes_filename_extension_to_serializer_factory(
    *, filename: str, expected_extension: str | None
) -> None:
    mock_serializer = _make_mock_serializer(exists=True)

    with patch(_FACTORY_PATH, return_value=mock_serializer) as mock_factory:
        await cache_audio_bytes_async(filename=filename, audio_bytes=b"audio")

    mock_factory.assert_called_once_with(
        category="seed-prompt-entries",
        data_type="audio_path",
        extension=expected_extension,
    )


async def test_raises_runtime_error_when_memory_not_configured() -> None:
    mock_serializer = MagicMock()
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.results_path = None
    mock_memory.results_storage_io = None
    mock_serializer._memory = mock_memory

    with patch(_FACTORY_PATH, return_value=mock_serializer):
        with pytest.raises(RuntimeError, match="Serializer memory is not properly configured"):
            await cache_audio_bytes_async(filename="clip.wav", audio_bytes=b"audio", log_prefix="TestLoader")

    mock_serializer.save_data_async.assert_not_called()


async def test_path_exists_failure_is_logged_and_treated_as_cache_miss(caplog: pytest.LogCaptureFixture) -> None:
    mock_serializer = _make_mock_serializer(exists=False)
    mock_serializer._memory.results_storage_io.path_exists_async = AsyncMock(
        side_effect=Exception("storage IO unavailable")
    )

    with patch(_FACTORY_PATH, return_value=mock_serializer):
        with caplog.at_level(logging.WARNING):
            await cache_audio_bytes_async(filename="clip.wav", audio_bytes=b"audio", log_prefix="TestLoader")

    assert any("[TestLoader] Failed to check if cached audio clip.wav exists" in message for message in caplog.messages)
    mock_serializer.save_data_async.assert_called_once()
