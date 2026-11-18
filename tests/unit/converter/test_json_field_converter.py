# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import (
    Base64Converter,
    Converter,
    ConverterResult,
    JsonFieldConverter,
    RandomCapitalLettersConverter,
    ROT13Converter,
    SelectiveTextConverter,
    TokenSelectionStrategy,
)
from pyrit.memory import MemoryInterface
from pyrit.models import Message, PromptDataType
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer


class _UpperConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text=prompt.upper(), output_type="text")


class _ImageConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text", "image_path")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text="image.png", output_type="image_path")


_REQUEST = '{"state": "hello", "questions": {"q": {"type": "noul", "instructions": "Is it a greeting?"}}}'


async def _convert(prompt: str, json_pointer: str, sub_converter: Converter | None = None) -> str:
    converter = JsonFieldConverter(sub_converter=sub_converter or _UpperConverter(), json_pointer=json_pointer)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_type == "text"
    return result.output_text


async def test_converts_only_the_selected_field():
    result = await _convert(_REQUEST, "/state", Base64Converter())

    assert result == _REQUEST.replace('"hello"', '"aGVsbG8="')


async def test_keeps_the_rest_of_the_document_byte_for_byte():
    # Re-serializing would turn 1e400 into Infinity, 1.10 into 1.1, drop the duplicate key, and
    # reflow the whitespace. The splice keeps all of them.
    prompt = (
        '{\n  "n": [1.10, 1E5, -0.0, 1e400, 123456789012345678901234567890, NaN],\n'
        '  "dup": 1, "dup": 2,\n  "state" :  "hello",\n  "tail": "\\u00e9"\n}'
    )

    result = await _convert(prompt, "/state")

    assert result == prompt.replace('"hello"', '"HELLO"')


@pytest.mark.parametrize(
    ("prompt", "json_pointer", "expected"),
    [
        ('{"a": {"b": {"c": "x"}}}', "/a/b/c", '{"a": {"b": {"c": "X"}}}'),
        ('{"m": ["x", "y", "z"]}', "/m/2", '{"m": ["x", "y", "Z"]}'),
        ('[{"content": "x"}, {"content": "y"}]', "/1/content", '[{"content": "x"}, {"content": "Y"}]'),
        ('{"a/b": "x", "a": {"b": "y"}}', "/a~1b", '{"a/b": "X", "a": {"b": "y"}}'),
        ('{"~1": "x", "/": "y"}', "/~01", '{"~1": "X", "/": "y"}'),
        ('{"": "x", "a": "y"}', "/", '{"": "X", "a": "y"}'),
        ('{"\\u0073tate": "x"}', "/state", '{"\\u0073tate": "X"}'),
        (' "x" ', "", ' "X" '),
        ('{"a": "x", "b": {"a": "y"}}', "/b/a", '{"a": "x", "b": {"a": "Y"}}'),
    ],
)
async def test_resolves_json_pointers(prompt: str, json_pointer: str, expected: str):
    assert await _convert(prompt, json_pointer) == expected


@pytest.mark.parametrize(
    ("field", "converted"),
    [
        ('"say \\"hi\\""', 'SAY "HI"'),
        ('"a\\\\b\\nc"', "A\\B\nC"),
        ('"\\u00e9t\\u00e9 \\ud83d\\ude00"', "ÉTÉ 😀"),
        ('""', ""),
    ],
)
async def test_decodes_the_field_and_writes_valid_json(field: str, converted: str):
    result = await _convert(f'{{"state": {field}, "k": 1}}', "/state")

    assert json.loads(result) == {"state": converted, "k": 1}


async def test_keeps_non_ascii_output_readable():
    result = await _convert('{"state": "e"}', "/state", ROT13Converter())

    assert result == '{"state": "r"}'
    homoglyphs = await _convert('{"state": "x"}', "/state", _HomoglyphConverter())
    assert homoglyphs == '{"state": "Ⓗ"}'


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ('"\\ud800"', '"\\ud800"'),
        ('"a\\udc00b"', '"A\\udc00B"'),
        ('"\\\\\\ud800"', '"\\\\\\ud800"'),
        ('"\\ud83d\\ude00"', '"😀"'),
    ],
)
async def test_writes_lone_surrogates_as_escapes(field: str, expected: str):
    result = await _convert(f'{{"state": {field}}}', "/state")

    assert result == f'{{"state": {expected}}}'
    result.encode("utf-8")


class _HomoglyphConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text="Ⓗ", output_type="text")


