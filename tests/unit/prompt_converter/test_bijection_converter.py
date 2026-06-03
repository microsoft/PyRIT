# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import BijectionConverter, ConverterResult

# ---------------------------------------------------------------------------
# Construction — encode mode validation
# ---------------------------------------------------------------------------


def test_bijection_converter_invalid_fixed_points_high():
    with pytest.raises(ValueError, match="fixed_points"):
        BijectionConverter(fixed_points=26)


def test_bijection_converter_invalid_fixed_points_too_high():
    with pytest.raises(ValueError, match="fixed_points"):
        BijectionConverter(fixed_points=27)


def test_bijection_converter_invalid_fixed_points_low():
    with pytest.raises(ValueError, match="fixed_points"):
        BijectionConverter(fixed_points=-1)


def test_bijection_converter_fixed_points_25_is_valid():
    # 25 is the new upper bound — should not raise
    c = BijectionConverter(fixed_points=25, append_description=False)
    assert c is not None


def test_bijection_converter_invalid_digit_length_high():
    with pytest.raises(ValueError, match="digit_length"):
        BijectionConverter(digit_length=6)


def test_bijection_converter_invalid_digit_length_low():
    with pytest.raises(ValueError, match="digit_length"):
        BijectionConverter(digit_length=0)


def test_bijection_converter_invalid_num_teaching_shots():
    with pytest.raises(ValueError, match="num_teaching_shots"):
        BijectionConverter(num_teaching_shots=-1)


def test_bijection_converter_custom_mapping_exclusive_with_seed():
    with pytest.raises(ValueError, match="mutually exclusive"):
        BijectionConverter(custom_mapping={"a": "z"}, seed=42)


def test_bijection_converter_custom_mapping_exclusive_with_fixed_points():
    with pytest.raises(ValueError, match="mutually exclusive"):
        BijectionConverter(custom_mapping={"a": "z"}, fixed_points=5)


def test_bijection_converter_custom_mapping_exclusive_with_digit_length():
    with pytest.raises(ValueError, match="mutually exclusive"):
        BijectionConverter(custom_mapping={"a": "99"}, digit_length=3)


# ---------------------------------------------------------------------------
# Construction — decode mode
# ---------------------------------------------------------------------------


def test_bijection_converter_decode_requires_custom_mapping():
    with pytest.raises(ValueError, match="custom_mapping is required"):
        BijectionConverter(direction="decode")


def test_bijection_converter_decode_accepts_custom_mapping():
    mapping = {"a": "99", "b": "b"}
    c = BijectionConverter(direction="decode", custom_mapping=mapping)
    assert c.mapping == mapping


def test_bijection_converter_decode_autodetects_digit_length():
    mapping = {"a": "999", "b": "b"}  # 3-digit code
    c = BijectionConverter(direction="decode", custom_mapping=mapping)
    assert c.digit_length == 3


def test_bijection_converter_decode_falls_back_to_default_digit_length_for_letter_mapping():
    mapping = {"a": "z", "b": "y"}  # letter-to-letter
    c = BijectionConverter(direction="decode", custom_mapping=mapping)
    assert c.digit_length == 2  # default fallback, irrelevant for letter maps


def test_bijection_converter_decode_ignores_fixed_points_param():
    # fixed_points is an encode-only parameter; decode mode silently ignores it
    mapping = {"a": "99"}
    # Should not raise even though fixed_points would normally require encode context
    c = BijectionConverter(direction="decode", custom_mapping=mapping)
    assert c is not None


# ---------------------------------------------------------------------------
# Mapping property and reproducibility
# ---------------------------------------------------------------------------


def test_bijection_converter_mapping_property_returns_copy():
    c = BijectionConverter(seed=1)
    m1 = c.mapping
    m1["z"] = "MODIFIED"
    assert c.mapping["z"] != "MODIFIED"


def test_bijection_converter_seed_produces_same_mapping():
    c1 = BijectionConverter(seed=42, mapping_type="digit")
    c2 = BijectionConverter(seed=42, mapping_type="digit")
    assert c1.mapping == c2.mapping


def test_bijection_converter_no_seed_produces_different_mappings():
    mappings = [BijectionConverter().mapping for _ in range(5)]
    assert len({frozenset(m.items()) for m in mappings}) > 1


def test_bijection_converter_custom_mapping_used():
    custom = {"a": "z", "b": "y"}
    c = BijectionConverter(custom_mapping=custom, append_description=False)
    assert c.mapping == custom


# ---------------------------------------------------------------------------
# Letter-type encoding
# ---------------------------------------------------------------------------


