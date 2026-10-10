# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# Deprecation test support: remove in 1.4.0.
import warnings
from contextlib import nullcontext

import pytest

from pyrit.converter import BinaryConverter, ConverterResult
from pyrit.converter.text_selection_strategy import WordIndexSelectionStrategy


async def test_binary_converter_8_bit_ascii():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_8)
    prompt = "A"
    expected_output = "01000001"  # 8-bit binary representation of 'A'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_16_bit_unicode():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_16)
    prompt = "é"  # Unicode character with code point U+00E9
    expected_output = "0000000011101001"  # 16-bit binary representation of 'é'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_32_bit_emoji():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_32)
    prompt = "😊"  # Emoji character with code point U+1F60A
    expected_output = "00000000000000011111011000001010"  # 32-bit binary representation of '😊'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_invalid_bits_per_char():
    with pytest.raises(TypeError, match="bits_per_char must be an instance of BinaryConverter.BitsPerChar Enum."):
        BinaryConverter(bits_per_char=10)  # Invalid bits_per_char


async def test_binary_converter_raises_when_selected_word_exceeds_bits():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_16)
    with pytest.raises(ValueError, match="bits_per_char=16 is too small"):
        await converter.convert_async(prompt="hello 👋", input_type="text")


async def test_binary_converter_ignores_unselected_word_exceeding_bits():
    # Only "hello" is converted, so the emoji in the unselected word is passed
    # through untouched and must not fail validation.
    converter = BinaryConverter(
        bits_per_char=BinaryConverter.BitsPerChar.BITS_16,
        word_selection_strategy=WordIndexSelectionStrategy(indices=[0]),
    )
    result = await converter.convert_async(prompt="hello 👋", input_type="text")
    expected_hello = " ".join(format(ord(char), "016b") for char in "hello")
    space_binary = format(ord(" "), "016b")
    assert result.output_text == f"{expected_hello} {space_binary} 👋"
    assert result.output_type == "text"


# Deprecation tests: remove in 1.4.0.
class TestBinaryConverterValidationDeprecation:
    @pytest.mark.parametrize(("index", "raises"), [(0, False), (1, True)])
    def test_validate_input_warns_and_checks_selected_words(self, *, index: int, raises: bool) -> None:
        converter = BinaryConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[index]))
        with pytest.warns(DeprecationWarning) as recorded:
            with pytest.raises(ValueError, match="Minimum required bits: 17") if raises else nullcontext():
                assert converter.validate_input("hello 👋") is None
        assert len(recorded) == 1
        assert str(recorded[0].message) == (
            "BinaryConverter.validate_input is deprecated and will be removed in 1.4.0. "
            "Use BinaryConverter.convert_async instead."
        )
        assert recorded[0].filename == __file__

    @pytest.mark.parametrize(("prompt", "index"), [("", 0), ("hello 👋", 0), ("hello 👋", 1)])
    async def test_builtin_validation_does_not_warn_async(self, *, prompt: str, index: int) -> None:
        class PlainBinaryConverter(BinaryConverter):
            pass

        for converter_type in (BinaryConverter, PlainBinaryConverter):
            converter = converter_type(word_selection_strategy=WordIndexSelectionStrategy(indices=[index]))
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                with pytest.raises(ValueError, match="bits_per_char=16") if index == 1 else nullcontext():
                    await converter.convert_async(prompt=prompt)

    @pytest.mark.parametrize("mode", ["accept", "reject", "super"])
    async def test_custom_validation_is_preserved_async(self, mode: str) -> None:
        validated_prompts: list[str] = []

        class CustomBinaryConverter(BinaryConverter):
            def validate_input(self, prompt: str) -> None:
                validated_prompts.append(prompt)
                if mode == "reject":
                    raise ValueError("Rejected by custom validation")
                if mode == "super":
                    super().validate_input(prompt)

        class InheritedCustomBinaryConverter(CustomBinaryConverter):
            pass

        for converter_type in (CustomBinaryConverter, InheritedCustomBinaryConverter):
            validated_prompts.clear()
            converter = converter_type(word_selection_strategy=WordIndexSelectionStrategy(indices=[0]))
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always", DeprecationWarning)
                with (
                    pytest.raises(ValueError, match="Rejected by custom validation")
                    if mode == "reject"
                    else nullcontext()
                ):
                    result = await converter.convert_async(prompt="hello 👋")
            assert validated_prompts == ["hello 👋"]
            assert [warning.category for warning in recorded] == ([DeprecationWarning] if mode == "super" else [])
            if mode != "reject":
                assert result.output_text.endswith("👋")
