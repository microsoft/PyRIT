# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter import CodeAttackConverter, ConverterResult

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


def test_invalid_language_raises():
    with pytest.raises(ValueError, match="Invalid language"):
        CodeAttackConverter(language="ruby")  # type: ignore[arg-type]


def test_valid_languages_construct():
    for lang in ("python_stack", "python_list", "python_string", "cpp", "go"):
        c = CodeAttackConverter(language=lang)  # type: ignore[arg-type]
        assert c is not None


async def test_unsupported_input_type_raises():
    converter = CodeAttackConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


# ---------------------------------------------------------------------------
# Template rendering -- each language produces the expected wrapper
# ---------------------------------------------------------------------------


async def test_python_stack_output_contains_deque_imports():
    converter = CodeAttackConverter(language="python_stack", verbose=False)
    result = await converter.convert_async(prompt="hello world")
    assert isinstance(result, ConverterResult)
    assert "from collections import deque" in result.output_text
    assert "my_stack" in result.output_text


async def test_python_list_output_contains_list_init():
    converter = CodeAttackConverter(language="python_list", verbose=False)
    result = await converter.convert_async(prompt="hello world")
    assert "my_list" in result.output_text
    assert "my_list.append" in result.output_text


async def test_python_string_output_contains_assignment():
    converter = CodeAttackConverter(language="python_string", verbose=False)
    result = await converter.convert_async(prompt="hello world")
    assert 'my_string = "hello world"' in result.output_text


async def test_cpp_output_contains_std_string():
    converter = CodeAttackConverter(language="cpp", verbose=False)
    result = await converter.convert_async(prompt="hello world")
    assert "std::string my_string" in result.output_text
    assert "hello world" in result.output_text


async def test_go_output_contains_myqueue():
    converter = CodeAttackConverter(language="go", verbose=False)
    result = await converter.convert_async(prompt="hello world")
    assert "myQueue" in result.output_text
    assert "hello world" in result.output_text


# ---------------------------------------------------------------------------
# Verbose vs non-verbose
# ---------------------------------------------------------------------------


async def test_verbose_false_uses_step_comments():
    converter = CodeAttackConverter(language="python_stack", verbose=False)
    result = await converter.convert_async(prompt="test prompt")
    assert "step 1" in result.output_text
    assert "paragraph" not in result.output_text


async def test_verbose_true_uses_paragraph_comments():
    converter = CodeAttackConverter(language="python_stack", verbose=True)
    result = await converter.convert_async(prompt="test prompt")
    assert "paragraph" in result.output_text
    assert "Generate 100 words" in result.output_text


async def test_cpp_verbose_flag_has_no_effect():
    c_false = CodeAttackConverter(language="cpp", verbose=False)
    c_true = CodeAttackConverter(language="cpp", verbose=True)
    r_false = await c_false.convert_async(prompt="test")
    r_true = await c_true.convert_async(prompt="test")
    assert r_false.output_text == r_true.output_text


async def test_go_verbose_flag_has_no_effect():
    c_false = CodeAttackConverter(language="go", verbose=False)
    c_true = CodeAttackConverter(language="go", verbose=True)
    r_false = await c_false.convert_async(prompt="test")
    r_true = await c_true.convert_async(prompt="test")
    assert r_false.output_text == r_true.output_text


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
    converter = CodeAttackConverter(language="python_stack", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    words_in_code = _extract_stack_words(result.output_text)
    # Decode: reverse the in-code order (stack was pushed in reverse)
    recovered = " ".join(words_in_code[::-1])
    assert recovered == prompt


async def test_python_list_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(language="python_list", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    words_in_code = _extract_list_words(result.output_text)
    recovered = " ".join(words_in_code)
    assert recovered == prompt


async def test_python_string_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(language="python_string", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'my_string = "([^"]+)"')
    assert recovered == prompt


async def test_cpp_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(language="cpp", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'std::string my_string = "([^"]+)"')
    assert recovered == prompt


async def test_go_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(language="go", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    recovered = _extract_string_value(result.output_text, r'myQueue := "([^"]+)"')
    assert recovered == prompt


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_empty_prompt_python_stack():
    converter = CodeAttackConverter(language="python_stack", verbose=False)
    result = await converter.convert_async(prompt="")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Empty prompt produces empty append sequence; template still renders
    assert "output_list" in result.output_text


async def test_empty_prompt_python_string():
    converter = CodeAttackConverter(language="python_string", verbose=False)
    result = await converter.convert_async(prompt="")
    assert 'my_string = ""' in result.output_text


async def test_special_characters_python_string():
    converter = CodeAttackConverter(language="python_string", verbose=False)
    result = await converter.convert_async(prompt="hello & world <test>")
    assert "hello & world <test>" in result.output_text


async def test_long_prompt_all_words_present_python_list():
    prompt = " ".join([f"word{i}" for i in range(50)])
    converter = CodeAttackConverter(language="python_list", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    words = _extract_list_words(result.output_text)
    assert words == prompt.split()


async def test_single_word_python_stack_does_not_split_chars():
    prompt = "hello"
    converter = CodeAttackConverter(language="python_stack", verbose=False)
    result = await converter.convert_async(prompt=prompt)

    words = _extract_stack_words(result.output_text)
    # Single word with no hyphens: reference code falls back to char-by-char.
    # Reversed chars joined == original word.
    recovered = "".join(words[::-1])
    assert recovered == prompt


async def test_output_type_is_text():
    converter = CodeAttackConverter(language="python_list", verbose=True)
    result = await converter.convert_async(prompt="any prompt")
    assert result.output_type == "text"
