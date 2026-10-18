# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, VariationSelectorSmugglerConverter


async def test_variation_selector_encode_basic():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


async def test_variation_selector_decode_roundtrip():
    encoder = VariationSelectorSmugglerConverter(action="encode")
    encoded = await encoder.convert_async(prompt="test", input_type="text")

    decoder = VariationSelectorSmugglerConverter(action="decode")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_text == "test"


async def test_variation_selector_no_embed():
    converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=False)
    result = await converter.convert_async(prompt="a", input_type="text")
    base_char = converter.utf8_base_char
    # With embed_in_base=False, a space separator is inserted after the base char
    assert result.output_text.startswith(base_char + " ")


async def test_variation_selector_empty():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="", input_type="text")
    # Empty input still produces base char prefix
    assert result.output_text == converter.utf8_base_char


def test_variation_selector_invalid_action():
    with pytest.raises(ValueError):
        VariationSelectorSmugglerConverter(action="invalid")


@pytest.mark.parametrize("base_char", ["", "ab", "😊x"])
def test_variation_selector_invalid_base_char(base_char: str) -> None:
    with pytest.raises(ValueError, match="base_char_utf8 must be exactly one character"):
        VariationSelectorSmugglerConverter(base_char_utf8=base_char)


@pytest.mark.parametrize("base_char", ["\ufe00", "\ufe0f", "\U000e0100", "\U000e01ef"])
def test_variation_selector_rejects_selector_base_char(base_char: str) -> None:
    with pytest.raises(ValueError, match="base_char_utf8 must not be a variation selector"):
        VariationSelectorSmugglerConverter(base_char_utf8=base_char)


@pytest.mark.parametrize("base_char", ["A", "\ufdff", "\ufe10", "\U000e00ff", "\U000e01f0"])
@pytest.mark.parametrize("embed_in_base", [True, False])
async def test_variation_selector_custom_base_char_roundtrip(base_char: str, embed_in_base: bool) -> None:
    encoder = VariationSelectorSmugglerConverter(action="encode", base_char_utf8=base_char, embed_in_base=embed_in_base)
    encoded = await encoder.convert_async(prompt="test", input_type="text")

    decoder = VariationSelectorSmugglerConverter(action="decode", base_char_utf8=base_char, embed_in_base=embed_in_base)
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")

    assert encoded.output_text.startswith(base_char)
    assert decoded.output_text == "test"


async def test_variation_selector_input_not_supported():
    converter = VariationSelectorSmugglerConverter(action="encode")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


@pytest.mark.parametrize("base_char", ["\U0001f60a", "\u2764", " "])
@pytest.mark.parametrize("embed_in_base", [True, False])
@pytest.mark.parametrize("hidden", ["\x0e", "\x0f"])
async def test_variation_selector_control_byte_framing_async(
    *, base_char: str, embed_in_base: bool, hidden: str
) -> None:
    encoder = VariationSelectorSmugglerConverter(base_char_utf8=base_char, embed_in_base=embed_in_base)

    encoded = await encoder.convert_async(prompt=hidden)

    assert encoded.output_text == base_char + " " + chr(0xFE00 + ord(hidden))


@pytest.mark.parametrize("base_char", ["\U0001f60a", "\u2764", " "])
@pytest.mark.parametrize("embed_in_base", [True, False])
@pytest.mark.parametrize("hidden, presentation", [("\x0e", "\ufe0f"), ("\x0f", "\ufe0e")])
@pytest.mark.parametrize("decode_embed", [True, False])
async def test_variation_selector_control_byte_after_emoji_carrier_async(
    *, base_char: str, embed_in_base: bool, hidden: str, presentation: str, decode_embed: bool
) -> None:
    encoder = VariationSelectorSmugglerConverter(base_char_utf8=base_char, embed_in_base=embed_in_base)
    encoded = await encoder.convert_async(prompt=hidden)
    decoder = VariationSelectorSmugglerConverter(action="decode", base_char_utf8=base_char, embed_in_base=decode_embed)

    decoded = await decoder.convert_async(prompt="I \u2764" + presentation + " this " + encoded.output_text)

    assert decoded.output_text == hidden


@pytest.mark.parametrize("embed_in_base", [True, False])
@pytest.mark.parametrize("hidden", ["\x0e", "\x0f"])
async def test_variation_selector_first_control_payload_async(*, embed_in_base: bool, hidden: str) -> None:
    encoder = VariationSelectorSmugglerConverter(embed_in_base=embed_in_base)
    first = await encoder.convert_async(prompt=hidden)
    second = await encoder.convert_async(prompt="second")
    decoder = VariationSelectorSmugglerConverter(action="decode", embed_in_base=embed_in_base)

    decoded = await decoder.convert_async(prompt=first.output_text + " " + second.output_text)

    assert decoded.output_text == hidden


@pytest.mark.parametrize("visible", ["Hello", "Hello ", "Hello  "])
@pytest.mark.parametrize("embed_in_base", [True, False])
@pytest.mark.parametrize("hidden", ["\x0e", "\x0f"])
async def test_variation_selector_control_byte_space_base_helper_async(
    *, visible: str, embed_in_base: bool, hidden: str
) -> None:
    converter = VariationSelectorSmugglerConverter(base_char_utf8=" ", embed_in_base=embed_in_base)

    _, combined = converter.encode_visible_hidden(visible=visible, hidden=hidden)

    assert converter.decode_visible_hidden(combined) == (visible, hidden)


@pytest.mark.parametrize("hidden", ["\x0e", "\x0f"])
async def test_variation_selector_legacy_unseparated_control_byte_async(*, hidden: str) -> None:
    decoder = VariationSelectorSmugglerConverter(action="decode")
    encoded = decoder.utf8_base_char + chr(0xFE00 + ord(hidden))

    decoded = await decoder.convert_async(prompt=encoded)

    assert decoded.output_text == hidden
