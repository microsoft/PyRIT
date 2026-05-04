# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.display_response import display_image_response


@pytest.fixture()
def _mock_central_memory():
    mock_memory = MagicMock()
    mock_memory.results_storage_io.read_file = AsyncMock(return_value=b"\x89PNG")
    with patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=mock_memory):
        yield mock_memory


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=False)
async def test_display_image_skips_when_not_notebook(mock_ipython, _mock_central_memory):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "image_path"
    piece.converted_value = "some/image.png"
    await display_image_response(piece)
    # No error — function should silently skip display outside notebook


async def test_display_image_logs_blocked_response(_mock_central_memory, caplog):
    piece = MagicMock()
    piece.response_error = "blocked"
    piece.converted_value_data_type = "text"
    with caplog.at_level(logging.INFO, logger="pyrit.common.display_response"):
        await display_image_response(piece)
    assert "Content blocked" in caplog.text


async def test_display_image_no_action_for_text_type(_mock_central_memory):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "text"
    await display_image_response(piece)


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=True)
@patch("pyrit.common.display_response.Image")
@patch("pyrit.common.display_response.display", create=True)
async def test_display_image_reads_and_displays(mock_display, mock_image, mock_ipython, _mock_central_memory):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "image_path"
    piece.converted_value = "path/to/img.png"

    mock_img_obj = MagicMock()
    mock_image.open.return_value = mock_img_obj

    await display_image_response(piece)

    _mock_central_memory.results_storage_io.read_file.assert_awaited_once_with("path/to/img.png")
    mock_image.open.assert_called_once()
    mock_display.assert_called_once_with(mock_img_obj)


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=True)
async def test_display_image_logs_error_on_read_failure(mock_ipython, _mock_central_memory, caplog):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "image_path"
    piece.converted_value = "bad/path.png"

    _mock_central_memory.results_storage_io.read_file = AsyncMock(side_effect=Exception("disk error"))

    with caplog.at_level(logging.ERROR, logger="pyrit.common.display_response"):
        await display_image_response(piece)
    assert "Failed to read image" in caplog.text


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=True)
async def test_display_image_logs_error_when_storage_io_is_none(mock_ipython, caplog):
    """Test that display_image_response logs error and returns when results_storage_io is None."""
    mock_memory = MagicMock()
    mock_memory.results_storage_io = None
    with patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=mock_memory):
        piece = MagicMock()
        piece.response_error = "none"
        piece.converted_value_data_type = "image_path"
        piece.converted_value = "some/image.png"

        with caplog.at_level(logging.ERROR, logger="pyrit.common.display_response"):
            await display_image_response(piece)
        assert "Failed to read image" in caplog.text


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=True)
@patch("pyrit.common.display_response.DiskStorageIO")
@patch("pyrit.common.display_response.Image")
@patch("pyrit.common.display_response.display", create=True)
async def test_display_image_azure_fallback_to_disk(mock_display, mock_image, mock_disk_io_cls, mock_ipython):
    """Test that when AzureBlobStorageIO read fails, it falls back to DiskStorageIO."""
    from pyrit.models import AzureBlobStorageIO

    mock_memory = MagicMock()
    mock_azure_io = MagicMock(spec=AzureBlobStorageIO)
    mock_azure_io.read_file = AsyncMock(side_effect=Exception("azure error"))
    mock_memory.results_storage_io = mock_azure_io

    mock_disk_instance = MagicMock()
    mock_disk_instance.read_file = AsyncMock(return_value=b"\x89PNG")
    mock_disk_io_cls.return_value = mock_disk_instance

    with patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=mock_memory):
        piece = MagicMock()
        piece.response_error = "none"
        piece.converted_value_data_type = "image_path"
        piece.converted_value = "some/image.png"

        await display_image_response(piece)

    mock_disk_instance.read_file.assert_awaited_once_with("some/image.png")
    mock_image.open.assert_called_once()
    mock_display.assert_called_once()


@patch("pyrit.common.display_response.is_in_ipython_session", return_value=True)
@patch("pyrit.common.display_response.DiskStorageIO")
async def test_display_image_azure_and_disk_both_fail(mock_disk_io_cls, mock_ipython, caplog):
    """Test that when both AzureBlobStorageIO and DiskStorageIO fail, error is logged and returns."""
    from pyrit.models import AzureBlobStorageIO

    mock_memory = MagicMock()
    mock_azure_io = MagicMock(spec=AzureBlobStorageIO)
    mock_azure_io.read_file = AsyncMock(side_effect=Exception("azure error"))
    mock_memory.results_storage_io = mock_azure_io

    mock_disk_instance = MagicMock()
    mock_disk_instance.read_file = AsyncMock(side_effect=Exception("disk also failed"))
    mock_disk_io_cls.return_value = mock_disk_instance

    with patch("pyrit.memory.CentralMemory.get_memory_instance", return_value=mock_memory):
        piece = MagicMock()
        piece.response_error = "none"
        piece.converted_value_data_type = "image_path"
        piece.converted_value = "some/image.png"

        with caplog.at_level(logging.ERROR, logger="pyrit.common.display_response"):
            await display_image_response(piece)

    assert "Failed to read image" in caplog.text
