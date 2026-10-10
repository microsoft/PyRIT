# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import binascii

import pytest

from pyrit.converter import (
    BinAsciiConverter,
    WordIndexSelectionStrategy,
    WordProportionSelectionStrategy,
)


class TestBinAsciiConverterHex:
    """Tests for hex encoding functionality."""

    async def test_initialization_default(self) -> None:
        """Test default initialization uses hex encoding."""
        converter = BinAsciiConverter()
        assert converter._encoding_func == "hex"

    async def test_hex_all_mode_simple(self) -> None:
        """Test hex encoding with all mode (default)."""
        converter = BinAsciiConverter(encoding_func="hex")

        result = await converter.convert_async(prompt="hello world")
        # "hello" = 68656C6C6F, space separator "20", "world" = 776F726C64
        expected = "68656C6C6F20776F726C64"

        assert result.output_text == expected

    async def test_hex_with_indices(self) -> None:
        """Test hex encoding with specific indices."""

        converter = BinAsciiConverter(
            encoding_func="hex", word_selection_strategy=WordIndexSelectionStrategy(indices=[0])
        )

        result = await converter.convert_async(prompt="hello world")
        expected = "68656C6C6F world"

        assert result.output_text == expected

    async def test_hex_special_characters(self) -> None:
        """Test hex encoding with special characters."""
        converter = BinAsciiConverter(encoding_func="hex")

        result = await converter.convert_async(prompt="test@123")
        # "test@123" in hex
        expected = "7465737440313233"

        assert result.output_text == expected

    async def test_hex_unicode_characters(self) -> None:
        """Test hex encoding with unicode characters."""
        converter = BinAsciiConverter(encoding_func="hex")

        result = await converter.convert_async(prompt="café")
        # "café" in UTF-8 hex: c=63, a=61, f=66, é=C3A9
        expected = "636166C3A9"

        assert result.output_text == expected

    async def test_hex_empty_string(self) -> None:
        """Test hex encoding with empty string."""
        converter = BinAsciiConverter(encoding_func="hex")

        result = await converter.convert_async(prompt="")

        assert result.output_text == ""

    async def test_hex_with_proportion(self) -> None:
        """Test hex encoding with proportion selection."""

        converter = BinAsciiConverter(
            encoding_func="hex", word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)
        )

        result = await converter.convert_async(prompt="one two three four")

        # At least one word should be converted, at least one should not be
        assert result.output_text != "one two three four"
        assert result.output_text is not None


class TestBinAsciiConverterQuotedPrintable:
    """Tests for quoted-printable encoding functionality."""

    async def test_qp_all_mode(self) -> None:
        """Test quoted-printable encoding with all mode."""
        converter = BinAsciiConverter(encoding_func="quoted-printable")

        result = await converter.convert_async(prompt="hello world")
        hello_qp = binascii.b2a_qp(b"hello").decode("ascii")
        world_qp = binascii.b2a_qp(b"world").decode("ascii")
        expected = f"{hello_qp}=20{world_qp}"

        assert result.output_text == expected

    async def test_qp_with_indices(self) -> None:
        """Test quoted-printable encoding with specific indices."""
        converter = BinAsciiConverter(
            encoding_func="quoted-printable", word_selection_strategy=WordIndexSelectionStrategy(indices=[0])
        )

        result = await converter.convert_async(prompt="hello world")
        hello_qp = binascii.b2a_qp(b"hello").decode("ascii")
        expected = f"{hello_qp} world"

        assert result.output_text == expected

    async def test_qp_special_characters(self) -> None:
        """Test quoted-printable with characters that need encoding."""
        converter = BinAsciiConverter(encoding_func="quoted-printable")

        result = await converter.convert_async(prompt="test=value")

        assert result.output_text is not None
        assert "=" in result.output_text


