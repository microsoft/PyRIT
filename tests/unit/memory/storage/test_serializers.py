# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import os
import re
import tempfile
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from PIL import Image

from pyrit.memory.storage import (
    AllowedCategories,
    BinaryPathDataTypeSerializer,
    DataTypeSerializer,
    ErrorDataTypeSerializer,
    ImagePathDataTypeSerializer,
    TextDataTypeSerializer,
    data_serializer_factory,
    set_message_piece_sha256_async,
    set_seed_sha256_async,
)
from pyrit.models import MessagePiece, SeedPrompt


def test_allowed_categories():
    entries = get_args(AllowedCategories)
    assert len(entries) == 2
    assert entries[0] == "seed-prompt-entries"
    assert entries[1] == "prompt-memory-entries"


def test_data_serializer_factory_text_no_data_throws(sqlite_instance):
    with pytest.raises(ValueError):
        data_serializer_factory(category="prompt-memory-entries", data_type="text")


def test_data_serializer_factory_text_with_data(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="text", value="test")
    assert isinstance(serializer, DataTypeSerializer)
    assert isinstance(serializer, TextDataTypeSerializer)
    assert serializer.data_type == "text"
    assert serializer.value == "test"
    assert serializer.data_on_disk() is False


def test_data_serializer_factory_error_with_data(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="error", value="test")
    assert isinstance(serializer, DataTypeSerializer)
    assert isinstance(serializer, ErrorDataTypeSerializer)
    assert serializer.data_type == "error"
    assert serializer.value == "test"
    assert serializer.data_on_disk() is False


async def test_data_serializer_text_read_data_throws(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="text", value="test")
    with pytest.raises(TypeError):
        await serializer.read_data_async()


async def test_data_serializer_text_save_data_throws(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="text", value="test")
    with pytest.raises(TypeError):
        await serializer.save_data_async(b"\x00")


async def test_data_serializer_error_read_data_throws(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="error", value="test")
    with pytest.raises(TypeError):
        await serializer.read_data_async()


async def test_data_serializer_error_save_data_throws(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="error", value="test")
    with pytest.raises(TypeError):
        await serializer.save_data_async(b"\x00")


async def test_data_serializer_factory_missing_category_raises_value_error():
    expected_error_message = (
        "The 'category' argument is mandatory and must be one of the following: "
        "('seed-prompt-entries', 'prompt-memory-entries')."
    )

    escaped_message = re.escape(expected_error_message)
    with pytest.raises(ValueError, match=escaped_message):
        await data_serializer_factory(data_type="text", value="test", category=None)