@pytest.mark.parametrize(
    ("prompt", "json_pointer", "message"),
    [
        ("", "/state", "requires a JSON document"),
        ("not json", "/state", "requires a JSON document"),
        ('{"state": "x"', "/state", "requires a JSON document"),
        ('{"state": "x"} trailing', "/state", "requires a JSON document"),
        ("[" * 100_000 + "]" * 100_000, "/0", "requires a JSON document"),
        ('{"other": "x"}', "/state", "The object at '' has no key 'state'"),
        ("{}", "/state", "has no key 'state'"),
        ('{"a": {}}', "/a/b", "The object at '/a' has no key 'b'"),
        ('{"state": "x", "state": "y"}', "/state", "has the key 'state' more than once"),
        ('{"a": 1}', "/a/b", "the value at '/a' is not an object or array"),
        ('{"a": "x"}', "/a/0", "the value at '/a' is not an object or array"),
        ('{"m": ["x"]}', "/m/1", "has 1 elements, so index 1 is out of range"),
        ('{"m": []}', "/m/0", "has 0 elements, so index 0 is out of range"),
        ('{"m": ["x"]}', "/m/-", "'-' is not an array index"),
        ('{"m": ["x", "y"]}', "/m/01", "'01' is not an array index"),
        ('{"m": ["x"]}', "/m/+0", "'+0' is not an array index"),
        ('{"m": ["x"]}', "/m/", "'' is not an array index"),
        ('{"m": ["x"]}', "/m/\u0660", "is not an array index"),
        ('{"state": {"text": "x"}}', "/state", "selects an object, not a string"),
        ('{"state": ["x"]}', "/state", "selects an array, not a string"),
        ('{"state": 1}', "/state", "selects a number, not a string"),
        ('{"state": true}', "/state", "selects a boolean, not a string"),
        ('{"state": null}', "/state", "selects null, not a string"),
        ('{"state": "x"}', "", "selects an object, not a string"),
    ],
)
async def test_rejects_documents_the_pointer_cannot_convert(prompt: str, json_pointer: str, message: str):
    sub_converter = AsyncMock(spec=_UpperConverter())
    converter = JsonFieldConverter(sub_converter=sub_converter, json_pointer=json_pointer)

    with pytest.raises(ValueError, match=re.escape(message)):
        await converter.convert_async(prompt=prompt)
    sub_converter.convert_async.assert_not_called()


async def test_reports_the_escaped_path_of_a_missing_key():
    converter = JsonFieldConverter(sub_converter=_UpperConverter(), json_pointer="/a~1b/c~0d/e")

    with pytest.raises(ValueError, match=r"The object at '/a~1b/c~0d' has no key 'e'"):
        await converter.convert_async(prompt='{"a/b": {"c~d": {}}}')


@pytest.mark.parametrize("json_pointer", ["state", "#/state", "/~2", "/a~", "/~/b"])
def test_init_rejects_invalid_json_pointers(json_pointer: str):
    with pytest.raises(ValueError, match="json_pointer"):
        JsonFieldConverter(sub_converter=_UpperConverter(), json_pointer=json_pointer)


def test_init_rejects_sub_converter_without_text_input():
    sub_converter = MagicMock(spec=Converter)
    sub_converter.input_supported.return_value = False

    with pytest.raises(ValueError, match="does not support text input"):
        JsonFieldConverter(sub_converter=sub_converter, json_pointer="/state")


def test_init_rejects_sub_converter_without_text_output():
    sub_converter = MagicMock(spec=Converter)
    sub_converter.input_supported.return_value = True
    sub_converter.output_supported.return_value = False

    with pytest.raises(ValueError, match="does not support text output"):
        JsonFieldConverter(sub_converter=sub_converter, json_pointer="/state")


async def test_rejects_non_text_input_type():
    converter = JsonFieldConverter(sub_converter=_UpperConverter(), json_pointer="/state")

    with pytest.raises(ValueError, match="only supports text input"):
        await converter.convert_async(prompt=_REQUEST, input_type="image_path")
    with pytest.raises(ValueError, match="only supports text input"):
        await converter.convert_tokens_async(prompt=_REQUEST, input_type="image_path")


async def test_rejects_non_text_sub_converter_output():
    converter = JsonFieldConverter(sub_converter=_ImageConverter(), json_pointer="/state")

    with pytest.raises(ValueError, match="requires text output, but received image_path"):
        await converter.convert_async(prompt=_REQUEST)
    with pytest.raises(ValueError, match="requires text output, but received image_path"):
        await converter.convert_tokens_async(prompt=_REQUEST)


