# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import TaskFramingConverter


async def test_convert_async_default_template_frames_as_task():
    converter = TaskFramingConverter()
    result = await converter.convert_async(prompt="How can I do X?")
    assert result.output_text == "TASK is 'How can I do X?'"
    assert result.output_type == "text"


async def test_convert_async_strip_characters_removes_from_input():
    converter = TaskFramingConverter(strip_characters="'")
    result = await converter.convert_async(prompt="don't do 'this'")
    assert result.output_text == "TASK is 'dont do this'"


async def test_convert_async_custom_template():
    converter = TaskFramingConverter(task_template="Please solve: {{ prompt }}")
    result = await converter.convert_async(prompt="the objective")
    assert result.output_text == "Please solve: the objective"


async def test_convert_async_placeholder_without_spaces_supported():
    converter = TaskFramingConverter(task_template="<{{prompt}}>")
    result = await converter.convert_async(prompt="x")
    assert result.output_text == "<x>"


async def test_convert_async_backslashes_inserted_literally():
    converter = TaskFramingConverter(task_template="[{{ prompt }}]")
    result = await converter.convert_async(prompt=r"a\1\g<0>b")
    assert result.output_text == r"[a\1\g<0>b]"


def test_init_template_missing_placeholder_raises():
    with pytest.raises(ValueError, match="must contain a"):
        TaskFramingConverter(task_template="no placeholder here")


async def test_convert_async_unsupported_input_type_raises():
    converter = TaskFramingConverter()
    with pytest.raises(ValueError, match="not supported"):
        await converter.convert_async(prompt="x", input_type="image_path")


def test_input_output_types():
    converter = TaskFramingConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.output_supported("text") is True
