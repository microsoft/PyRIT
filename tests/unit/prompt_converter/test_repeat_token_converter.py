# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import RepeatTokenConverter


@pytest.mark.asyncio
async def test_repeat_token_converter_prepend() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="prepend")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == " test test testhow to cut down a tree?"
    assert output.output_type == "text"


@pytest.mark.asyncio
async def test_repeat_token_converter_append() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="append")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test"
    assert output.output_type == "text"


@pytest.mark.asyncio
async def test_repeat_token_converter_split_two_sentence() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree? I need to know.", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test I need to know."
    assert output.output_type == "text"


@pytest.mark.asyncio
async def test_repeat_token_converter_split_one_sentence() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree? test test test"
    assert output.output_type == "text"


@pytest.mark.asyncio
async def test_repeat_token_converter_split_no_punctuation() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="split")
    output = await converter.convert_async(prompt="how to cut down a tree", input_type="text")
    assert output.output_text == " test test testhow to cut down a tree"
    assert output.output_type == "text"


@pytest.mark.asyncio
async def test_repeat_token_converter_repeat() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="repeat")
    output = await converter.convert_async(prompt="how to cut down a tree? I need to know.", input_type="text")
    assert output.output_text == " test test test"
    assert output.output_type == "text"


def test_repeat_token_converter_input_supported() -> None:
    converter = RepeatTokenConverter(token_to_repeat="test", times_to_repeat=3, token_insert_mode="repeat")
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
