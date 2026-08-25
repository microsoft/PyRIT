# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack.component.adversarial_conversation_manager import (
    _BLOCKED_FEEDBACK_TEXT,
    _DEFAULT_ADVERSARIAL_SCHEMA_NAME,
    _EMPTY_FEEDBACK_TEXT,
    AdversarialReply,
    AdversarialTurn,
    _AdversarialConversationManager,
    _build_adversarial_feedback_text,
    _build_adversarial_prompt_metadata,
    _parse_adversarial_reply,
)
from pyrit.executor.attack.core.attack_config import (
    DEFAULT_ADVERSARIAL_FIRST_MESSAGE,
    DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE,
    AttackAdversarialConfig,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
    get_common_json_schema,
)
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

OTHER_SCHEMA: dict = {"type": "object", "properties": {"next_message": {"type": "string"}}}

# A schema that does not declare the ``next_message`` property the attack loop consumes.
SCHEMA_WITHOUT_NEXT_MESSAGE: dict = {"type": "object", "properties": {"rationale": {"type": "string"}}}

VALID_JSON = (
    '{"next_message": "hello target", "rationale": "build rapport", "last_response_summary": "no prior response"}'
)


# --- factories ---------------------------------------------------------------


def _target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockChat", class_module="test_module")
    return target


def _system_prompt(value: str = "system {{ objective }}", *, schema: dict | None = None) -> SeedPrompt:
    return SeedPrompt(value=value, data_type="text", response_json_schema=schema, is_jinja_template=True)


def _first_message(value: str = "open {{ objective }}", *, schema: dict | None = None) -> SeedPrompt:
    return SeedPrompt(value=value, data_type="text", response_json_schema=schema, is_jinja_template=True)


def _per_turn(value: str = "{{ feedback_text }}") -> SeedPrompt:
    return SeedPrompt(value=value, data_type="text", is_jinja_template=True)


def _normalizer(return_text: str | None) -> MagicMock:
    normalizer = MagicMock(spec=PromptNormalizer)
    response = None if return_text is None else Message.from_prompt(prompt=return_text, role="assistant")
    normalizer.send_prompt_async = AsyncMock(return_value=response)
    return normalizer


def _response_message(value: str = "target said hi", *, data_type: str = "text", error: str = "none") -> Message:
    piece = MessagePiece(role="assistant", original_value=value, original_value_data_type=data_type)
    piece.response_error = error
    return Message(message_pieces=[piece])


def _seed_message(value: str = "seed prompt") -> Message:
    return Message(message_pieces=[MessagePiece(role="user", original_value=value, original_value_data_type="text")])


def _placeholder_seed(*, media: str = "/path/to/seed.png") -> Message:
    conversation_id = "seed-conv"
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="",
                original_value_data_type="text",
                conversation_id=conversation_id,
                prompt_metadata={"adversarial_placeholder": True},
            ),
            MessagePiece(
                role="user",
                original_value=media,
                original_value_data_type="image_path",
                conversation_id=conversation_id,
            ),
        ]
    )


def _manager(**overrides) -> _AdversarialConversationManager:
    kwargs: dict = {
        "adversarial_target": _target(),
        "adversarial_system_prompt": _system_prompt(schema=None),
        "adversarial_next_user_message": _per_turn(),
        "objective": "obj",
    }
    kwargs.update(overrides)
    return _AdversarialConversationManager(**kwargs)


# --- _build_adversarial_prompt_metadata --------------------------------------


def test_build_metadata_returns_empty_without_schema():
    assert _build_adversarial_prompt_metadata(response_json_schema=None) == {}


def test_build_metadata_forwards_schema():
    metadata = _build_adversarial_prompt_metadata(response_json_schema=SCHEMA)
    assert metadata["response_format"] == "json"
    assert metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA


# --- _parse_adversarial_reply ------------------------------------------------


def test_parse_reply_happy_path():
    reply = _parse_adversarial_reply(VALID_JSON, schema=SCHEMA)
    assert reply.next_message == "hello target"
    assert reply.rationale == "build rapport"
    assert reply.last_response_summary == "no prior response"
    assert reply.raw == VALID_JSON


