# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, JsonStringConverter


@pytest.fixture
def json_string_converter() -> JsonStringConverter:
    return JsonStringConverter()


@pytest.mark.asyncio
async def test_json_string_converter_basic(json_string_converter: JsonStringConverter):
    prompt = "Hello World"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Hello World"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_escapes_quotes(json_string_converter: JsonStringConverter):
    prompt = 'Hello "World"'
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == 'Hello \\"World\\"'
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_escapes_newlines(json_string_converter: JsonStringConverter):
    prompt = "Hello\nWorld"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == "Hello\\nWorld"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_escapes_carriage_return(json_string_converter: JsonStringConverter):
    prompt = "Hello\rWorld"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == "Hello\\rWorld"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_escapes_tabs(json_string_converter: JsonStringConverter):
    prompt = "Hello\tWorld"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == "Hello\\tWorld"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_escapes_backslashes(json_string_converter: JsonStringConverter):
    prompt = "Hello\\World"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == "Hello\\\\World"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_complex_string(json_string_converter: JsonStringConverter):
    prompt = 'Line 1\nLine 2\t"quoted"\r\nEnd\\'
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == 'Line 1\\nLine 2\\t\\"quoted\\"\\r\\nEnd\\\\'
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_unicode(json_string_converter: JsonStringConverter):
    prompt = "Vanakkam சென்னை!"
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text is not None
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_empty_string(json_string_converter: JsonStringConverter):
    prompt = ""
    result = await json_string_converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_json_string_converter_input_supported(json_string_converter: JsonStringConverter):
    assert json_string_converter.input_supported("text") is True
    assert json_string_converter.input_supported("image_path") is False


@pytest.mark.asyncio
async def test_json_string_converter_output_supported(json_string_converter: JsonStringConverter):
    assert json_string_converter.output_supported("text") is True
    assert json_string_converter.output_supported("image_path") is False


@pytest.mark.asyncio
async def test_json_string_converter_invalid_input_type(json_string_converter: JsonStringConverter):
    with pytest.raises(ValueError, match="Input type not supported"):
        await json_string_converter.convert_async(prompt="test", input_type="image_path")
