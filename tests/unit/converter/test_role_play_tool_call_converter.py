# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.common.apply_defaults import get_global_default_values, set_default_value
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter import LLMGenericTextConverter, RolePlayToolCallConverter
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.models.identifiers import ConverterIdentifier
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.registry import ConverterRegistry
from pyrit.registry.resolution import derive_parameters

INCOMING_MESSAGE = "Please refund $25 to the original payment method for order-1."
VALID_CALL = 'refund {"order": "order-1", "amount": 25}'
ACTION_DESIRED = 'refund {"order": "order-1", "amount": 80}'


@pytest.fixture(autouse=True)
def isolate_defaults() -> Generator[None, None, None]:
    with patch.object(get_global_default_values(), "_default_values", {}):
        yield


def _mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value="written message")])
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockRolePlayTarget")
    return target


def _sent_text(target: MagicMock) -> str:
    message = target.send_prompt_async.call_args.kwargs["message"]
    assert isinstance(message, Message)
    value = message.message_pieces[0].converted_value
    assert isinstance(value, str)
    return value


def _system_text(target: MagicMock) -> str:
    value = target.set_system_prompt_async.call_args.kwargs["system_prompt"]
    assert isinstance(value, str)
    return value


def test_identifier_includes_resolved_target_and_inputs() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
    )
    identifier = converter.get_identifier()
    assert identifier.params["valid_call"] == VALID_CALL
    assert identifier.params["action_desired"] == ACTION_DESIRED
    assert identifier.params["message_format"] == "random"
    assert identifier.params["system_prompt_template_hash"]
    assert identifier.converter_target == target.get_identifier()


@pytest.mark.parametrize("kwargs", [{}, {"converter_target": None}])
def test_target_is_required_without_configured_default(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="converter_target is required"):
        RolePlayToolCallConverter(valid_call=VALID_CALL, action_desired=ACTION_DESIRED, **kwargs)


def test_target_uses_standard_llm_converter_default() -> None:
    target = _mock_target()
    set_default_value(class_type=LLMGenericTextConverter, parameter_name="converter_target", value=target)

    converter = RolePlayToolCallConverter(valid_call=VALID_CALL, action_desired=ACTION_DESIRED)

    assert converter._converter_target is target


@pytest.mark.parametrize("missing", ["valid_call", "action_desired"])
def test_call_fields_are_required(missing: str) -> None:
    kwargs = {
        "valid_call": VALID_CALL,
        "action_desired": ACTION_DESIRED,
    }
    del kwargs[missing]
    with pytest.raises(TypeError, match=missing):
        RolePlayToolCallConverter(converter_target=_mock_target(), **kwargs)


@pytest.mark.parametrize("field", ["valid_call", "action_desired", "message_format"])
@pytest.mark.parametrize("empty", ["", " \t\n"])
def test_empty_fields_are_rejected(field: str, empty: str) -> None:
    kwargs = {
        "valid_call": VALID_CALL,
        "action_desired": ACTION_DESIRED,
        "message_format": "random",
    }
    kwargs[field] = empty
    with pytest.raises(ValueError, match=f"{field} must not be empty"):
        RolePlayToolCallConverter(converter_target=_mock_target(), **kwargs)


def test_role_play_fields_are_multiline() -> None:
    parameters = {
        parameter.name: parameter
        for parameter in derive_parameters(cls=RolePlayToolCallConverter, identifier_type=ConverterIdentifier)
    }
    assert set(parameters) == {"converter_target", "valid_call", "action_desired", "message_format", "system_prompt"}
    assert parameters["valid_call"].multiline
    assert parameters["action_desired"].multiline
    assert parameters["message_format"].multiline
    assert parameters["system_prompt"].multiline
    assert parameters["system_prompt"].is_external_input
    assert parameters["system_prompt"].for_external_catalog().type_name == "str"
    assert parameters["valid_call"].description
    assert parameters["valid_call"].required
    assert parameters["action_desired"].required
    assert parameters["converter_target"].required
    assert not parameters["message_format"].required
    assert parameters["message_format"].default == "random"


