# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import numpy as np
import pytest

from pyrit.converter import AddImageVideoConverter


def is_opencv_installed() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


@pytest.fixture(autouse=True)
def video_converter_sample_video(tmp_path: Path, patch_central_database) -> str:
    video_path = str(tmp_path / "test_video.mp4")
    width, height = 640, 480
    if is_opencv_installed():
        import cv2

        video_encoding = cv2.VideoWriter.fourcc(*"mp4v")
        output_video = cv2.VideoWriter(video_path, video_encoding, 1, (width, height))
        for _i in range(10):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            output_video.write(frame)
        output_video.release()
    return video_path


@pytest.fixture
def video_converter_sample_image(tmp_path: Path) -> str:
    image_path = str(tmp_path / "test_image.png")
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    if is_opencv_installed():
        import cv2

        cv2.imwrite(image_path, image)
    return image_path


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_video_converter_initialization(video_converter_sample_video: str) -> None:
    converter = AddImageVideoConverter(
        video_path=video_converter_sample_video,
        img_position=(10, 10),
        img_resize_size=(100, 100),
    )
    assert converter._video_path == video_converter_sample_video
    assert converter._img_position == (10, 10)
    assert converter._img_resize_size == (100, 100)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_invalid_image_path(video_converter_sample_video: str) -> None:
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)
    with pytest.raises(FileNotFoundError):
        await converter._add_image_to_video_async(image_path="invalid_image.png")


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_invalid_video_path(video_converter_sample_image: str) -> None:
    converter = AddImageVideoConverter(video_path="invalid_video.mp4")
    with pytest.raises(FileNotFoundError):
        await converter._add_image_to_video_async(image_path=video_converter_sample_image)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter(video_converter_sample_video: str, video_converter_sample_image: str) -> None:
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)
    result = await converter._add_image_to_video_async(image_path=video_converter_sample_image)
    assert result


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_video_converter_convert_async(
    video_converter_sample_video: str, video_converter_sample_image: str
) -> None:
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)
    converted_video = await converter.convert_async(prompt=video_converter_sample_image, input_type="image_path")
    assert converted_video
    assert Path(converted_video.output_text).is_file()
    assert converted_video.output_type == "video_path"


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_to_video_raises_when_decode_returns_none(video_converter_sample_video: str) -> None:
    """Guard at line 146: cv2.imdecode returns None raises ValueError."""
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)

    mock_image_serializer = AsyncMock()
    mock_image_serializer.read_data_async = AsyncMock(return_value=b"not_valid_image_data")

    mock_video_serializer = AsyncMock()
    video_bytes = await asyncio.to_thread(Path(video_converter_sample_video).read_bytes)
    mock_video_serializer.read_data_async = AsyncMock(return_value=video_bytes)

    def factory_side_effect(*, category, data_type, value):
        if data_type == "image_path":
            return mock_image_serializer
        return mock_video_serializer

    with patch(
        "pyrit.converter.add_image_to_video_converter.data_serializer_factory",
        side_effect=factory_side_effect,
    ):
        with pytest.raises(ValueError, match="Failed to decode overlay image"):
            await converter._add_image_to_video_async(image_path="fake_image.png")


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_add_image_to_video_removes_temporary_files(
    tmp_path: Path, video_converter_sample_video: str, video_converter_sample_image: str
) -> None:
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)
    files_before = set(tmp_path.iterdir())

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        await converter._add_image_to_video_async(image_path=video_converter_sample_image)

    assert set(tmp_path.iterdir()) == files_before


async def test_add_image_video_converter_preserves_azure_blob_url() -> None:
    video_url = "https://account.blob.core.windows.net/container/clip.MP4?sv=fake"
    converter = AddImageVideoConverter(video_path=video_url)
    image_serializer = AsyncMock()
    image_serializer.read_data_async.return_value = b"image"
    video_serializer = AsyncMock()
    video_serializer.read_data_async.return_value = b"video"
    serializer_factory = MagicMock(side_effect=[image_serializer, video_serializer])

    with (
        patch.dict("sys.modules", {"cv2": MagicMock()}),
        patch("pyrit.converter.add_image_to_video_converter.data_serializer_factory", serializer_factory),
        patch.object(asyncio, "to_thread", new=AsyncMock(return_value=b"converted")),
    ):
        result = await converter._add_image_to_video_async(image_path="image.png")

    assert result == b"converted"
    assert converter._get_video_extension() == "mp4"
    assert serializer_factory.call_args_list == [
        call(category="prompt-memory-entries", data_type="image_path", value="image.png"),
        call(category="prompt-memory-entries", data_type="video_path", value=video_url),
    ]


def test_add_image_video_converter_rejects_output_path(video_converter_sample_video: str, tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="output_path"):
        AddImageVideoConverter(
            video_path=video_converter_sample_video,
            output_path=tmp_path / "output.mp4",  # type: ignore[call-arg]
        )
