# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import DecisionOptionOrderConverter, DecisionStateConverter, ROT13Converter
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_normalizer.converter_configuration import ConverterConfiguration

_OPTIONS = {
    "billing": "A question about a charge or invoice.",
    "refund": "The customer asks for money back.",
    "technical": "Something in the product does not work.",
    "account": "Login, password, or profile changes.",
    "other": "Anything else.",
}


def _request(questions: dict[str, object], state: object = "I was charged twice.") -> str:
    return json.dumps({"state": state, "questions": questions})


def _choice(options: dict[str, str] = _OPTIONS) -> dict[str, object]:
    return {"type": "choice", "instructions": "Which team should handle this ticket?", "criteria": dict(options)}


def _order(result_text: str, question: str = "route") -> list[str]:
    return list(json.loads(result_text)["questions"][question]["criteria"])


async def test_reverse_reverses_option_order():
    result = await DecisionOptionOrderConverter().convert_async(prompt=_request({"route": _choice()}))

    assert result.output_type == "text"
    assert _order(result.output_text) == list(_OPTIONS)[::-1]


async def test_reorder_keeps_option_definitions_and_state():
    prompt = _request({"route": _choice()}, state="Ⓗello ⟪marked⟫")

    result = await DecisionOptionOrderConverter(order="shuffle", seed=1).convert_async(prompt=prompt)

    converted = json.loads(result.output_text)
    original = json.loads(prompt)
    assert converted["state"] == original["state"]
    assert converted["questions"]["route"]["criteria"] == original["questions"]["route"]["criteria"]
    assert converted["questions"]["route"]["instructions"] == original["questions"]["route"]["instructions"]
    assert "Ⓗello" in result.output_text


@pytest.mark.parametrize("seed", range(20))
async def test_shuffle_always_changes_order(seed: int):
    result = await DecisionOptionOrderConverter(order="shuffle", seed=seed).convert_async(
        prompt=_request({"route": _choice()})
    )

    order = _order(result.output_text)
    assert order != list(_OPTIONS)
    assert sorted(order) == sorted(_OPTIONS)


@pytest.mark.parametrize("order", ["reverse", "shuffle"])
async def test_two_options_are_swapped(order: str):
    options = {"yes": "Escalate to a human.", "no": "Do not escalate."}

    result = await DecisionOptionOrderConverter(order=order, seed=3).convert_async(  # type: ignore[arg-type]
        prompt=_request({"route": _choice(options)})
    )

    assert _order(result.output_text) == ["no", "yes"]


async def test_shuffle_seed_is_reproducible():
    prompt = _request({"route": _choice()})

    first = await DecisionOptionOrderConverter(order="shuffle", seed=7).convert_async(prompt=prompt)
    second = await DecisionOptionOrderConverter(order="shuffle", seed=7).convert_async(prompt=prompt)

    assert first.output_text == second.output_text


async def test_shuffle_uses_configured_root_seed():
    prompt = _request({"route": _choice()})

    configure_random_seed(seed=11)
    try:
        first = await DecisionOptionOrderConverter(order="shuffle").convert_async(prompt=prompt)
        second = await DecisionOptionOrderConverter(order="shuffle").convert_async(prompt=prompt)
    finally:
        configure_random_seed(seed=None)

    assert first.output_text == second.output_text


async def test_reorders_every_choice_question_and_leaves_other_types():
    questions = {
        "route": _choice(),
        "priority": _choice({"low": "Can wait.", "high": "Urgent."}),
        "is_spam": {"type": "noul", "instructions": "Is this spam?"},
        "severity": {"type": "score", "instructions": "How severe?", "criteria": ["none", "low", "high"]},
        "single": _choice({"only": "The only option."}),
    }

    result = await DecisionOptionOrderConverter().convert_async(prompt=_request(questions))

    converted = json.loads(result.output_text)["questions"]
    assert list(converted["route"]["criteria"]) == list(_OPTIONS)[::-1]
    assert list(converted["priority"]["criteria"]) == ["high", "low"]
    assert converted["is_spam"] == questions["is_spam"]
    assert converted["severity"] == questions["severity"]
    assert converted["single"] == questions["single"]


@pytest.mark.parametrize(
    "questions",
    [
        {"is_spam": {"type": "noul", "instructions": "Is this spam?"}},
        {"single": _choice({"only": "The only option."})},
        {"route": {"type": "choice", "criteria": ["billing", "refund"]}},
        {"route": "not an object"},
        {},
        [],
    ],
)
async def test_rejects_requests_without_reorderable_choice(questions: object):
    with pytest.raises(ValueError, match="requires at least one choice question with two or more options"):
        await DecisionOptionOrderConverter().convert_async(prompt=_request(questions))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        ("not json", "requires a decision request JSON object"),
        ('{"state": "hello"}', "requires a decision request with state and questions"),
        ("[1, 2]", "requires a decision request with state and questions"),
    ],
)
async def test_rejects_invalid_requests(prompt: str, message: str):
    with pytest.raises(ValueError, match=message):
        await DecisionOptionOrderConverter().convert_async(prompt=prompt)


async def test_rejects_non_text_input_type():
    with pytest.raises(ValueError, match="only supports text input, got image_path"):
        await DecisionOptionOrderConverter().convert_async(
            prompt=_request({"route": _choice()}), input_type="image_path"
        )


def test_init_rejects_unknown_order():
    with pytest.raises(ValueError, match="order must be 'reverse' or 'shuffle', got 'sorted'"):
        DecisionOptionOrderConverter(order="sorted")  # type: ignore[arg-type]


async def test_convert_tokens_async_leaves_state_markers_for_state_converters(patch_central_database: MemoryInterface):
    converters = [DecisionOptionOrderConverter(), DecisionStateConverter(sub_converter=ROT13Converter())]
    prompt = _request({"route": _choice()}, state="Refund my ⟪order⟫ now")
    message = Message.from_prompt(prompt=prompt, role="user")

    await PromptNormalizer().convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=converters)], message=message
    )

    converted = json.loads(message.message_pieces[0].converted_value)
    assert converted["state"] == "Refund my beqre now"
    assert list(converted["questions"]["route"]["criteria"]) == list(_OPTIONS)[::-1]


async def test_convert_tokens_async_ignores_keep_tokens():
    prompt = _request({"route": _choice()})

    result = await DecisionOptionOrderConverter().convert_tokens_async(prompt=prompt, keep_tokens=True)

    assert _order(result.output_text) == list(_OPTIONS)[::-1]


def test_identifier_includes_order_and_seed():
    identifier = DecisionOptionOrderConverter(order="shuffle", seed=5).get_identifier()

    assert identifier.params["order"] == "shuffle"
    assert identifier.params["seed"] == 5
    assert identifier.hash != DecisionOptionOrderConverter().get_identifier().hash
