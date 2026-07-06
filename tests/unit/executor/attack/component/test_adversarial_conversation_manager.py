# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack.component.adversarial_conversation_manager import (
    _BLOCKED_FEEDBACK_TEXT,
    _EMPTY_FEEDBACK_TEXT,
    AdversarialConversationManager,
    _build_adversarial_feedback_text,
    _build_adversarial_prompt_metadata,
    _parse_adversarial_reply,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
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

VALID_JSON = (
    '{"next_message": "hello target", "rationale": "build rapport", "last_response_summary": "no prior response"}'
)


# --- factories ---------------------------------------------------------------


def _target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockChat", class_module="test_module")
    return target


def _system_prompt(*, schema: dict | None = None) -> SeedPrompt:
    return SeedPrompt(
        value="system {{ objective }}", data_type="text", response_json_schema=schema, is_jinja_template=True
    )


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


def _manager(**overrides) -> AdversarialConversationManager:
    kwargs: dict = {
        "adversarial_target": _target(),
        "system_prompt": _system_prompt(schema=None),
        "adversarial_prompt_template": _per_turn(),
        "objective": "obj",
    }
    kwargs.update(overrides)
    return AdversarialConversationManager(**kwargs)


# --- _build_adversarial_prompt_metadata --------------------------------------


def test_build_metadata_returns_empty_without_schema():
    assert _build_adversarial_prompt_metadata(response_json_schema=None) == {}


def test_build_metadata_forwards_schema():
    metadata = _build_adversarial_prompt_metadata(response_json_schema=SCHEMA)
    assert metadata["response_format"] == "json"
    assert metadata[JSON_SCHEMA_METADATA_KEY] is SCHEMA


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
        manager = _manager(system_prompt=_system_prompt(schema=SCHEMA))
        assert manager.has_schema is True
        assert manager.response_json_schema == SCHEMA

    def test_resolves_schema_from_first_message(self):
        manager = _manager(
            system_prompt=_system_prompt(schema=None),
            first_message=_first_message(schema=SCHEMA),
        )
        assert manager.has_schema is True
        assert manager.response_json_schema == SCHEMA

    def test_no_schema_is_raw_path(self):
        manager = _manager(system_prompt=_system_prompt(schema=None))
        assert manager.has_schema is False
        assert manager.response_json_schema is None

    def test_raises_when_both_declare_schema(self):
        with pytest.raises(ValueError, match="only one of them"):
            _manager(
                system_prompt=_system_prompt(schema=SCHEMA),
                first_message=_first_message(schema=OTHER_SCHEMA),
            )

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
            adversarial_prompt_template=per_turn,
            first_message=first,
        )
        assert manager.adversarial_target is target
        assert manager.adversarial_prompt_template is per_turn
        assert manager.first_message is first


# --- first-message rendering -------------------------------------------------


class TestRenderFirstMessage:
    def test_renders_objective(self):
        manager = _manager(
            first_message=_first_message("open {{ objective }}"),
            objective="the goal",
        )
        assert manager._render_first_message() == "open the goal"

    def test_without_template_raises(self):
        manager = _manager(first_message=None)
        with pytest.raises(ValueError, match="No first message configured"):
            manager._render_first_message()

    def test_without_objective_raises_when_needed(self):
        manager = _manager(
            first_message=_first_message("open {{ objective }}"),
            objective=None,
        )
        with pytest.raises(ValueError, match="No objective configured"):
            manager._render_first_message()

    def test_renders_static_first_message_without_objective(self):
        manager = _manager(
            first_message=_first_message("static opening"),
            objective=None,
        )
        assert manager._render_first_message() == "static opening"


# --- get_first_message_async -------------------------------------------------


class TestGetFirstMessageAsync:
    async def test_raw_path_sends_rendered_first_message(self):
        normalizer = _normalizer("raw opening")
        manager = _manager(
            first_message=_first_message("open {{ objective }}"),
            objective="the goal",
            prompt_normalizer=normalizer,
        )
        reply = await manager.get_first_message_async()
        assert reply.next_message == "raw opening"
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "open the goal"

    async def test_schema_path_parses_and_forwards_metadata(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(
            system_prompt=_system_prompt(schema=None),
            first_message=_first_message("open {{ objective }}", schema=SCHEMA),
            objective="the goal",
            prompt_normalizer=normalizer,
        )
        reply = await manager.get_first_message_async()
        assert reply.next_message == "hello target"
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].prompt_metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA

    async def test_no_template_raises(self):
        manager = _manager(first_message=None, prompt_normalizer=_normalizer("x"))
        with pytest.raises(ValueError, match="No first message configured"):
            await manager.get_first_message_async()


