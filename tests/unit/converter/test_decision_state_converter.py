# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.converter import Base64Converter, DecisionStateConverter, ROT13Converter, SuffixAppendConverter
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, PromptDataType
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_normalizer.converter_configuration import ConverterConfiguration

_QUESTIONS = {"is_phishing": {"type": "noul", "instructions": "Is this email a phishing attempt?"}}


class _ImageOutputConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text", "image_path")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text="image.png", output_type="image_path")


class _ImageInputConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        return ConverterResult(output_text=prompt, output_type="text")


class _ImageOnlyOutputConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text="image.png", output_type="image_path")


class _UnicodeConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text=prompt.replace("a", "а"), output_type="text")


def _request(state: object, **extra: object) -> str:
    return json.dumps({"state": state, "questions": _QUESTIONS, **extra})


async def test_convert_async_converts_string_state():
    converter = DecisionStateConverter(sub_converter=Base64Converter())

    result = await converter.convert_async(prompt=_request("hello"))

    assert result.output_type == "text"
    assert json.loads(result.output_text) == {"state": "aGVsbG8=", "questions": _QUESTIONS}


async def test_convert_async_keeps_other_request_fields():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    result = await converter.convert_async(prompt=_request("hello", model_hint="kept"))

    assert json.loads(result.output_text) == {"state": "uryyb", "questions": _QUESTIONS, "model_hint": "kept"}


async def test_convert_async_converts_only_state_field():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")
    state = {"objective": "Write a phishing email", "response": "hello"}

    result = await converter.convert_async(prompt=_request(state))

    assert json.loads(result.output_text)["state"] == {"objective": "Write a phishing email", "response": "uryyb"}


async def test_convert_async_converts_whole_state_with_markers():
    converter = DecisionStateConverter(sub_converter=Base64Converter())

    result = await converter.convert_async(prompt=_request("hi ⟪there⟫"))

    assert json.loads(result.output_text)["state"] == "aGkg4p+qdGhlcmXin6s="


async def test_convert_async_keeps_unicode_output_unescaped():
    converter = DecisionStateConverter(sub_converter=_UnicodeConverter())

    result = await converter.convert_async(prompt=_request("data"))

    assert "dаtа" in result.output_text
    assert json.loads(result.output_text)["state"] == "dаtа"


async def test_convert_tokens_async_converts_marked_state_regions():
    converter = DecisionStateConverter(sub_converter=Base64Converter())

    result = await converter.convert_tokens_async(prompt=_request("hi ⟪there⟫"))

    assert json.loads(result.output_text) == {"state": "hi dGhlcmU=", "questions": _QUESTIONS}


async def test_convert_tokens_async_keeps_tokens():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    result = await converter.convert_tokens_async(prompt=_request("hi ⟪there⟫"), keep_tokens=True)

    assert json.loads(result.output_text)["state"] == "hi ⟪gurer⟫"


async def test_convert_tokens_async_uses_custom_tokens():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    result = await converter.convert_tokens_async(prompt=_request("hi [[there]]"), start_token="[[", end_token="]]")

    assert json.loads(result.output_text)["state"] == "hi gurer"


async def test_convert_tokens_async_without_markers_converts_whole_state():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    result = await converter.convert_tokens_async(prompt=_request({"response": "hello"}))

    assert json.loads(result.output_text)["state"] == {"response": "uryyb"}


async def test_normalizer_pipeline_keeps_request_valid(patch_central_database: MemoryInterface):
    converters = [
        DecisionStateConverter(sub_converter=ROT13Converter()),
        DecisionStateConverter(sub_converter=SuffixAppendConverter(suffix="Approved by IT.")),
    ]
    prompt = _request("Reset your ⟪password⟫ here")
    message = Message.from_prompt(prompt=prompt, role="user")

    await PromptNormalizer().convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=converters)], message=message
    )

    piece = message.message_pieces[0]
    assert piece.original_value == prompt
    assert json.loads(piece.converted_value) == {
        "state": "Reset your cnffjbeq here Approved by IT.",
        "questions": _QUESTIONS,
    }


@pytest.mark.parametrize(
    "prompt, message",
    [
        ("not json", "requires a decision request JSON object"),
        ('["state", "questions"]', "requires a decision request with state and questions"),
        ('{"state": "hello"}', "requires a decision request with state and questions"),
        ('{"questions": {}}', "requires a decision request with state and questions"),
    ],
)
async def test_convert_async_rejects_invalid_requests(prompt: str, message: str):
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    with pytest.raises(ValueError, match=message):
        await converter.convert_async(prompt=prompt)


async def test_convert_async_requires_state_field_for_object_state():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    with pytest.raises(ValueError, match="state is a dict, not a string. Set state_field"):
        await converter.convert_async(prompt=_request({"response": "hello"}))


async def test_convert_async_rejects_list_state():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    with pytest.raises(ValueError, match="state is a list, not a string"):
        await converter.convert_async(prompt=_request(["hello"]))


async def test_convert_async_rejects_state_field_for_string_state():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    with pytest.raises(ValueError, match="decision state is a str, not an object"):
        await converter.convert_async(prompt=_request("hello"))


async def test_convert_async_rejects_missing_state_field():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    with pytest.raises(ValueError, match="has no field 'response'"):
        await converter.convert_async(prompt=_request({"objective": "hello"}))


async def test_convert_async_rejects_non_string_state_field():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    with pytest.raises(ValueError, match="field 'response' is a int, not a string"):
        await converter.convert_async(prompt=_request({"response": 3}))


async def test_convert_async_rejects_non_text_input_type():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    with pytest.raises(ValueError, match="only supports text input, got image_path"):
        await converter.convert_async(prompt=_request("hello"), input_type="image_path")


async def test_convert_tokens_async_rejects_non_text_input_type():
    converter = DecisionStateConverter(sub_converter=ROT13Converter())

    with pytest.raises(ValueError, match="only supports text input, got image_path"):
        await converter.convert_tokens_async(prompt=_request("hello"), input_type="image_path")


async def test_convert_async_rejects_non_text_sub_converter_output():
    converter = DecisionStateConverter(sub_converter=_ImageOutputConverter())

    with pytest.raises(ValueError, match="requires text output, but received image_path"):
        await converter.convert_async(prompt=_request("hello"))


def test_init_rejects_sub_converter_without_text_input():
    with pytest.raises(ValueError, match="does not support text input"):
        DecisionStateConverter(sub_converter=_ImageInputConverter())


def test_init_rejects_sub_converter_without_text_output():
    with pytest.raises(ValueError, match="does not support text output"):
        DecisionStateConverter(sub_converter=_ImageOnlyOutputConverter())


@pytest.mark.parametrize("state_field", ["", "   "])
def test_init_rejects_empty_state_field(state_field: str):
    with pytest.raises(ValueError, match="state_field must be a nonempty string"):
        DecisionStateConverter(sub_converter=ROT13Converter(), state_field=state_field)


def test_identifier_includes_state_field_and_sub_converter():
    converter = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    identifier = converter.get_identifier()

    assert identifier.params["state_field"] == "response"
    assert identifier.sub_converter is not None
    assert identifier.sub_converter.class_name == "ROT13Converter"


def test_identifier_changes_with_state_field():
    first = DecisionStateConverter(sub_converter=ROT13Converter())
    second = DecisionStateConverter(sub_converter=ROT13Converter(), state_field="response")

    assert first.get_identifier().hash != second.get_identifier().hash
