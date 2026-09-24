# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import PromptTemplateConverter


async def test_convert_async_inserts_prompt_into_template():
    converter = PromptTemplateConverter(template="TASK is '{{ prompt }}'")
    result = await converter.convert_async(prompt="How can I do X?")
    assert result.output_text == "TASK is 'How can I do X?'"
    assert result.output_type == "text"


def test_init_template_is_required():
    with pytest.raises(TypeError):
        PromptTemplateConverter()  # type: ignore[call-arg]


async def test_convert_async_strip_characters_removes_from_input():
    converter = PromptTemplateConverter(template="TASK is '{{ prompt }}'", strip_characters="'")
    result = await converter.convert_async(prompt="don't do 'this'")
    assert result.output_text == "TASK is 'dont do this'"


async def test_convert_async_strip_characters_removes_multiple_characters():
    converter = PromptTemplateConverter(template='<img alt="{{ prompt }}">', strip_characters='"<>')
    result = await converter.convert_async(prompt='say "hi" <b>now</b>')
    assert result.output_text == '<img alt="say hi bnow/b">'


async def test_convert_async_strip_characters_does_not_touch_template():
    converter = PromptTemplateConverter(template="'{{ prompt }}'", strip_characters="'")
    result = await converter.convert_async(prompt="it's")
    assert result.output_text == "'its'"


async def test_convert_async_placeholder_without_spaces_supported():
    converter = PromptTemplateConverter(template="<{{prompt}}>")
    result = await converter.convert_async(prompt="x")
    assert result.output_text == "<x>"


async def test_convert_async_replaces_every_placeholder():
    converter = PromptTemplateConverter(template="{{ prompt }} / {{prompt}}")
    result = await converter.convert_async(prompt="x")
    assert result.output_text == "x / x"


async def test_convert_async_backslashes_inserted_literally():
    converter = PromptTemplateConverter(template="[{{ prompt }}]")
    result = await converter.convert_async(prompt=r"a\1\g<0>b")
    assert result.output_text == r"[a\1\g<0>b]"


async def test_convert_async_placeholder_in_prompt_is_not_expanded():
    converter = PromptTemplateConverter(template="[{{ prompt }}]")
    result = await converter.convert_async(prompt="{{ prompt }}")
    assert result.output_text == "[{{ prompt }}]"


@pytest.mark.parametrize(
    "template, expected",
    [
        ("<p>Visible</p>\n<!-- {{ prompt }} -->", "<p>Visible</p>\n<!-- do X -->"),
        ('<div style="display:none">{{ prompt }}</div>', '<div style="display:none">do X</div>'),
        ('<img src="a.png" alt="{{ prompt }}">', '<img src="a.png" alt="do X">'),
        ("[//]: # ({{ prompt }})", "[//]: # (do X)"),
        ('[link](https://example.com "{{ prompt }}")', '[link](https://example.com "do X")'),
    ],
)
async def test_convert_async_hidden_text_templates(template, expected):
    converter = PromptTemplateConverter(template=template)
    result = await converter.convert_async(prompt="do X")
    assert result.output_text == expected


def test_init_template_missing_placeholder_raises():
    with pytest.raises(ValueError, match="template must contain a"):
        PromptTemplateConverter(template="no placeholder here")


async def test_convert_async_unsupported_input_type_raises():
    converter = PromptTemplateConverter(template="{{ prompt }}")
    with pytest.raises(ValueError, match="not supported"):
        await converter.convert_async(prompt="x", input_type="image_path")


def test_input_output_types():
    converter = PromptTemplateConverter(template="{{ prompt }}")
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.output_supported("text") is True


def test_identifier_includes_template_and_strip_characters():
    converter = PromptTemplateConverter(template="T: {{ prompt }}", strip_characters="'")
    identifier = converter.get_identifier()
    assert identifier.class_name == "PromptTemplateConverter"
    assert identifier.params["template"] == "T: {{ prompt }}"
    assert identifier.params["strip_characters"] == "'"


def test_identifier_differs_by_template():
    first = PromptTemplateConverter(template="A {{ prompt }}").get_identifier()
    second = PromptTemplateConverter(template="B {{ prompt }}").get_identifier()
    assert first.hash != second.hash