def test_image_path_normalizer_factory(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    assert isinstance(serializer, DataTypeSerializer)
    assert isinstance(serializer, ImagePathDataTypeSerializer)
    assert serializer.data_type == "image_path"
    assert serializer.data_on_disk()


async def test_image_path_save_data(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    await serializer.save_data_async(b"\x00")
    serializer_value = serializer.value
    assert serializer_value
    assert serializer_value.endswith(".png")
    assert os.path.isabs(serializer_value)
    assert os.path.exists(serializer_value)
    assert os.path.isfile(serializer_value)


async def test_image_path_read_data(sqlite_instance):
    data = b"\x00\x11\x22\x33"
    normalizer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    await normalizer.save_data_async(data)
    assert await normalizer.read_data_async() == data
    read_normalizer = data_serializer_factory(
        category="prompt-memory-entries", data_type="image_path", value=normalizer.value
    )
    assert await read_normalizer.read_data_async() == data


async def test_image_path_read_data_base64(sqlite_instance):
    data = b"AAAA"

    normalizer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    await normalizer.save_data_async(data)
    base_64_data = await normalizer.read_data_base64_async()
    assert base_64_data
    assert base_64_data == "QUFBQQ=="


async def test_path_not_exists(sqlite_instance):
    file_path = "non_existing_file.txt"
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path", value=file_path)

    with pytest.raises(FileNotFoundError):
        await serializer.read_data_async()


def test_get_extension(sqlite_instance):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as temp_file:
        temp_file_path = temp_file.name
        expected_extension = ".jpg"
        extension = DataTypeSerializer.get_extension(temp_file_path)
        assert extension == expected_extension


def test_get_mime_type(sqlite_instance):
    with tempfile.NamedTemporaryFile(suffix=".jpg") as temp_file:
        temp_file_path = temp_file.name
        expected_mime_type = "image/jpeg"
        mime_type = DataTypeSerializer.get_mime_type(temp_file_path)
        assert mime_type == expected_mime_type


async def test_save_b64_image(sqlite_instance):
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    await serializer.save_b64_image_async("\x00")
    serializer_value = str(serializer.value)
    assert serializer_value
    assert serializer_value.endswith(".png")
    assert os.path.isabs(serializer_value)
    assert os.path.exists(serializer_value)
    assert os.path.isfile(serializer_value)


async def test_audio_path_save_data(sqlite_instance):
    """Test saving audio data to disk."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="audio_path")
    await serializer.save_data_async(b"audio_data")
    assert serializer.value.endswith(".mp3")
    assert os.path.exists(serializer.value)
    assert os.path.isfile(serializer.value)


async def test_audio_path_read_data(sqlite_instance):
    """Test reading audio data from disk."""
    data = b"audio_content"
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="audio_path")
    await serializer.save_data_async(data)
    read_data = await serializer.read_data_async()
    assert read_data == data


async def test_video_path_save_data(sqlite_instance):
    """Test saving video data to disk."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="video_path")
    video_data = b"video_data"
    await serializer.save_data_async(video_data)
    assert serializer.value.endswith(".mp4")  # Assuming the default extension is '.mp4'
    assert os.path.exists(serializer.value)
    assert os.path.isfile(serializer.value)


async def test_video_path_read_data(sqlite_instance):
    """Test reading video data from disk."""
    video_data = b"video_content"
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="video_path")
    await serializer.save_data_async(video_data)
    read_data = await serializer.read_data_async()
    assert read_data == video_data


async def test_video_path_save_with_custom_extension(sqlite_instance):
    """Test saving video data with a custom file extension."""
    custom_extension = "avi"
    serializer = data_serializer_factory(
        category="prompt-memory-entries", data_type="video_path", extension=custom_extension
    )
    video_data = b"video_data"
    await serializer.save_data_async(video_data)
    assert serializer.value.endswith(f".{custom_extension}")
    assert os.path.exists(serializer.value)
    assert os.path.isfile(serializer.value)


async def test_get_sha256_from_text(sqlite_instance):
    """Test SHA256 hash calculation for text data."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="text", value="test_string")
    sha256_hash = await serializer.get_sha256_async()
    expected_hash = hashlib.sha256(b"test_string").hexdigest()
    assert sha256_hash == expected_hash


async def test_get_sha256_from_image_file(sqlite_instance):
    """Test SHA256 hash calculation for file data."""
    data = b"file_content.png"
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    await serializer.save_data_async(data)
    sha256_hash = await serializer.get_sha256_async()
    expected_hash = hashlib.sha256(data).hexdigest()
    assert sha256_hash == expected_hash


def test_is_azure_storage_url(sqlite_instance):
    """Test Azure Storage URL validation."""
    valid_url = "https://mystorageaccount.blob.core.windows.net/container/file.txt"
    invalid_url = "https://example.com/file.txt"

    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="url", value=valid_url)
    assert serializer._is_azure_storage_url(valid_url) is True
    assert serializer._is_azure_storage_url(invalid_url) is False


async def test_read_data_local_file_with_dummy_image(sqlite_instance):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_image_file:
        image_path = temp_image_file.name
        image = Image.new("RGB", (10, 10), color="red")
        image.save(image_path)

    try:
        mock_storage_io = AsyncMock()
        mock_storage_io.path_exists_async.return_value = True
        with open(image_path, "rb") as f:
            mock_storage_io.read_file_async.return_value = f.read()

        with patch("pyrit.memory.storage.serializers.DiskStorageIO", return_value=mock_storage_io):
            serializer = data_serializer_factory(
                category="prompt-memory-entries", data_type="image_path", value=image_path
            )

            data = await serializer.read_data_async()

            with open(image_path, "rb") as f:
                expected_data = f.read()
            assert data == expected_data

            mock_storage_io.path_exists_async.assert_awaited_once_with(path=image_path)
            mock_storage_io.read_file_async.assert_awaited_once_with(image_path)
    finally:
        # Clean up the temporary file
        if os.path.exists(image_path):
            os.remove(image_path)


async def test_get_data_filename(sqlite_instance):
    """Test get_data_filename when a file_name is provided."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    provided_filename = "custom_image_name"
    filename = await serializer.get_data_filename_async(file_name=provided_filename)
    assert str(filename).endswith(f"{provided_filename}.{serializer.file_extension}")
    assert os.path.isabs(filename)
    assert os.path.exists(os.path.dirname(filename))
    assert not os.path.exists(filename)  # File should not exist yet


def test_binary_path_normalizer_factory(sqlite_instance):
    """Test factory creates BinaryPathDataTypeSerializer correctly."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="binary_path")
    assert isinstance(serializer, DataTypeSerializer)
    assert isinstance(serializer, BinaryPathDataTypeSerializer)
    assert serializer.data_type == "binary_path"
    assert serializer.data_on_disk()


def test_binary_path_normalizer_factory_with_value(sqlite_instance):
    """Test factory creates BinaryPathDataTypeSerializer with value."""
    serializer = data_serializer_factory(
        category="prompt-memory-entries", data_type="binary_path", value="/path/to/file.bin"
    )
    assert isinstance(serializer, BinaryPathDataTypeSerializer)
    assert serializer.data_type == "binary_path"
    assert serializer.value == "/path/to/file.bin"
    assert serializer.data_on_disk()


async def test_binary_path_save_data(sqlite_instance):
    """Test saving binary data to disk."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="binary_path")
    await serializer.save_data_async(b"\x00\x01\x02\x03")
    serializer_value = serializer.value
    assert serializer_value
    assert serializer_value.endswith(".bin")
    assert os.path.isabs(serializer_value)
    assert os.path.exists(serializer_value)
    assert os.path.isfile(serializer_value)


async def test_binary_path_read_data(sqlite_instance):
    """Test reading binary data from disk."""
    data = b"\x00\x11\x22\x33\x44\x55"
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="binary_path")
    await serializer.save_data_async(data)
    assert await serializer.read_data_async() == data
    # Test reading with a new serializer initialized with the saved path
    read_serializer = data_serializer_factory(
        category="prompt-memory-entries", data_type="binary_path", value=serializer.value
    )
    assert await read_serializer.read_data_async() == data


