# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.converter import (
    LLMGenericTextConverter,
    MaliciousQuestionGeneratorConverter,
    MathPromptConverter,
    NoiseConverter,
    ScientificTranslationConverter,
    TenseConverter,
    ToneConverter,
    ToxicSentenceGeneratorConverter,
    TranslationConverter,
    VariationConverter,
)
from pyrit.exceptions import InvalidJsonException
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.registry import ConverterRegistry


@pytest.fixture
def mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="prompt value",
            )
        ]
    )
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockLLMTarget")
    return target


async def test_noise_converter_sets_system_prompt_default(mock_target) -> None:
    converter = NoiseConverter(converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "Grammar error, Delete random letter" in system_arg


async def test_noise_converter_sets_system_prompt(mock_target) -> None:
    converter = NoiseConverter(converter_target=mock_target, noise="extra random periods")
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "extra random periods" in system_arg


async def test_tone_converter_sets_system_prompt(mock_target) -> None:
    converter = ToneConverter(tone="formal", converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "formal" in system_arg


async def test_tense_converter_sets_system_prompt(mock_target) -> None:
    converter = TenseConverter(tense="past", converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "past" in system_arg


async def test_malicious_question_converter_sets_system_prompt(mock_target) -> None:
    converter = MaliciousQuestionGeneratorConverter(converter_target=mock_target)
    await converter.convert_async(prompt="being awesome")

    mock_target.set_system_prompt_async.assert_called_once()

    system_arg = mock_target.set_system_prompt_async.call_args[1]["system_prompt"]
    assert isinstance(system_arg, str)
    assert "Please act as an expert in this domain: being awesome" in system_arg


def test_generic_llm_converter_input_supported() -> None:
    target = MagicMock(spec=PromptTarget)
    converter = LLMGenericTextConverter(converter_target=target)
    assert converter.input_supported("text") is True
    assert converter.input_supported("audio_path") is False


def test_generic_llm_converter_user_prompt_without_objective_raises() -> None:
    target = MagicMock(spec=PromptTarget)
    user_template = MagicMock()
    with pytest.raises(ValueError):
        LLMGenericTextConverter(converter_target=target, user_prompt_template_with_objective=user_template)


def test_generic_llm_converter_init_default_templates_empty() -> None:
    target = MagicMock(spec=PromptTarget)
    converter = LLMGenericTextConverter(converter_target=target)
    assert converter._system_prompt_template is None
    assert converter._user_prompt_template_with_objective is None


def test_generic_llm_converter_default_no_retry_exceptions() -> None:
    target = MagicMock(spec=PromptTarget)
    converter = LLMGenericTextConverter(converter_target=target)
    assert converter._retry_exceptions == ()


def test_generic_llm_converter_class_attr_retry_exceptions() -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (ValueError,)

    target = MagicMock(spec=PromptTarget)
    converter = _RetryingConverter(converter_target=target)
    assert converter._retry_exceptions == (ValueError,)


def test_generic_llm_converter_instance_retry_exceptions_overrides_class_attr() -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (ValueError,)

    target = MagicMock(spec=PromptTarget)
    converter = _RetryingConverter(converter_target=target, retry_exceptions=(KeyError,))
    assert converter._retry_exceptions == (KeyError,)


async def test_convert_async_no_user_template_sets_only_original_value(mock_target) -> None:
    converter = LLMGenericTextConverter(converter_target=mock_target)
    await converter.convert_async(prompt="hello")

    sent_message = mock_target.send_prompt_async.call_args[1]["message"]
    piece = sent_message.message_pieces[0]
    assert piece.original_value == "hello"
    assert piece.converted_value == "hello"


async def test_convert_async_with_user_template_preserves_original_and_renders_converted(mock_target) -> None:
    user_template = SeedPrompt(
        value="Wrap: [{{ objective }}]",
        parameters=["objective"],
        data_type="text",
    )
    converter = LLMGenericTextConverter(converter_target=mock_target, user_prompt_template_with_objective=user_template)
    await converter.convert_async(prompt="raw input")

    sent_message = mock_target.send_prompt_async.call_args[1]["message"]
    piece = sent_message.message_pieces[0]
    assert piece.original_value == "raw input"
    assert piece.converted_value == "Wrap: [raw input]"


async def test_convert_async_user_template_receives_extra_kwargs(mock_target) -> None:
    user_template = SeedPrompt(
        value="Lang={{ language }} Obj={{ objective }}",
        parameters=["objective", "language"],
        data_type="text",
    )
    converter = LLMGenericTextConverter(
        converter_target=mock_target,
        user_prompt_template_with_objective=user_template,
        language="spanish",
    )
    await converter.convert_async(prompt="hello")

    sent_message = mock_target.send_prompt_async.call_args[1]["message"]
    assert sent_message.message_pieces[0].converted_value == "Lang=spanish Obj=hello"


async def test_convert_async_input_validation_raises_before_set_system_prompt(mock_target) -> None:
    system_template = SeedPrompt(value="sys", data_type="text")
    converter = LLMGenericTextConverter(converter_target=mock_target, system_prompt_template=system_template)
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="hello", input_type="image_path")
    mock_target.set_system_prompt_async.assert_not_called()
    mock_target.send_prompt_async.assert_not_called()


async def test_convert_async_process_response_hook_called(mock_target) -> None:
    class _UpperCaseConverter(LLMGenericTextConverter):
        def _process_response(self, response_text: str) -> str:
            return response_text.upper()

    converter = _UpperCaseConverter(converter_target=mock_target)
    result = await converter.convert_async(prompt="anything")
    assert result.output_text == "PROMPT VALUE"


async def test_send_with_retries_no_retry_when_empty_exception_tuple(mock_target) -> None:
    converter = LLMGenericTextConverter(converter_target=mock_target)
    mock_target.send_prompt_async.side_effect = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        await converter.convert_async(prompt="hello")
    assert mock_target.send_prompt_async.call_count == 1


async def test_send_with_retries_retries_on_configured_exception(mock_target) -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)

        def _process_response(self, response_text: str) -> str:
            raise InvalidJsonException(message="bad")

    converter = _RetryingConverter(converter_target=mock_target)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(InvalidJsonException):
            await converter.convert_async(prompt="hello")
    assert mock_target.send_prompt_async.call_count == 2  # RETRY_MAX_NUM_ATTEMPTS=2 in conftest


async def test_send_with_retries_does_not_retry_unrelated_exception(mock_target) -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)

    converter = _RetryingConverter(converter_target=mock_target)
    mock_target.send_prompt_async.side_effect = ValueError("not retried")
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(ValueError, match="not retried"):
            await converter.convert_async(prompt="hello")
    assert mock_target.send_prompt_async.call_count == 1


