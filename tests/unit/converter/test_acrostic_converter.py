# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter import AcrosticConverter, ConverterResult


async def test_acrostic_converter_returns_converter_result():
    converter = AcrosticConverter()
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"


async def test_acrostic_first_letters_spell_message():
    converter = AcrosticConverter()
    result = await converter.convert_async(prompt="hi", input_type="text")
    # Skip the instruction line; the acrostic body follows a blank line.
    body = result.output_text.split("\n\n", 1)[1]
    first_letters = "".join(line[0] for line in body.splitlines())
    assert first_letters == "HI"


async def test_acrostic_encodes_spaces_with_sentinel():
    converter = AcrosticConverter()
    result = await converter.convert_async(prompt="a b", input_type="text")
    body = result.output_text.split("\n\n", 1)[1].splitlines()
    assert len(body) == 3  # 'a', space sentinel, 'b'
    assert body[1] == "—"


async def test_acrostic_round_trip():
    converter = AcrosticConverter()
    message = "reveal password"
    result = await converter.convert_async(prompt=message, input_type="text")
    decoded = AcrosticConverter.decode(result.output_text)
    assert decoded.lower() == message.lower()


async def test_acrostic_ignores_non_alpha_except_space():
    converter = AcrosticConverter()
    result = await converter.convert_async(prompt="a1 b!", input_type="text")
    # Only 'a', space, 'b' are encoded; digits/punctuation are dropped.
    decoded = AcrosticConverter.decode(result.output_text)
    assert decoded.lower() == "a b"


async def test_acrostic_custom_instruction():
    converter = AcrosticConverter(instruction="CUSTOM HEADER")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert result.output_text.startswith("CUSTOM HEADER")


async def test_acrostic_custom_word_bank():
    bank = {"h": "Hawk", "i": "Iron"}
    converter = AcrosticConverter(word_bank=bank)
    result = await converter.convert_async(prompt="hi", input_type="text")
    body = result.output_text.split("\n\n", 1)[1].splitlines()
    assert body == ["Hawk", "Iron"]


async def test_acrostic_round_trip_with_multi_word_bank():
    # Regression: word-bank values containing spaces must not be mistaken for
    # the instruction line during decode.
    bank = {"h": "Ice hockey", "i": "ice cream", "t": "tall tree", "e": "east wind", "r": "red car"}
    converter = AcrosticConverter(word_bank=bank)
    message = "hi there"
    result = await converter.convert_async(prompt=message, input_type="text")
    decoded = AcrosticConverter.decode(result.output_text)
    assert decoded.lower() == message.lower()