async def test_binary_path_save_with_custom_extension(sqlite_instance):
    """Test saving binary data with a custom file extension."""
    custom_extension = "pdf"
    serializer = data_serializer_factory(
        category="prompt-memory-entries", data_type="binary_path", extension=custom_extension
    )
    binary_data = b"PDF binary content"
    await serializer.save_data_async(binary_data)
    assert serializer.value.endswith(f".{custom_extension}")
    assert os.path.exists(serializer.value)
    assert os.path.isfile(serializer.value)


async def test_binary_path_subdirectory(sqlite_instance):
    """Test that binary data is stored in the correct subdirectory."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="binary_path")
    await serializer.save_data_async(b"test data")
    assert "/binaries/" in serializer.value or "\\binaries\\" in serializer.value


def test_get_storage_io_raises_when_results_storage_io_none():
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    serializer.value = "https://account.blob.core.windows.net/container/path/image.png"
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with pytest.raises(RuntimeError, match="results_storage_io is not configured"):
            serializer._get_storage_io()


async def test_save_data_raises_when_results_storage_io_none():
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with patch.object(
            serializer, "get_data_filename_async", new_callable=AsyncMock, return_value="local/path/img.png"
        ):
            with pytest.raises(RuntimeError, match="Storage IO not initialized"):
                await serializer.save_data_async(b"\x89PNG")


async def test_save_b64_image_raises_when_results_storage_io_none():
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with patch.object(
            serializer, "get_data_filename_async", new_callable=AsyncMock, return_value="local/path/img.png"
        ):
            import base64

            b64_data = base64.b64encode(b"\x89PNG").decode()
            with pytest.raises(RuntimeError, match="Storage IO not initialized"):
                await serializer.save_b64_image_async(b64_data)


async def test_save_formatted_audio_raises_when_results_storage_io_none():
    from pyrit.memory.storage import data_serializer_factory as factory

    serializer = factory(category="prompt-memory-entries", data_type="audio_path")
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    azure_url = "https://account.blob.core.windows.net/container/audio/test.wav"
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with patch.object(serializer, "get_data_filename_async", new_callable=AsyncMock, return_value=azure_url):
            with patch("wave.open"):
                with patch("aiofiles.open", new_callable=MagicMock) as mock_aio:
                    mock_file = MagicMock()
                    mock_file.__aenter__ = AsyncMock(return_value=mock_file)
                    mock_file.__aexit__ = AsyncMock(return_value=False)
                    mock_file.read = AsyncMock(return_value=b"audio_bytes")
                    mock_aio.return_value = mock_file
                    with pytest.raises(RuntimeError, match="results_storage_io is not initialized"):
                        await serializer.save_formatted_audio_async(data=b"\x00\x01\x02")


async def test_save_formatted_audio_writes_local_wav_via_to_thread(sqlite_instance, tmp_path):
    """save_formatted_audio (local-disk path) should produce a readable WAV via _write_wav_sync."""
    import wave

    from pyrit.memory.storage import data_serializer_factory as factory

    serializer = factory(category="prompt-memory-entries", data_type="audio_path")
    output_path = tmp_path / "out.wav"
    with patch.object(serializer, "get_data_filename_async", new_callable=AsyncMock, return_value=str(output_path)):
        pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"
        await serializer.save_formatted_audio_async(
            data=pcm,
            num_channels=1,
            sample_width=2,
            sample_rate=16000,
        )

    assert os.path.exists(serializer.value)
    assert serializer.value == str(output_path)
    with wave.open(str(output_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


def test_write_wav_sync_produces_readable_wav(tmp_path):
    """_write_wav_sync should produce a WAV file readable by wave.open with the same metadata and frames."""
    import wave

    from pyrit.memory.storage.serializers import _write_wav_sync

    out_path = tmp_path / "direct.wav"
    pcm = b"\x10\x00\x20\x00\x30\x00\x40\x00"
    _write_wav_sync(
        str(out_path),
        num_channels=2,
        sample_width=2,
        sample_rate=8000,
        data=pcm,
    )

    assert out_path.exists()
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 8000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


async def test_save_formatted_audio_writes_azure_wav_via_storage_io(sqlite_instance, tmp_path):
    """Azure-storage branch should round-trip data through _write_wav_sync to storage_io.write_file."""
    import wave

    from pyrit.common import path as common_path
    from pyrit.memory.storage import data_serializer_factory as factory

    captured: dict[str, bytes] = {}

    async def _capture_write(file_path, data):
        captured["data"] = data
        captured["path"] = str(file_path)

    mock_storage_io = MagicMock()
    mock_storage_io.write_file_async = AsyncMock(side_effect=_capture_write)
    mock_memory = MagicMock()
    mock_memory.results_storage_io = mock_storage_io

    serializer = factory(category="prompt-memory-entries", data_type="audio_path")
    azure_url = "https://account.blob.core.windows.net/container/audio/test.wav"

    pcm = b"\xaa\xbb\xcc\xdd\xee\xff\x00\x11"
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with patch.object(serializer, "get_data_filename_async", new_callable=AsyncMock, return_value=azure_url):
            # Redirect so the temp_audio.wav write lands in tmp_path
            with patch.object(common_path, "DB_DATA_PATH", str(tmp_path)):
                from pyrit.memory.storage import serializers as dts_module

                with patch.object(dts_module, "DB_DATA_PATH", str(tmp_path)):
                    await serializer.save_formatted_audio_async(
                        data=pcm,
                        num_channels=1,
                        sample_width=2,
                        sample_rate=16000,
                    )

    assert captured["path"] == azure_url
    # Bytes written to storage_io must be a valid WAV with the requested metadata + frames
    written_wav = tmp_path / "written.wav"
    written_wav.write_bytes(captured["data"])
    with wave.open(str(written_wav), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.readframes(wav_file.getnframes()) == pcm
    # Temp file should be cleaned up
    assert not (tmp_path / "temp_audio.wav").exists()


async def test_get_data_filename_raises_when_results_storage_io_none():
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    serializer._file_path = None
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    mock_memory.results_path = "/local/results"
    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with pytest.raises(RuntimeError, match="results_storage_io is not initialized"):
            await serializer.get_data_filename_async()


async def test_get_data_filename_uses_db_data_path_when_results_path_falsy():
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
    serializer._file_path = None
    mock_memory = MagicMock()
    mock_memory.results_path = None
    mock_storage_io = AsyncMock()
    mock_memory.results_storage_io = mock_storage_io
    with (
        patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory),
        patch("pyrit.common.path.DB_DATA_PATH", "/fallback/db_data"),
    ):
        result = await serializer.get_data_filename_async(file_name="test_file")
    result_str = str(result).replace("\\", "/")
    assert "/fallback/db_data" in result_str
    assert result_str.endswith(".png")


async def test_save_formatted_audio_azure_storage_unlinks_local_temp(tmp_path):
    """save_formatted_audio_async cleans up the local temp WAV after writing to Azure storage."""
    from pyrit.memory.storage import data_serializer_factory as factory

    serializer = factory(category="prompt-memory-entries", data_type="audio_path")
    mock_memory = MagicMock()
    mock_storage_io = AsyncMock()
    mock_memory.results_storage_io = mock_storage_io
    azure_url = "https://account.blob.core.windows.net/container/audio/test.wav"

    with (
        patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory),
        patch.object(serializer, "get_data_filename_async", new_callable=AsyncMock, return_value=azure_url),
        patch("pyrit.memory.storage.serializers.DB_DATA_PATH", tmp_path),
    ):
        await serializer.save_formatted_audio_async(data=b"\x00\x01\x02\x03")

    # The local temp file written via wave.open should have been unlinked after upload.
    assert list(tmp_path.glob("*.wav")) == []
    mock_storage_io.write_file_async.assert_awaited_once()
    assert mock_storage_io.write_file_async.call_args[0][0] == azure_url
    assert serializer.value == azure_url


async def test_set_message_piece_sha256_async_sets_text_hashes(sqlite_instance):
    piece = MessagePiece(role="user", original_value="Hello")
    piece.original_value = "newvalue"
    piece.converted_value = "newvalue"

    await set_message_piece_sha256_async(piece)

    expected = "70e01503173b8e904d53b40b3ebb3bded5e5d3add087d3463a4b1abe92f1a8ca"
    assert piece.original_value_sha256 == expected
    assert piece.converted_value_sha256 == expected


async def test_set_seed_sha256_async_sets_text_hash(sqlite_instance):
    seed = SeedPrompt(value="Hello1", data_type="text")

    await set_seed_sha256_async(seed)

    assert seed.value_sha256 == "948edbe7ede5aa7423476ae29dcd7d61e7711a071aea0d83698377effa896525"


async def test_save_formatted_audio_async_cleans_up_temp_file_on_azure_upload_failure(tmp_path):
    """Regression test: temp file must be deleted even when Azure upload fails."""
    serializer = data_serializer_factory(category="prompt-memory-entries", data_type="audio_path")

    mock_memory = MagicMock()
    mock_storage_io = AsyncMock()
    mock_storage_io.write_file_async.side_effect = RuntimeError("Azure upload failed")
    mock_memory.results_storage_io = mock_storage_io

    azure_url = "https://account.blob.core.windows.net/container/audio/test.wav"

    # Record existing wav files BEFORE test runs
    existing_wav_files = set(tmp_path.glob("*.wav"))

    with patch.object(type(serializer), "_memory", new_callable=PropertyMock, return_value=mock_memory):
        with patch.object(serializer, "get_data_filename_async", new_callable=AsyncMock, return_value=azure_url):
            with patch("pyrit.memory.storage.serializers.DB_DATA_PATH", tmp_path):
                with pytest.raises(RuntimeError, match="Azure upload failed"):
                    await serializer.save_formatted_audio_async(data=b"\x00\x01\x02")

    # Check no NEW wav files leaked after test
    leaked_files = set(tmp_path.glob("*.wav")) - existing_wav_files
    assert leaked_files == set(), f"Temp files leaked: {leaked_files}"
