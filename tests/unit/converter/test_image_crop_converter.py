# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.converter import ImageCropConverter


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


def _coordinate_image(size=(100, 100)) -> Image.Image:
    """An image whose pixel values encode their own coordinates."""
    image = Image.new("RGB", size)
    image.putdata([(x, y, 0) for y in range(size[1]) for x in range(size[0])])
    return image


def test_image_crop_converter_initialization_output_format_validation():
    """Test validation of output_format parameter."""
    for unsupported_format in ["GIF", "BMP", "TIFF", "ICO", "WEBM", "SVG", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageCropConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    for supported_format in ["JPEG", "PNG", "WEBP"]:
        converter = ImageCropConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format

    converter = ImageCropConverter(output_format=None)
    assert converter._output_format is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"left": -0.1},
        {"right": 1.1},
        {"top": -0.5},
        {"bottom": 2.0},
        {"left": 0.5, "right": 0.5},
        {"left": 0.9, "right": 0.1},
        {"top": 0.5, "bottom": 0.5},
        {"top": 0.9, "bottom": 0.1},
        {"left": 1.0, "right": 1.0},
        {"top": 0.0, "bottom": 0.0},
    ],
)
def test_image_crop_converter_invalid_box(kwargs) -> None:
    """Boxes that are empty or outside the image should fail during construction."""
    with pytest.raises(ValueError, match="Crop fractions must satisfy"):
        ImageCropConverter(**kwargs)


@pytest.mark.parametrize("name", ["left", "top", "right", "bottom"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_image_crop_converter_non_finite_fraction(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=f"Crop fraction '{name}' must be finite"):
        ImageCropConverter(**{name: value})


def test_image_crop_converter_valid_full_image_box() -> None:
    converter = ImageCropConverter(left=0.0, top=0.0, right=1.0, bottom=1.0)
    cropped_io, _ = converter._transform_image(_coordinate_image(), "PNG")
    assert Image.open(cropped_io).size == (100, 100)


def test_image_crop_converter_defaults() -> None:
    converter = ImageCropConverter()
    assert (converter._left, converter._top, converter._right, converter._bottom) == (0.1, 0.1, 0.9, 0.9)


def test_image_crop_converter_identifier_includes_all_fractions() -> None:
    params = ImageCropConverter(left=0.2, top=0.25, right=0.8, bottom=0.75).get_identifier().params
    assert params["left"] == 0.2
    assert params["top"] == 0.25
    assert params["right"] == 0.8
    assert params["bottom"] == 0.75


def test_image_crop_converter_identifier_distinguishes_fractions() -> None:
    default = ImageCropConverter()
    other = ImageCropConverter(bottom=0.5)
    assert default.get_identifier().hash != other.get_identifier().hash


def test_image_crop_converter_output_size_and_region() -> None:
    """The default box keeps the central 80% in each dimension of a 100x100 image."""
    cropped_io, output_format = ImageCropConverter()._transform_image(_coordinate_image(), "PNG")
    cropped = Image.open(cropped_io)

    assert output_format == "PNG"
    assert cropped.size == (80, 80)
    # The top-left output pixel is the source pixel at (10, 10), which encodes itself.
    assert cropped.getpixel((0, 0)) == (10, 10, 0)
    assert cropped.getpixel((79, 79)) == (89, 89, 0)


def test_image_crop_converter_custom_box_region() -> None:
    converter = ImageCropConverter(left=0.5, top=0.0, right=1.0, bottom=0.5)
    cropped_io, _ = converter._transform_image(_coordinate_image(), "PNG")
    cropped = Image.open(cropped_io)

    assert cropped.size == (50, 50)
    assert cropped.getpixel((0, 0)) == (50, 0, 0)
    assert cropped.getpixel((49, 49)) == (99, 49, 0)


def test_image_crop_converter_very_small_image() -> None:
    """A 2x2 image cropped to its central fraction still yields a 1x1 output."""
    image = Image.new("RGB", (2, 2), color=(10, 20, 30))
    cropped_io, _ = ImageCropConverter(left=0.5, top=0.5, right=1.0, bottom=1.0)._transform_image(image, "PNG")
    cropped = Image.open(cropped_io)

    assert cropped.size == (1, 1)
    assert cropped.getpixel((0, 0)) == (10, 20, 30)


def test_image_crop_converter_empty_box_for_tiny_image() -> None:
    """A fraction that rounds to zero pixels on a tiny image raises instead of returning an empty crop."""
    image = Image.new("RGB", (2, 2), color=(10, 20, 30))
    with pytest.raises(ValueError, match="at least 1x1"):
        ImageCropConverter(left=0.0, top=0.0, right=0.4, bottom=1.0)._transform_image(image, "PNG")


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        ("JPEG", None, "JPEG"),
        ("WEBP", None, "WEBP"),
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "PNG", "PNG"),
    ],
)
async def test_image_crop_converter_format_preservation_and_conversion_async(
    sample_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test format preservation and conversion between formats via convert_async."""
    converter = ImageCropConverter(output_format=output_format)
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
async def test_image_crop_converter_transparency_handling_async(
    sample_transparent_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test transparency handling across formats."""
    converter = ImageCropConverter(output_format=output_format)
    image_bytes = sample_transparent_image_bytes(format=input_format)
    image = Image.open(BytesIO(image_bytes))

    assert image.has_transparency_data

    res_converted_io, res_output_format = converter._transform_image(image, input_format)
    assert res_converted_io
    assert res_output_format == expected_output_format

    output_image = Image.open(res_converted_io)
    assert output_image.has_transparency_data is False


@pytest.mark.parametrize("mode", ["L", "LA", "1", "I;16", "P", "CMYK"])
def test_image_crop_converter_handles_non_rgb_png_modes(mode: str) -> None:
    cropped_io, output_format = ImageCropConverter()._transform_image(Image.new(mode, (40, 30)), "PNG")
    cropped = Image.open(cropped_io)

    assert output_format == "PNG"
    assert cropped.size == (32, 24)


def test_image_crop_converter_preserves_png_alpha() -> None:
    image = Image.new("RGBA", (20, 20), color=(200, 40, 20, 128))

    cropped_io, output_format = ImageCropConverter()._transform_image(image, "PNG")

    assert output_format == "PNG"
    with Image.open(cropped_io) as cropped:
        assert cropped.mode == "RGBA"
        assert cropped.size == (16, 16)
        assert cropped.getpixel((0, 0)) == (200, 40, 20, 128)