def test_parse_reply_normalizes_camel_case():
    camel = '{"nextMessage": "hi", "rationale": "r", "lastResponseSummary": "s"}'
    reply = _parse_adversarial_reply(camel, schema=SCHEMA)
    assert reply.next_message == "hi"
    assert reply.last_response_summary == "s"


def test_parse_reply_strips_markdown_fences():
    wrapped = f"```json\n{VALID_JSON}\n```"
    reply = _parse_adversarial_reply(wrapped, schema=SCHEMA)
    assert reply.next_message == "hello target"


def test_parse_reply_invalid_json_raises():
    with pytest.raises(InvalidJsonException):
        _parse_adversarial_reply("not json at all", schema=SCHEMA)


@pytest.mark.parametrize(
    "response_text",
    ["[]", "null", '"text"', "42"],
    ids=["array", "null", "string", "number"],
)
def test_parse_reply_non_object_json_raises(response_text: str) -> None:
    with pytest.raises(InvalidJsonException, match="must be a JSON object"):
        _parse_adversarial_reply(response_text, schema=SCHEMA)


def test_parse_reply_missing_key_raises():
    with pytest.raises(InvalidJsonException, match="Missing required keys"):
        _parse_adversarial_reply('{"next_message": "hi", "rationale": "r"}', schema=SCHEMA)


def test_parse_reply_extra_key_raises():
    extra = '{"next_message": "hi", "rationale": "r", "last_response_summary": "s", "surprise": "x"}'
    with pytest.raises(InvalidJsonException, match="Unexpected keys"):
        _parse_adversarial_reply(extra, schema=SCHEMA)


def test_parse_reply_requires_next_message_even_without_required_list():
    # A schema with no ``required`` list still cannot omit next_message: it is the field the
    # attack loop sends to the objective target.
    with pytest.raises(InvalidJsonException, match="next_message"):
        _parse_adversarial_reply('{"surprise": "x"}', schema=OTHER_SCHEMA)


def test_parse_reply_coerces_non_string_next_message():
    # A non-enforcing target can emit a JSON number for next_message; the attack loop needs a
    # str, so the value is coerced rather than rejected (matches crescendo's own str() handling).
    numeric = '{"next_message": 42, "rationale": "r", "last_response_summary": "s"}'
    reply = _parse_adversarial_reply(numeric, schema=SCHEMA)
    assert reply.next_message == "42"


# --- init / schema resolution ------------------------------------------------


class TestManagerInit:
    def test_resolves_schema_from_system_prompt(self):
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA))
        assert manager.response_json_schema == SCHEMA

    def test_resolves_schema_from_first_message(self):
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=None),
            adversarial_first_user_message=_first_message(schema=SCHEMA),
        )
        assert manager.response_json_schema == SCHEMA

    def test_defaults_to_canonical_schema_when_none_declared(self):
        # There is no unvalidated raw-text path: when neither prompt declares a schema, the manager
        # falls back to the shared canonical adversarial_chat schema so every reply is validated.
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=None))
        assert manager.response_json_schema == get_common_json_schema(_DEFAULT_ADVERSARIAL_SCHEMA_NAME)
        assert "next_message" in manager.response_json_schema["properties"]

    def test_raises_when_both_declare_schema(self):
        with pytest.raises(ValueError, match="only one of them"):
            _manager(
                adversarial_system_prompt=_system_prompt(schema=SCHEMA),
                adversarial_first_user_message=_first_message(schema=OTHER_SCHEMA),
            )

    def test_raises_when_declared_schema_omits_next_message(self):
        # A declared schema that cannot carry next_message cannot drive the manager; fail fast at
        # construction rather than after the first adversarial round trip.
        with pytest.raises(ValueError, match="next_message"):
            _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA_WITHOUT_NEXT_MESSAGE))

    def test_conversation_id_generated_when_omitted(self):
        assert _manager().conversation_id

    def test_conversation_id_explicit_is_preserved(self):
        assert _manager(conversation_id="conv-9").conversation_id == "conv-9"

    def test_exposes_target_and_templates(self):
        target = _target()
        per_turn = _per_turn()
        first = _first_message()
        manager = _manager(
            adversarial_target=target,
            adversarial_next_user_message=per_turn,
            adversarial_first_user_message=first,
        )
        assert manager.adversarial_target is target
        assert manager.adversarial_next_user_message is per_turn
        assert manager.adversarial_first_user_message is first


