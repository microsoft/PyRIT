# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from io import BytesIO
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from PIL import Image

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import ImageNoiseConverter


@pytest.fixture
def seeded() -> Iterator[None]:
    """Seed PyRIT's randomness so the noise in a test is deterministic."""
    configure_random_seed(seed=1234)
    yield
    configure_random_seed(seed=None)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _transform_png(converter: ImageNoiseConverter, image: Image.Image) -> Image.Image:
    output, output_format = converter._transform_image(image, "PNG")
    assert output_format == "PNG"
    result = Image.open(output)
    result.load()
    return result


async def _convert_png_async(converter: ImageNoiseConverter, image_bytes: bytes) -> bytes:
    """Run convert_async on a PNG path and return the bytes it saved."""
    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_async.return_value = image_bytes
        mock_serializer.value = "noisy.png"
        mock_factory.return_value = mock_serializer

        result = await converter.convert_async(prompt="input.png", input_type="image_path")

        assert result.output_type == "image_path"
        mock_serializer.save_data_async.assert_called_once()
        saved_data = mock_serializer.save_data_async.call_args.kwargs["data"]
        assert isinstance(saved_data, bytes)
        return saved_data


def _color_key_png() -> Image.Image:
    """An RGB PNG whose left half is the transparent color key (red) and whose right half is opaque blue."""
    image = Image.new("RGB", (40, 10), color=(255, 0, 0))
    image.paste((0, 0, 255), (20, 0, 40, 10))
    image.info["transparency"] = (255, 0, 0)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return Image.open(BytesIO(buffer.getvalue()))


