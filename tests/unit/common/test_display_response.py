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


@pytest.mark.asyncio
@patch("pyrit.common.display_response.is_in_ipython_session", return_value=False)
async def test_display_image_skips_when_not_notebook(mock_ipython, _mock_central_memory):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "image_path"
    piece.converted_value = "some/image.png"
    await display_image_response(piece)
    # No error — function should silently skip display outside notebook


@pytest.mark.asyncio
async def test_display_image_logs_blocked_response(_mock_central_memory, caplog):
    piece = MagicMock()
    piece.response_error = "blocked"
    piece.converted_value_data_type = "text"
    with caplog.at_level(logging.INFO, logger="pyrit.common.display_response"):
        await display_image_response(piece)
    assert "Content blocked" in caplog.text


@pytest.mark.asyncio
async def test_display_image_no_action_for_text_type(_mock_central_memory):
    piece = MagicMock()
    piece.response_error = "none"
    piece.converted_value_data_type = "text"
    await display_image_response(piece)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
