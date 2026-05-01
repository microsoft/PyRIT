# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import numpy as np
import pytest

from pyrit.prompt_converter import AddImageVideoConverter


def is_opencv_installed():
    try:
        import cv2  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


@pytest.fixture(autouse=True)
def video_converter_sample_video(tmp_path, patch_central_database):
    video_path = str(tmp_path / "test_video.mp4")
    width, height = 640, 480
    if is_opencv_installed():
        import cv2  # noqa: F401

        video_encoding = cv2.VideoWriter_fourcc(*"mp4v")
        output_video = cv2.VideoWriter(video_path, video_encoding, 1, (width, height))
        for _i in range(10):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            output_video.write(frame)
        output_video.release()
    return video_path


@pytest.fixture
def video_converter_sample_image(tmp_path):
    image_path = str(tmp_path / "test_image.png")
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    if is_opencv_installed():
        import cv2

        cv2.imwrite(image_path, image)
    return image_path


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_video_converter_initialization(tmp_path, video_converter_sample_video):
    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(
        video_path=video_converter_sample_video,
        output_path=output_path,
        img_position=(10, 10),
        img_resize_size=(100, 100),
    )
    assert converter._video_path == video_converter_sample_video
    assert converter._output_path == output_path
    assert converter._img_position == (10, 10)
    assert converter._img_resize_size == (100, 100)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_invalid_image_path(tmp_path, video_converter_sample_video):
    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(video_path=video_converter_sample_video, output_path=output_path)
    with pytest.raises(FileNotFoundError):
        await converter._add_image_to_video(image_path="invalid_image.png", output_path=output_path)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_invalid_video_path(tmp_path, video_converter_sample_image):
    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(video_path="invalid_video.mp4", output_path=output_path)
    with pytest.raises(FileNotFoundError):
        await converter._add_image_to_video(image_path=video_converter_sample_image, output_path=output_path)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter(tmp_path, video_converter_sample_video, video_converter_sample_image):
    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(video_path=video_converter_sample_video, output_path=output_path)
    result_path = await converter._add_image_to_video(image_path=video_converter_sample_image, output_path=output_path)
    assert result_path == output_path


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_convert_async(
    tmp_path, video_converter_sample_video, video_converter_sample_image
):
    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(video_path=video_converter_sample_video, output_path=output_path)
    converted_video = await converter.convert_async(prompt=video_converter_sample_image, input_type="image_path")
    assert converted_video
    assert converted_video.output_text == output_path
    assert converted_video.output_type == "video_path"


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_to_video_raises_when_decode_returns_none(tmp_path, video_converter_sample_video):
    """Guard at line 146: cv2.imdecode returns None raises ValueError."""
    from unittest.mock import AsyncMock, patch

    output_path = str(tmp_path / "output_video.mp4")
    converter = AddImageVideoConverter(video_path=video_converter_sample_video, output_path=output_path)

    mock_image_serializer = AsyncMock()
    mock_image_serializer.read_data = AsyncMock(return_value=b"not_valid_image_data")
    mock_image_serializer._is_azure_storage_url = lambda x: False

    mock_video_serializer = AsyncMock()
    with open(video_converter_sample_video, "rb") as f:
        video_bytes = f.read()
    mock_video_serializer.read_data = AsyncMock(return_value=video_bytes)
    mock_video_serializer._is_azure_storage_url = lambda x: False

    def factory_side_effect(*, category, data_type, value):
        if data_type == "image_path":
            return mock_image_serializer
        return mock_video_serializer

    with patch(
        "pyrit.prompt_converter.add_image_to_video_converter.data_serializer_factory",
        side_effect=factory_side_effect,
    ):
        with pytest.raises(ValueError, match="Failed to decode overlay image"):
            await converter._add_image_to_video(image_path="fake_image.png", output_path=output_path)