class TestBinAsciiConverterUUencode:
    """Tests for UUencode functionality."""

    async def test_uuencode_all_mode(self) -> None:
        """Test UUencode encoding with all mode."""
        converter = BinAsciiConverter(encoding_func="UUencode")

        result = await converter.convert_async(prompt="hello world")

        assert result.output_text is not None
        assert len(result.output_text) > 0

    async def test_uuencode_with_indices(self) -> None:
        """Test UUencode encoding with specific indices."""
        converter = BinAsciiConverter(
            encoding_func="UUencode", word_selection_strategy=WordIndexSelectionStrategy(indices=[0])
        )

        result = await converter.convert_async(prompt="hello world")

        assert " world" in result.output_text or "world" in result.output_text

    async def test_uuencode_long_text_chunking(self) -> None:
        """Test UUencode with text longer than 45 bytes to trigger chunking."""
        converter = BinAsciiConverter(encoding_func="UUencode")

        long_text = "a" * 100
        result = await converter.convert_async(prompt=long_text)

        assert result.output_text is not None
        assert len(result.output_text) > 0


class TestBinAsciiConverterErrorHandling:
    """Tests for error handling."""

    async def test_invalid_encoding_function(self) -> None:
        """Test that invalid encoding function raises ValueError."""
        converter = BinAsciiConverter(encoding_func="hex")
        converter._encoding_func = "invalid"  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Unsupported encoding function"):
            await converter.convert_async(prompt="test")

    async def test_invalid_encoding_function_at_init(self) -> None:
        """Test that invalid encoding function at initialization raises ValueError."""
        with pytest.raises(ValueError, match="Invalid encoding_func"):
            BinAsciiConverter(encoding_func="invalid")  # type: ignore[arg-type]


class TestBinAsciiConverterSeparator:
    """An all-words join restores the separator the words were split on."""

    @pytest.mark.parametrize("separator", [",", "|", "::", "é"])
    async def test_hex_custom_separator_round_trips(self, separator: str) -> None:
        converter = BinAsciiConverter(encoding_func="hex", word_split_separator=separator)
        prompt = separator.join(["alpha", "beta", "gamma"])

        result = await converter.convert_async(prompt=prompt, input_type="text")

        assert binascii.unhexlify(result.output_text).decode("utf-8") == prompt

    @pytest.mark.parametrize("separator", [",", "|", "::", "==", "é"])
    async def test_quoted_printable_custom_separator_round_trips(self, separator: str) -> None:
        converter = BinAsciiConverter(encoding_func="quoted-printable", word_split_separator=separator)
        prompt = separator.join(["alpha", "beta", "gamma"])

        result = await converter.convert_async(prompt=prompt, input_type="text")

        assert binascii.a2b_qp(result.output_text).decode("utf-8") == prompt

    async def test_hex_custom_separator_is_not_replaced_by_space(self) -> None:
        """A comma and a space used to encode identically, so the comma was unrecoverable."""
        comma = BinAsciiConverter(encoding_func="hex", word_split_separator=",")
        space = BinAsciiConverter(encoding_func="hex")

        comma_result = await comma.convert_async(prompt="alpha,beta", input_type="text")
        space_result = await space.convert_async(prompt="alpha beta", input_type="text")

        assert comma_result.output_text != space_result.output_text

    @pytest.mark.parametrize(
        ("encoding_func", "expected"),
        [("hex", "616C7068612062657461"), ("quoted-printable", "alpha=20beta")],
    )
    async def test_default_separator_output_unchanged(self, encoding_func: str, expected: str) -> None:
        converter = BinAsciiConverter(encoding_func=encoding_func)

        result = await converter.convert_async(prompt="alpha beta", input_type="text")

        assert result.output_text == expected

    @pytest.mark.parametrize("encoding_func", ["hex", "quoted-printable"])
    async def test_none_separator_joins_with_encoded_space(self, encoding_func: str) -> None:
        with_none = BinAsciiConverter(encoding_func=encoding_func, word_split_separator=None)
        with_space = BinAsciiConverter(encoding_func=encoding_func)

        none_result = await with_none.convert_async(prompt="alpha beta", input_type="text")
        space_result = await with_space.convert_async(prompt="alpha beta", input_type="text")

        assert none_result.output_text == space_result.output_text
