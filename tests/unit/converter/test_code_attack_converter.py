# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import re

import pytest

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter import CodeAttackConverter, ConverterResult
from pyrit.converter.code_attack_converter import _TEMPLATE_ENCODING

Template = CodeAttackConverter.Template
Encoding = CodeAttackConverter.Encoding

# Matches a whole double-quoted literal including its escape sequences.
_LITERAL = r'"((?:[^"\\]|\\.)*)"'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_literals(converted: str, call: str) -> list[str]:
    """Return the decoded values of every ``call("...")`` in code order.

    The literal is evaluated rather than string-matched, so assertions compare
    the value the target language would actually see, not its escaped form.
    """
    raw = re.findall(rf"{call}\({_LITERAL}\)", converted)
    return [ast.literal_eval(f'"{item}"') for item in raw]


def _decode_assignment(converted: str, prefix: str) -> str:
    """Return the decoded value of a ``prefix "..."`` assignment."""
    match = re.search(rf"{prefix}\s*{_LITERAL}", converted)
    assert match is not None, f"Assignment {prefix!r} not found in output"
    return ast.literal_eval(f'"{match.group(1)}"')


def _write_template(directory, body: str, name: str = "custom.yaml"):
    """Write a template YAML into ``directory`` and return its path."""
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_invalid_template_type_raises():
    with pytest.raises(TypeError, match="CodeAttackConverter.Template"):
        CodeAttackConverter(template="python_stack")  # type: ignore[arg-type]


@pytest.mark.parametrize("template", list(Template), ids=lambda t: t.name)
async def test_every_template_member_renders(template):
    """Every built-in template must actually render the objective, not just construct."""
    converter = CodeAttackConverter(template=template)
    result = await converter.convert_async(prompt="cut down a tree")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text.strip(), f"{template.name} rendered empty output"
    # The template placeholder must have been substituted.
    assert "wrapped_input" not in result.output_text
    # The encoded objective must be present in the rendered code.
    assert re.search(_LITERAL, result.output_text), f"{template.name} produced no string literal"


def test_custom_path_template_constructs(tmp_path):
    fake_yaml = _write_template(tmp_path, "name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")
    converter = CodeAttackConverter(template=fake_yaml, encoding=Encoding.PYTHON_STRING)
    assert converter._encoding is Encoding.PYTHON_STRING


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

    words_in_code = _decode_literals(result.output_text, "my_stack.append")
    # Decode: reverse the in-code order (stack was pushed in reverse)
    recovered = " ".join(words_in_code[::-1])
    assert recovered == prompt


async def test_python_list_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt=prompt)

    words_in_code = _decode_literals(result.output_text, "my_list.append")
    recovered = " ".join(words_in_code)
    assert recovered == prompt


async def test_python_string_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.PYTHON_STRING)
    result = await converter.convert_async(prompt=prompt)

    recovered = _decode_assignment(result.output_text, "my_string =")
    assert recovered == prompt


async def test_cpp_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.CPP)
    result = await converter.convert_async(prompt=prompt)

    recovered = _decode_assignment(result.output_text, "std::string my_string =")
    assert recovered == prompt


async def test_go_roundtrip():
    prompt = "tell me how to pick a lock"
    converter = CodeAttackConverter(template=Template.GO)
    result = await converter.convert_async(prompt=prompt)

    recovered = _decode_assignment(result.output_text, "myQueue :=")
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
    # Bare unescaped inner quotes would produce malformed code: my_string = "say "hi""
    assert 'my_string = "say "hi""' not in result.output_text
    # The literal must decode back to exactly the original text.
    assert _decode_assignment(result.output_text, "my_string =") == 'say "hi"'


async def test_embedded_double_quote_python_list():
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt='say "hi" now')
    assert _decode_literals(result.output_text, "my_list.append") == ["say", '"hi"', "now"]


async def test_embedded_double_quote_python_stack():
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt='say "hi" now')
    assert _decode_literals(result.output_text, "my_stack.append")[::-1] == ["say", '"hi"', "now"]


async def test_embedded_double_quote_cpp():
    converter = CodeAttackConverter(template=Template.CPP)
    result = await converter.convert_async(prompt='say "hi"')
    assert _decode_assignment(result.output_text, "std::string my_string =") == 'say "hi"'


async def test_embedded_double_quote_go():
    converter = CodeAttackConverter(template=Template.GO)
    result = await converter.convert_async(prompt='say "hi"')
    assert _decode_assignment(result.output_text, "myQueue :=") == 'say "hi"'


