# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common import convert_local_image_to_data_url
from pyrit.memory.sqlite_memory import SQLiteMemory


@pytest.mark.asyncio()
async def test_convert_image_to_data_url_file_not_found():
    with pytest.raises(FileNotFoundError):
        await convert_local_image_to_data_url("nonexistent.jpg")


@pytest.mark.asyncio()
async def test_convert_image_with_unsupported_extension():
    with NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name

    assert os.path.exists(tmp_file_name)

    with pytest.raises(ValueError) as exc_info:
        await convert_local_image_to_data_url(tmp_file_name)

    assert "Unsupported image format" in str(exc_info.value)

    os.remove(tmp_file_name)


@pytest.mark.asyncio
async def test_convert_local_image_to_data_url_unsupported_format():
    # Should raise ValueError for unsupported extension
    with NamedTemporaryFile(suffix=".webp", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name
    try:
        with pytest.raises(ValueError) as excinfo:
            await convert_local_image_to_data_url(tmp_file_name)
        assert "Unsupported image format" in str(excinfo.value)
    finally:
        os.remove(tmp_file_name)


@pytest.mark.asyncio
async def test_convert_local_image_to_data_url_missing_file():
    # Should raise FileNotFoundError for missing file
    with pytest.raises(FileNotFoundError):
        await convert_local_image_to_data_url("not_a_real_file.jpg")


@pytest.mark.asyncio()
@patch("os.path.exists", return_value=True)
@patch("mimetypes.guess_type", return_value=("image/jpg", None))
@patch("pyrit.models.data_type_serializer.ImagePathDataTypeSerializer")
@patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=SQLiteMemory(db_path=":memory:"))
async def test_convert_image_to_data_url_success(
    mock_get_memory_instance, mock_serializer_class, mock_guess_type, mock_exists
):
    with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name
    mock_serializer_instance = MagicMock()
    mock_serializer_instance.read_data_base64 = AsyncMock(return_value="encoded_base64_string")
    mock_serializer_class.return_value = mock_serializer_instance

    assert os.path.exists(tmp_file_name)

    result = await convert_local_image_to_data_url(tmp_file_name)
    assert "data:image/jpeg;base64,encoded_base64_string" in result

    # Assertions for the mocks
    mock_serializer_class.assert_called_once_with(
        category="prompt-memory-entries", prompt_text=tmp_file_name, extension=".jpg"
    )
    mock_serializer_instance.read_data_base64.assert_called_once()

    os.remove(tmp_file_name)
