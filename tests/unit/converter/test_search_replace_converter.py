# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter import ConverterResult, SearchReplaceConverter


async def test_search_replace_basic():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    result = await converter.convert_async(prompt="hello there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "world there"
    assert result.output_type == "text"


async def test_search_replace_regex():
    converter = SearchReplaceConverter(pattern=r"\d+", replace="NUM")
    result = await converter.convert_async(prompt="abc123def456", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "abcNUMdefNUM"
    assert result.output_type == "text"


async def test_search_replace_list_replacement():
    converter = SearchReplaceConverter(pattern="hello", replace=["X"])
    result = await converter.convert_async(prompt="hello there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "X there"
    assert result.output_type == "text"


async def test_search_replace_no_match():
    converter = SearchReplaceConverter(pattern="xyz", replace="abc")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello"
    assert result.output_type == "text"


async def test_search_replace_empty_string():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_search_replace_input_not_supported():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="hello", input_type="image_path")


@pytest.mark.parametrize(
    ("prompt", "replace", "expected"),
    [
        ("ab", "-", "-a-b-"),
        ("", "-", "-"),
        ("ab", "", "ab"),
        ("ab", ["-"], "-a-b-"),
    ],
)
async def test_search_replace_empty_pattern_async(*, prompt: str, replace: str | list[str], expected: str) -> None:
    converter = SearchReplaceConverter(pattern="", replace=replace)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text == expected
    assert result.output_type == "text"


@pytest.mark.parametrize("invalid_pattern", [None, 123, r"[unclosed"])
def test_search_replace_invalid_pattern(invalid_pattern):
    with pytest.raises(ValueError):
        SearchReplaceConverter(pattern=invalid_pattern, replace="world")


@pytest.mark.parametrize("invalid_replace", [[], None, 123, ["valid", 123], ["valid", None]])
def test_search_replace_invalid_replace(invalid_replace):
    with pytest.raises(ValueError):
        SearchReplaceConverter(pattern="hello", replace=invalid_replace)


@pytest.mark.parametrize("invalid_flags", ["flags", 2.5, True, False, None])
def test_search_replace_invalid_regex_flags(invalid_flags):
    with pytest.raises(ValueError, match="regex_flags must be an integer"):
        SearchReplaceConverter(pattern="hello", replace="world", regex_flags=invalid_flags)


def test_search_replace_build_identifier():
    converter = SearchReplaceConverter(pattern="test", replace=["A", "B"], regex_flags=re.IGNORECASE)
    identifier = converter._build_identifier()
    assert identifier.params["pattern"] == "test"
    assert identifier.params["replace_list"] == ["A", "B"]
    assert identifier.params["regex_flags"] == re.IGNORECASE


async def test_search_replace_regex_flags_execution():
    converter = SearchReplaceConverter(pattern="HELLO", replace="hi", regex_flags=re.IGNORECASE)
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert result.output_text == "hi world"


async def test_search_replace_empty_string_replacement():
    converter = SearchReplaceConverter(pattern="bad_", replace="")
    result = await converter.convert_async(prompt="bad_word bad_code", input_type="text")
    assert result.output_text == "word code"
