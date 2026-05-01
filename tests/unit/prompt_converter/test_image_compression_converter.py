# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.prompt_converter import ImageCompressionConverter


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


def test_image_compression_converter_initialization_output_format_validation():
    """Test validation of output_format parameter."""
    for unsupported_format in ["GIF", "BMP", "TIFF", "ICO", "WEBM", "SVG", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageCompressionConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    supported_formats = ["JPEG", "PNG", "WEBP"]
    for supported_format in supported_formats:
        converter = ImageCompressionConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format

    converter = ImageCompressionConverter(output_format=None)
    assert converter._output_format is None


def test_image_compression_converter_initialization_quality_validation():
    """Test validation of quality parameter."""
    for invalid_quality in [-1, -10, 101, 150, 999]:
        with pytest.raises(ValueError, match="Quality must be between 0 and 100"):
            ImageCompressionConverter(quality=invalid_quality)

    for valid_quality in [0, 1, 50, 95, 100]:
        converter = ImageCompressionConverter(quality=valid_quality)
        assert converter._quality == valid_quality

    converter = ImageCompressionConverter(quality=None)
    assert converter._quality is None


def test_image_compression_converter_initialization_compress_level_validation():
    """Test validation of compress_level parameter."""
    for invalid_level in [-1, -5, 10, 15, 100]:
        with pytest.raises(ValueError, match="Compress level must be between 0 and 9"):
            ImageCompressionConverter(compress_level=invalid_level)

    for valid_level in [0, 1, 5, 9]:
        converter = ImageCompressionConverter(compress_level=valid_level)
        assert converter._compress_level == valid_level

    converter = ImageCompressionConverter(compress_level=None)
    assert converter._compress_level is None


def test_image_compression_converter_initialization_method_validation():
    """Test validation of method parameter for WEBP format."""
    for invalid_method in [-1, -5, 7, 10, 100]:
        with pytest.raises(ValueError, match="Method must be between 0 and 6 for WEBP format"):
            ImageCompressionConverter(method=invalid_method)

    for valid_method in [0, 1, 3, 6]:
        converter = ImageCompressionConverter(method=valid_method)
        assert converter._method == valid_method

    converter = ImageCompressionConverter(method=None)
    assert converter._method is None


def test_image_compression_converter_initialization_min_compression_threshold_validation():
    """Test validation of min_compression_threshold parameter."""
    for invalid_threshold in [-1, -10, -100, -1024]:
        with pytest.raises(ValueError, match="Minimum compression threshold must be a non-negative integer"):
            ImageCompressionConverter(min_compression_threshold=invalid_threshold)

    for valid_threshold in [0, 1, 512, 1024, 2048]:
        converter = ImageCompressionConverter(min_compression_threshold=valid_threshold)
        assert converter._min_compression_threshold == valid_threshold


def test_image_compression_converter_initialization_background_color_validation():
    """Test validation of background_color parameter."""
    invalid_colors = [
        "black",
        [0, 0, 0],
        (0, 0),
        (0, 0, 256),
        (-0.5, 0, 0),
        (None, 0, 0),
    ]

    for invalid_color in invalid_colors:
        with pytest.raises(ValueError, match="Background color must be a tuple of three integers between 0 and 255"):
            ImageCompressionConverter(background_color=invalid_color)  # type: ignore[arg-type]

    valid_colors = [
        (0, 0, 0),
        (255, 255, 255),
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (128, 128, 128),
    ]

    for valid_color in valid_colors:
        converter = ImageCompressionConverter(background_color=valid_color)
        assert converter._background_color == valid_color


def test_image_compression_converter_initialization_valid_combinations():
    """Test that valid parameter combinations work correctly."""
    converter = ImageCompressionConverter(
        output_format="JPEG",
        quality=85,
        optimize=True,
        progressive=True,
        compress_level=6,
        lossless=False,
        method=4,
        background_color=(255, 255, 255),
        min_compression_threshold=2048,
        fallback_to_original=False,
    )

    assert converter._output_format == "JPEG"
    assert converter._quality == 85
    assert converter._optimize is True
    assert converter._progressive is True
    assert converter._compress_level == 6
    assert converter._lossless is False
    assert converter._method == 4
    assert converter._background_color == (255, 255, 255)
    assert converter._min_compression_threshold == 2048
    assert converter._fallback_to_original is False


def test_image_compression_converter_quality_warning():
    """Test that high quality values for JPEG trigger a warning."""
    with patch("pyrit.prompt_converter.image_compression_converter.logger") as mock_logger:
        ImageCompressionConverter(output_format="JPEG", quality=98)
        mock_logger.warning.assert_called_once()


@pytest.mark.parametrize(
    "input_format, output_format, expected_output_format",
    [
        # Format preservation
        ("JPEG", None, "JPEG"),
        ("WEBP", None, "WEBP"),
        # Cross-format conversion
        ("PNG", "JPEG", "JPEG"),
        ("WEBP", "PNG", "PNG"),
    ],
)
async def test_image_compression_converter_format_preservation_and_conversion(
    sqlite_instance, sample_image_bytes, input_format, output_format, expected_output_format
):
    """Test format preservation and conversion between formats."""
    converter = ImageCompressionConverter(
        output_format=output_format, min_compression_threshold=100, fallback_to_original=False
    )
    # Create larger image to ensure compression happens
    image_bytes = sample_image_bytes(format=input_format, size=(2048, 2048))

    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
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
    "input_format, output_format, background_color, expected_output_format",
    [
        ("PNG", "JPEG", (255, 255, 255), "JPEG"),
        ("WEBP", "JPEG", (0, 0, 0), "JPEG"),
        ("TIFF", "JPEG", (255, 0, 0), "JPEG"),
    ],
)
async def test_image_compression_converter_transparency_handling(
    sqlite_instance,
    sample_transparent_image_bytes,
    input_format,
    output_format,
    background_color,
    expected_output_format,
):
    """Test transparency handling across formats with different background colors."""
    converter = ImageCompressionConverter(output_format=output_format, background_color=background_color)
    image_bytes = sample_transparent_image_bytes(format=input_format)
    image = Image.open(BytesIO(image_bytes))

    assert image.has_transparency_data  # before compression, the image should have transparency

    res_compressed_io, res_output_format = converter._compress_image(image, input_format, len(image_bytes))
    assert res_compressed_io
    assert res_output_format == expected_output_format

    output_image = Image.open(res_compressed_io)
    assert output_image.has_transparency_data is False  # after compression, the image should not have transparency