async def test_long_prompt_all_words_present_python_list():
    prompt = " ".join([f"word{i}" for i in range(50)])
    converter = CodeAttackConverter(template=Template.PYTHON_LIST)
    result = await converter.convert_async(prompt=prompt)

    words = _decode_literals(result.output_text, "my_list.append")
    assert words == prompt.split()


async def test_single_word_python_stack_does_not_split_chars():
    prompt = "hello"
    converter = CodeAttackConverter(template=Template.PYTHON_STACK)
    result = await converter.convert_async(prompt=prompt)

    words = _decode_literals(result.output_text, "my_stack.append")
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
    custom = _write_template(tmp_path, "name: custom\nvalue: 'ENCODED: {{ wrapped_input }}'\ndata_type: text\n")

    converter = CodeAttackConverter(template=custom, encoding=Encoding.PYTHON_STRING)
    result = await converter.convert_async(prompt="hello world")
    assert "ENCODED:" in result.output_text
    assert "hello world" in result.output_text


# ---------------------------------------------------------------------------
# Non-BMP Unicode: the objective must survive as literal UTF-8
#
# json.dumps(ensure_ascii=True) would emit a surrogate pair ("😀").
# Python evaluates that to two lone surrogates and Go and C++ reject surrogate
# escapes outright, so the encoders must emit the character itself.
# ---------------------------------------------------------------------------

_EMOJI = "\U0001f600"
_TREE = "\U0001f332"


def _assert_no_surrogate_escape(converted: str) -> None:
    assert "\\ud83d" not in converted.lower(), "non-BMP character was escaped as a surrogate pair"


async def test_non_bmp_roundtrip_python_string():
    prompt = f"cut down a tree {_EMOJI}"
    result = await CodeAttackConverter(template=Template.PYTHON_STRING).convert_async(prompt=prompt)
    _assert_no_surrogate_escape(result.output_text)
    assert _decode_assignment(result.output_text, "my_string =") == prompt


async def test_non_bmp_roundtrip_cpp():
    prompt = f"cut down a tree {_EMOJI}"
    result = await CodeAttackConverter(template=Template.CPP).convert_async(prompt=prompt)
    _assert_no_surrogate_escape(result.output_text)
    assert _decode_assignment(result.output_text, "std::string my_string =") == prompt


async def test_non_bmp_roundtrip_go():
    prompt = f"cut down a tree {_EMOJI}"
    result = await CodeAttackConverter(template=Template.GO).convert_async(prompt=prompt)
    _assert_no_surrogate_escape(result.output_text)
    assert _decode_assignment(result.output_text, "myQueue :=") == prompt


async def test_non_bmp_roundtrip_python_list():
    prompt = f"burn {_EMOJI} the {_TREE} tree"
    result = await CodeAttackConverter(template=Template.PYTHON_LIST).convert_async(prompt=prompt)
    _assert_no_surrogate_escape(result.output_text)
    assert _decode_literals(result.output_text, "my_list.append") == ["burn", _EMOJI, "the", _TREE, "tree"]


async def test_non_bmp_roundtrip_python_stack():
    prompt = f"burn {_EMOJI} the {_TREE} tree"
    result = await CodeAttackConverter(template=Template.PYTHON_STACK).convert_async(prompt=prompt)
    _assert_no_surrogate_escape(result.output_text)
    decoded = _decode_literals(result.output_text, "my_stack.append")[::-1]
    assert decoded == ["burn", _EMOJI, "the", _TREE, "tree"]


@pytest.mark.parametrize(
    "raw",
    ["tab\tsep", "line\nbreak", "back\\slash", 'quote"inside', "ctrl\x01then1234", "del\x7fchar"],
    ids=["tab", "newline", "backslash", "quote", "control", "delete"],
)
async def test_control_characters_roundtrip_python_string(raw):
    """Control and escape characters must decode back to exactly the input."""
    result = await CodeAttackConverter(template=Template.PYTHON_STRING).convert_async(prompt=raw)
    assert _decode_assignment(result.output_text, "my_string =") == raw


# ---------------------------------------------------------------------------
# Identifier: derived from template contents, not from where the file lives
# ---------------------------------------------------------------------------


def test_identifier_exposes_template_hash_and_encoding():
    identifier = CodeAttackConverter(template=Template.PYTHON_LIST).get_identifier()
    params = identifier.params
    assert params["template"] == "PYTHON_LIST"
    assert params["encoding"] == "python_list"
    assert re.fullmatch(r"[0-9a-f]{16}", params["template_hash"])


