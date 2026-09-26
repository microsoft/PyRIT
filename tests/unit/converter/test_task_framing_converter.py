# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import PromptTemplateConverter, TaskFramingConverter


def _make(**kwargs) -> TaskFramingConverter:
    with pytest.warns(DeprecationWarning, match="PromptTemplateConverter"):
        return TaskFramingConverter(**kwargs)


def test_init_emits_deprecation_warning():
    with pytest.warns(DeprecationWarning, match=r"TaskFramingConverter is deprecated and will be removed in 1\.4\.0"):
        TaskFramingConverter()


def test_is_prompt_template_converter_subclass():
    assert isinstance(_make(), PromptTemplateConverter)


def test_docstring_does_not_opt_into_registry_alias_skip():
    # The registry skips classes whose docstring starts with "Deprecated alias";
    # this class must stay buildable by name until it is removed.
    assert not (TaskFramingConverter.__doc__ or "").strip().startswith("Deprecated alias")


async def test_convert_async_default_template_frames_as_task():
    converter = _make()
    result = await converter.convert_async(prompt="How can I do X?")
    assert result.output_text == "TASK is 'How can I do X?'"
    assert result.output_type == "text"


async def test_convert_async_strip_characters_removes_from_input():
    converter = _make(strip_characters="'")
    result = await converter.convert_async(prompt="don't do 'this'")
    assert result.output_text == "TASK is 'dont do this'"


async def test_convert_async_custom_template():
    converter = _make(task_template="Please solve: {{ prompt }}")
    result = await converter.convert_async(prompt="the objective")
    assert result.output_text == "Please solve: the objective"


def test_init_template_missing_placeholder_raises():
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="must contain a"):
        TaskFramingConverter(task_template="no placeholder here")


def test_identifier_keeps_task_template_param_name():
    identifier = _make(strip_characters="'").get_identifier()
    assert identifier.class_name == "TaskFramingConverter"
    assert identifier.params["task_template"] == "TASK is '{{ prompt }}'"
    assert identifier.params["strip_characters"] == "'"
    assert "template" not in identifier.params
