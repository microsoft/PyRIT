# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult
from pyrit.prompt_converter.token_smuggling import (
    AsciiSmugglerConverter,
    SneakyBitsSmugglerConverter,
    VariationSelectorSmugglerConverter,
)


@pytest.mark.asyncio
async def test_convert_async_encode_unicode_tags_control():
    # Test encoding using the Unicode Tags mode with control tags enabled.
    converter = AsciiSmugglerConverter(action="encode", unicode_tags=True)
    prompt = "Hello, World!"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # In Unicode Tags mode with unicode_tags flag True, the output should start with U+E0001 and end with U+E007F.
    assert result.output_text.startswith(chr(0xE0001))
    assert result.output_text.endswith(chr(0xE007F))


@pytest.mark.asyncio
async def test_convert_async_encode_unicode_tags():
    # Test encoding using the Unicode Tags mode without control tags.
    converter = AsciiSmugglerConverter(action="encode", unicode_tags=False)
    prompt = "Hello, World!"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert not result.output_text.startswith(chr(0xE0001))
    assert not result.output_text.endswith(chr(0xE007F))


@pytest.mark.asyncio
async def test_convert_async_decode_unicode_tags():
    # The following encoded message is "Hi" with control tags
    encoded_message = chr(0xE0001) + chr(0xE0000) + chr(0xE0048) + chr(0xE0000) + chr(0xE0069) + chr(0xE007F)
    converter = AsciiSmugglerConverter(action="decode", unicode_tags=True)
    result = await converter.convert_async(prompt=encoded_message, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Hi"


@pytest.mark.asyncio
async def test_encode_decode_unicode_tags_control():
    # Test round-trip encoding/decoding with Unicode Tags control mode.
    base_string = "Hello, World!"
    encode_converter = AsciiSmugglerConverter(action="encode", unicode_tags=True)
    encoded_result = await encode_converter.convert_async(prompt=base_string, input_type="text")

    decode_converter = AsciiSmugglerConverter(action="decode", unicode_tags=True)
    decoded_result = await decode_converter.convert_async(prompt=encoded_result.output_text, input_type="text")

    assert isinstance(decoded_result, ConverterResult)
    assert decoded_result.output_type == "text"
    assert decoded_result.output_text == base_string


@pytest.mark.asyncio
async def test_encode_decode_unicode_tags():
    # Test encoding and decoding without unicode tags to ensure input integrity.
    base_string = "Hello, World!"
    encode_converter = AsciiSmugglerConverter(action="encode", unicode_tags=False)
    encoded_result = await encode_converter.convert_async(prompt=base_string, input_type="text")

    decode_converter = AsciiSmugglerConverter(action="decode", unicode_tags=False)
    decoded_result = await decode_converter.convert_async(prompt=encoded_result.output_text, input_type="text")

    assert isinstance(decoded_result, ConverterResult)
    assert decoded_result.output_type == "text"
    assert decoded_result.output_text == base_string


@pytest.mark.asyncio
async def test_convert_async_encode_sneaky_bits():
    # Test encoding using the Sneaky Bits mode.
    converter = SneakyBitsSmugglerConverter(action="encode")
    prompt = "Hello, World!"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # In Sneaky Bits mode, the output should consist solely of the two invisible bit characters.
    valid_chars = {converter.zero_char, converter.one_char}
    assert all(ch in valid_chars for ch in result.output_text)


@pytest.mark.asyncio
async def test_convert_async_decode_sneaky_bits():
    # Test decoding using the Sneaky Bits mode.
    original_text = "Test Sneaky Bits"
    encode_converter = SneakyBitsSmugglerConverter(action="encode")
    encoded_result = await encode_converter.convert_async(prompt=original_text, input_type="text")

    decode_converter = SneakyBitsSmugglerConverter(action="decode")
    decoded_result = await decode_converter.convert_async(prompt=encoded_result.output_text, input_type="text")
    assert isinstance(decoded_result, ConverterResult)
    assert decoded_result.output_type == "text"
    assert decoded_result.output_text == original_text


@pytest.mark.asyncio
async def test_convert_async_encode_only_modes():
    prompt = "Test"

    # Unicode Tags Control
    converter_control = AsciiSmugglerConverter(action="encode", unicode_tags=True)
    result_control = await converter_control.convert_async(prompt=prompt, input_type="text")
    assert result_control.output_text != ""
    assert result_control.output_text.startswith(chr(0xE0001))
    assert result_control.output_text.endswith(chr(0xE007F))
    assert len(result_control.output_text) == len(prompt) + 2  # Explicit length check

    # Unicode Tags (no control)
    converter_no_control = AsciiSmugglerConverter(action="encode", unicode_tags=False)
    result_no_control = await converter_no_control.convert_async(prompt=prompt, input_type="text")
    assert result_no_control.output_text != ""
    assert not result_no_control.output_text.startswith(chr(0xE0001))
    assert not result_no_control.output_text.endswith(chr(0xE007F))
    assert len(result_no_control.output_text) == len(prompt)  # Explicit length check

    # Sneaky Bits
    converter_sneaky = SneakyBitsSmugglerConverter(action="encode")
    result_sneaky = await converter_sneaky.convert_async(prompt=prompt, input_type="text")
    assert result_sneaky.output_text != ""
    valid_chars = {converter_sneaky.zero_char, converter_sneaky.one_char}
    assert all(ch in valid_chars for ch in result_sneaky.output_text)
    assert len(result_sneaky.output_text) == len(prompt.encode("utf-8")) * 8  # Explicit length check


@pytest.mark.asyncio
async def test_convert_async_encode_variation_selector_smuggler():
    # Test encoding using the variation_selector_smuggler mode.
    converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=True)
    prompt = "Hello, World!"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"

    # Check that the encoded message starts with the base character (default: 😊)
    base_char = converter.utf8_base_char
    assert result.output_text.startswith(base_char)

    # Verify that the characters following the base character are valid variation selectors.
    for ch in result.output_text[len(base_char) :]:
        code = ord(ch)
        valid = (0xFE00 <= code <= 0xFE0F) or (0xE0100 <= code <= 0xE01EF)
        assert valid, f"Character {ch} with code U+{code:X} is not a valid variation selector."


