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


def _write_video(path: Path, *, fps: float, frames: int = 5, size: tuple[int, int] = (128, 96)) -> bytes:
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, size)
    assert writer.isOpened()
    try:
        for _ in range(frames):
            writer.write(np.zeros((size[1], size[0], 3), dtype=np.uint8))
    finally:
        writer.release()
    return path.read_bytes()


def _png(image: np.ndarray) -> bytes:
    import cv2

    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _first_frame_and_fps(video_bytes: bytes, path: Path) -> tuple[np.ndarray, float]:
    import cv2

    path.write_bytes(video_bytes)
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        assert ok
        return frame, capture.get(cv2.CAP_PROP_FPS)
    finally:
        capture.release()


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_accepts_grayscale_image(tmp_path: Path) -> None:
    video_bytes = _write_video(tmp_path / "input.mp4", fps=10)
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4", img_position=(8, 8), img_resize_size=(32, 24))
    gray = np.full((24, 32), 200, dtype=np.uint8)

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        output = converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=_png(gray))

    frame, _ = _first_frame_and_fps(output, tmp_path / "output.mp4")
    # The overlay's middle is the image's gray; the rest of the frame stays black.
    assert abs(int(frame[20, 24].mean()) - 200) < 20
    assert int(frame[80, 100].mean()) < 20


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_scales_16_bit_image(tmp_path: Path) -> None:
    video_bytes = _write_video(tmp_path / "input.mp4", fps=10)
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4", img_position=(8, 8), img_resize_size=(32, 24))
    # 40000 of 65535 is 156 of 255; cast without scaling, it would wrap to 64
    gray16 = np.full((24, 32), 40000, dtype=np.uint16)

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        output = converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=_png(gray16))

    frame, _ = _first_frame_and_fps(output, tmp_path / "output.mp4")
    assert abs(int(frame[20, 24].mean()) - 156) < 20


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_rejects_float_image(tmp_path: Path) -> None:
    import cv2

    video_bytes = _write_video(tmp_path / "input.mp4", fps=10)
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4", img_position=(8, 8), img_resize_size=(32, 24))
    success, encoded = cv2.imencode(".tiff", np.full((24, 32), 0.5, dtype=np.float32))
    assert success

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        with pytest.raises(ValueError, match="Unsupported overlay image depth: float32"):
            converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=encoded.tobytes())


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_keeps_fractional_frame_rate(tmp_path: Path) -> None:
    video_bytes = _write_video(tmp_path / "input.mp4", fps=29.97)
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4", img_position=(8, 8), img_resize_size=(32, 24))

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        output = converter._add_image_to_video_sync(
            video_bytes=video_bytes, image_bytes=_png(np.zeros((24, 32, 3), dtype=np.uint8))
        )

    _, fps = _first_frame_and_fps(output, tmp_path / "output.mp4")
    assert fps == pytest.approx(29.97, abs=0.01)


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
@pytest.mark.parametrize("video_bytes", [b"", b"not a video"])
def test_add_image_to_video_raises_for_unreadable_video(tmp_path: Path, video_bytes: bytes) -> None:
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4")
    files_before = set(tmp_path.iterdir())

    with patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path):
        with pytest.raises(ValueError, match="Could not read the input video"):
            converter._add_image_to_video_sync(
                video_bytes=video_bytes, image_bytes=_png(np.zeros((24, 32, 3), dtype=np.uint8))
            )

    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_raises_when_video_has_no_frames(tmp_path: Path) -> None:
    import cv2

    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4")
    files_before = set(tmp_path.iterdir())
    # The container opens but holds no decodable frames
    capture = MagicMock(spec=cv2.VideoCapture)
    capture.isOpened.return_value = True
    capture.read.return_value = (False, None)
    capture.get.side_effect = lambda prop: {
        cv2.CAP_PROP_FPS: 10.0,
        cv2.CAP_PROP_FRAME_WIDTH: 128,
        cv2.CAP_PROP_FRAME_HEIGHT: 96,
    }[prop]

    with (
        patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path),
        patch.object(cv2, "VideoCapture", return_value=capture),
    ):
        with pytest.raises(ValueError, match="it has no frames"):
            converter._add_image_to_video_sync(
                video_bytes=b"container", image_bytes=_png(np.zeros((24, 32, 3), dtype=np.uint8))
            )

    capture.release.assert_called_once()
    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
def test_add_image_to_video_raises_when_writer_cannot_open(tmp_path: Path) -> None:
    import cv2

    video_bytes = _write_video(tmp_path / "input.mp4", fps=10)
    (tmp_path / "input.mp4").unlink()
    converter = AddImageVideoConverter(video_path=tmp_path / "input.mp4")
    files_before = set(tmp_path.iterdir())
    writer = MagicMock(spec=cv2.VideoWriter)
    writer.isOpened.return_value = False

    with (
        patch("pyrit.converter.add_image_to_video_converter.DB_DATA_PATH", tmp_path),
        patch.object(cv2, "VideoWriter", return_value=writer) as video_writer,
    ):
        video_writer.fourcc = cv2.VideoWriter_fourcc
        with pytest.raises(ValueError, match="Could not open a video writer"):
            converter._add_image_to_video_sync(
                video_bytes=video_bytes, image_bytes=_png(np.zeros((24, 32, 3), dtype=np.uint8))
            )

    writer.write.assert_not_called()
    assert set(tmp_path.iterdir()) == files_before