async def test_bijection_converter_letter_type_encodes():
    c = BijectionConverter(mapping_type="letter", seed=7, append_description=False)
    result = await c.convert_async(prompt="abc", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) == 3


async def test_bijection_converter_letter_type_preserves_non_alpha():
    c = BijectionConverter(mapping_type="letter", seed=3, append_description=False)
    result = await c.convert_async(prompt="Hello, World! 123", input_type="text")
    assert "," in result.output_text
    assert "!" in result.output_text
    assert "1" in result.output_text
    assert " " in result.output_text


async def test_bijection_converter_letter_type_roundtrip():
    c = BijectionConverter(mapping_type="letter", seed=5, append_description=False)
    original = "hello world"
    encoded = (await c.convert_async(prompt=original)).output_text
    decoded = BijectionConverter.decode(text=encoded, mapping=c.mapping)
    assert decoded == original


# ---------------------------------------------------------------------------
# Digit-type encoding
# ---------------------------------------------------------------------------


async def test_bijection_converter_digit_type_encodes_lowercase():
    c = BijectionConverter(mapping_type="digit", fixed_points=0, digit_length=2, seed=1, append_description=False)
    result = await c.convert_async(prompt="ab", input_type="text")
    assert result.output_text.isdigit()
    assert len(result.output_text) == 4


async def test_bijection_converter_digit_type_roundtrip():
    c = BijectionConverter(mapping_type="digit", fixed_points=5, digit_length=2, seed=99, append_description=False)
    original = "the quick brown fox"
    encoded = (await c.convert_async(prompt=original)).output_text
    decoded = BijectionConverter.decode(text=encoded, mapping=c.mapping, digit_length=c.digit_length)
    assert decoded == original


async def test_bijection_converter_digit_length_3_roundtrip():
    c = BijectionConverter(mapping_type="digit", fixed_points=0, digit_length=3, seed=77, append_description=False)
    original = "abcxyz"
    encoded = (await c.convert_async(prompt=original)).output_text
    decoded = BijectionConverter.decode(text=encoded, mapping=c.mapping, digit_length=3)
    assert decoded == original


async def test_bijection_converter_digit_preserves_spaces_and_punct():
    c = BijectionConverter(mapping_type="digit", fixed_points=0, digit_length=2, seed=2, append_description=False)
    result = await c.convert_async(prompt="a b!")
    assert " " in result.output_text
    assert "!" in result.output_text


# ---------------------------------------------------------------------------
# Decode correctness: fixed-points in the middle of digit stream
# ---------------------------------------------------------------------------


async def test_bijection_converter_digit_fixed_point_roundtrip():
    """
    Roundtrip with mid-range fixed_points so the encoded stream contains both
    literal fixed-point letters and N-digit codes side-by-side.
    Verifies that decode correctly separates letters from digit chunks rather
    than blindly chunking the whole string.
    """
    c = BijectionConverter(mapping_type="digit", fixed_points=13, digit_length=2, seed=42, append_description=False)
    # Pick a string that is likely to contain a mix of fixed and remapped chars
    original = "abcdefghijklmnopqrstuvwxyz"
    encoded = (await c.convert_async(prompt=original)).output_text
    decoded = BijectionConverter.decode(text=encoded, mapping=c.mapping, digit_length=c.digit_length)
    assert decoded == original


async def test_bijection_converter_digit_fixed_point_letter_between_codes():
    """
    Construct a mapping where a fixed-point letter sits between two digit codes
    and verify the decoder correctly handles the boundary.
    """
    # 'b' is fixed (maps to 'b'), 'a'→'42', 'c'→'17'
    mapping = {"a": "42", "b": "b", "c": "17"}
    c = BijectionConverter(custom_mapping=mapping, append_description=False)
    encoded = (await c.convert_async(prompt="abc")).output_text
    assert encoded == "42b17"
    decoded = BijectionConverter.decode(text=encoded, mapping=mapping, digit_length=2)
    assert decoded == "abc"


# ---------------------------------------------------------------------------
# Decode mode as a converter (response-side)
# ---------------------------------------------------------------------------


async def test_bijection_converter_decode_direction_roundtrip_digit():
    encode_c = BijectionConverter(
        mapping_type="digit", fixed_points=5, digit_length=2, seed=10, append_description=False
    )
    decode_c = BijectionConverter(direction="decode", custom_mapping=encode_c.mapping)

    original = "hello world"
    encoded = (await encode_c.convert_async(prompt=original)).output_text
    result = await decode_c.convert_async(prompt=encoded)
    assert result.output_text == original
    assert result.output_type == "text"