async def test_image_compression_converter_skip(sqlite_instance, sample_image_bytes):
    """Test cases for skipping compression and fallback to original image."""

    # 1: Skip compression for small images
    converter = ImageCompressionConverter(min_compression_threshold=4567)
    small_image_bytes = sample_image_bytes(format="PNG", size=(50, 50))
    assert len(small_image_bytes) < 4567

    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = small_image_bytes
        mock_factory.return_value = mock_serializer

        result = await converter.convert_async(prompt="small_image.png", input_type="image_path")
        assert result.output_text == "small_image.png"

        # Verify that compression was skipped - save_b64_image should not be called
        mock_serializer.save_b64_image.assert_not_called()

    # 2: Fallback to original when compression increases file size
    converter_fallback = ImageCompressionConverter(fallback_to_original=True, quality=100)
    large_image_bytes = sample_image_bytes(format="PNG", size=(500, 500))

    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = large_image_bytes
        mock_factory.return_value = mock_serializer

        # Mock _compress_image to return larger size
        with patch.object(converter_fallback, "_compress_image") as mock_compress:
            larger_bytes = BytesIO(b"x" * (len(large_image_bytes) + 1000))
            mock_compress.return_value = (larger_bytes, "PNG")

            result = await converter_fallback.convert_async(prompt="test.png", input_type="image_path")
            assert result.output_text == "test.png"

            mock_serializer.save_b64_image.assert_not_called()
            mock_compress.assert_called_once()  # compression was attempted but resulted in larger size


async def test_image_compression_converter_actually_compresses(sqlite_instance, sample_image_bytes):
    """Test that compression actually reduces file size for appropriate images."""
    converter = ImageCompressionConverter(compress_level=9)

    # Create a large image that should benefit from compression
    large_image_bytes = sample_image_bytes(format="PNG", size=(1024, 1024))
    original_size = len(large_image_bytes)
    image = Image.open(BytesIO(large_image_bytes))

    compressed_io, output_format = converter._compress_image(image, "PNG", original_size)
    compressed_size = len(compressed_io.getvalue())

    assert compressed_size < original_size


async def test_image_compression_converter_url_format_conversion(sqlite_instance, sample_image_bytes):
    """Test successful compression of image from URL."""
    converter = ImageCompressionConverter(output_format="WEBP", min_compression_threshold=100)
    test_url = "https://example.com/test_image.jpeg"
    large_image_bytes = sample_image_bytes(format="JPEG", size=(2048, 2048))

    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
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


async def test_image_compression_converter_url_input_fallback_scenarios(sqlite_instance, sample_image_bytes):
    """Test URL input fallback scenarios (small image size)."""
    converter = ImageCompressionConverter(min_compression_threshold=5000, fallback_to_original=True)
    test_url = "https://example.com/small_image.png"
    small_image_bytes = sample_image_bytes(format="PNG", size=(100, 100))

    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.file_extension = "png"
        mock_serializer.value = "fallback_image.png"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        with patch.object(converter, "_read_image_from_url") as mock_read_url:
            mock_read_url.return_value = small_image_bytes

            result = await converter.convert_async(prompt=test_url, input_type="url")

            assert result.output_text == "fallback_image.png"
            assert result.output_type == "image_path"
            mock_read_url.assert_called_once_with(test_url)
            mock_serializer.save_data.assert_called_once_with(small_image_bytes)
            mock_serializer.save_b64_image.assert_not_called()


async def test_image_compression_converter_invalid_url():
    """Test handling of invalid URLs."""
    converter = ImageCompressionConverter()
    invalid_urls = ["ftp://example.com/image.png", "file:///local/path/image.png", "not-url", "example.com/image.png"]
    for invalid_url in invalid_urls:
        with pytest.raises(ValueError, match="Invalid URL"):
            await converter.convert_async(prompt=invalid_url, input_type="url")


async def test_image_compression_converter_corrupted_image_bytes():
    """Test handling of corrupted image bytes."""
    converter = ImageCompressionConverter()
    corrupted_bytes = b"notanimagefile"
    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data.return_value = corrupted_bytes
        mock_factory.return_value = mock_serializer
        with pytest.raises(Exception):  # noqa: B017
            await converter.convert_async(prompt="corrupted.png", input_type="image_path")


async def test_image_compression_converter_output_format_fallback(sample_image_bytes):
    """Test fallback to JPEG when original format is unsupported (and no output_format specified)."""
    img = Image.new("RGB", (100, 100), color=(123, 123, 123))
    img_bytes = BytesIO()
    img.save(img_bytes, format="TIFF")
    img_bytes = img_bytes.getvalue()
    converter = ImageCompressionConverter(output_format=None)
    with patch("pyrit.prompt_converter.image_compression_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_factory.return_value = mock_serializer
        mock_serializer.read_data.return_value = img_bytes
        await converter.convert_async(prompt="test.tiff", input_type="image_path")
        assert mock_serializer.file_extension == "jpeg"