# --- resolve_config (attack-facing resolution) -------------------------------


def _adversarial_config(**overrides) -> AttackAdversarialConfig:
    kwargs: dict = {"target": _target()}
    kwargs.update(overrides)
    return AttackAdversarialConfig(**kwargs)


def _param_system_prompt(*, schema: dict | None = None) -> SeedPrompt:
    # A SeedPrompt system prompt that declares the ``objective`` parameter the resolver validates.
    return SeedPrompt(
        value="system {{ objective }}",
        data_type="text",
        parameters=["objective"],
        response_json_schema=schema,
        is_jinja_template=True,
    )


class TestResolveConfig:
    """``resolve_config`` is the single owner of adversarial-prompt resolution the attacks call."""

    def test_template_mode_applies_default_first_and_next_messages(self):
        # first_message / adversarial_prompt_template unset -> the manager supplies the canonical
        # defaults so template-mode attacks never re-implement the fallbacks.
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(
                system_prompt="system {{ objective }}",
                first_message=None,
                adversarial_prompt_template=None,
            ),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
            resolve_user_messages=True,
        )
        assert resolved.first_message is not None
        assert resolved.first_message.value == DEFAULT_ADVERSARIAL_FIRST_MESSAGE
        assert resolved.first_message.is_jinja_template
        assert resolved.next_message_template is not None
        assert resolved.next_message_template.value == DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE

    def test_template_mode_coerces_string_messages(self):
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(
                system_prompt="system {{ objective }}",
                first_message="open {{ objective }}",
                adversarial_prompt_template="{{ feedback_text }}",
            ),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
            resolve_user_messages=True,
        )
        assert isinstance(resolved.first_message, SeedPrompt)
        assert resolved.first_message.value == "open {{ objective }}"
        assert isinstance(resolved.next_message_template, SeedPrompt)
        assert resolved.next_message_template.value == "{{ feedback_text }}"

    def test_template_mode_preserves_seed_prompt_messages(self):
        first = _first_message()
        per_turn = _per_turn()
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(
                system_prompt="system {{ objective }}",
                first_message=first,
                adversarial_prompt_template=per_turn,
            ),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
            resolve_user_messages=True,
        )
        assert resolved.first_message is first
        assert resolved.next_message_template is per_turn

    def test_override_mode_leaves_user_messages_none(self):
        # The config's first_message / adversarial_prompt_template default to non-None strings, but
        # override-mode attacks build the adversarial prompt themselves, so both stay None.
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(system_prompt="system {{ objective }}"),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
            resolve_user_messages=False,
        )
        assert resolved.first_message is None
        assert resolved.next_message_template is None

    def test_resolves_inline_system_prompt_string(self):
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(system_prompt="system {{ objective }}"),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
        )
        assert isinstance(resolved.system_prompt, SeedPrompt)
        assert resolved.system_prompt.value == "system {{ objective }}"

    def test_preserves_seed_prompt_system_prompt(self):
        system = _param_system_prompt()
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(system_prompt=system),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
        )
        assert resolved.system_prompt is system

    def test_raises_when_system_prompt_missing_required_parameters(self):
        with pytest.raises(ValueError, match="needs an objective"):
            _AdversarialConversationManager.resolve_config(
                config=_adversarial_config(system_prompt=_system_prompt("no parameters here", schema=None)),
                default_system_prompt_path="unused.yaml",
                system_prompt_required_parameters=["objective"],
                system_prompt_error_message="Adversarial system prompt needs an objective",
            )

    def test_raises_when_both_prompts_declare_schema(self):
        # Template mode resolves the first message, so a schema on both prompts is caught up front.
        with pytest.raises(ValueError, match="only one of them"):
            _AdversarialConversationManager.resolve_config(
                config=_adversarial_config(
                    system_prompt=_param_system_prompt(schema=SCHEMA),
                    first_message=_first_message(schema=OTHER_SCHEMA),
                ),
                default_system_prompt_path="unused.yaml",
                system_prompt_required_parameters=["objective"],
                resolve_user_messages=True,
            )

    def test_override_mode_ignores_first_message_schema_conflict(self):
        # Override mode never resolves a first message, so a schema declared there cannot conflict.
        resolved = _AdversarialConversationManager.resolve_config(
            config=_adversarial_config(
                system_prompt=_param_system_prompt(schema=SCHEMA),
                first_message=_first_message(schema=OTHER_SCHEMA),
            ),
            default_system_prompt_path="unused.yaml",
            system_prompt_required_parameters=["objective"],
            resolve_user_messages=False,
        )
        assert resolved.first_message is None

    def test_raises_on_invalid_first_message_type(self):
        with pytest.raises(ValueError, match="First message must be a string or SeedPrompt"):
            _AdversarialConversationManager.resolve_config(
                config=_adversarial_config(system_prompt="system {{ objective }}", first_message=123),
                default_system_prompt_path="unused.yaml",
                system_prompt_required_parameters=["objective"],
                resolve_user_messages=True,
            )

    def test_raises_on_invalid_next_message_type(self):
        with pytest.raises(ValueError, match="Adversarial prompt template must be a string or SeedPrompt"):
            _AdversarialConversationManager.resolve_config(
                config=_adversarial_config(
                    system_prompt="system {{ objective }}",
                    first_message="open {{ objective }}",
                    adversarial_prompt_template=123,
                ),
                default_system_prompt_path="unused.yaml",
                system_prompt_required_parameters=["objective"],
                resolve_user_messages=True,
            )


