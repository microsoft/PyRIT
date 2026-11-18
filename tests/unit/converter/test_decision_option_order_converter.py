# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import DecisionOptionOrderConverter
from pyrit.memory import MemoryInterface
from pyrit.models import Message
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.registry.resolution import resolve_constructor_args

_OPTIONS = {
    "billing": "A question about a charge or invoice.",
    "refund": "The customer asks for money back.",
    "technical": "Something in the product does not work.",
    "account": "Login, password, or profile changes.",
    "other": "Anything else.",
}

_LONE_SURROGATE_REQUEST = (
    r'{"state": {"te\ud800xt": "a\udc00b"}, "questions": {"route": {"type": "choice", '
    r'"criteria": {"bill\ud83d": "charge\ude00", "refund": "money"}}}}'
)


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

    result = await DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE, seed=1).convert_async(
        prompt=prompt
    )

    converted = json.loads(result.output_text)
    original = json.loads(prompt)
    assert converted["state"] == original["state"]
    assert converted["questions"]["route"]["criteria"] == original["questions"]["route"]["criteria"]
    assert converted["questions"]["route"]["instructions"] == original["questions"]["route"]["instructions"]
    assert "Ⓗello" in result.output_text


@pytest.mark.parametrize("seed", range(20))
async def test_shuffle_always_changes_order(seed: int):
    result = await DecisionOptionOrderConverter(
        order=DecisionOptionOrderConverter.Order.SHUFFLE, seed=seed
    ).convert_async(prompt=_request({"route": _choice()}))

    order = _order(result.output_text)
    assert order != list(_OPTIONS)
    assert sorted(order) == sorted(_OPTIONS)


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("order", list(DecisionOptionOrderConverter.Order))
async def test_two_options_are_swapped(order: DecisionOptionOrderConverter.Order, seed: int):
    options = {"yes": "Escalate to a human.", "no": "Do not escalate."}

    result = await DecisionOptionOrderConverter(order=order, seed=seed).convert_async(
        prompt=_request({"route": _choice(options)})
    )

    assert _order(result.output_text) == ["no", "yes"]


async def test_shuffle_seed_is_reproducible():
    prompt = _request({"route": _choice()})

    first = await DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE, seed=7).convert_async(
        prompt=prompt
    )
    second = await DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE, seed=7).convert_async(
        prompt=prompt
    )

    assert first.output_text == second.output_text


async def test_shuffle_uses_configured_root_seed():
    prompt = _request({"route": _choice()})

    configure_random_seed(seed=11)
    try:
        first = await DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE).convert_async(
            prompt=prompt
        )
        second = await DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE).convert_async(
            prompt=prompt
        )
    finally:
        configure_random_seed(seed=None)

    assert first.output_text == second.output_text


async def test_reorders_every_choice_question_and_leaves_other_types():
    questions = {
        "route": _choice(),
        "priority": _choice({"low": "Can wait.", "high": "Urgent."}),
        "is_spam": {"type": "noul", "criteria": {"true": "It is spam.", "false": "It is not spam."}},
        "severity": {"type": "score", "instructions": "How severe?", "criteria": ["none", "low", "high"]},
        "single": _choice({"only": "The only option."}),
    }

    result = await DecisionOptionOrderConverter().convert_async(prompt=_request(questions))

    converted = json.loads(result.output_text)["questions"]
    assert list(converted["route"]["criteria"]) == list(_OPTIONS)[::-1]
    assert list(converted["priority"]["criteria"]) == ["high", "low"]
    assert converted["is_spam"] == questions["is_spam"]
    assert list(converted["is_spam"]["criteria"]) == ["true", "false"]
    assert converted["severity"] == questions["severity"]
    assert converted["single"] == questions["single"]


@pytest.mark.parametrize(
    "questions",
    [
        {"is_spam": {"type": "noul", "instructions": "Is this spam?"}},
        {"is_spam": {"type": "noul", "criteria": {"true": "It is spam.", "false": "It is not spam."}}},
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


@pytest.mark.parametrize("order", ["reverse", "sorted", None])
def test_init_rejects_order_that_is_not_the_enum(order: object):
    with pytest.raises(TypeError, match="order must be a DecisionOptionOrderConverter.Order"):
        DecisionOptionOrderConverter(order=order)  # type: ignore[arg-type]


async def test_convert_tokens_async_leaves_state_markers_in_place(patch_central_database: MemoryInterface):
    prompt = _request({"route": _choice()}, state="Refund my ⟪order⟫ now")
    message = Message.from_prompt(prompt=prompt, role="user")

    await PromptNormalizer().convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=[DecisionOptionOrderConverter()])],
        message=message,
    )

    converted = json.loads(message.message_pieces[0].converted_value)
    assert converted["state"] == "Refund my ⟪order⟫ now"
    assert list(converted["questions"]["route"]["criteria"]) == list(_OPTIONS)[::-1]


