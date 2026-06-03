# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import os
from io import BytesIO

import pytest
from PIL import Image, ImageFont

from pyrit.prompt_converter import AddTextImageConverter


@pytest.fixture
def text_image_converter_sample_image_bytes():
    img = Image.new("RGB", (100, 100), color=(125, 125, 125))
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    return img_bytes.getvalue()


def test_add_text_image_converter_initialization():
    converter = AddTextImageConverter(
        text_to_add="Sample text", font_name="helvetica.ttf", color=(255, 255, 255), font_size=20, x_pos=10, y_pos=10
    )
    assert converter._text_to_add == "Sample text"
    assert converter._font_name == "helvetica.ttf"
    assert converter._color == (255, 255, 255)
    assert converter._font_size == 20
    assert converter._x_pos == 10
    assert converter._y_pos == 10
    assert converter._font is not None
    assert type(converter._font) is ImageFont.FreeTypeFont


def test_add_text_image_converter_invalid_font():
    with pytest.raises(ValueError):
        AddTextImageConverter(text_to_add="Sample text", font_name="helvetica.otf")  # Invalid font extension


def test_add_text_image_converter_positional_arg_deprecation():
    with pytest.warns(
        DeprecationWarning, match="Passing text_to_add as a positional argument to AddTextImageConverter"
    ):
        converter = AddTextImageConverter("Sample text")
    assert converter._text_to_add == "Sample text"


def test_add_text_image_converter_positional_and_keyword_raises():
    with pytest.raises(TypeError, match="Cannot pass text_to_add as both positional and keyword"):
        AddTextImageConverter("Sample text", text_to_add="Sample text")


def test_add_text_image_converter_too_many_positional_args_raises():
    with pytest.raises(TypeError, match="takes at most 1 positional argument"):
        AddTextImageConverter("Sample text", "extra")


def test_add_text_image_converter_invalid_text_to_add():
    with pytest.raises(ValueError):
        AddTextImageConverter(text_to_add="", font_name="helvetica.ttf")


def test_add_text_image_converter_fallback_to_default_font(text_image_converter_sample_image_bytes, caplog):
    converter = AddTextImageConverter(
        text_to_add="New text!",
        font_name="nonexistent_font.ttf",
        color=(255, 255, 255),
        font_size=20,
        x_pos=10,
        y_pos=10,
    )
    image = Image.open(BytesIO(text_image_converter_sample_image_bytes))
    try:
        pixels_before = list(image.get_flattened_data())
        updated_image = converter._add_text_to_image(image)
        pixels_after = list(updated_image.get_flattened_data())
    finally:
        image.close()
    assert any(
        record.levelname == "WARNING" and "Cannot open font resource" in record.message for record in caplog.records
    )
    assert pixels_before != pixels_after


def test_text_image_converter_add_text_to_image(text_image_converter_sample_image_bytes):
    converter = AddTextImageConverter(text_to_add="Hello, World!", font_name="helvetica.ttf", color=(255, 255, 255))
    image = Image.open(BytesIO(text_image_converter_sample_image_bytes))
    try:
        pixels_before = list(image.get_flattened_data())
        updated_image = converter._add_text_to_image(image)
        pixels_after = list(updated_image.get_flattened_data())
    finally:
        image.close()
    assert updated_image
    # Check if at least one pixel changed, indicating that text was added
    assert pixels_before != pixels_after


async def test_add_text_image_converter_invalid_input_image() -> None:
    converter = AddTextImageConverter(text_to_add="test")
    with pytest.raises(FileNotFoundError):
        assert await converter.convert_async(prompt="mock_image.png", input_type="image_path")  # type: ignore[arg-type]


async def test_add_text_image_converter_convert_async(tmp_path, sqlite_instance) -> None:
    converter = AddTextImageConverter(text_to_add="test")
    mock_image = Image.new("RGB", (400, 300), (255, 255, 255))
    image_path = str(tmp_path / "test.png")
    mock_image.save(image_path)

    converted_image = await converter.convert_async(prompt=image_path, input_type="image_path")
    assert converted_image
    assert converted_image.output_text
    assert converted_image.output_type == "image_path"
    assert os.path.exists(converted_image.output_text)
    os.remove(converted_image.output_text)


def test_text_image_converter_input_supported():
    converter = AddTextImageConverter(text_to_add="Sample text")
    assert converter.input_supported("image_path") is True
    assert converter.input_supported("text") is False