def test_identifier_does_not_leak_absolute_path():
    identifier = CodeAttackConverter(template=Template.CPP).get_identifier()
    assert "/" not in str(identifier.params["template"])
    assert str(CONVERTER_SEED_PROMPT_PATH) not in str(identifier.params)


def test_identifier_stable_across_paths_with_identical_content(tmp_path):
    """Same template contents at two different paths must give the same identifier."""
    body = "name: custom\nvalue: 'X {{ wrapped_input }}'\ndata_type: text\n"
    first = _write_template(tmp_path, body, name="one.yaml")
    second = _write_template(tmp_path, body, name="two.yaml")

    left = CodeAttackConverter(template=first, encoding=Encoding.PYTHON_STRING).get_identifier()
    right = CodeAttackConverter(template=second, encoding=Encoding.PYTHON_STRING).get_identifier()

    assert first != second
    assert left.params["template_hash"] == right.params["template_hash"]
    assert left.params == right.params


def test_identifier_sensitive_to_template_content(tmp_path):
    """Changing the template body must change the identifier."""
    original = _write_template(
        tmp_path, "name: custom\nvalue: 'X {{ wrapped_input }}'\ndata_type: text\n", name="one.yaml"
    )
    modified = _write_template(
        tmp_path, "name: custom\nvalue: 'Y {{ wrapped_input }}'\ndata_type: text\n", name="two.yaml"
    )

    left = CodeAttackConverter(template=original, encoding=Encoding.PYTHON_STRING).get_identifier()
    right = CodeAttackConverter(template=modified, encoding=Encoding.PYTHON_STRING).get_identifier()

    assert left.params["template_hash"] != right.params["template_hash"]


def test_identifier_sensitive_to_encoding(tmp_path):
    """Same template, different encoding, must not collide."""
    body = "name: custom\nvalue: 'X {{ wrapped_input }}'\ndata_type: text\n"
    path = _write_template(tmp_path, body)

    left = CodeAttackConverter(template=path, encoding=Encoding.PYTHON_LIST).get_identifier()
    right = CodeAttackConverter(template=path, encoding=Encoding.GO).get_identifier()

    assert left.params["encoding"] != right.params["encoding"]
    assert left.params != right.params


def test_identifier_distinguishes_builtin_templates():
    """Two built-in templates must not share an identifier."""
    left = CodeAttackConverter(template=Template.PYTHON_STACK).get_identifier()
    right = CodeAttackConverter(template=Template.PYTHON_STACK_VERBOSE).get_identifier()
    assert left.params["template"] != right.params["template"]
    assert left.params["template_hash"] != right.params["template_hash"]


# ---------------------------------------------------------------------------
# Custom template validation, all at construction time
# ---------------------------------------------------------------------------


def test_missing_template_file_raises_at_construction(tmp_path):
    with pytest.raises(FileNotFoundError):
        CodeAttackConverter(template=tmp_path / "does_not_exist.yaml", encoding=Encoding.PYTHON_STRING)


def test_malformed_yaml_raises_at_construction(tmp_path):
    broken = _write_template(tmp_path, "name: x\n  bad indent: [\n", name="broken.yaml")
    with pytest.raises(ValueError, match="Invalid YAML"):
        CodeAttackConverter(template=broken, encoding=Encoding.PYTHON_STRING)


def test_template_without_wrapped_input_raises(tmp_path):
    """A template that never references wrapped_input silently drops the objective."""
    static = _write_template(tmp_path, "name: static\nvalue: 'no parameter here'\ndata_type: text\n")
    with pytest.raises(ValueError, match="wrapped_input"):
        CodeAttackConverter(template=static, encoding=Encoding.PYTHON_STRING)


def test_path_without_encoding_raises(tmp_path):
    """A custom path cannot infer its data structure, so encoding is required."""
    custom = _write_template(tmp_path, "name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")
    with pytest.raises(ValueError, match="encoding is required"):
        CodeAttackConverter(template=custom)


