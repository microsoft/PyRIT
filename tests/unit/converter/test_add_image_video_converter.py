# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import Generator
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import numpy as np
import pytest
from numpy.typing import NDArray

import pyrit.converter.add_image_to_video_converter as converter_module
from pyrit.converter import AddImageVideoConverter


def is_opencv_installed() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


def _create_video_bytes(*, path: Path, fps: float = 30.0, frame_count: int = 6) -> bytes:
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (128, 96))
    assert writer.isOpened()
    try:
        for index in range(frame_count):
            writer.write(np.full((96, 128, 3), (40 + 10 * index, 80, 120), dtype=np.uint8))
    finally:
        writer.release()
    return path.read_bytes()


def _create_image_bytes(*, dtype: type[np.uint8] | type[np.uint16], values: tuple[int, ...]) -> bytes:
    import cv2

    shape = (24, 32) if len(values) == 1 else (24, 32, len(values))
    image = np.full(shape, values[0] if len(values) == 1 else values, dtype=dtype)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return bytes(encoded)


def _decode_video(video_bytes: bytes) -> tuple[float, list[NDArray[np.uint8]], list[float]]:
    import av

    with av.open(BytesIO(video_bytes)) as container:
        stream = container.streams.video[0]
        assert stream.average_rate is not None
        frames = list(container.decode(video=0))
        assert frames
        timestamps = []
        for frame in frames:
            assert frame.pts is not None and frame.time_base is not None
            timestamps.append(float(frame.pts * frame.time_base))
        arrays = [np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8) for frame in frames]
        return float(stream.average_rate), arrays, timestamps


@pytest.fixture
def video_handles() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    cv2 = pytest.importorskip("cv2")
    capture = MagicMock(spec=cv2.VideoCapture)
    capture.isOpened.return_value = True
    properties = {cv2.CAP_PROP_FPS: 29.97, cv2.CAP_PROP_FRAME_WIDTH: 128.0, cv2.CAP_PROP_FRAME_HEIGHT: 96.0}
    capture.get.side_effect = properties.__getitem__
    writer = MagicMock(spec=cv2.VideoWriter)
    writer.isOpened.return_value = True
    with (
        patch.object(cv2, "VideoCapture", return_value=capture),
        patch.object(cv2, "VideoWriter", return_value=writer),
    ):
        yield capture, writer


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


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        pytest.param(np.uint8, (128,), id="gray8"),
        pytest.param(np.uint16, (0,), id="gray16-black"),
        pytest.param(np.uint16, (16384,), id="gray16-quarter"),
        pytest.param(np.uint16, (32768,), id="gray16-midpoint"),
        pytest.param(np.uint16, (65535,), id="gray16-white"),
        pytest.param(np.uint8, (32, 128, 224), id="rgb8"),
        pytest.param(np.uint16, (8224, 32896, 57568), id="rgb16"),
        pytest.param(np.uint8, (0, 255, 0, 0), id="rgba8-transparent"),
        pytest.param(np.uint8, (0, 255, 0, 128), id="rgba8-half"),
        pytest.param(np.uint8, (0, 255, 0, 255), id="rgba8-opaque"),
        pytest.param(np.uint16, (0, 65535, 0, 0), id="rgba16-transparent"),
        pytest.param(np.uint16, (0, 65535, 0, 32768), id="rgba16-half"),
        pytest.param(np.uint16, (0, 65535, 0, 65535), id="rgba16-opaque"),
    ],
)
def test_add_image_to_video_preserves_overlay_pixels(
    *, tmp_path: Path, dtype: type[np.uint8] | type[np.uint16], values: tuple[int, ...]
) -> None:
    video_path = tmp_path / "input.mp4"
    video_bytes = _create_video_bytes(path=video_path)
    image_bytes = _create_image_bytes(dtype=dtype, values=values)
    converter = AddImageVideoConverter(video_path=video_path, img_position=(8, 8), img_resize_size=(32, 24))
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path):
        result = converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=image_bytes)

    fps, frames, timestamps = _decode_video(result)
    assert fps == pytest.approx(30.0)
    assert len(frames) == 6
    assert timestamps == pytest.approx([index / 30 for index in range(6)], abs=0.001)
    scale = 255 / np.iinfo(dtype).max
    channels = np.rint(np.array(values, dtype=np.float64) * scale)
    color = np.repeat(channels, 3) if len(values) == 1 else channels[:3]
    alpha = channels[3] / 255 if len(values) == 4 else 1.0
    for index, frame in enumerate(frames):
        assert frame.shape == (96, 128, 3)
        background = np.array([40 + 10 * index, 80, 120])
        np.testing.assert_allclose(
            np.median(frame[12:28, 12:36], axis=(0, 1)), alpha * color + (1 - alpha) * background, atol=10
        )
        np.testing.assert_allclose(np.median(frame[50:70, 60:80], axis=(0, 1)), background, atol=10)
    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
