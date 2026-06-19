# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack.component.adversarial_conversation_manager import (
    AdversarialConversationManager,
    AdversarialReply,
    _build_adversarial_prompt_metadata,
    _parse_adversarial_reply,
)
from pyrit.models import JSON_SCHEMA_METADATA_KEY, ComponentIdentifier, Message, SeedPrompt
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget

pytestmark = pytest.mark.usefixtures("patch_central_database")

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "next_message": {"type": "string"},
        "rationale": {"type": "string"},
        "last_response_summary": {"type": "string"},
    },
    "required": ["next_message", "rationale", "last_response_summary"],
    "additionalProperties": False,
}

VALID_JSON = (
    '{"next_message": "hello target", "rationale": "build rapport", "last_response_summary": "no prior response"}'
)


def _seed_prompt(*, schema: dict | None) -> MagicMock:
    sp = MagicMock(spec=SeedPrompt)
    sp.response_json_schema = schema
    sp.render_template_value_silent.return_value = "rendered first turn"
    return sp


def _mock_normalizer(return_text: str | None) -> MagicMock:
    normalizer = MagicMock(spec=PromptNormalizer)
    if return_text is None:
        response = None
    else:
        response = MagicMock()
        response.get_value.return_value = return_text
    normalizer.send_prompt_async = AsyncMock(return_value=response)
    return normalizer


def _mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockChat", class_module="test_module")
    return target


# --- _build_adversarial_prompt_metadata --------------------------------------


def test_build_metadata_returns_empty_without_schema():
    assert _build_adversarial_prompt_metadata(response_json_schema=None) == {}


def test_build_metadata_forwards_schema():
    metadata = _build_adversarial_prompt_metadata(response_json_schema=SCHEMA)
    assert metadata["response_format"] == "json"
    assert metadata[JSON_SCHEMA_METADATA_KEY] is SCHEMA


# --- _parse_adversarial_reply ------------------------------------------------


def test_parse_reply_happy_path():
    reply = _parse_adversarial_reply(VALID_JSON)
    assert reply.next_message == "hello target"
    assert reply.rationale == "build rapport"
    assert reply.last_response_summary == "no prior response"
    assert reply.raw == VALID_JSON


def test_parse_reply_normalizes_camel_case():
    camel = '{"nextMessage": "hi", "rationale": "r", "lastResponseSummary": "s"}'
    reply = _parse_adversarial_reply(camel)
    assert reply.next_message == "hi"
    assert reply.last_response_summary == "s"


def test_parse_reply_strips_markdown_fences():
    wrapped = f"```json\n{VALID_JSON}\n```"
    reply = _parse_adversarial_reply(wrapped)
    assert reply.next_message == "hello target"


def test_parse_reply_invalid_json_raises():
    with pytest.raises(InvalidJsonException):
        _parse_adversarial_reply("not json at all")


def test_parse_reply_missing_key_raises():
    with pytest.raises(InvalidJsonException, match="Missing required keys"):
        _parse_adversarial_reply('{"next_message": "hi", "rationale": "r"}')


def test_parse_reply_extra_key_raises():
    extra = '{"next_message": "hi", "rationale": "r", "last_response_summary": "s", "surprise": "x"}'
    with pytest.raises(InvalidJsonException, match="Unexpected keys"):
        _parse_adversarial_reply(extra)


# --- AdversarialConversationManager init / schema resolution -----------------


def test_init_resolves_schema_from_system_prompt():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=SCHEMA),
        seed_prompt=_seed_prompt(schema=None),
    )
    assert manager.has_schema is True
    assert manager.response_json_schema is SCHEMA


def test_init_resolves_schema_from_seed_prompt():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        seed_prompt=_seed_prompt(schema=SCHEMA),
    )
    assert manager.response_json_schema is SCHEMA


def test_init_no_schema_is_raw_path():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
    )
    assert manager.has_schema is False
    assert manager.response_json_schema is None


