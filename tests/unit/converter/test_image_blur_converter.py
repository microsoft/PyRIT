# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.converter import ImageBlurConverter


def _transform_png(converter: ImageBlurConverter, image: Image.Image) -> Image.Image:
    output, output_format = converter._transform_image(image, "PNG")
    assert output_format == "PNG"
    result = Image.open(output)
    result.load()
    return result


def _sharp_edge(mode: str = "RGB") -> Image.Image:
    """A 40x10 image that is black on the left half and white on the right half."""
    image = Image.new(mode, (40, 10), color=0)
    image.paste(255 if mode in ("L", "1") else (255, 255, 255), (20, 0, 40, 10))
    return image


def _color_key_png() -> Image.Image:
    """An RGB PNG whose left half is the transparent color key (red) and whose right half is opaque blue."""
    image = Image.new("RGB", (40, 10), color=(255, 0, 0))
    image.paste((0, 0, 255), (20, 0, 40, 10))
    image.info["transparency"] = (255, 0, 0)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return Image.open(BytesIO(buffer.getvalue()))


def test_image_blur_converter_output_format_validation():
    for unsupported_format in ["GIF", "BMP", "TIFF", "jpg", "png"]:
        with pytest.raises(ValueError, match="Output format must be one of 'JPEG', 'PNG', or 'WEBP'"):
            ImageBlurConverter(output_format=unsupported_format)  # type: ignore[arg-type]

    for supported_format in ["JPEG", "PNG", "WEBP"]:
        converter = ImageBlurConverter(output_format=supported_format)  # type: ignore[arg-type]
        assert converter._output_format == supported_format


