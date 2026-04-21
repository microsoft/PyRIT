# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.prompt_converter import ImageResizingConverter


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


def test_image_resizing_converter_initialization_output_format_validation():
    """Test validation of output_format parameter."""
    for unsupported_format in ["GIF", "BMP", "TIFF", "ICO", "WEBM", "SVG", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageResizingConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    for supported_format in ["JPEG", "PNG", "WEBP"]:
        converter = ImageResizingConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format

    converter = ImageResizingConverter(output_format=None)
    assert converter._output_format is None


def test_image_resizing_converter_initialization_scale_factor_validation():
    """Test validation of scale_factor parameter."""
    for invalid_scale_factor in [0.0, -0.1, -1.0, -100.0]:
        with pytest.raises(ValueError, match="Scale factor must be positive"):
            ImageResizingConverter(scale_factor=invalid_scale_factor)

    for valid_scale_factor in [0.1, 0.5, 1.0, 2.0, 10.0]:
        converter = ImageResizingConverter(scale_factor=valid_scale_factor)
        assert converter._scale_factor == valid_scale_factor


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        ("JPEG", None, "JPEG"),
        ("WEBP", None, "WEBP"),
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "PNG", "PNG"),
    ],
)
@pytest.mark.asyncio
async def test_image_resizing_converter_format_preservation_and_conversion(
    sample_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test format preservation and conversion between formats via convert_async."""
    converter = ImageResizingConverter(output_format=output_format)
    image_bytes = sample_image_bytes(format=input_format)

    with patch("pyrit.prompt_converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = image_bytes
        mock_serializer.save_b64_image = AsyncMock()
        # Set the value to match input format initially
        mock_serializer.value = f"test_image.{input_format.lower()}"
        # Mock the file_extension property to be settable
        mock_serializer.file_extension = input_format.lower()
        mock_factory.return_value = mock_serializer

        await converter.convert_async(prompt=f"test_image.{input_format.lower()}", input_type="image_path")

        # Verify the save method was called
        mock_serializer.save_b64_image.assert_called_once()
        mock_serializer.read_data.assert_called_once()

        # Check that file extension was updated correctly
        expected_extension = expected_output_format.lower()
        assert mock_serializer.file_extension == expected_extension


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "JPEG", "JPEG"),
        ("TIFF", "JPEG", "JPEG"),
    ],
)
@pytest.mark.asyncio
async def test_image_resizing_converter_transparency_handling(
    sample_transparent_image_bytes,
    input_format,
    output_format,
    expected_output_format,
):
    """Test transparency handling across formats with different background colors."""
    converter = ImageResizingConverter(output_format=output_format)
    image_bytes = sample_transparent_image_bytes(format=input_format)
    image = Image.open(BytesIO(image_bytes))

    assert image.has_transparency_data  # before conversion, the image should have transparency

    res_converted_io, res_output_format = converter._transform_image(image, input_format)
    assert res_converted_io
    assert res_output_format == expected_output_format

    output_image = Image.open(res_converted_io)
    assert output_image.has_transparency_data is False  # after conversion, the image should not have transparency


@pytest.mark.asyncio
async def test_image_resizing_converter_convert_async_url_input(sample_image_bytes):
    """Test successful resizing of image from URL."""
    converter = ImageResizingConverter(output_format="WEBP", scale_factor=0.5)
    test_url = "https://example.com/test_image.jpeg"
    image_bytes = sample_image_bytes(format="JPEG")

    with patch("pyrit.prompt_converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.file_extension = "jpeg"
        mock_serializer.value = "resized_image.webp"
        mock_serializer.save_b64_image = AsyncMock()
        mock_factory.return_value = mock_serializer

        with patch.object(converter, "_read_image_from_url") as mock_read_url:
            mock_read_url.return_value = image_bytes

            result = await converter.convert_async(prompt=test_url, input_type="url")

            assert result.output_text == "resized_image.webp"
            assert result.output_type == "image_path"
            assert mock_serializer.file_extension == "webp"
            mock_serializer.save_b64_image.assert_called_once()


@pytest.mark.asyncio
async def test_image_resizing_converter_url_format_conversion(sample_image_bytes):
    """Test successful conversion of image from URL."""
    converter = ImageResizingConverter(output_format="WEBP")
    test_url = "https://example.com/test_image.jpeg"
    large_image_bytes = sample_image_bytes(format="JPEG", size=(2048, 2048))

    with patch("pyrit.prompt_converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.file_extension = "jpeg"
        mock_serializer.value = "converted_image.webp"
        mock_serializer.save_b64_image = AsyncMock()
        mock_factory.return_value = mock_serializer

        with patch.object(converter, "_read_image_from_url") as mock_read_url:
            mock_read_url.return_value = large_image_bytes

            result = await converter.convert_async(prompt=test_url, input_type="url")

            assert result.output_text == "converted_image.webp"
            assert result.output_type == "image_path"
            # Verify file extension was updated to match WEBP output format
            assert mock_serializer.file_extension == "webp"
            mock_serializer.save_b64_image.assert_called_once()


@pytest.mark.asyncio
async def test_image_resizing_converter_invalid_url():
    """Test handling of invalid URLs."""
    converter = ImageResizingConverter()
    invalid_urls = ["ftp://example.com/image.png", "file:///local/path/image.png", "not-url", "example.com/image.png"]
    for invalid_url in invalid_urls:
        with pytest.raises(ValueError, match="Invalid URL"):
            await converter.convert_async(prompt=invalid_url, input_type="url")


@pytest.mark.asyncio
async def test_image_resizing_converter_corrupted_image_bytes():
    """Test handling of corrupted image bytes."""
    converter = ImageResizingConverter()
    corrupted_bytes = b"notanimagefile"
    with patch("pyrit.prompt_converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = corrupted_bytes
        mock_factory.return_value = mock_serializer
        with pytest.raises(Exception):  # noqa: B017
            await converter.convert_async(prompt="corrupted.png", input_type="image_path")


@pytest.mark.asyncio
async def test_image_resizing_converter_output_format_fallback():
    """Test fallback to JPEG when original format is unsupported (and no output_format specified)."""
    img = Image.new("RGB", (100, 100), color=(123, 123, 123))
    img_bytes = BytesIO()
    img.save(img_bytes, format="TIFF")
    img_bytes = img_bytes.getvalue()
    converter = ImageResizingConverter(output_format=None)
    with patch("pyrit.prompt_converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_factory.return_value = mock_serializer
        mock_serializer.read_data.return_value = img_bytes
        await converter.convert_async(prompt="test.tiff", input_type="image_path")
        assert mock_serializer.file_extension == "jpeg"


@pytest.mark.asyncio
async def test_image_resizing_converter_output_dimensions(sample_image_bytes):
    """Test that output image dimensions match expected scaled size."""
    scale_factor = 0.5
    original_size = (200, 200)
    converter = ImageResizingConverter(scale_factor=scale_factor)
    image_bytes = sample_image_bytes(format="PNG", size=original_size)
    image = Image.open(BytesIO(image_bytes))

    resized_io, _ = converter._transform_image(image, "PNG")
    resized_image = Image.open(resized_io)

    expected_width = int(original_size[0] * scale_factor)
    expected_height = int(original_size[1] * scale_factor)
    assert resized_image.size == (expected_width, expected_height)