def test_init_raises_when_both_declare_schema():
    with pytest.raises(ValueError, match="only one of them"):
        AdversarialConversationManager(
            target=_mock_target(),
            system_prompt=_seed_prompt(schema=SCHEMA),
            seed_prompt=_seed_prompt(schema=SCHEMA),
        )


def test_explicit_schema_override_wins():
    override: dict = {"type": "object"}
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        response_json_schema=override,
    )
    assert manager.response_json_schema is override


def test_init_generates_conversation_id_when_omitted():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
    )
    assert manager.conversation_id
    explicit = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        conversation_id="conv-9",
    )
    assert explicit.conversation_id == "conv-9"


# --- seed prompt rendering ---------------------------------------------------


def test_render_seed_prompt_renders_objective():
    seed = _seed_prompt(schema=None)
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        seed_prompt=seed,
        objective="do thing",
    )
    assert manager._render_seed_prompt() == "rendered first turn"
    seed.render_template_value_silent.assert_called_once_with(objective="do thing")


def test_render_seed_prompt_without_seed_raises():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        seed_prompt=None,
        objective="x",
    )
    with pytest.raises(ValueError, match="No seed prompt configured"):
        manager._render_seed_prompt()


def test_render_seed_prompt_without_objective_raises():
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        seed_prompt=_seed_prompt(schema=None),
    )
    with pytest.raises(ValueError, match="No objective configured"):
        manager._render_seed_prompt()


# --- get_next_message_async --------------------------------------------------


async def _send(manager: AdversarialConversationManager) -> AdversarialReply:
    return await manager.get_next_message_async(objective_target_response="adversarial turn")


async def test_get_next_message_raw_path_returns_raw_text():
    normalizer = _mock_normalizer("just raw adversarial text")
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        prompt_normalizer=normalizer,
    )

    reply = await _send(manager)

    assert reply.next_message == "just raw adversarial text"
    assert reply.rationale is None
    sent_message = normalizer.send_prompt_async.call_args.kwargs["message"]
    piece = sent_message.message_pieces[0]
    assert JSON_SCHEMA_METADATA_KEY not in (piece.prompt_metadata or {})


async def test_get_next_message_schema_path_forwards_metadata_and_parses():
    normalizer = _mock_normalizer(VALID_JSON)
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=SCHEMA),
        prompt_normalizer=normalizer,
    )

    reply = await _send(manager)

    assert reply.next_message == "hello target"
    assert reply.rationale == "build rapport"
    sent_message = normalizer.send_prompt_async.call_args.kwargs["message"]
    piece = sent_message.message_pieces[0]
    assert piece.prompt_metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA


async def test_get_next_message_renders_seed_when_no_prompt():
    normalizer = _mock_normalizer("raw text")
    seed = _seed_prompt(schema=None)
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        seed_prompt=seed,
        objective="do thing",
        prompt_normalizer=normalizer,
    )

    reply = await manager.get_next_message_async()

    assert reply.next_message == "raw text"
    seed.render_template_value_silent.assert_called_once_with(objective="do thing")
    sent_message = normalizer.send_prompt_async.call_args.kwargs["message"]
    assert sent_message.message_pieces[0].converted_value == "rendered first turn"


async def test_get_next_message_no_response_raises():
    normalizer = _mock_normalizer(None)
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=None),
        prompt_normalizer=normalizer,
    )

    with pytest.raises(ValueError, match="No response received from adversarial chat"):
        await _send(manager)


async def test_get_next_message_schema_path_invalid_reply_raises():
    normalizer = _mock_normalizer("totally not json")
    manager = AdversarialConversationManager(
        target=_mock_target(),
        system_prompt=_seed_prompt(schema=SCHEMA),
        prompt_normalizer=normalizer,
    )

    with pytest.raises(InvalidJsonException):
        await _send(manager)


def test_adversarial_reply_is_message_constructible():
    # Guards that next_message round-trips into a user Message for the objective target.
    reply = _parse_adversarial_reply(VALID_JSON)
    message = Message.from_prompt(prompt=reply.next_message, role="user")
    assert message.get_value() == "hello target"