# --- set_adversarial_system_prompt -------------------------------------------


class TestSetAdversarialSystemPrompt:
    def test_renders_objective_and_max_turns_and_sets_on_conversation(self):
        target = _target()
        manager = _manager(
            adversarial_target=target,
            adversarial_system_prompt=_system_prompt("SYS obj={{ objective }} turns={{ max_turns }}"),
            objective="the goal",
            max_turns=7,
            conversation_id="conv-sys",
        )
        manager.set_adversarial_system_prompt()
        target.set_system_prompt.assert_called_once()
        kwargs = target.set_system_prompt.call_args.kwargs
        assert kwargs["system_prompt"] == "SYS obj=the goal turns=7"
        assert kwargs["conversation_id"] == "conv-sys"

    def test_empty_rendered_system_prompt_raises(self):
        manager = _manager(adversarial_system_prompt=_system_prompt("{{ objective }}"), objective="")
        with pytest.raises(ValueError, match="must be defined"):
            manager.set_adversarial_system_prompt()


# --- first-message rendering -------------------------------------------------


class TestRenderFirstMessage:
    def test_renders_objective(self):
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            objective="the goal",
        )
        assert manager._render_first_message() == "open the goal"

    def test_without_template_raises(self):
        manager = _manager(adversarial_first_user_message=None)
        with pytest.raises(ValueError, match="No first message configured"):
            manager._render_first_message()

    def test_without_objective_raises_when_needed(self):
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            objective=None,
        )
        with pytest.raises(ValueError, match="No objective configured"):
            manager._render_first_message()

    def test_renders_static_first_message_without_objective(self):
        manager = _manager(
            adversarial_first_user_message=_first_message("static opening"),
            objective=None,
        )
        assert manager._render_first_message() == "static opening"


# --- get_next_message_async: prompt selection --------------------------------