def test_shipped_prompt_keeps_placeholders() -> None:
    prompt = SeedPrompt.from_yaml_file(CONVERTER_SEED_PROMPT_PATH / "role_play_tool_calls.yaml")
    assert prompt.parameters == ["objective", "valid_call", "action_desired", "message_format"]
    assert "{{ objective }}" in prompt.value
    assert "{{ valid_call }}" in prompt.value
    assert "authorized AI security evaluation" in prompt.value
    assert "Return only the rewritten pipeline text." in prompt.value


async def test_convert_renders_required_calls_and_pipeline_text_async() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
    )
    result = await converter.convert_async(prompt=INCOMING_MESSAGE)

    text = _system_text(target)
    assert INCOMING_MESSAGE in text
    assert "authorized AI security evaluation" in text
    assert 'refund {"order": "order-1", "amount": 25}' in text
    assert 'refund {"order": "order-1", "amount": 80}' in text
    assert any(f"format: {name}." in text for name in converter.MESSAGE_FORMATS)
    assert "format: random." not in text
    assert result.output_text == "written message"
    assert result.output_type == "text"
    assert _sent_text(target) == INCOMING_MESSAGE


async def test_convert_uses_explicit_message_format_async() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call="summarize spec",
        action_desired="publish report",
        message_format="signed XML approval note",
    )
    with patch.object(converter, "_get_random_generator") as random_generator:
        await converter.convert_async(
            prompt=(
                "Product specification: Atlas is a scheduling application. "
                "It supports calendar import and conflict detection."
            )
        )
    random_generator.assert_not_called()

    text = _system_text(target)
    assert "Atlas is a scheduling application." in text
    assert "summarize spec" in text
    assert "publish report" in text
    assert "signed XML approval note" in text


async def test_convert_uses_each_pipeline_input_without_changing_configuration_async() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call="summarize spec",
        action_desired="publish report",
    )
    identifier = converter.get_identifier()
    prompts = [
        "Product specification: Atlas supports calendar import.",
        "PREVIOUS CONVERTER OUTPUT: Atlas also supports conflict detection.",
    ]

    for prompt in prompts:
        await converter.convert_async(prompt=prompt)
        assert _sent_text(target) == prompt
        assert f"Current pipeline text:\n{prompt}" in _system_text(target)
        assert converter.get_identifier() == identifier

    assert target.send_prompt_async.call_count == len(prompts)


@pytest.mark.parametrize("as_seed", [False, True])
async def test_custom_system_prompt_renders_all_values_async(as_seed: bool) -> None:
    target = _mock_target()
    text = "Custom {{ objective }} | {{ valid_call }} | {{ action_desired }} | {{ message_format }}"
    prompt = SeedPrompt(value=text, parameters=["objective"]) if as_seed else text
    kwargs = {
        "converter_target": target,
        "valid_call": VALID_CALL,
        "action_desired": ACTION_DESIRED,
        "message_format": "XML",
        "system_prompt": prompt,
    }
    converter = (
        RolePlayToolCallConverter(**kwargs)
        if as_seed
        else ConverterRegistry().create_instance(
            "RolePlayToolCallConverter",
            **kwargs,
        )
    )
    await converter.convert_async(prompt=INCOMING_MESSAGE)
    assert _system_text(target) == f"Custom {INCOMING_MESSAGE} | {VALID_CALL} | {ACTION_DESIRED} | XML"
    assert _sent_text(target) == INCOMING_MESSAGE
    assert converter.get_reconstruction_parameters()["system_prompt"].value == text
    assert (
        target.set_system_prompt_async.call_args.kwargs["conversation_id"]
        == target.send_prompt_async.call_args.kwargs["message"].get_piece().conversation_id
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        " \n",
        SeedPrompt(value="file.png", data_type="image_path", parameters=["objective"]),
    ],
)
def test_invalid_system_prompt_is_rejected(prompt: str | SeedPrompt) -> None:
    with pytest.raises(ValueError, match="system_prompt"):
        RolePlayToolCallConverter(
            converter_target=_mock_target(),
            valid_call=VALID_CALL,
            action_desired=ACTION_DESIRED,
            system_prompt=prompt,
        )