@pytest.mark.parametrize("extension", ["mp4", "avi", "mov"])
@pytest.mark.parametrize("fps", [29.97, 23.976, 30.0, 0.5])
def test_add_image_to_video_preserves_frame_rate(*, tmp_path: Path, extension: str, fps: float) -> None:
    video_path = tmp_path / f"input.{extension}"
    video_bytes = _create_video_bytes(path=video_path, fps=fps)
    image_bytes = _create_image_bytes(dtype=np.uint8, values=(128,))
    converter = AddImageVideoConverter(video_path=video_path, img_position=(8, 8), img_resize_size=(32, 24))

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path):
        result = converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=image_bytes)

    output_fps, frames, timestamps = _decode_video(result)
    assert output_fps == pytest.approx(fps, abs=0.001)
    assert len(frames) == 6
    assert all(frame.shape == (96, 128, 3) for frame in frames)
    assert timestamps == pytest.approx([index / fps for index in range(6)], abs=0.001)
    assert timestamps[-1] + 1 / output_fps == pytest.approx(6 / fps, abs=0.002)


@pytest.mark.parametrize(
    ("failure", "fps", "message"),
    [
        ("capture", 30.0, "Failed to open the input video"),
        ("fps", 0.0, "Could not determine the frame rate"),
        ("fps", -1.0, "Could not determine the frame rate"),
        ("fps", float("nan"), "Could not determine the frame rate"),
        ("fps", float("inf"), "Could not determine the frame rate"),
        ("writer", 30.0, "Failed to create a video writer for '.mp4'"),
    ],
)
def test_add_image_to_video_releases_handles_on_video_errors(
    *, tmp_path: Path, video_handles: tuple[MagicMock, MagicMock], failure: str, fps: float, message: str
) -> None:
    capture, writer = video_handles
    if failure == "capture":
        capture.isOpened.return_value = False
    elif failure == "fps":
        capture.get.side_effect = None
        capture.get.return_value = fps
    else:
        writer.isOpened.return_value = False
    converter = AddImageVideoConverter(video_path="video.mp4")
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path), pytest.raises(ValueError, match=message):
        converter._add_image_to_video_sync(video_bytes=b"video", image_bytes=b"image")

    capture.release.assert_called_once()
    if failure == "writer":
        writer.release.assert_called_once()
    else:
        writer.release.assert_not_called()
    writer.write.assert_not_called()
    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
@pytest.mark.parametrize("video_bytes", [b"", b"not a video"])
def test_add_image_to_video_rejects_unreadable_video(*, tmp_path: Path, video_bytes: bytes) -> None:
    converter = AddImageVideoConverter(video_path="video.mp4")
    image_bytes = _create_image_bytes(dtype=np.uint8, values=(128,))
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path), pytest.raises(ValueError, match="Failed to open"):
        converter._add_image_to_video_sync(video_bytes=video_bytes, image_bytes=image_bytes)

    assert set(tmp_path.iterdir()) == files_before


@pytest.mark.parametrize("image_bytes", [b"", b"not an image"])
def test_add_image_to_video_releases_handles_on_invalid_image(
    *, tmp_path: Path, video_handles: tuple[MagicMock, MagicMock], image_bytes: bytes
) -> None:
    capture, writer = video_handles
    converter = AddImageVideoConverter(video_path="video.mp4")
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path), pytest.raises(ValueError, match="Failed to decode"):
        converter._add_image_to_video_sync(video_bytes=b"video", image_bytes=image_bytes)

    capture.release.assert_called_once()
    writer.release.assert_called_once()
    writer.write.assert_not_called()
    assert set(tmp_path.iterdir()) == files_before