async def test_send_with_retries_succeeds_after_one_failure(mock_target) -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)
        _calls = 0

        def _process_response(self, response_text: str) -> str:
            type(self)._calls += 1
            if type(self)._calls == 1:
                raise InvalidJsonException(message="first")
            return response_text

    converter = _RetryingConverter(converter_target=mock_target)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await converter.convert_async(prompt="hello")
    assert result.output_text == "prompt value"
    assert mock_target.send_prompt_async.call_count == 2


async def test_send_with_retries_uses_static_attempt_count_when_provided(mock_target) -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)

        def _process_response(self, response_text: str) -> str:
            raise InvalidJsonException(message="bad")

    converter = _RetryingConverter(converter_target=mock_target, max_retry_attempts=4)
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(InvalidJsonException):
            await converter.convert_async(prompt="hello")
    assert mock_target.send_prompt_async.call_count == 4


async def test_send_with_retries_no_wait_by_default(mock_target) -> None:
    """Default wait is none (0 seconds), matching pyrit_json_retry behavior."""

    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)

        def _process_response(self, response_text: str) -> str:
            raise InvalidJsonException(message="bad")

    converter = _RetryingConverter(converter_target=mock_target)
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(InvalidJsonException):
            await converter.convert_async(prompt="hello")
    for call in mock_sleep.call_args_list:
        assert call.args[0] == 0.0