async def test_sub_converter_errors_propagate():
    sub_converter = _UpperConverter()
    converter = JsonFieldConverter(sub_converter=sub_converter, json_pointer="/state")

    with (
        patch.object(sub_converter, "convert_async", AsyncMock(side_effect=RuntimeError("boom"))),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await converter.convert_async(prompt=_REQUEST)


@pytest.mark.parametrize(
    ("field", "keep_tokens", "expected_state"),
    [
        ('"a ⟪b⟫ c"', False, "a Yg== c"),
        ('"a \\u27eab\\u27eb c"', False, "a Yg== c"),
        ('"a ⟪b⟫ c"', True, "a ⟪Yg==⟫ c"),
        ('"⟪⟪b⟫⟫ ⟪c⟫"', False, "⟪Yg==⟫ Yw=="),
        ('"abc"', False, "YWJj"),
        ('"abc"', True, "⟪YWJj⟫"),
    ],
)
async def test_convert_tokens_async_resolves_markers_inside_the_field(
    field: str, keep_tokens: bool, expected_state: str
):
    converter = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state")

    result = await converter.convert_tokens_async(prompt=f'{{"state": {field}, "n": 1.10}}', keep_tokens=keep_tokens)

    assert result.output_type == "text"
    assert json.loads(result.output_text) == {"state": expected_state, "n": 1.1}
    assert result.output_text.endswith(', "n": 1.10}')


async def test_convert_tokens_async_leaves_markers_in_other_fields():
    converter = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state")
    prompt = '{"⟪key⟫": "⟪x⟫", "state": "abc", "other": "keep ⟪this⟫"}'

    result = await converter.convert_tokens_async(prompt=prompt)

    assert result.output_text == prompt.replace('"abc"', '"YWJj"')


async def test_convert_tokens_async_rejects_malformed_markers_in_the_field():
    converter = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state")

    with pytest.raises(ValueError, match="Unmatched start token"):
        await converter.convert_tokens_async(prompt='{"state": "a ⟪b"}')


async def test_convert_tokens_async_uses_custom_tokens():
    converter = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state")

    result = await converter.convert_tokens_async(prompt='{"state": "a [[b]] ⟪c⟫"}', start_token="[[", end_token="]]")

    assert json.loads(result.output_text) == {"state": "a Yg== ⟪c⟫"}


async def test_convert_tokens_async_passes_markers_to_a_selective_sub_converter():
    sub_converter = SelectiveTextConverter(
        sub_converter=Base64Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    converter = JsonFieldConverter(sub_converter=sub_converter, json_pointer="/state")

    result = await converter.convert_tokens_async(prompt='{"state": "a ⟪b⟫ c"}')

    assert json.loads(result.output_text) == {"state": "a ⟪Yg==⟫ c"}


async def test_stacked_converters_convert_their_own_fields(patch_central_database: MemoryInterface):
    prompt = '{"state": "a ⟪b⟫", "note": "⟪c⟫ d", "questions": {}}'
    message = Message.from_prompt(prompt=prompt, role="user")
    converters = [
        JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state"),
        JsonFieldConverter(sub_converter=ROT13Converter(), json_pointer="/note"),
    ]

    await PromptNormalizer().convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=converters)], message=message
    )

    assert message.message_pieces[0].converted_value == '{"state": "a Yg==", "note": "p d", "questions": {}}'
    assert message.message_pieces[0].original_value == prompt


async def test_wrapped_stochastic_converter_follows_the_configured_seed():
    converter = JsonFieldConverter(sub_converter=RandomCapitalLettersConverter(percentage=50.0), json_pointer="/state")
    prompt = '{"state": "abcdefghijklmnopqrstuvwxyz"}'

    configure_random_seed(seed=3)
    try:
        first = await converter.convert_async(prompt=prompt)
        second = await converter.convert_async(prompt=prompt)
        configure_random_seed(seed=4)
        other = await converter.convert_async(prompt=prompt)
    finally:
        configure_random_seed(seed=None)

    assert first.output_text == second.output_text
    assert first.output_text != other.output_text


def test_identifier_includes_pointer_and_sub_converter():
    identifier = JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state").get_identifier()

    assert identifier.params["json_pointer"] == "/state"
    assert (
        identifier.hash
        != JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/note").get_identifier().hash
    )
    assert (
        identifier.hash
        != JsonFieldConverter(sub_converter=ROT13Converter(), json_pointer="/state").get_identifier().hash
    )


async def test_output_reaches_the_decision_sdk_as_a_valid_request(patch_central_database: MemoryInterface):
    litellm = pytest.importorskip("litellm")
    from litellm.types.decisions import DecisionsResponse

    from pyrit.prompt_target import LiteLLMDecisionTarget

    sdk = MagicMock(spec=litellm)
    sdk.asystemone = None
    sdk.adecisions = AsyncMock(
        return_value=DecisionsResponse.model_validate({"answers": {"q": {"type": "noul", "noul": 0.2}}})
    )
    converted = await JsonFieldConverter(sub_converter=Base64Converter(), json_pointer="/state").convert_async(
        prompt=_REQUEST
    )

    with patch.object(LiteLLMDecisionTarget, "_import_litellm", return_value=sdk):
        target = LiteLLMDecisionTarget(model_name="provider/decision-model", api_key="test-api-key")
        await target.send_prompt_async(message=Message.from_prompt(prompt=converted.output_text, role="user"))

    assert sdk.adecisions.call_args.kwargs["state"] == "aGVsbG8="
    assert sdk.adecisions.call_args.kwargs["questions"] == json.loads(_REQUEST)["questions"]