def test_add_image_to_video_rejects_unsupported_image_depth(
    *, tmp_path: Path, video_handles: tuple[MagicMock, MagicMock]
) -> None:
    import cv2

    success, encoded = cv2.imencode(".tiff", np.full((24, 32), 0.5, dtype=np.float32))
    assert success
    decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    assert decoded.dtype == np.float32
    capture, writer = video_handles
    converter = AddImageVideoConverter(video_path="video.mp4")
    files_before = set(tmp_path.iterdir())

    with (
        patch.object(converter_module, "DB_DATA_PATH", tmp_path),
        pytest.raises(ValueError, match="Unsupported overlay image depth: float32"),
    ):
        converter._add_image_to_video_sync(video_bytes=b"video", image_bytes=encoded.tobytes())

    capture.release.assert_called_once()
    writer.release.assert_called_once()
    writer.write.assert_not_called()
    assert set(tmp_path.iterdir()) == files_before


def test_add_image_to_video_releases_handles_on_write_error(
    *, tmp_path: Path, video_handles: tuple[MagicMock, MagicMock]
) -> None:
    capture, writer = video_handles
    capture.read.return_value = (True, np.zeros((96, 128, 3), dtype=np.uint8))
    writer.write.side_effect = RuntimeError("Encoder failed")
    converter = AddImageVideoConverter(video_path="video.mp4", img_resize_size=(32, 24))
    image_bytes = _create_image_bytes(dtype=np.uint8, values=(128,))
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path), pytest.raises(RuntimeError, match="Encoder failed"):
        converter._add_image_to_video_sync(video_bytes=b"video", image_bytes=image_bytes)

    capture.release.assert_called_once()
    writer.release.assert_called_once()
    assert set(tmp_path.iterdir()) == files_before


def test_add_image_video_converter_rejects_empty_video_path() -> None:
    with pytest.raises(ValueError, match="valid video path"):
        AddImageVideoConverter(video_path="")


async def test_add_image_video_converter_rejects_unsupported_input_async(video_converter_sample_video: str) -> None:
    converter = AddImageVideoConverter(video_path=video_converter_sample_video)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="image.png", input_type="text")


@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
@pytest.mark.parametrize(("dtype", "value"), [(np.uint8, 128), (np.uint16, 32768)])
async def test_convert_async_preserves_grayscale_pixels_async(
    *, tmp_path: Path, video_converter_sample_video: str, dtype: type[np.uint8] | type[np.uint16], value: int
) -> None:
    image_path = tmp_path / "grayscale.png"
    image_bytes = await asyncio.to_thread(_create_image_bytes, dtype=dtype, values=(value,))
    await asyncio.to_thread(image_path.write_bytes, image_bytes)
    converter = AddImageVideoConverter(
        video_path=video_converter_sample_video, img_position=(8, 8), img_resize_size=(32, 24)
    )
    files_before = set(tmp_path.iterdir())

    with patch.object(converter_module, "DB_DATA_PATH", tmp_path):
        results = await asyncio.gather(
            converter.convert_async(prompt=str(image_path)),
            converter.convert_async(prompt=str(image_path)),
        )

    assert results[0].output_text != results[1].output_text
    for result in results:
        assert result.output_type == "video_path"
        assert Path(result.output_text).suffix == ".mp4"
        video_bytes = await asyncio.to_thread(Path(result.output_text).read_bytes)
        fps, frames, _ = await asyncio.to_thread(_decode_video, video_bytes)
        assert fps == pytest.approx(1.0)
        assert len(frames) == 10
        for frame in frames:
            assert frame.shape == (480, 640, 3)
            np.testing.assert_allclose(np.median(frame[12:28, 12:36], axis=(0, 1)), [128, 128, 128], atol=10)
    assert set(tmp_path.iterdir()) == files_before