class TestGetNextMessageAsync:
    async def test_first_turn_renders_first_message(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            objective="the goal",
            prompt_normalizer=normalizer,
        )
        turn = await manager.get_next_message_async(turn_index=0, last_response=None)
        assert isinstance(turn, AdversarialTurn)
        assert turn.reply is not None and turn.reply.next_message == "hello target"
        assert turn.bypassed is False
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "open the goal"
        # The reply's next_message becomes the ready-to-send objective message.
        assert turn.objective_message.get_value() == "hello target"

    async def test_next_turn_renders_template_with_objective_and_feedback_text(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(
            adversarial_next_user_message=_per_turn("OBJ={{ objective }}|FB={{ feedback_text }}"),
            objective="my objective",
            use_score_as_feedback=True,
            prompt_normalizer=normalizer,
        )
        score = SimpleNamespace(score_value="true", score_rationale="because")
        await manager.get_next_message_async(turn_index=1, score=score, last_response=_response_message("target text"))
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "OBJ=my objective|FB=target text\n\nbecause"

    async def test_override_prompt_text_used_verbatim(self):
        # Override mode (Crescendo / TAP): the attack supplies the built adversarial prompt text and
        # the manager skips template rendering entirely (next-message template may even be None).
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA),
            adversarial_next_user_message=None,
            prompt_normalizer=normalizer,
        )
        turn = await manager.get_next_message_async(
            turn_index=2, last_response=_response_message("ignored"), adversarial_prompt_text="OVERRIDE TEXT"
        )
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "OVERRIDE TEXT"
        assert turn.reply is not None and turn.reply.next_message == "hello target"

    async def test_schema_metadata_forwarded(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=normalizer)
        await manager.get_next_message_async(turn_index=1, last_response=_response_message())
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].prompt_metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA

    async def test_no_response_raises(self):
        manager = _manager(prompt_normalizer=_normalizer(None))
        with pytest.raises(ValueError, match="No response received for conversation ID"):
            await manager.get_next_message_async(turn_index=1, last_response=_response_message())

    async def test_invalid_reply_raises(self):
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=_normalizer("totally not json")
        )
        with pytest.raises(InvalidJsonException):
            await manager.get_next_message_async(turn_index=1, last_response=_response_message())

    async def test_non_object_reply_retries_then_succeeds(self) -> None:
        normalizer = _normalizer(None)
        normalizer.send_prompt_async.side_effect = [
            Message.from_prompt(prompt="[]", role="assistant"),
            Message.from_prompt(prompt=VALID_JSON, role="assistant"),
        ]
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA),
            prompt_normalizer=normalizer,
        )

        turn = await manager.get_next_message_async(turn_index=1, last_response=_response_message())

        assert turn.reply is not None
        assert turn.reply.next_message == "hello target"
        assert normalizer.send_prompt_async.call_count == 2


# --- get_next_message_async: bypass path -------------------------------------


class TestBypass:
    async def test_seed_without_placeholder_bypasses_and_duplicates(self):
        # Regression: the red_teaming bypass path used to return the seed message without
        # ``.duplicate()`` while Crescendo/TAP duplicated it. The manager now always duplicates, so
        # the objective send gets fresh ids and the caller's seed is never mutated or double-persisted.
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(prompt_normalizer=normalizer)
        seed = _seed_message("custom seed")

        turn = await manager.get_next_message_async(turn_index=0, seed_message=seed)

        assert turn.bypassed is True
        assert turn.reply is None
        assert turn.objective_message is not seed
        assert turn.objective_message.get_value() == "custom seed"
        assert turn.objective_message.message_pieces[0].id != seed.message_pieces[0].id
        normalizer.send_prompt_async.assert_not_called()

    async def test_seed_with_placeholder_does_not_bypass(self):
        # A placeholder seed must route through the adversarial chat so the generated text fills the
        # slot; it is not a bypass.
        normalizer = _normalizer(VALID_JSON)
        router = MagicMock()
        router.fill_adversarial_placeholders.return_value = Message.from_prompt(prompt="FILLED", role="user")
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        turn = await manager.get_next_message_async(turn_index=0, seed_message=_placeholder_seed(), last_response=None)

        assert turn.bypassed is False
        normalizer.send_prompt_async.assert_called_once()


