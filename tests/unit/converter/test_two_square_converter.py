# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TwoSquareConverter


async def test_two_square_wikipedia_known_answer():
    # Canonical Wikipedia vertical two-square vector (omits Q, keeps I/J separate):
    # key1="example", key2="keyword", plaintext "help me obiwan kenobi" -> HEDLXWSDJYANHOTKDG.
    converter = TwoSquareConverter(key1="example", key2="keyword", merge_letter="Q", merge_target="I")
    result = await converter.convert_async(prompt="help me obiwan kenobi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "HEDLXWSDJYANHOTKDG"
    assert result.output_type == "text"


async def test_two_square_is_involution():
    # The vertical two-square cipher is its own inverse: encrypting twice returns the
    # sanitized plaintext (uppercase, letters only, with any odd-length padding).
    converter = TwoSquareConverter(key1="example", key2="keyword", merge_letter="Q", merge_target="I")
    plaintext = "help me obiwan kenobi"
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert twice.output_text == "HELPMEOBIWANKENOBI"


async def test_two_square_default_keys_roundtrip_with_padding():
    # Default keys merge J->I. Odd-length input is padded with a single X, and the
    # involution property recovers the padded sanitized plaintext.
    converter = TwoSquareConverter()
    plaintext = "attackatdawn"  # 12 letters, even
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert twice.output_text == "ATTACKATDAWN"


async def test_two_square_odd_length_is_padded():
    converter = TwoSquareConverter()
    result = await converter.convert_async(prompt="abc", input_type="text")
    # 3 letters -> padded to 4 -> 4 ciphertext letters.
    assert len(result.output_text) == 4
    # Round-tripping shows the appended padding character.
    twice = await converter.convert_async(prompt=result.output_text, input_type="text")
    assert twice.output_text == "ABCX"


async def test_two_square_non_letters_and_case_removed():
    converter = TwoSquareConverter()
    with_noise = await converter.convert_async(prompt="He llo, 123 World!", input_type="text")
    without_noise = await converter.convert_async(prompt="helloworld", input_type="text")
    assert with_noise.output_text == without_noise.output_text
    # Output is uppercase letters only.
    assert with_noise.output_text.isalpha()
    assert with_noise.output_text.isupper()


async def test_two_square_j_merged_into_i_by_default():
    converter = TwoSquareConverter()
    merged = await converter.convert_async(prompt="jump", input_type="text")
    as_i = await converter.convert_async(prompt="iump", input_type="text")
    assert merged.output_text == as_i.output_text


async def test_two_square_different_keys_produce_different_output():
    conv_a = TwoSquareConverter(key1="alpha", key2="bravo")
    conv_b = TwoSquareConverter(key1="charlie", key2="delta")
    result_a = await conv_a.convert_async(prompt="meetatdawn", input_type="text")
    result_b = await conv_b.convert_async(prompt="meetatdawn", input_type="text")
    assert result_a.output_text != result_b.output_text


async def test_two_square_empty_prompt():
    converter = TwoSquareConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""


async def test_two_square_same_column_digraph_maps_to_itself():
    # With key1="example"/key2="keyword" omitting Q, the digraph "HE" shares a column and
    # the vertical rule maps it to itself.
    converter = TwoSquareConverter(key1="example", key2="keyword", merge_letter="Q", merge_target="I")
    result = await converter.convert_async(prompt="he", input_type="text")
    assert result.output_text == "HE"


async def test_two_square_identifier_includes_params():
    converter = TwoSquareConverter(key1="alpha", key2="bravo")
    identifier = converter.get_identifier()
    params = identifier.params
    assert params["key1"] == "alpha"
    assert params["key2"] == "bravo"
    assert params["merge_letter"] == "J"
    assert params["merge_target"] == "I"
    assert params["padding_char"] == "X"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"key1": ""},
        {"key1": "has space"},
        {"key1": "abc1"},
        {"key2": "!!"},
        {"merge_letter": "JJ"},
        {"merge_letter": "1"},
        {"merge_letter": "I", "merge_target": "I"},
        {"padding_char": "J"},  # J is merged out of the default reduced alphabet
        {"padding_char": "55"},
        {"merge_target": "J"},  # J not in reduced alphabet when merge_letter is J
    ],
)
def test_two_square_invalid_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        TwoSquareConverter(**kwargs)


async def test_two_square_rejects_unsupported_input_type():
    converter = TwoSquareConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