@pytest.mark.parametrize("radius", [0, 0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_image_blur_converter_rejects_invalid_radius(radius: float) -> None:
    with pytest.raises(ValueError, match="Radius must be finite and greater than 0"):
        ImageBlurConverter(radius=radius)


def test_image_blur_converter_defaults() -> None:
    converter = ImageBlurConverter()
    assert converter._radius == 2.0
    assert converter._output_format is None


def test_image_blur_converter_identifier_includes_radius() -> None:
    params = ImageBlurConverter(radius=3.5).get_identifier().params
    assert params["radius"] == 3.5
    assert ImageBlurConverter(radius=3.5).get_identifier() != ImageBlurConverter().get_identifier()


def test_image_blur_converter_turns_a_sharp_edge_into_a_gradient() -> None:
    blurred = _transform_png(ImageBlurConverter(radius=2.0), _sharp_edge())

    row = [blurred.getpixel((x, 5))[0] for x in range(40)]
    # Far from the edge the image is unchanged.
    assert row[0] == 0
    assert row[39] == 255
    # Across the edge the values now rise step by step instead of jumping from 0 to 255.
    edge = row[15:25]
    assert all(earlier <= later for earlier, later in zip(edge, edge[1:], strict=False))
    assert len({value for value in edge if 0 < value < 255}) >= 4


def test_image_blur_converter_larger_radius_blurs_more() -> None:
    narrow = _transform_png(ImageBlurConverter(radius=1.0), _sharp_edge())
    wide = _transform_png(ImageBlurConverter(radius=4.0), _sharp_edge())

    def intermediate(image: Image.Image) -> int:
        return sum(1 for x in range(40) if 0 < image.getpixel((x, 5))[0] < 255)

    assert intermediate(wide) > intermediate(narrow)


@pytest.mark.parametrize("size", [(40, 10), (7, 33), (1, 1)])
def test_image_blur_converter_preserves_size(size: tuple[int, int]) -> None:
    blurred = _transform_png(ImageBlurConverter(radius=5.0), Image.new("RGB", size, color=(10, 200, 30)))
    assert blurred.size == size


@pytest.mark.parametrize("mode", ["L", "1", "P", "I;16", "CMYK"])
def test_image_blur_converter_handles_non_rgb_modes(mode: str) -> None:
    image = _sharp_edge("L").convert(mode)

    blurred = _transform_png(ImageBlurConverter(), image)

    assert blurred.size == (40, 10)
    assert blurred.mode in ("RGB", "RGBA")
    assert any(0 < blurred.getpixel((x, 5))[0] < 255 for x in range(40))


def test_image_blur_converter_keeps_transparent_colors_out_of_visible_edges() -> None:
    # Left half fully transparent but with a hidden red color, right half opaque blue.
    image = Image.new("RGBA", (40, 10), color=(255, 0, 0, 0))
    image.paste((0, 0, 255, 255), (20, 0, 40, 10))

    blurred = _transform_png(ImageBlurConverter(radius=2.0), image)

    assert blurred.mode == "RGBA"
    alpha = [blurred.getpixel((x, 5))[3] for x in range(40)]
    # The alpha edge is blurred too.
    assert any(0 < value < 255 for value in alpha)
    # No visible pixel picks up the hidden red.
    for x in range(40):
        pixel = blurred.getpixel((x, 5))
        assert isinstance(pixel, tuple)
        red, _, blue, visible = pixel
        if visible > 0:
            assert red == 0
            assert blue > 200


def test_image_blur_converter_keeps_color_key_colors_out_of_visible_edges() -> None:
    with _color_key_png() as source:
        assert source.mode == "RGB"
        blurred = _transform_png(ImageBlurConverter(radius=2.0), source)

    assert blurred.mode == "RGBA"
    alpha = [blurred.getpixel((x, 5))[3] for x in range(40)]
    assert alpha[0] == 0
    assert alpha[39] == 255
    assert any(0 < value < 255 for value in alpha)
    for x in range(40):
        pixel = blurred.getpixel((x, 5))
        assert isinstance(pixel, tuple)
        red, _, blue, visible = pixel
        if visible > 0:
            assert red == 0
            assert blue > 200


@pytest.mark.parametrize("mode", ["LA", "PA"])
def test_image_blur_converter_keeps_alpha_for_other_transparent_modes(mode: str) -> None:
    image = Image.new("RGBA", (20, 10), color=(0, 0, 0, 0))
    image.paste((200, 200, 200, 255), (10, 0, 20, 10))
    image = image.convert(mode)

    blurred = _transform_png(ImageBlurConverter(), image)

    assert blurred.mode == "RGBA"
    assert blurred.getpixel((0, 5))[3] == 0
    assert blurred.getpixel((19, 5))[3] == 255


def test_image_blur_converter_jpeg_output_from_transparent_input() -> None:
    image = Image.new("RGBA", (30, 20), color=(90, 90, 90, 128))

    output, output_format = ImageBlurConverter(output_format="JPEG")._transform_image(image, "PNG")

    assert output_format == "JPEG"
    with Image.open(output) as blurred:
        assert blurred.size == (30, 20)
        assert blurred.mode == "RGB"


async def test_image_blur_converter_convert_image_path_async() -> None:
    image_bytes = BytesIO()
    _sharp_edge().save(image_bytes, format="PNG")

    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_async.return_value = image_bytes.getvalue()
        mock_serializer.value = "blurred.png"
        mock_factory.return_value = mock_serializer

        result = await ImageBlurConverter().convert_async(prompt="input.png", input_type="image_path")

    assert result.output_text == "blurred.png"
    assert result.output_type == "image_path"
    assert mock_serializer.file_extension == "png"
    mock_serializer.save_b64_image_async.assert_called_once()


async def test_image_blur_converter_url_input_async() -> None:
    converter = ImageBlurConverter(output_format="WEBP")
    image_bytes = BytesIO()
    _sharp_edge().save(image_bytes, format="PNG")

    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory") as mock_factory:
        mock_serializer = AsyncMock()
        mock_serializer.value = "blurred.webp"
        mock_factory.return_value = mock_serializer

        with patch.object(converter, "_read_image_from_url_async", return_value=image_bytes.getvalue()):
            result = await converter.convert_async(prompt="https://example.com/image.png", input_type="url")

    assert result.output_text == "blurred.webp"
    assert mock_serializer.file_extension == "webp"


async def test_image_blur_converter_rejects_unsupported_input_async() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await ImageBlurConverter().convert_async(prompt="hello", input_type="text")
