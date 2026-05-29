# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.prompt_converter import ImageOverlayConverter


def _create_image_bytes(*, size: tuple[int, int] = (200, 200), color: tuple[int, ...] = (125, 125, 125)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_rgba_image_bytes(*, size: tuple[int, int] = (50, 50), color: tuple[int, ...] = (255, 0, 0, 128)) -> bytes:
    img = Image.new("RGBA", size, color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_init_valid_params():
    converter = ImageOverlayConverter(base_image="base.png")
    assert converter._base_image == "base.png"
    assert converter._position == (0, 0)
    assert converter._overlay_size is None
    assert converter._opacity == 1.0


def test_init_all_params():
    converter = ImageOverlayConverter(
        base_image="base.png",
        position=(50, 100),
        overlay_size=(80, 60),
        opacity=0.5,
    )
    assert converter._base_image == "base.png"
    assert converter._position == (50, 100)
    assert converter._overlay_size == (80, 60)
    assert converter._opacity == 0.5


def test_init_empty_base_image_raises():
    with pytest.raises(ValueError, match="valid base_image path"):
        ImageOverlayConverter(base_image="")


@pytest.mark.parametrize("opacity", [-0.1, 1.1, 2.0, -1.0])
def test_init_invalid_opacity_raises(opacity: float):
    with pytest.raises(ValueError, match="Opacity must be between 0.0 and 1.0"):
        ImageOverlayConverter(base_image="base.png", opacity=opacity)


@pytest.mark.parametrize("overlay_size", [(0, 50), (50, 0), (-10, 50), (50, -10)])
def test_init_invalid_overlay_size_raises(overlay_size: tuple[int, int]):
    with pytest.raises(ValueError, match="overlay_size must be a tuple of two positive integers"):
        ImageOverlayConverter(base_image="base.png", overlay_size=overlay_size)


def test_init_valid_boundary_opacity():
    converter_zero = ImageOverlayConverter(base_image="base.png", opacity=0.0)
    assert converter_zero._opacity == 0.0
    converter_one = ImageOverlayConverter(base_image="base.png", opacity=1.0)
    assert converter_one._opacity == 1.0


def test_input_supported():
    converter = ImageOverlayConverter(base_image="base.png")
    assert converter.input_supported("image_path") is True
    assert converter.input_supported("text") is False
    assert converter.input_supported("url") is False


def test_build_identifier():
    converter = ImageOverlayConverter(
        base_image="base.png",
        position=(10, 20),
        overlay_size=(100, 100),
        opacity=0.7,
    )
    identifier = converter.get_identifier()
    assert identifier.class_name == "ImageOverlayConverter"
    assert identifier.class_module == "pyrit.prompt_converter.image_overlay_converter"


def test_composite_images_default_settings():
    converter = ImageOverlayConverter(base_image="base.png")
    base = Image.new("RGB", (200, 200), color=(255, 255, 255))
    overlay = Image.new("RGB", (50, 50), color=(255, 0, 0))

    result = converter._composite_images(base=base, overlay=overlay)

    assert result.size == (200, 200)
    # PNG output preserves the alpha channel
    assert result.mode == "RGBA"
    # The top-left 50x50 region should have overlay color
    pixel_in_overlay = result.getpixel((25, 25))
    assert pixel_in_overlay[:3] == (255, 0, 0)
    # A pixel outside the overlay region should retain base color
    pixel_outside = result.getpixel((150, 150))
    assert pixel_outside[:3] == (255, 255, 255)


def test_composite_images_jpeg_base_flattens_to_rgb():
    converter = ImageOverlayConverter(base_image="base.jpg")
    base = Image.new("RGB", (200, 200), color=(255, 255, 255))
    overlay = Image.new("RGBA", (50, 50), color=(255, 0, 0, 128))

    result = converter._composite_images(base=base, overlay=overlay)

    # JPEG output cannot carry alpha; the result must be flattened to RGB
    assert result.mode == "RGB"
    assert result.size == (200, 200)


def test_composite_images_with_position():
    converter = ImageOverlayConverter(base_image="base.png", position=(100, 100))
    base = Image.new("RGB", (200, 200), color=(255, 255, 255))
    overlay = Image.new("RGB", (50, 50), color=(0, 0, 255))

    result = converter._composite_images(base=base, overlay=overlay)

    # Pixel at the overlay position should have overlay color
    pixel_in_overlay = result.getpixel((125, 125))
    assert pixel_in_overlay[:3] == (0, 0, 255)
    # Pixel before the overlay position should retain base color
    pixel_before = result.getpixel((50, 50))
    assert pixel_before[:3] == (255, 255, 255)


def test_composite_images_with_resize():
    converter = ImageOverlayConverter(base_image="base.png", overlay_size=(100, 100))
    base = Image.new("RGB", (200, 200), color=(255, 255, 255))
    overlay = Image.new("RGB", (50, 50), color=(0, 255, 0))

    result = converter._composite_images(base=base, overlay=overlay)

    # After resize to 100x100, pixel at (75, 75) should have overlay color
    pixel_in_resized = result.getpixel((75, 75))
    assert pixel_in_resized[:3] == (0, 255, 0)
    # Pixel at (150, 150) should remain base color
    pixel_outside = result.getpixel((150, 150))
    assert pixel_outside[:3] == (255, 255, 255)


def test_composite_images_with_opacity():
    converter = ImageOverlayConverter(base_image="base.png", opacity=0.5)
    base = Image.new("RGB", (200, 200), color=(0, 0, 0))
    overlay = Image.new("RGB", (50, 50), color=(255, 255, 255))

    result = converter._composite_images(base=base, overlay=overlay)

    # With 50% opacity on white overlay over black base, expect a middle gray
    pixel = result.getpixel((25, 25))
    r, g, b = pixel[:3]
    assert 100 < r < 200
    assert 100 < g < 200
    assert 100 < b < 200


def test_composite_images_with_zero_opacity():
    converter = ImageOverlayConverter(base_image="base.png", opacity=0.0)
    base = Image.new("RGB", (200, 200), color=(100, 100, 100))
    overlay = Image.new("RGB", (50, 50), color=(255, 0, 0))

    result = converter._composite_images(base=base, overlay=overlay)

    # With 0 opacity, the overlay should be invisible
    pixel = result.getpixel((25, 25))
    assert pixel[:3] == (100, 100, 100)


def test_composite_images_preserves_rgba_overlay_alpha():
    converter = ImageOverlayConverter(base_image="base.png", opacity=1.0)
    base = Image.new("RGB", (200, 200), color=(0, 0, 0))
    overlay = Image.new("RGBA", (50, 50), color=(255, 255, 255, 128))

    result = converter._composite_images(base=base, overlay=overlay)

    # Semi-transparent white overlay on black should produce a mid-gray
    pixel = result.getpixel((25, 25))
    r = pixel[0]
    assert 100 < r < 200


def test_composite_images_warns_when_overlay_out_of_bounds(caplog):
    import logging

    converter = ImageOverlayConverter(base_image="base.png", position=(500, 500))
    base = Image.new("RGB", (200, 200), color=(255, 255, 255))
    overlay = Image.new("RGB", (50, 50), color=(255, 0, 0))

    with caplog.at_level(logging.WARNING, logger="pyrit.prompt_converter.image_overlay_converter"):
        converter._composite_images(base=base, overlay=overlay)

    assert any("falls entirely outside" in record.message for record in caplog.records) < 200


async def test_convert_async_unsupported_input_type_raises():
    converter = ImageOverlayConverter(base_image="base.png")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="hello", input_type="text")


async def test_convert_async_default_settings():
    converter = ImageOverlayConverter(base_image="base.png")
    base_bytes = _create_image_bytes(size=(200, 200), color=(255, 255, 255))
    overlay_bytes = _create_image_bytes(size=(50, 50), color=(255, 0, 0))

    with patch("pyrit.prompt_converter.image_overlay_converter.data_serializer_factory") as mock_factory:
        mock_base_serializer = AsyncMock()
        mock_base_serializer.read_data.return_value = base_bytes

        mock_overlay_serializer = AsyncMock()
        mock_overlay_serializer.read_data.return_value = overlay_bytes

        mock_output_serializer = AsyncMock()
        mock_output_serializer.save_b64_image = AsyncMock()
        mock_output_serializer.value = "result_image.png"

        mock_factory.side_effect = [mock_base_serializer, mock_overlay_serializer, mock_output_serializer]

        result = await converter.convert_async(prompt="overlay.png", input_type="image_path")

        assert result.output_text == "result_image.png"
        assert result.output_type == "image_path"
        mock_base_serializer.read_data.assert_called_once()
        mock_overlay_serializer.read_data.assert_called_once()
        mock_output_serializer.save_b64_image.assert_called_once()

        # Verify the saved image is valid base64-encoded image data
        saved_data = mock_output_serializer.save_b64_image.call_args.kwargs["data"]
        decoded = base64.b64decode(saved_data)
        img = Image.open(BytesIO(decoded))
        assert img.size == (200, 200)


async def test_convert_async_with_position_and_resize():
    converter = ImageOverlayConverter(
        base_image="base.png",
        position=(50, 50),
        overlay_size=(100, 100),
        opacity=0.8,
    )
    base_bytes = _create_image_bytes(size=(300, 300), color=(0, 0, 0))
    overlay_bytes = _create_rgba_image_bytes(size=(50, 50), color=(255, 255, 255, 255))

    with patch("pyrit.prompt_converter.image_overlay_converter.data_serializer_factory") as mock_factory:
        mock_base_serializer = AsyncMock()
        mock_base_serializer.read_data.return_value = base_bytes

        mock_overlay_serializer = AsyncMock()
        mock_overlay_serializer.read_data.return_value = overlay_bytes

        mock_output_serializer = AsyncMock()
        mock_output_serializer.save_b64_image = AsyncMock()
        mock_output_serializer.value = "result.png"

        mock_factory.side_effect = [mock_base_serializer, mock_overlay_serializer, mock_output_serializer]

        result = await converter.convert_async(prompt="overlay.png", input_type="image_path")

        assert result.output_text == "result.png"
        assert result.output_type == "image_path"

        # Verify the composited image
        saved_data = mock_output_serializer.save_b64_image.call_args.kwargs["data"]
        decoded = base64.b64decode(saved_data)
        img = Image.open(BytesIO(decoded))
        assert img.size == (300, 300)


async def test_convert_async_serializer_factory_called_correctly():
    converter = ImageOverlayConverter(base_image="my_base.png")
    base_bytes = _create_image_bytes()
    overlay_bytes = _create_image_bytes(size=(50, 50))

    with patch("pyrit.prompt_converter.image_overlay_converter.data_serializer_factory") as mock_factory:
        mock_base_serializer = AsyncMock()
        mock_base_serializer.read_data.return_value = base_bytes

        mock_overlay_serializer = AsyncMock()
        mock_overlay_serializer.read_data.return_value = overlay_bytes

        mock_output_serializer = AsyncMock()
        mock_output_serializer.save_b64_image = AsyncMock()
        mock_output_serializer.value = "out.png"

        mock_factory.side_effect = [mock_base_serializer, mock_overlay_serializer, mock_output_serializer]

        await converter.convert_async(prompt="overlay_img.png", input_type="image_path")

        assert mock_factory.call_count == 3
        base_call = mock_factory.call_args_list[0]
        assert base_call.kwargs["category"] == "prompt-memory-entries"
        assert base_call.kwargs["value"] == "my_base.png"
        assert base_call.kwargs["data_type"] == "image_path"

        overlay_call = mock_factory.call_args_list[1]
        assert overlay_call.kwargs["category"] == "prompt-memory-entries"
        assert overlay_call.kwargs["value"] == "overlay_img.png"
        assert overlay_call.kwargs["data_type"] == "image_path"

        output_call = mock_factory.call_args_list[2]
        assert output_call.kwargs["category"] == "prompt-memory-entries"
        assert output_call.kwargs["data_type"] == "image_path"
        assert output_call.kwargs["extension"] == "png"


async def test_convert_async_jpeg_base_normalizes_extension_to_jpg():
    converter = ImageOverlayConverter(base_image="my_base.jpg")
    base_bytes = _create_image_bytes()
    overlay_bytes = _create_image_bytes(size=(50, 50))

    with patch("pyrit.prompt_converter.image_overlay_converter.data_serializer_factory") as mock_factory:
        mock_base_serializer = AsyncMock()
        mock_base_serializer.read_data.return_value = base_bytes

        mock_overlay_serializer = AsyncMock()
        mock_overlay_serializer.read_data.return_value = overlay_bytes

        mock_output_serializer = AsyncMock()
        mock_output_serializer.save_b64_image = AsyncMock()
        mock_output_serializer.value = "out.jpg"

        mock_factory.side_effect = [mock_base_serializer, mock_overlay_serializer, mock_output_serializer]

        await converter.convert_async(prompt="overlay_img.png", input_type="image_path")

        output_call = mock_factory.call_args_list[2]
        assert output_call.kwargs["extension"] == "jpg"
