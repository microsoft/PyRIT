# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os

import pytest
from PIL import Image, ImageFont

from pyrit.prompt_converter import AddImageTextConverter, AddTextImageConverter


@pytest.fixture
def image_text_converter_sample_image(tmp_path):
    img_path = str(tmp_path / "test.png")
    img = Image.new("RGB", (100, 100), color=(125, 125, 125))
    img.save(img_path)
    return img_path


@pytest.fixture
def large_sample_image(tmp_path):
    img_path = str(tmp_path / "test_large.png")
    img = Image.new("RGB", (1600, 800), color=(200, 200, 200))
    img.save(img_path)
    return img_path


def test_add_image_text_converter_initialization(image_text_converter_sample_image):
    converter = AddImageTextConverter(
        img_to_add=image_text_converter_sample_image,
        font_name="helvetica.ttf",
        color=(255, 255, 255),
        font_size=20,
    )
    assert converter._img_to_add == image_text_converter_sample_image
    assert converter._font_name == "helvetica.ttf"
    assert converter._color == (255, 255, 255)
    assert converter._font_size_max == 20
    assert converter._font_size_min == 20
    assert converter._auto_font_size is False
    assert converter._font is not None
    assert type(converter._font) is ImageFont.FreeTypeFont


def test_add_image_text_converter_positional_arg_deprecation(image_text_converter_sample_image):
    with pytest.warns(FutureWarning, match="Passing 'img_to_add' as a positional argument is deprecated"):
        converter = AddImageTextConverter(image_text_converter_sample_image)
    assert converter._img_to_add == image_text_converter_sample_image


def test_add_image_text_converter_positional_and_keyword_raises(image_text_converter_sample_image):
    with pytest.raises(TypeError, match="Cannot pass img_to_add as both positional and keyword"):
        AddImageTextConverter(image_text_converter_sample_image, img_to_add=image_text_converter_sample_image)


def test_add_image_text_converter_too_many_positional_args_raises(image_text_converter_sample_image):
    with pytest.raises(TypeError, match="takes at most 1 positional argument"):
        AddImageTextConverter(image_text_converter_sample_image, "extra")


def test_add_image_text_converter_x_pos_y_pos_deprecation(image_text_converter_sample_image):
    with pytest.warns(FutureWarning, match="x_pos and y_pos are deprecated"):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, x_pos=50, y_pos=50)


def test_add_image_text_converter_x_pos_y_pos_deprecation_default_value(image_text_converter_sample_image):
    with pytest.warns(FutureWarning, match="x_pos and y_pos are deprecated"):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, x_pos=10)


def test_add_image_text_converter_no_x_pos_y_pos_no_warning(image_text_converter_sample_image):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        AddImageTextConverter(img_to_add=image_text_converter_sample_image)


def test_add_image_text_converter_x_pos_with_bounding_box_raises(image_text_converter_sample_image):
    with pytest.raises(ValueError, match="Cannot pass x_pos/y_pos together with bounding_box"):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, x_pos=10, bounding_box=(0, 0, 100, 100))


def test_add_image_text_converter_invalid_font(image_text_converter_sample_image):
    with pytest.raises(ValueError):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, font_name="helvetica.otf")


def test_add_image_text_converter_null_img_to_add():
    with pytest.raises(ValueError):
        AddImageTextConverter(img_to_add="", font_name="helvetica.ttf")


def test_add_image_text_converter_fallback_to_default_font(image_text_converter_sample_image, caplog):
    AddImageTextConverter(
        img_to_add=image_text_converter_sample_image,
        font_name="nonexistent_font.ttf",
        color=(255, 255, 255),
        font_size=20,
    )
    assert any(
        record.levelname == "WARNING" and "Cannot open font resource" in record.message for record in caplog.records
    )


def test_add_image_text_converter_font_size_tuple(image_text_converter_sample_image):
    converter = AddImageTextConverter(
        img_to_add=image_text_converter_sample_image,
        font_size=(10, 60),
    )
    assert converter._font_size_min == 10
    assert converter._font_size_max == 60
    assert converter._auto_font_size is True


def test_add_image_text_converter_font_size_tuple_invalid(image_text_converter_sample_image):
    with pytest.raises(ValueError, match="font_size tuple must be"):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, font_size=(60, 10))


def test_add_image_text_converter_font_size_tuple_zero_min(image_text_converter_sample_image):
    with pytest.raises(ValueError, match="font_size tuple must be"):
        AddImageTextConverter(img_to_add=image_text_converter_sample_image, font_size=(0, 10))


def test_image_text_converter_add_text_to_image(image_text_converter_sample_image):
    converter = AddImageTextConverter(
        img_to_add=image_text_converter_sample_image, font_name="helvetica.ttf", color=(255, 255, 255)
    )
    with Image.open(image_text_converter_sample_image) as image:
        pixels_before = list(image.get_flattened_data())
    updated_image = converter._add_text_to_image("Sample Text!")
    pixels_after = list(updated_image.get_flattened_data())
    assert updated_image
    # Check if at least one pixel changed, indicating that text was added
    assert pixels_before != pixels_after