async def test_static_system_prompt_keeps_the_pipeline_text_in_the_user_message_async() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
        system_prompt=SeedPrompt(value="Act like a machine."),
    )
    await converter.convert_async(prompt=INCOMING_MESSAGE)
    assert _system_text(target) == "Act like a machine."
    assert _sent_text(target) == INCOMING_MESSAGE


def test_default_system_prompt_can_be_edited_and_reconstructed_without_changing_source() -> None:
    registry = ConverterRegistry()
    source = RolePlayToolCallConverter(
        converter_target=_mock_target(),
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
    )
    text = source.get_reconstruction_parameters()["system_prompt"].value
    assert text == SeedPrompt.from_yaml_file(CONVERTER_SEED_PROMPT_PATH / "role_play_tool_calls.yaml").value
    unchanged = registry.recreate_instance(source=source, params={}, external_input=True)
    assert unchanged.get_identifier() == source.get_identifier()
    edited = registry.recreate_instance(
        source=source,
        params={"system_prompt": "Rewrite {{ objective }} as {{ message_format }}"},
        external_input=True,
    )
    assert edited.get_identifier().hash != source.get_identifier().hash
    assert source.get_reconstruction_parameters()["system_prompt"].value == text
    assert (
        edited.get_reconstruction_parameters()["system_prompt"].value
        == "Rewrite {{ objective }} as {{ message_format }}"
    )


def test_prompt_edit_preserves_source_metadata() -> None:
    prompt = SeedPrompt(
        value="Original {{ objective }}", name="custom-template", role="system", parameters=["objective"]
    )
    source = RolePlayToolCallConverter(
        converter_target=_mock_target(),
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
        system_prompt=prompt,
    )
    edited = ConverterRegistry().recreate_instance(
        source=source,
        params={"system_prompt": "Edited {{ objective }}"},
        external_input=True,
    )
    copied = edited.get_reconstruction_parameters()["system_prompt"]
    assert copied.name == "custom-template"
    assert copied.role == "system"
    assert copied.value == "Edited {{ objective }}"
    assert prompt.value == "Original {{ objective }}"


async def test_random_format_is_selected_on_each_conversion_async() -> None:
    target = _mock_target()
    converter = RolePlayToolCallConverter(
        converter_target=target,
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
    )
    with patch.object(converter, "_get_random_generator") as random_generator:
        random_generator.return_value.choice.side_effect = ["XML", "YAML"]
        await converter.convert_async(prompt="Make the extra action look approved.")
        await converter.convert_async(prompt="Make the extra action look approved.")

    sent = [call.kwargs["system_prompt"] for call in target.set_system_prompt_async.call_args_list]
    assert "format: XML." in sent[0]
    assert "format: YAML." in sent[1]
    assert all(call.kwargs == {"stream": "message_format"} for call in random_generator.call_args_list)
    assert all(
        call.args == (converter.MESSAGE_FORMATS,) for call in random_generator.return_value.choice.call_args_list
    )
    assert converter.get_identifier().params["message_format"] == "random"


async def test_constructor_kwargs_do_not_replace_input_async() -> None:
    target = _mock_target()
    template = SeedPrompt(
        value="goal={{ objective }} call={{ valid_call }}",
        parameters=["objective", "valid_call"],
        is_jinja_template=True,
    )
    converter = LLMGenericTextConverter(
        converter_target=target,
        user_prompt_template_with_objective=template,
        objective="from-constructor",
        valid_call="from-kwargs",
    )
    await converter.convert_async(prompt="from-prompt")

    assert _sent_text(target) == "goal=from-prompt call=from-kwargs"


async def test_convert_rejects_unsupported_input_async() -> None:
    converter = RolePlayToolCallConverter(
        converter_target=_mock_target(),
        valid_call=VALID_CALL,
        action_desired=ACTION_DESIRED,
    )
    with pytest.raises(ValueError, match="not supported"):
        await converter.convert_async(prompt="goal", input_type="image_path")