# --- get_next_message_async: objective-message construction -------------------


class TestBuildObjectiveMessage:
    async def test_text_only_without_router(self):
        manager = _manager(prompt_normalizer=_normalizer(VALID_JSON))
        turn = await manager.get_next_message_async(turn_index=1, last_response=_response_message())
        assert turn.objective_message.get_value() == "hello target"
        assert len(turn.objective_message.message_pieces) == 1

    async def test_router_builds_objective_message_with_turn_index(self):
        normalizer = _normalizer(VALID_JSON)
        router = MagicMock()
        router.build_adversarial_input_message.return_value = Message.from_prompt(prompt="ADV_SENT", role="user")
        router.build_objective_input_message.return_value = Message.from_prompt(prompt="OBJ_MSG", role="user")
        last = _response_message("last")
        manager = _manager(prompt_normalizer=normalizer, modality_router=router)

        turn = await manager.get_next_message_async(turn_index=3, last_response=last)

        router.build_objective_input_message.assert_called_once()
        kwargs = router.build_objective_input_message.call_args.kwargs
        assert kwargs["text"] == "hello target"
        assert kwargs["last_response"] is last
        assert kwargs["turn_index"] == 3
        assert turn.objective_message.get_value() == "OBJ_MSG"

    async def test_placeholder_seed_fills_via_router(self):
        normalizer = _normalizer(VALID_JSON)
        router = MagicMock()
        router.build_adversarial_input_message.return_value = Message.from_prompt(prompt="ADV_SENT", role="user")
        router.fill_adversarial_placeholders.return_value = Message.from_prompt(prompt="FILLED", role="user")
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        turn = await manager.get_next_message_async(turn_index=0, seed_message=_placeholder_seed(), last_response=None)

        router.fill_adversarial_placeholders.assert_called_once()
        assert router.fill_adversarial_placeholders.call_args.kwargs["adversarial_text"] == "hello target"
        assert turn.objective_message.get_value() == "FILLED"

    async def test_placeholder_seed_without_router_raises(self):
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            prompt_normalizer=_normalizer(VALID_JSON),
        )
        with pytest.raises(ValueError, match="requires a modality_router"):
            await manager.get_next_message_async(turn_index=0, seed_message=_placeholder_seed(), last_response=None)


# --- modality-router integration (adversarial send) --------------------------