async def test_send_with_retries_uses_exponential_wait_when_max_seconds_provided(mock_target) -> None:
    class _RetryingConverter(LLMGenericTextConverter):
        RETRY_EXCEPTIONS = (InvalidJsonException,)

        def _process_response(self, response_text: str) -> str:
            raise InvalidJsonException(message="bad")

    converter = _RetryingConverter(converter_target=mock_target, retry_wait_max_seconds=10)
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(InvalidJsonException):
            await converter.convert_async(prompt="hello")
    # waits should be > 0 between attempts (exponential backoff)
    nonzero_waits = [c for c in mock_sleep.call_args_list if c.args[0] > 0]
    assert len(nonzero_waits) >= 1


def _custom_template(value: str, *parameters: str) -> SeedPrompt:
    return SeedPrompt(value=value, data_type="text", parameters=list(parameters))


@pytest.mark.parametrize(
    "converter_class, kwargs, template",
    [
        (ToneConverter, {"tone": "angry"}, _custom_template("Rewrite in a {{ tone }} tone, in French.", "tone")),
        (TenseConverter, {"tense": "past"}, _custom_template("Use the {{ tense }} tense, as a poem.", "tense")),
        (
            TranslationConverter,
            {"language": "french"},
            _custom_template("Translate to {{ language }} slang.", "language"),
        ),
        (
            NoiseConverter,
            {},
            _custom_template("Add {{ number_errors }} typos: {{ noise }}", "noise", "number_errors"),
        ),
        (ScientificTranslationConverter, {"mode": "academic"}, _custom_template("Rewrite as a lab report.")),
        (
            VariationConverter,
            {},
            _custom_template("Give {{ number_iterations }} rhyming variations.", "number_iterations"),
        ),
    ],
)
def test_llm_converter_identifier_includes_custom_prompt_template(
    mock_target, converter_class, kwargs, template
) -> None:
    default = converter_class(converter_target=mock_target, **kwargs)
    custom = converter_class(converter_target=mock_target, prompt_template=template, **kwargs)
    same_custom = converter_class(converter_target=mock_target, prompt_template=template, **kwargs)

    default_params = default.get_identifier().params
    custom_params = custom.get_identifier().params
    assert default_params["system_prompt_template_hash"] != custom_params["system_prompt_template_hash"]
    assert default.get_identifier().hash != custom.get_identifier().hash
    assert custom.get_identifier().hash == same_custom.get_identifier().hash
    for key, value in kwargs.items():
        assert custom_params[key] == value


def test_generic_llm_converter_identifier_includes_template_parameters(mock_target) -> None:
    template = _custom_template("Translate the text into {{ language }}.", "language")

    french = LLMGenericTextConverter(converter_target=mock_target, system_prompt_template=template, language="French")
    spanish = LLMGenericTextConverter(converter_target=mock_target, system_prompt_template=template, language="Spanish")
    same_french = LLMGenericTextConverter(
        converter_target=mock_target, system_prompt_template=template, language="French"
    )
    no_parameters = LLMGenericTextConverter(converter_target=mock_target, system_prompt_template=template)

    assert len(french.get_identifier().params["template_parameters_hash"]) == 16
    assert french.get_identifier().hash != spanish.get_identifier().hash
    assert french.get_identifier().hash == same_french.get_identifier().hash
    assert no_parameters.get_identifier().params.get("template_parameters_hash") is None


async def _sent_prompt(converter: LLMGenericTextConverter, target: MagicMock) -> str:
    await converter.convert_async(prompt="hello")
    return target.send_prompt_async.call_args.kwargs["message"].message_pieces[0].converted_value