async def test_convert_tokens_async_ignores_keep_tokens():
    prompt = _request({"route": _choice()})

    result = await DecisionOptionOrderConverter().convert_tokens_async(prompt=prompt, keep_tokens=True)

    assert _order(result.output_text) == list(_OPTIONS)[::-1]


async def test_lone_surrogates_stay_escaped_everywhere_in_the_request():
    result = await DecisionOptionOrderConverter().convert_async(prompt=_LONE_SURROGATE_REQUEST)

    assert result.output_text == (
        r'{"state": {"te\ud800xt": "a\udc00b"}, "questions": {"route": {"type": "choice", '
        r'"criteria": {"refund": "money", "bill\ud83d": "charge\ude00"}}}}'
    )
    result.output_text.encode("utf-8")


async def test_valid_text_is_not_escaped_with_the_lone_surrogates():
    prompt = _request({"route": _choice()}, state="placeholder").replace(
        '"placeholder"', r'"\\ud800 \ud83d\ude00 é \ud800"'
    )

    result = await DecisionOptionOrderConverter().convert_async(prompt=prompt)

    assert r'"state": "\\ud800 😀 é \ud800"' in result.output_text


async def test_lone_surrogate_request_is_stored_after_conversion(
    patch_central_database: MemoryInterface, sqlite_instance: MemoryInterface
):
    message = Message.from_prompt(prompt=_LONE_SURROGATE_REQUEST, role="user")
    await PromptNormalizer().convert_values_async(
        converter_configurations=[ConverterConfiguration(converters=[DecisionOptionOrderConverter()])],
        message=message,
    )

    message.message_pieces[0].conversation_id = "lone-surrogate"
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=message.message_pieces)

    stored = await sqlite_instance.get_message_pieces_async(conversation_id="lone-surrogate")
    assert json.loads(stored[0].converted_value) == json.loads(message.message_pieces[0].converted_value)
    assert json.loads(stored[0].converted_value)["state"] == {"te\ud800xt": "a\udc00b"}


def test_identifier_includes_order_and_seed():
    identifier = DecisionOptionOrderConverter(order=DecisionOptionOrderConverter.Order.SHUFFLE, seed=5).get_identifier()

    assert identifier.params["order"] == "shuffle"
    assert identifier.params["seed"] == 5
    assert identifier.hash != DecisionOptionOrderConverter().get_identifier().hash


async def test_output_reaches_the_decision_sdk_as_a_valid_request(patch_central_database: MemoryInterface):
    litellm = pytest.importorskip("litellm")
    from litellm.types.decisions import ChoiceQuestion, DecisionsResponse

    from pyrit.prompt_target import LiteLLMDecisionTarget

    sdk = MagicMock(spec=litellm)
    sdk.asystemone = None
    sdk.adecisions = AsyncMock(
        return_value=DecisionsResponse.model_validate({"answers": {"urgent": {"type": "noul", "noul": 0.2}}})
    )
    prompt = _request({"route": _choice(), "urgent": {"type": "noul", "instructions": "Is this urgent?"}})
    converted = await DecisionOptionOrderConverter().convert_async(prompt=prompt)

    with patch.object(LiteLLMDecisionTarget, "_import_litellm", return_value=sdk):
        target = LiteLLMDecisionTarget(model_name="provider/decision-model", api_key="test-api-key")
        await target.send_prompt_async(message=Message.from_prompt(prompt=converted.output_text, role="user"))

    questions = sdk.adecisions.call_args.kwargs["questions"]
    assert list(ChoiceQuestion.model_validate(questions["route"]).criteria) == list(_OPTIONS)[::-1]
    assert questions["urgent"] == json.loads(prompt)["questions"]["urgent"]


@pytest.mark.parametrize("raw", ["shuffle", "SHUFFLE", DecisionOptionOrderConverter.Order.SHUFFLE])
def test_registry_resolves_order_from_raw_values(raw: object):
    args = resolve_constructor_args(cls=DecisionOptionOrderConverter, raw_args={"order": raw})

    assert DecisionOptionOrderConverter(**args).get_identifier().params["order"] == "shuffle"