# --- get_next_message_async --------------------------------------------------


class TestGetNextMessageAsync:
    async def test_raw_path_returns_raw_text_and_no_metadata(self):
        normalizer = _normalizer("just raw adversarial text")
        manager = _manager(system_prompt=_system_prompt(schema=None), prompt_normalizer=normalizer)
        reply = await manager.get_next_message_async(score=None, last_response=_response_message())
        assert reply.next_message == "just raw adversarial text"
        assert reply.rationale is None
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert not (sent.message_pieces[0].prompt_metadata or {})

    async def test_schema_path_forwards_metadata_and_parses(self):
        normalizer = _normalizer(VALID_JSON)
        manager = _manager(system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=normalizer)
        reply = await manager.get_next_message_async(score=None, last_response=_response_message())
        assert reply.next_message == "hello target"
        assert reply.rationale == "build rapport"
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].prompt_metadata[JSON_SCHEMA_METADATA_KEY] == SCHEMA

    async def test_renders_template_with_objective_and_feedback_text(self):
        normalizer = _normalizer("raw")
        manager = _manager(
            adversarial_prompt_template=_per_turn("OBJ={{ objective }}|FB={{ feedback_text }}"),
            objective="my objective",
            use_score_as_feedback=True,
            prompt_normalizer=normalizer,
        )
        score = SimpleNamespace(score_value="true", score_rationale="because")
        await manager.get_next_message_async(score=score, last_response=_response_message("target text"))
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "OBJ=my objective|FB=target text\n\nbecause"

    async def test_no_response_raises(self):
        manager = _manager(prompt_normalizer=_normalizer(None))
        with pytest.raises(ValueError, match="No response received from adversarial chat"):
            await manager.get_next_message_async(score=None, last_response=_response_message())

    async def test_schema_path_invalid_reply_raises(self):
        manager = _manager(
            system_prompt=_system_prompt(schema=SCHEMA), prompt_normalizer=_normalizer("totally not json")
        )
        with pytest.raises(InvalidJsonException):
            await manager.get_next_message_async(score=None, last_response=_response_message())

    async def test_raise_on_invalid_json_false_returns_raw(self):
        normalizer = _normalizer("totally not json")
        manager = _manager(
            system_prompt=_system_prompt(schema=SCHEMA),
            raise_on_invalid_json=False,
            prompt_normalizer=normalizer,
        )
        reply = await manager.get_next_message_async(score=None, last_response=_response_message())
        assert reply.next_message == "totally not json"


# --- modality-router integration ---------------------------------------------


class TestModalityRouterIntegration:
    async def test_next_turn_builds_message_via_router(self):
        normalizer = _normalizer("raw")
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        last = _response_message("last media")
        seed = _response_message("seed media")
        manager = _manager(prompt_normalizer=normalizer, modality_router=router)

        await manager.get_next_message_async(score=None, last_response=last, seed_message=seed)

        router.build_adversarial_input_message.assert_called_once()
        kwargs = router.build_adversarial_input_message.call_args.kwargs
        assert kwargs["last_response"] is last
        assert kwargs["seed_message"] is seed
        assert normalizer.send_prompt_async.call_args.kwargs["message"] is routed

    async def test_first_turn_forwards_seed_media_via_router(self):
        normalizer = _normalizer("raw")
        routed = Message.from_prompt(prompt="ROUTED", role="user")
        router = MagicMock()
        router.build_adversarial_input_message.return_value = routed
        seed = _response_message("seed media")
        manager = _manager(
            first_message=_first_message("open {{ objective }}"),
            objective="goal",
            prompt_normalizer=normalizer,
            modality_router=router,
        )

        await manager.get_first_message_async(seed_message=seed)

        kwargs = router.build_adversarial_input_message.call_args.kwargs
        assert kwargs["seed_message"] is seed
        assert kwargs["last_response"] is None
        assert normalizer.send_prompt_async.call_args.kwargs["message"] is routed

    async def test_no_router_sends_text_only_message(self):
        normalizer = _normalizer("raw")
        manager = _manager(
            adversarial_prompt_template=_per_turn("prompt: {{ feedback_text }}"),
            prompt_normalizer=normalizer,
        )
        await manager.get_next_message_async(score=None, last_response=_response_message("hi there"))
        sent = normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent.message_pieces[0].converted_value == "prompt: hi there"


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