@pytest.mark.parametrize(
    "template_value, first_words, second_words",
    [
        ("{{ objective }} {{ words }}", ("alpha", "beta"), ["alpha", "beta"]),
        ("{{ objective }}{% for word in words %} {{ word }}{% endfor %}", {"a": 1, "b": 2}, {"b": 2, "a": 1}),
        ("{{ objective }} {{ words }}", 1, 1.0),
        ("{{ objective }} {{ words }}", {1: "one"}, {"1": "one"}),
    ],
)
async def test_generic_llm_converter_identifier_distinguishes_values_that_render_differently(
    mock_target, template_value, first_words, second_words
) -> None:
    template = _custom_template(template_value, "objective", "words")
    first = LLMGenericTextConverter(
        converter_target=mock_target, user_prompt_template_with_objective=template, words=first_words
    )
    second = LLMGenericTextConverter(
        converter_target=mock_target, user_prompt_template_with_objective=template, words=second_words
    )

    assert await _sent_prompt(first, mock_target) != await _sent_prompt(second, mock_target)
    assert first.get_identifier().hash != second.get_identifier().hash

    # Both configurations get their own default registry name instead of colliding.
    instances = ConverterRegistry().instances
    instances.register(first)
    instances.register(second)
    assert instances.get(first.get_identifier().unique_name) is first
    assert instances.get(second.get_identifier().unique_name) is second


def test_generic_llm_converter_identifier_is_stable_for_equal_template_parameters(mock_target) -> None:
    template = _custom_template("{{ objective }} {{ words }} {{ path }} {{ tags }}", "objective")

    first = LLMGenericTextConverter(
        converter_target=mock_target,
        user_prompt_template_with_objective=template,
        words={"alpha": ["a", ("b", 2)]},
        path=Path("styles") / "noir.yaml",
        tags={"x", "y", "z"},
    )
    # Same values, keyword arguments given in a different order.
    second = LLMGenericTextConverter(
        converter_target=mock_target,
        user_prompt_template_with_objective=template,
        tags={"z", "y", "x"},
        path=Path("styles") / "noir.yaml",
        words={"alpha": ["a", ("b", 2)]},
    )

    assert first.get_identifier().hash == second.get_identifier().hash


class _UnrepresentableValue:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")

    def __str__(self) -> str:
        return "custom"


@pytest.mark.parametrize(
    "words",
    [
        {1: "first", "two": "second"},
        {("a", "b"): "pair", None: "none"},
        {frozenset({1, "x"}), 2.5, b"bytes"},
        _UnrepresentableValue(),
    ],
)
async def test_generic_llm_converter_converts_with_any_renderable_template_parameter(mock_target, words) -> None:
    self_referencing: list[Any] = ["loop"]
    self_referencing.append(self_referencing)
    converter = LLMGenericTextConverter(
        converter_target=mock_target,
        user_prompt_template_with_objective=_custom_template("{{ objective }} {{ words }} {{ loop }}", "objective"),
        words=words,
        loop=self_referencing,
    )

    result = await converter.convert_async(prompt="hello")

    assert result.output_text == "prompt value"
    request = mock_target.send_prompt_async.call_args.kwargs["message"]
    assert request.message_pieces[0].converter_identifiers[0].hash == converter.get_identifier().hash
    assert len(converter.get_identifier().params["template_parameters_hash"]) == 16


@pytest.mark.parametrize(
    "converter_class",
    [MathPromptConverter, MaliciousQuestionGeneratorConverter, ToxicSentenceGeneratorConverter],
)
async def test_per_call_prompt_does_not_change_converter_identifier(mock_target, converter_class) -> None:
    read_before = converter_class(converter_target=mock_target)
    read_after = converter_class(converter_target=mock_target)
    identifier_before_conversion = read_before.get_identifier()

    sent_identifiers = []
    for converter in (read_before, read_after):
        for prompt in ("hello", "goodbye"):
            await converter.convert_async(prompt=prompt)
            request = mock_target.send_prompt_async.call_args.kwargs["message"]
            sent_identifiers.append(request.message_pieces[0].converter_identifiers[0])

    assert read_after.get_identifier().hash == identifier_before_conversion.hash
    assert {identifier.hash for identifier in sent_identifiers} == {identifier_before_conversion.hash}
    assert identifier_before_conversion.params.get("template_parameters_hash") is None