@pytest.mark.asyncio
async def test_add_image_text_converter_invalid_input_text(image_text_converter_sample_image) -> None:
    converter = AddImageTextConverter(img_to_add=image_text_converter_sample_image)
    with pytest.raises(ValueError):
        assert await converter.convert_async(prompt="", input_type="text")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_add_image_text_converter_invalid_file_path():
    converter = AddImageTextConverter(img_to_add="nonexistent_image.png", font_name="helvetica.ttf")
    with pytest.raises(FileNotFoundError):
        assert await converter.convert_async(prompt="Sample Text!", input_type="text")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_add_image_text_converter_convert_async(
    image_text_converter_sample_image, patch_central_database
) -> None:
    converter = AddImageTextConverter(img_to_add=image_text_converter_sample_image)
    converted_image = await converter.convert_async(prompt="Sample Text!", input_type="text")
    assert converted_image
    assert converted_image.output_text
    assert converted_image.output_type == "image_path"
    assert os.path.exists(converted_image.output_text)


def test_text_image_converter_input_supported(image_text_converter_sample_image):
    converter = AddImageTextConverter(img_to_add=image_text_converter_sample_image)
    assert converter.input_supported("image_path") is False
    assert converter.input_supported("text") is True


@pytest.mark.asyncio
async def test_add_image_text_converter_equal_to_add_text_image(
    image_text_converter_sample_image, patch_central_database
) -> None:
    converter = AddImageTextConverter(img_to_add=image_text_converter_sample_image)
    converted_image = await converter.convert_async(prompt="Sample Text!", input_type="text")
    text_image_converter = AddTextImageConverter(text_to_add="Sample Text!")
    converted_text_image = await text_image_converter.convert_async(
        prompt=image_text_converter_sample_image, input_type="image_path"
    )
    with Image.open(converted_image.output_text) as img1:
        pixels_image_text = list(img1.get_flattened_data())
    with Image.open(converted_text_image.output_text) as img2:
        pixels_text_image = list(img2.get_flattened_data())
    assert pixels_image_text == pixels_text_image


# --- Bounding box feature tests ---


def test_add_image_text_converter_invalid_bounding_box(image_text_converter_sample_image):
    with pytest.raises(ValueError, match="bounding_box must have x2 > x1 and y2 > y1"):
        AddImageTextConverter(
            img_to_add=image_text_converter_sample_image,
            bounding_box=(100, 100, 50, 200),
        )


def test_add_image_text_converter_bounding_box_renders_text(large_sample_image):
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=20,
        bounding_box=(100, 100, 400, 300),
    )
    with Image.open(large_sample_image) as image:
        pixels_before = list(image.get_flattened_data())
    updated_image = converter._add_text_to_image("Hello World")
    pixels_after = list(updated_image.get_flattened_data())
    assert pixels_before != pixels_after


def test_add_image_text_converter_bounding_box_with_center(large_sample_image):
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=20,
        bounding_box=(100, 100, 500, 400),
        center_text=True,
    )
    updated_image = converter._add_text_to_image("Centered Text")
    assert updated_image is not None
    assert updated_image.size == (1600, 800)


def test_add_image_text_converter_bounding_box_with_rotation(large_sample_image):
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=20,
        bounding_box=(100, 100, 500, 400),
        rotation=10.0,
        center_text=True,
    )
    updated_image = converter._add_text_to_image("Rotated Text")
    assert updated_image is not None
    assert updated_image.size == (1600, 800)


def test_add_image_text_converter_auto_font_size(large_sample_image):
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=(10, 60),
        bounding_box=(100, 100, 300, 200),
        center_text=True,
    )
    updated_image = converter._add_text_to_image(
        "This is a long text that should auto-shrink to fit inside the small bounding box region"
    )
    assert updated_image is not None


def test_add_image_text_converter_bounding_box_identifier(large_sample_image):
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        bounding_box=(100, 100, 400, 300),
        rotation=10.0,
        center_text=True,
        font_size=(8, 15),
    )
    identifier = converter.get_identifier()
    params = identifier.params
    assert params["bounding_box"] == (100, 100, 400, 300)
    assert params["rotation"] == 10.0
    assert params["center_text"] is True
    assert params["font_size_min"] == 8
    assert params["font_size_max"] == 15


@pytest.mark.asyncio
async def test_add_image_text_converter_bounding_box_convert_async(large_sample_image, patch_central_database) -> None:
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=(10, 30),
        bounding_box=(100, 100, 500, 400),
        center_text=True,
    )
    result = await converter.convert_async(prompt="Comic text in a box", input_type="text")
    assert result.output_type == "image_path"
    assert os.path.exists(result.output_text)


def test_add_image_text_converter_no_bounding_box_uses_full_image(large_sample_image):
    """When no bounding_box is given, the full image is used as the bounding box."""
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=20,
    )
    with Image.open(large_sample_image) as image:
        pixels_before = list(image.get_flattened_data())
    updated_image = converter._add_text_to_image("Full image text")
    pixels_after = list(updated_image.get_flattened_data())
    assert pixels_before != pixels_after


def test_add_image_text_converter_auto_font_size_no_bounding_box(large_sample_image):
    """Auto font sizing works without explicit bounding_box (uses full image)."""
    converter = AddImageTextConverter(
        img_to_add=large_sample_image,
        font_size=(10, 60),
    )
    updated_image = converter._add_text_to_image("Auto-sized text on full image")
    assert updated_image is not None
