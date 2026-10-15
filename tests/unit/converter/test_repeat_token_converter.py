# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import RepeatTokenConverter


async def test_repeat_token_converter_prepend() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="prepend")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == " test test testhow to cut down a tree?"
    assert output.output_type == "text"


async def test_repeat_token_converter_append() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="append")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test"
    assert output.output_type == "text"


async def test_repeat_token_converter_split_two_sentence() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree? I need to know.", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test I need to know."
    assert output.output_type == "text"


async def test_repeat_token_converter_split_one_sentence() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test"
    assert output.output_type == "text"


async def test_repeat_token_converter_split_no_punctuation() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree", input_type="text")
    assert output.output_text == " test test testhow to cut down a tree"
    assert output.output_type == "text"


async def test_repeat_token_converter_repeat() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="repeat")
    output = await converter.convert_async(prompt="how to cut down a tree? I need to know.", input_type="text")
    assert output.output_text == " test test test"
    assert output.output_type == "text"


def test_repeat_token_converter_input_supported() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="repeat")
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


@pytest.mark.parametrize("invalid_token", ["", "   ", None, 123, []])
def test_repeat_token_converter_invalid_token(invalid_token: str) -> None:
    with pytest.raises(ValueError, match="token_to_repeat must be a non-empty string"):
        RepeatTokenConverter(token_to_repeat=invalid_token, times_to_repeat=3)


@pytest.mark.parametrize("invalid_times", [0, -1, -100, 2.5, False, True, "3", None])
def test_repeat_token_converter_invalid_times(invalid_times: int) -> None:
    with pytest.raises(ValueError, match="times_to_repeat must be an integer greater than or equal to 1"):
        RepeatTokenConverter(token_to_repeat="test", times_to_repeat=invalid_times)


@pytest.mark.parametrize("invalid_mode", ["invalid", "middle", "SPLIT", "custom", 123, [], {}, ["split"]])
def test_repeat_token_converter_invalid_mode(invalid_mode: object) -> None:
    with pytest.raises(ValueError, match="Invalid token_insert_mode"):
        RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode=invalid_mode)  # type: ignore[arg-type]


async def test_repeat_token_converter_default_mode_is_split() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=2, token_insert_mode=None)
    output = await converter.convert_async(prompt="hello. world", input_type="text")
    assert output.output_text == "hello. test test world"


def test_repeat_token_converter_build_identifier() -> None:
    converter = RepeatTokenConverter(token_to_repeat="  word  ", times_to_repeat=5, token_insert_mode="prepend")
    identifier = converter._build_identifier()
    assert identifier.params["token_to_repeat"] == "word"
    assert identifier.params["times_to_repeat"] == 5
    assert identifier.params["token_insert_mode"] == "prepend"