class TestModalityRouterIntegration:
    async def test_next_turn_builds_adversarial_message_via_router(self):
        normalizer = _normalizer(VALID_JSON)
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        router.build_objective_input_message.return_value = Message.from_prompt(prompt="OBJ", role="user")
        last = _response_message("last media")
        manager = _manager(prompt_normalizer=normalizer, modality_router=router)

        await manager.get_next_message_async(turn_index=1, last_response=last)

        router.build_adversarial_input_message.assert_called_once()
        kwargs = router.build_adversarial_input_message.call_args.kwargs
        assert kwargs["last_response"] is last
        assert normalizer.send_prompt_async.call_args.kwargs["message"] is routed

    async def test_first_turn_forwards_seed_media_via_router(self):
        normalizer = _normalizer(VALID_JSON)
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        router.fill_adversarial_placeholders.return_value = Message.from_prompt(prompt="FILLED", role="user")
        seed = _placeholder_seed()
        manager = _manager(
            adversarial_first_user_message=_first_message("open {{ objective }}"),
            objective="goal",
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        await manager.get_next_message_async(turn_index=0, seed_message=seed, last_response=None)

        kwargs = router.build_adversarial_input_message.call_args.kwargs
        assert kwargs["seed_message"] is seed
        assert kwargs["last_response"] is None
        assert normalizer.send_prompt_async.call_args.kwargs["message"] is routed

    async def test_no_router_sends_text_only_message(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(
            adversarial_next_user_message=_per_turn("prompt: {{ feedback_text }}"),
            prompt_normalizer=normalizer,
        )
        await manager.get_next_message_async(turn_index=1, last_response=_response_message("hi there"))
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "prompt: hi there"


# --- generate_adversarial_reply_async (override-mode send/parse) -------------


class TestGenerateAdversarialReplyAsync:
    """The send/parse-only entry point used by attacks (TAP) that inspect the parsed reply before
    deciding what to send to the objective target. It must share the manager's schema/metadata,
    modality routing, and JSON-retry mechanics with ``get_next_message_async`` while stopping at the
    parsed reply — it must never build an objective-target message itself.
    """

    async def test_returns_parsed_reply_without_objective_message(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=normalizer)

        reply = await manager.generate_adversarial_reply_async(prompt_text="built by the attack")

        assert isinstance(reply, AdversarialReply)
        assert reply.next_message == "hello target"
        assert reply.rationale == "build rapport"
        assert reply.last_response_summary == "no prior response"

    async def test_prompt_text_used_verbatim_without_router(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=normalizer)

        await manager.generate_adversarial_reply_async(prompt_text="EXACT OVERRIDE TEXT")

        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "EXACT OVERRIDE TEXT"

    async def test_does_not_build_objective_message_even_with_router(self):
        # A router is configured, but the send/parse path must never call the objective-side builders;
        # TAP owns objective-message construction after scoring the reply.
        normalizer = _normalizer(VALID_JSON)
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA),
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        await manager.generate_adversarial_reply_async(prompt_text="x")

        router.build_objective_input_message.assert_not_called()
        router.fill_adversarial_placeholders.assert_not_called()

    async def test_forwards_media_via_router(self):
        normalizer = _normalizer(VALID_JSON)
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        seed = _placeholder_seed()
        last = _response_message("last media")
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA),
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        await manager.generate_adversarial_reply_async(prompt_text="x", seed_message=seed, last_response=last)

        kwargs = router.build_adversarial_input_message.call_args.kwargs
        assert kwargs["seed_message"] is seed
        assert kwargs["last_response"] is last
        assert normalizer.send_prompt_async.call_args.kwargs["message"] is routed

    async def test_schema_metadata_forwarded(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=normalizer)

        await manager.generate_adversarial_reply_async(prompt_text="x")

        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].prompt_metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA

    async def test_no_response_raises(self):
        manager = _manager(adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=_normalizer(None))
        with pytest.raises(ValueError, match="No response received for conversation ID"):
            await manager.generate_adversarial_reply_async(prompt_text="x")

    async def test_invalid_json_raises(self):
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=_normalizer("totally not json")
        )
        with pytest.raises(InvalidJsonException):
            await manager.generate_adversarial_reply_async(prompt_text="x")

    async def test_schemaless_prompt_still_enforces_canonical_schema(self):
        # Override-mode attacks with a schemaless custom prompt still get canonical-schema enforcement:
        # a reply missing required keys is rejected rather than silently returned.
        manager = _manager(
            adversarial_system_prompt=_system_prompt(schema=None),
            prompt_normalizer=_normalizer('{"next_message": "x"}'),
        )
        with pytest.raises(InvalidJsonException, match="Missing required keys"):
            await manager.generate_adversarial_reply_async(prompt_text="x")


# --- media-drop warning ------------------------------------------------------


class TestMediaDropWarning:
    async def test_media_only_response_without_router_warns(self, caplog):
        # A media-only objective response with no router silently degrades to a "please continue"
        # nudge; that likely means a text-only adversarial paired with an image objective, so warn.
        manager = _manager(prompt_normalizer=_normalizer(VALID_JSON))
        last = _response_message("/tmp/out.png", data_type="image_path")
        with caplog.at_level(logging.WARNING):
            await manager.get_next_message_async(turn_index=1, last_response=last)
        assert "non-text media" in caplog.text

    async def test_media_only_response_is_silent_when_router_forwards(self, caplog):
        normalizer = _normalizer(VALID_JSON)
        router = MagicMock()
        router.response_media_is_forwardable_to_adversarial.return_value = True
        router.build_adversarial_input_message.return_value = Message.from_prompt(prompt="ADV", role="user")
        router.build_objective_input_message.return_value = Message.from_prompt(prompt="OBJ", role="user")
        manager = _manager(prompt_normalizer=normalizer, modality_router=router)
        last = _response_message("/tmp/out.png", data_type="image_path")
        with caplog.at_level(logging.WARNING):
            await manager.get_next_message_async(turn_index=1, last_response=last)
        assert "non-text media" not in caplog.text