@pytest.mark.asyncio
async def test_convert_async_decode_variation_selector_smuggler():
    # Test decoding using the variation_selector_smuggler mode.
    # First encode a known string.
    original_text = "Hello, World!"
    encode_converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=True)
    encoded_result = await encode_converter.convert_async(prompt=original_text, input_type="text")

    decode_converter = VariationSelectorSmugglerConverter(action="decode", embed_in_base=True)
    decoded_result = await decode_converter.convert_async(prompt=encoded_result.output_text, input_type="text")

    assert isinstance(decoded_result, ConverterResult)
    assert decoded_result.output_type == "text"
    assert decoded_result.output_text == original_text


@pytest.mark.asyncio
async def test_encode_decode_variation_selector_smuggler_multibyte():
    # Test round-trip encoding/decoding with multibyte characters.
    base_string = "Ciao, mondo! 😊"
    encode_converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=True)
    encoded_result = await encode_converter.convert_async(prompt=base_string, input_type="text")

    decode_converter = VariationSelectorSmugglerConverter(action="decode", embed_in_base=True)
    decoded_result = await decode_converter.convert_async(prompt=encoded_result.output_text, input_type="text")

    assert isinstance(decoded_result, ConverterResult)
    assert decoded_result.output_type == "text"
    assert decoded_result.output_text == base_string


@pytest.mark.asyncio
async def test_encode_decode_visible_hidden():
    # Test mixing visible + hidden text
    converter = VariationSelectorSmugglerConverter(embed_in_base=True)
    visible_text = "Hallo wie geht es dir?"
    hidden_text = "Das ist eine geheime Nachricht!"

    # Encode both visible and hidden text.
    _, combined = converter.encode_visible_hidden(visible=visible_text, hidden=hidden_text)

    # Confirm that the combined string starts with the visible text.
    assert combined.startswith(visible_text)

    # Now decode the combined string.
    decoded_visible, decoded_hidden = converter.decode_visible_hidden(combined)

    # Check that the visible part and hidden part are correctly recovered.
    assert decoded_visible == visible_text
    assert decoded_hidden == hidden_text


@pytest.mark.asyncio
async def test_embed_in_base_false_inserts_separator():
    # Test that when embed_in_base is False, space is inserted after base char.
    converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=False)
    prompt = "Secret"
    _, encoded = converter.encode_message(prompt)
    base_char = converter.utf8_base_char
    # Expect the encoded string to be: base_char + " " + payload
    assert encoded.startswith(base_char + " "), (
        "Encoded text should start with base_char followed by a space when embed_in_base is False."
    )


# Test for the input_supported method
def test_input_supported():
    # Verify input type filtering
    converter = AsciiSmugglerConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False
    assert converter.input_supported("audio") is False
    assert converter.input_supported("video") is False
    assert converter.input_supported("other") is False