def test_image_noise_converter_output_format_validation():
    for unsupported_format in ["GIF", "BMP", "TIFF", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageNoiseConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    for supported_format in ["JPEG", "PNG", "WEBP"]:
        converter = ImageNoiseConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format


@pytest.mark.parametrize("standard_deviation", [0, 0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_image_noise_converter_rejects_invalid_standard_deviation(standard_deviation: float) -> None:
    with pytest.raises(ValueError, match="Standard deviation must be finite and greater than 0"):
        ImageNoiseConverter(standard_deviation=standard_deviation)


def test_image_noise_converter_defaults() -> None:
    converter = ImageNoiseConverter()
    assert converter._standard_deviation == 25.0
    assert converter._output_format is None


def test_image_noise_converter_identifier_includes_standard_deviation() -> None:
    params = ImageNoiseConverter(standard_deviation=7.5).get_identifier().params
    assert params["standard_deviation"] == 7.5
    assert ImageNoiseConverter(standard_deviation=7.5).get_identifier() != ImageNoiseConverter().get_identifier()


def test_image_noise_converter_adds_zero_mean_noise_of_the_given_size(seeded: None) -> None:
    image = Image.new("RGB", (64, 48), color=(128, 128, 128))

    noisy = np.asarray(_transform_png(ImageNoiseConverter(standard_deviation=20.0), image), dtype=np.float64)

    assert noisy.shape == (48, 64, 3)
    difference = noisy - 128.0
    assert np.count_nonzero(difference) > difference.size * 0.9
    assert abs(difference.mean()) < 1.0
    assert 18.0 < difference.std() < 22.0
    # Each channel gets its own noise.
    assert not np.array_equal(noisy[..., 0], noisy[..., 1])


def test_image_noise_converter_clips_instead_of_wrapping(seeded: None) -> None:
    for value, in_range in ((255, lambda a: a.min() > 100), (0, lambda a: a.max() < 155)):
        image = Image.new("RGB", (40, 40), color=(value, value, value))

        noisy = np.asarray(_transform_png(ImageNoiseConverter(standard_deviation=25.0), image))

        assert in_range(noisy)
        assert np.count_nonzero(noisy == value) > noisy.size * 0.4


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_image_noise_converter_preserves_size_and_alpha(seeded: None, mode: str) -> None:
    alpha = np.tile(np.arange(0, 250, 10, dtype=np.uint8), (20, 1))
    image = Image.new(mode, (25, 20), color=(90, 0) if mode == "LA" else (90, 90, 90, 0))
    image.putalpha(Image.fromarray(alpha))

    noisy = _transform_png(ImageNoiseConverter(), image)

    assert noisy.size == (25, 20)
    assert noisy.mode == "RGBA"
    assert np.array_equal(np.asarray(noisy.getchannel("A")), alpha)
    assert not np.array_equal(np.asarray(noisy)[..., :3], np.full((20, 25, 3), 90))


def test_image_noise_converter_keeps_color_key_transparency(seeded: None) -> None:
    with _color_key_png() as source:
        assert source.mode == "RGB"
        noisy = _transform_png(ImageNoiseConverter(), source)

    assert noisy.mode == "RGBA"
    alpha = np.asarray(noisy.getchannel("A"))
    assert (alpha[:, :20] == 0).all()
    assert (alpha[:, 20:] == 255).all()


@pytest.mark.parametrize("mode", ["L", "1", "P", "I;16", "CMYK"])
def test_image_noise_converter_handles_non_rgb_modes(seeded: None, mode: str) -> None:
    image = Image.new(mode, (30, 20))

    noisy = _transform_png(ImageNoiseConverter(), image)

    assert noisy.size == (30, 20)
    assert noisy.mode == "RGB"
    assert np.asarray(noisy).any()


def test_image_noise_converter_jpeg_output_from_transparent_input(seeded: None) -> None:
    image = Image.new("RGBA", (30, 20), color=(90, 90, 90, 128))

    output, output_format = ImageNoiseConverter(output_format="JPEG")._transform_image(image, "PNG")

    assert output_format == "JPEG"
    with Image.open(output) as noisy:
        assert noisy.size == (30, 20)
        assert noisy.mode == "RGB"


async def test_image_noise_converter_same_seed_gives_identical_output_async() -> None:
    image_bytes = _png_bytes(Image.new("RGB", (32, 32), color=(128, 128, 128)))
    numpy_state = np.random.get_state()

    try:
        configure_random_seed(seed=42)
        first = await _convert_png_async(ImageNoiseConverter(), image_bytes)
        second = await _convert_png_async(ImageNoiseConverter(), image_bytes)
        configure_random_seed(seed=43)
        other_seed = await _convert_png_async(ImageNoiseConverter(), image_bytes)
    finally:
        configure_random_seed(seed=None)

    first_pixels = np.asarray(Image.open(BytesIO(first)))
    assert np.array_equal(first_pixels, np.asarray(Image.open(BytesIO(second))))
    assert not np.array_equal(first_pixels, np.asarray(Image.open(BytesIO(other_seed))))
    assert not np.array_equal(first_pixels, np.full((32, 32, 3), 128))

    # The converter uses its own generator, so the global NumPy state is untouched.
    current_numpy_state = np.random.get_state()
    assert current_numpy_state[0] == numpy_state[0]
    assert np.array_equal(current_numpy_state[1], numpy_state[1])
    assert current_numpy_state[2:] == numpy_state[2:]


async def test_image_noise_converter_url_input_async() -> None:
    converter = ImageNoiseConverter(output_format="WEBP")
    image_bytes = _png_bytes(Image.new("RGB", (16, 16), color=(10, 200, 30)))

    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.value = "noisy.webp"
        mock_factory.return_value = mock_serializer

        with patch.object(converter, "_read_image_from_url_async", return_value=image_bytes):
            result = await converter.convert_async(prompt="https://example.com/image.png", input_type="url")

    assert result.output_text == "noisy.webp"
    assert result.output_type == "image_path"
    assert mock_serializer.file_extension == "webp"


async def test_image_noise_converter_rejects_unsupported_input_async() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await ImageNoiseConverter().convert_async(prompt="hello", input_type="text")