# --- round trip --------------------------------------------------------------


def test_adversarial_reply_is_message_constructible():
    # Guards that next_message round-trips into a user Message for the objective target.
    reply = _parse_adversarial_reply(VALID_JSON, schema=SCHEMA)
    message = Message.from_prompt(prompt=reply.next_message, role="user")
    assert message.get_value() == "hello target"


# --- feedback text -----------------------------------------------------------


def _feedback_score(rationale: str = "because") -> Score:
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=["test"],
        score_value_description="d",
        score_rationale=rationale,
        score_metadata={},
        message_piece_id="00000000-0000-0000-0000-000000000000",
    )


def _multi_piece_response(*specs: tuple[str, str, str]) -> Message:
    """Build a multi-piece response from ``(value, data_type, error)`` specs sharing a conversation."""
    conversation_id = "00000000-0000-0000-0000-000000000001"
    pieces = []
    for value, data_type, error in specs:
        piece = MessagePiece(
            role="assistant",
            original_value=value,
            original_value_data_type=data_type,
            conversation_id=conversation_id,
        )
        piece.response_error = error
        pieces.append(piece)
    return Message(message_pieces=pieces)


class TestBuildAdversarialFeedbackText:
    """Coverage for the per-turn feedback text the manager renders into the adversarial prompt."""

    def test_blocked_returns_rewrite_notice(self):
        message = _response_message("", error="blocked")
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == _BLOCKED_FEEDBACK_TEXT

    def test_error_surfaces_error_code(self):
        message = _response_message("", error="processing")
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == "Request to target failed: processing"

    def test_text_passed_through(self):
        message = _response_message("hello")
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == "hello"

    def test_text_appends_rationale_when_enabled(self):
        message = _response_message("hello")
        result = _build_adversarial_feedback_text(
            last_response=message, score=_feedback_score("why"), use_score_as_feedback=True
        )
        assert result == "hello\n\nwhy"

    def test_text_ignores_rationale_when_disabled(self):
        message = _response_message("hello")
        result = _build_adversarial_feedback_text(
            last_response=message, score=_feedback_score("why"), use_score_as_feedback=False
        )
        assert result == "hello"

    def test_non_text_response_uses_rationale_only(self):
        message = _response_message("/tmp/out.png", data_type="image_path")
        result = _build_adversarial_feedback_text(
            last_response=message, score=_feedback_score("why"), use_score_as_feedback=True
        )
        assert result == "why"

    def test_empty_response_nudges_to_continue(self):
        message = _response_message("/tmp/out.png", data_type="image_path")
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == _EMPTY_FEEDBACK_TEXT

    def test_blocked_piece_after_clean_piece_is_detected(self):
        """A blocked later piece is not masked by an earlier clean text piece (any-piece semantics)."""
        message = _multi_piece_response(("some text", "text", "none"), ("", "text", "blocked"))
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == _BLOCKED_FEEDBACK_TEXT

    def test_error_piece_after_clean_piece_is_detected(self):
        """An errored later piece is not masked by an earlier clean piece (any-piece semantics)."""
        message = _multi_piece_response(("some text", "text", "none"), ("", "text", "processing"))
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == "Request to target failed: processing"

    def test_multiple_text_pieces_are_joined(self):
        message = _multi_piece_response(("first", "text", "none"), ("second", "text", "none"))
        result = _build_adversarial_feedback_text(last_response=message, score=None, use_score_as_feedback=False)
        assert result == "first\nsecond"
