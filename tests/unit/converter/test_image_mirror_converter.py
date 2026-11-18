# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.converter import ImageMirrorConverter


@pytest.fixture
def sample_image_bytes():
    """Sample RGB image for testing with configurable format and size."""

    def _create_image(format="PNG", size=(200, 200)):  # noqa: A002
        img = Image.new("RGB", size, color=(125, 125, 125))
        img_bytes = BytesIO()
        img.save(img_bytes, format=format)
        return img_bytes.getvalue()

    return _create_image


@pytest.fixture
def sample_transparent_image_bytes():
    """Sample RGBA image with transparency for testing with configurable format."""

    def _create_image(format="PNG"):  # noqa: A002
        img = Image.new("RGBA", (200, 200), color=(125, 125, 125, 128))
        img_bytes = BytesIO()
        img.save(img_bytes, format=format)
        return img_bytes.getvalue()

    return _create_image


def _quadrant_image() -> Image.Image:
    """A 2x2 image with a distinct color in each quadrant."""
    image = Image.new("RGB", (2, 2))
    image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)])
    return image


def test_image_mirror_converter_initialization_output_format_validation():
    """Test validation of output_format parameter."""
    for unsupported_format in ["GIF", "BMP", "TIFF", "ICO", "WEBM", "SVG", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageMirrorConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    for supported_format in ["JPEG", "PNG", "WEBP"]:
        converter = ImageMirrorConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format

    converter = ImageMirrorConverter(output_format=None)
    assert converter._output_format is None


@pytest.mark.parametrize("direction", ["diagonal", "Horizontal", "VERTICAL", "", "none", None, 90])
def test_image_mirror_converter_invalid_direction(direction) -> None:
    """Directions other than "horizontal" and "vertical" should fail during construction."""
    with pytest.raises(ValueError, match="Direction must be 'horizontal' or 'vertical'"):
        ImageMirrorConverter(direction=direction)  # type: ignore[arg-type]


def test_image_mirror_converter_default_direction() -> None:
    assert ImageMirrorConverter()._direction == "horizontal"


def test_image_mirror_converter_identifier_includes_direction() -> None:
    params = ImageMirrorConverter(direction="vertical").get_identifier().params
    assert params["direction"] == "vertical"


def test_image_mirror_converter_identifier_distinguishes_direction() -> None:
    horizontal = ImageMirrorConverter(direction="horizontal")
    vertical = ImageMirrorConverter(direction="vertical")
    assert horizontal.get_identifier().hash != vertical.get_identifier().hash


def test_image_mirror_converter_horizontal_flip_moves_pixels() -> None:
    mirrored_io, output_format = ImageMirrorConverter(direction="horizontal")._transform_image(_quadrant_image(), "PNG")
    mirrored = Image.open(mirrored_io)

    assert output_format == "PNG"
    assert mirrored.size == (2, 2)
    # Each row is reversed: red/green swap places, blue/yellow swap places.
    assert list(mirrored.getdata()) == [(0, 255, 0), (255, 0, 0), (255, 255, 0), (0, 0, 255)]


def test_image_mirror_converter_vertical_flip_moves_pixels() -> None:
    mirrored_io, output_format = ImageMirrorConverter(direction="vertical")._transform_image(_quadrant_image(), "PNG")
    mirrored = Image.open(mirrored_io)

    assert output_format == "PNG"
    assert mirrored.size == (2, 2)
    # Each column is reversed: red/blue swap places, green/yellow swap places.
    assert list(mirrored.getdata()) == [(0, 0, 255), (255, 255, 0), (255, 0, 0), (0, 255, 0)]


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_image_mirror_converter_mirroring_twice_restores_original(direction: str) -> None:
    converter = ImageMirrorConverter(direction=direction)
    image = Image.new("RGB", (30, 20))
    image.putdata([((x * 8) % 256, (y * 12) % 256, ((x + y) * 5) % 256) for y in range(20) for x in range(30)])

    once_io, _ = converter._transform_image(image, "PNG")
    twice_io, _ = converter._transform_image(Image.open(once_io), "PNG")

    assert Image.open(twice_io).tobytes() == image.tobytes()


def test_image_mirror_converter_preserves_size(sample_image_bytes) -> None:
    image = Image.open(BytesIO(sample_image_bytes(format="PNG", size=(120, 70))))

    mirrored_io, _ = ImageMirrorConverter()._transform_image(image, "PNG")

    assert Image.open(mirrored_io).size == (120, 70)


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        ("JPEG", None, "JPEG"),
        ("WEBP", None, "WEBP"),
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "PNG", "PNG"),
    ],
)
async def test_image_mirror_converter_format_preservation_and_conversion_async(
    sample_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test format preservation and conversion between formats via convert_async."""
    converter = ImageMirrorConverter(output_format=output_format)
    image_bytes = sample_image_bytes(format=input_format)

    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_async.return_value = image_bytes
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = f"test_image.{input_format.lower()}"
        mock_serializer.file_extension = input_format.lower()
        mock_factory.return_value = mock_serializer

        await converter.convert_async(prompt=f"test_image.{input_format.lower()}", input_type="image_path")

        mock_serializer.save_b64_image_async.assert_called_once()
        mock_serializer.read_data_async.assert_called_once()
        assert mock_serializer.file_extension == expected_output_format.lower()


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "JPEG", "JPEG"),
        ("TIFF", "JPEG", "JPEG"),
    ],
)
async def test_image_mirror_converter_transparency_handling_async(
    sample_transparent_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test transparency handling across formats."""
    converter = ImageMirrorConverter(output_format=output_format)
    image_bytes = sample_transparent_image_bytes(format=input_format)
    image = Image.open(BytesIO(image_bytes))

    assert image.has_transparency_data

    res_converted_io, res_output_format = converter._transform_image(image, input_format)
    assert res_converted_io
    assert res_output_format == expected_output_format

    output_image = Image.open(res_converted_io)
    assert output_image.has_transparency_data is False


@pytest.mark.parametrize("mode", ["L", "LA", "1", "P", "CMYK"])
def test_image_mirror_converter_handles_non_rgb_png_modes(mode: str) -> None:
    mirrored_io, output_format = ImageMirrorConverter()._transform_image(Image.new(mode, (40, 30)), "PNG")
    mirrored = Image.open(mirrored_io)

    assert output_format == "PNG"
    assert mirrored.size == (40, 30)


def test_image_mirror_converter_preserves_png_alpha() -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(200, 40, 20, 0), (40, 200, 20, 255)])

    mirrored_io, output_format = ImageMirrorConverter(direction="horizontal")._transform_image(image, "PNG")

    assert output_format == "PNG"
    with Image.open(mirrored_io) as mirrored:
        assert mirrored.mode == "RGBA"
        assert list(mirrored.getdata()) == [(40, 200, 20, 255), (200, 40, 20, 0)]
