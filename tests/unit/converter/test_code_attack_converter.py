# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter import CodeAttackConverter, ConverterResult

Template = CodeAttackConverter.Template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_stack_words(converted: str) -> list[str]:
    """Parse my_stack.append("word") calls and return the words in code order."""
    return re.findall(r'my_stack\.append\("([^"]+)"\)', converted)


def _extract_list_words(converted: str) -> list[str]:
    """Parse my_list.append("word") calls and return the words in code order."""
    return re.findall(r'my_list\.append\("([^"]+)"\)', converted)


def _extract_string_value(converted: str, pattern: str) -> str:
    """Extract the string literal from a variable-assignment line."""
    m = re.search(pattern, converted)
    assert m is not None, f"Pattern {pattern!r} not found in output"
    return m.group(1)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_invalid_template_type_raises():
    with pytest.raises(TypeError, match="CodeAttackConverter.Template"):
        CodeAttackConverter(template="python_stack")  # type: ignore[arg-type]


def test_all_template_members_construct():
    for tmpl in Template:
        c = CodeAttackConverter(template=tmpl)
        assert c is not None


def test_custom_path_template_constructs(tmp_path):
    fake_yaml = tmp_path / "custom.yaml"
    fake_yaml.write_text("name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")
    c = CodeAttackConverter(template=fake_yaml)
    assert c._language == "python_string"


async def test_unsupported_input_type_raises():
    converter = CodeAttackConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


# ---------------------------------------------------------------------------
# Template rendering -- each language produces the expected wrapper
# ---------------------------------------------------------------------------


async def test_python_stack_output_contains_deque_imports():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt="hello world")
    assert isinstance(result, ConverterResult)
    assert "from collections import deque" in result.output_text
    assert "my_stack" in result.output_text


async def test_python_list_output_contains_list_init():
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt="hello world")
    assert "my_list" in result.output_text
    assert "my_list.append" in result.output_text


async def test_python_string_output_contains_assignment():
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt="hello world")
    assert 'my_string = "hello world"' in result.output_text


async def test_cpp_output_contains_std_string():
    converter = CodeAttackConverter(template=Template.CPP)
    result = await converter.convert_async(prompt="hello world")
    assert "std::string my_string" in result.output_text
    assert "hello world" in result.output_text


async def test_go_output_contains_myqueue():
    converter = CodeAttackConverter(template=Template.GO)
    result = await converter.convert_async(prompt="hello world")
    assert "myQueue" in result.output_text
    assert "hello world" in result.output_text


# ---------------------------------------------------------------------------
# Verbose vs non-verbose
# ---------------------------------------------------------------------------


async def test_non_verbose_uses_step_comments():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt="test prompt")
    assert "step 1" in result.output_text
    assert "paragraph" not in result.output_text


async def test_verbose_uses_paragraph_comments():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK_VERBOSE)
    result = await converter.convert_async(prompt="test prompt")
    assert "paragraph" in result.output_text
    assert "Generate 100 words" in result.output_text


async def test_cpp_has_no_verbose_variant():
    # CPP and GO enum members both resolve to the same template.
    r_cpp = await CodeAttackConverter(template=Template.CPP).convert_async(prompt="test")
    # Only CPP template exists; there is no CPP_VERBOSE member.
    assert "std::string" in r_cpp.output_text


async def test_go_template_renders():
    r_go = await CodeAttackConverter(template=Template.GO).convert_async(prompt="test")
    assert "myQueue" in r_go.output_text


# ---------------------------------------------------------------------------
# Lossless round-trip: encode then decode the data structure
#
# These tests assert word-recovery, not byte-identity. python_stack and
# python_list split on [\s\-]+, so hyphens and repeated whitespace are
# normalised away. Use single-space, no-hyphen inputs to keep the round-trip
# exact. See the CodeAttackConverter class docstring for details.
# ---------------------------------------------------------------------------


async def test_python_stack_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt=prompt)

    words_in_code = _extract_stack_words(result.output_text)
    # Decode: reverse the in-code order (stack was pushed in reverse)
    recovered = " ".join(words_in_code[::-1])
    assert recovered == prompt


async def test_python_list_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt=prompt)

    words_in_code = _extract_list_words(result.output_text)
    recovered = " ".join(words_in_code)
    assert recovered == prompt


async def test_python_string_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'my_string = "([^"]+)"')
    assert recovered == prompt


async def test_cpp_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.CPP)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'std::string my_string = "([^"]+)"')
    assert recovered == prompt


async def test_go_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.GO)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'myQueue := "([^"]+)"')
    assert recovered == prompt


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_empty_prompt_python_stack():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt="")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Empty prompt produces empty append sequence; template still renders
    assert "output_list" in result.output_text


async def test_empty_prompt_python_string():
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt="")
    assert 'my_string = ""' in result.output_text


async def test_special_characters_python_string():
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt="hello & world <test>")
    assert "hello & world <test>" in result.output_text


async def test_embedded_double_quote_python_string():
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt='say "hi"')
    # Bare unescaped inner quotes produce malformed code: my_string = "say "hi""
    assert 'my_string = "say "hi""' not in result.output_text
    # json.dumps escapes: my_string = "say \"hi\""
    assert '\\"hi\\"' in result.output_text


async def test_embedded_double_quote_python_list():
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt='say "hi" now')
    assert '\\"hi\\"' in result.output_text


async def test_embedded_double_quote_python_stack():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt='say "hi" now')
    assert '\\"hi\\"' in result.output_text


async def test_embedded_double_quote_cpp():
    converter = CodeAttackConverter(template=Template.CPP)
    result = await converter.convert_async(prompt='say "hi"')
    assert '\\"hi\\"' in result.output_text


async def test_embedded_double_quote_go():
    converter = CodeAttackConverter(template=Template.GO)
    result = await converter.convert_async(prompt='say "hi"')
    assert '\\"hi\\"' in result.output_text


async def test_long_prompt_all_words_present_python_list():
    prompt = " ".join([f"word{i}" for i in range(50)])
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt=prompt)

    words = _extract_list_words(result.output_text)
    assert words == prompt.split()


async def test_single_word_python_stack_does_not_split_chars():
    prompt = "hello"
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt=prompt)

    words = _extract_stack_words(result.output_text)
    # Single word with no hyphens: reference code falls back to char-by-char.
    # Reversed chars joined == original word.
    recovered = "".join(words[::-1])
    assert recovered == prompt


async def test_output_type_is_text():
    converter = CodeAttackConverter(template=Template.PYTHON_LIST_VERBOSE)
    result = await converter.convert_async(prompt="any prompt")
    assert result.output_type == "text"


async def test_default_template_is_python_stack_verbose():
    converter = CodeAttackConverter()
    result = await converter.convert_async(prompt="test")
    # PYTHON_STACK_VERBOSE -> stack structure + verbose paragraph comments
    assert "my_stack" in result.output_text
    assert "paragraph" in result.output_text


async def test_custom_path_template_renders(tmp_path):
    yaml_content = "name: custom\nvalue: 'ENCODED: {{ wrapped_input }}'\ndata_type: text\n"
    custom = tmp_path / "custom.yaml"
    custom.write_text(yaml_content)

    converter = CodeAttackConverter(template=custom)
    result = await converter.convert_async(prompt="hello world")
    assert "ENCODED:" in result.output_text
    assert "hello world" in result.output_text
