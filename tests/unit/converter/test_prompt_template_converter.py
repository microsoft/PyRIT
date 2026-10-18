# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from html.parser import HTMLParser

import pytest

from pyrit.converter import PromptTemplateConverter, SearchReplaceConverter


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


# Markdown hidden-text recipes from the converters doc: put the prompt on one line,
# backslash-escape ``\`` and the delimiter, then insert it with PromptTemplateConverter.
_MARKDOWN_RECIPES = {
    # name: (template, characters to backslash-escape, text a reader should see)
    "comment": ("Welcome to the docs.\n\n[//]: # ({{ prompt }})", r"([\\()])", "Welcome to the docs."),
    "link_title": ('See [our FAQ](https://example.com/faq "{{ prompt }}").', r'([\\"])', "See our FAQ."),
}

_TRICKY_MARKDOWN_PROMPTS = [
    "do X",
    "line one\n\nline two",
    "a\r\n\r\nb",
    "a\r\rb",
    "hello\\",
    "tail \\\\",
    "a)b",
    '\\"',
    'say "hi"',
    "(nested (parens))",
    "end)\n\nvisible",
    "x\n    indented code",
    "- item\n# heading",
    "<b>bold</b>",
    "[x](y)",
]


async def _apply_markdown_recipe(*, template: str, escape_pattern: str, prompt: str) -> str:
    converters = [
        SearchReplaceConverter(pattern=r"\s*[\r\n]\s*", replace=" "),
        SearchReplaceConverter(pattern=escape_pattern, replace=r"\\\1"),
        PromptTemplateConverter(template=template),
    ]
    text = prompt
    for converter in converters:
        text = (await converter.convert_async(prompt=text)).output_text
    return text


class _VisibleTextParser(HTMLParser):
    """Collects the text a reader sees; tag attributes (e.g. ``title``) are not visible."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _render_visible_text(*, markdown: str, renderer: str) -> str:
    if renderer == "markdown-it-py":
        markdown_it = pytest.importorskip("markdown_it")
        rendered = markdown_it.MarkdownIt("commonmark").render(markdown)
    else:
        mistune = pytest.importorskip("mistune")
        rendered = mistune.create_markdown()(markdown)
    parser = _VisibleTextParser()
    parser.feed(rendered)
    return " ".join("".join(parser.parts).split())


@pytest.mark.parametrize("renderer", ["markdown-it-py", "mistune"])
@pytest.mark.parametrize("recipe", sorted(_MARKDOWN_RECIPES))
@pytest.mark.parametrize("prompt", _TRICKY_MARKDOWN_PROMPTS)
async def test_markdown_hidden_text_recipe_stays_hidden_when_rendered(renderer, recipe, prompt):
    template, escape_pattern, expected_visible = _MARKDOWN_RECIPES[recipe]
    markdown = await _apply_markdown_recipe(template=template, escape_pattern=escape_pattern, prompt=prompt)
    assert _render_visible_text(markdown=markdown, renderer=renderer) == expected_visible


async def test_markdown_link_title_recipe_keeps_prompt_in_title():
    markdown_it = pytest.importorskip("markdown_it")
    template, escape_pattern, _ = _MARKDOWN_RECIPES["link_title"]
    markdown = await _apply_markdown_recipe(
        template=template, escape_pattern=escape_pattern, prompt='say "hi"\n\nthen (leave) \\'
    )
    link = next(
        token
        for token in markdown_it.MarkdownIt("commonmark").parseInline(markdown)[0].children
        if token.type == "link_open"
    )
    assert link.attrs["title"] == 'say "hi" then (leave) \\'