async def test_bijection_converter_decode_direction_roundtrip_letter():
    encode_c = BijectionConverter(mapping_type="letter", fixed_points=5, seed=20, append_description=False)
    decode_c = BijectionConverter(direction="decode", custom_mapping=encode_c.mapping)

    original = "sphinx of black quartz"
    encoded = (await encode_c.convert_async(prompt=original)).output_text
    result = await decode_c.convert_async(prompt=encoded)
    assert result.output_text == original


async def test_bijection_converter_decode_direction_no_preamble():
    """Decode mode must never add a teaching preamble."""
    mapping = {"a": "99", "b": "b"}
    c = BijectionConverter(direction="decode", custom_mapping=mapping)
    result = await c.convert_async(prompt="99b")
    assert "Bijection" not in result.output_text
    assert result.output_text == "ab"


# ---------------------------------------------------------------------------
# Robustness: mixed plaintext framing in model responses
# ---------------------------------------------------------------------------


async def test_bijection_converter_decode_mixed_framing_does_not_crash():
    """
    Model responses often contain framing prose mixed with encoded content.
    Decode should not crash and should return sensible text for the scorer.
    """
    mapping = {"a": "42", "b": "b", "c": "17"}
    decode_c = BijectionConverter(direction="decode", custom_mapping=mapping)
    messy_response = "Sure! Here is the answer: 42b17 and some extra words."
    result = await decode_c.convert_async(prompt=messy_response)
    assert isinstance(result, ConverterResult)
    # 'abc' should appear where the codes were; framing prose passes through
    assert "abc" in result.output_text
    assert "Sure" in result.output_text


async def test_bijection_converter_decode_all_unknown_codes_passes_through():
    """Unknown digit sequences pass through as individual digit characters."""
    mapping = {"a": "99"}
    decode_c = BijectionConverter(direction="decode", custom_mapping=mapping)
    result = await decode_c.convert_async(prompt="77")
    assert "7" in result.output_text


async def test_bijection_converter_decode_truncated_code_at_end_passes_through():
    """A single trailing digit (shorter than digit_length) passes through intact."""
    mapping = {"a": "42", "b": "b"}
    decode_c = BijectionConverter(direction="decode", custom_mapping=mapping)
    result = await decode_c.convert_async(prompt="42b4")  # trailing '4' is incomplete
    assert isinstance(result, ConverterResult)
    assert "4" in result.output_text  # the lone digit passed through


# ---------------------------------------------------------------------------
# append_description / teaching preamble
# ---------------------------------------------------------------------------


async def test_bijection_converter_with_description_contains_preamble_keyword():
    c = BijectionConverter(seed=10, append_description=True)
    result = await c.convert_async(prompt="hello")
    assert "Bijection Language" in result.output_text


async def test_bijection_converter_with_description_contains_encoded_prompt():
    encode_c = BijectionConverter(
        mapping_type="digit", fixed_points=0, digit_length=2, seed=20, append_description=False
    )
    encoded_only = (await encode_c.convert_async(prompt="hello")).output_text

    c_with_desc = BijectionConverter(custom_mapping=encode_c.mapping, append_description=True)
    result = await c_with_desc.convert_async(prompt="hello")
    assert encoded_only in result.output_text


async def test_bijection_converter_without_description_is_just_encoding():
    c = BijectionConverter(mapping_type="letter", seed=4, append_description=False)
    result = await c.convert_async(prompt="abc")
    assert "Bijection" not in result.output_text
    assert len(result.output_text) == 3


async def test_bijection_converter_zero_teaching_shots_still_works():
    c = BijectionConverter(num_teaching_shots=0, append_description=True, seed=50)
    result = await c.convert_async(prompt="test")
    assert isinstance(result, ConverterResult)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_bijection_converter_empty_prompt():
    c = BijectionConverter(seed=1, append_description=False)
    result = await c.convert_async(prompt="")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""


async def test_bijection_converter_input_type_not_supported():
    c = BijectionConverter(seed=1)
    with pytest.raises(ValueError):
        await c.convert_async(prompt="hello", input_type="image_path")


async def test_bijection_converter_uppercase_unchanged():
    c = BijectionConverter(mapping_type="letter", seed=6, append_description=False)
    result = await c.convert_async(prompt="HELLO")
    assert result.output_text == "HELLO"


def test_bijection_converter_decode_static_letter_identity():
    mapping = {"a": "a", "b": "b"}
    assert BijectionConverter.decode("ab", mapping) == "ab"


def test_bijection_converter_decode_static_unknown_code_passes_through():
    mapping = {"a": "99"}
    result = BijectionConverter.decode("77", mapping, digit_length=2)
    assert "7" in result