def test_invalid_encoding_type_raises(tmp_path):
    custom = _write_template(tmp_path, "name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")
    with pytest.raises(TypeError, match="Encoding"):
        CodeAttackConverter(template=custom, encoding="python_string")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("template", "wrong_encoding"),
    [
        (Template.PYTHON_STACK, Encoding.PYTHON_LIST),
        (Template.PYTHON_STACK_VERBOSE, Encoding.PYTHON_STRING),
        (Template.PYTHON_LIST, Encoding.PYTHON_STACK),
        (Template.PYTHON_STRING, Encoding.GO),
        (Template.CPP, Encoding.PYTHON_LIST),
        (Template.GO, Encoding.CPP),
    ],
    ids=lambda v: v.name,
)
def test_builtin_template_with_mismatched_encoding_raises(template, wrong_encoding):
    """A built-in wrapper paired with another encoding would silently lose the objective.

    Template.PYTHON_STACK + Encoding.PYTHON_LIST declares my_stack, writes the
    objective into my_list, and then decodes from the still-empty my_stack.
    """
    with pytest.raises(ValueError, match="must not be passed with the built-in template"):
        CodeAttackConverter(template=template, encoding=wrong_encoding)


@pytest.mark.parametrize("template", list(Template), ids=lambda t: t.name)
def test_builtin_template_with_its_own_encoding_also_raises(template):
    """The parameter is rejected outright, not validated, so even a matching pair raises."""
    matching = _TEMPLATE_ENCODING[template]
    with pytest.raises(ValueError, match="Built-in templates imply their encoding"):
        CodeAttackConverter(template=template, encoding=matching)


def test_mismatch_error_names_template_mapped_and_passed_encodings():
    with pytest.raises(ValueError) as excinfo:
        CodeAttackConverter(template=Template.PYTHON_STACK, encoding=Encoding.GO)
    message = str(excinfo.value)
    assert "Template.PYTHON_STACK" in message
    assert "Encoding.PYTHON_STACK" in message  # the mapped encoding
    assert "Encoding.GO" in message  # the one passed


@pytest.mark.parametrize("template", list(Template), ids=lambda t: t.name)
def test_builtin_template_without_encoding_still_works(template):
    """Every built-in must still construct with no encoding= and use its mapped encoding."""
    converter = CodeAttackConverter(template=template)
    assert converter._encoding is _TEMPLATE_ENCODING[template]


def test_custom_template_with_extra_variable_raises(tmp_path):
    """An unsupported variable must fail at construction, not on first conversion."""
    extra = _write_template(tmp_path, "name: custom\nvalue: '{{ wrapped_input }} {{ suffix }}'\ndata_type: text\n")
    with pytest.raises(ValueError, match="unsupported template") as excinfo:
        CodeAttackConverter(template=extra, encoding=Encoding.PYTHON_STRING)
    assert "suffix" in str(excinfo.value)


def test_custom_template_names_every_extra_variable(tmp_path):
    extra = _write_template(
        tmp_path,
        "name: custom\nvalue: '{{ wrapped_input }} {{ alpha }} {{ beta }}'\ndata_type: text\n",
    )
    with pytest.raises(ValueError) as excinfo:
        CodeAttackConverter(template=extra, encoding=Encoding.PYTHON_STRING)
    message = str(excinfo.value)
    assert "alpha" in message and "beta" in message


async def test_custom_template_with_only_wrapped_input_constructs_and_renders(tmp_path):
    """The supported single-variable case must keep working."""
    ok = _write_template(tmp_path, "name: custom\nvalue: 'X {{ wrapped_input }} Y'\ndata_type: text\n")

    converter = CodeAttackConverter(template=ok, encoding=Encoding.PYTHON_STRING)
    result = await converter.convert_async(prompt="hello world")

    assert result.output_text.startswith("X ")
    assert result.output_text.rstrip().endswith(" Y")
    assert _decode_assignment(result.output_text, "my_string =") == "hello world"


async def test_custom_path_supports_non_python_string_encodings(tmp_path):
    """Regression: a custom template used to be forced to python_string."""
    custom = _write_template(tmp_path, "name: custom\nvalue: '{{ wrapped_input }}'\ndata_type: text\n")

    result = await CodeAttackConverter(template=custom, encoding=Encoding.GO).convert_async(prompt="a b")
    assert "myQueue :=" in result.output_text

    result = await CodeAttackConverter(template=custom, encoding=Encoding.PYTHON_LIST).convert_async(prompt="a b")
    assert _decode_literals(result.output_text, "my_list.append") == ["a", "b"]


def test_template_loaded_once_at_construction(tmp_path):
    """Deleting the file after construction must not break conversion."""
    custom = _write_template(tmp_path, "name: custom\nvalue: 'X {{ wrapped_input }}'\ndata_type: text\n")
    converter = CodeAttackConverter(template=custom, encoding=Encoding.PYTHON_STRING)
    custom.unlink()
    assert converter._seed_prompt is not None
