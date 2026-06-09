# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import fields

import pytest

from pyrit.message_normalizer import (
    GenericSystemSquashNormalizer,
    HistorySquashNormalizer,
    JsonSchemaNormalizer,
)
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


@pytest.fixture
def adapt_all_policy():
    return CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.MULTI_MESSAGE_PIECES: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.EDITABLE_HISTORY: UnsupportedCapabilityBehavior.RAISE,
        }
    )


@pytest.fixture
def make_message():
    def _make(role: ChatMessageRole, content: str) -> Message:
        return Message(message_pieces=[MessagePiece(role=role, original_value=content)])

    return _make


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_init_with_defaults_uses_raise_policy():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    # Default policy is RAISE for all adaptable capabilities
    assert config.policy.get_behavior(capability=CapabilityName.MULTI_TURN) == UnsupportedCapabilityBehavior.RAISE


def test_init_with_explicit_policy(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    assert config.policy is adapt_all_policy


def test_init_all_supported_empty_pipeline(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    assert config.pipeline.normalizers == ()


def test_init_missing_capability_adapt_builds_pipeline(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    assert len(config.pipeline.normalizers) == 2
    assert isinstance(config.pipeline.normalizers[0], GenericSystemSquashNormalizer)
    assert isinstance(config.pipeline.normalizers[1], HistorySquashNormalizer)


def test_init_missing_capability_raise_policy_skips_normalizer():
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True)
    config = TargetConfiguration(
        capabilities=caps,
        policy=CapabilityHandlingPolicy(
            behaviors={
                CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            }
        ),
    )
    # RAISE policy: pipeline construction succeeds but no normalizer is added for missing capabilities.
    # Validation is deferred to ensure_can_handle().
    assert len(config.pipeline.normalizers) == 0


def test_init_missing_json_schema_default_policy_adds_normalizer():
    # Default policy adapts JSON_SCHEMA; a target lacking native support gets the JSON-schema normalizer.
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    assert len(config.pipeline.normalizers) == 1
    assert isinstance(config.pipeline.normalizers[0], JsonSchemaNormalizer)


def test_init_supports_json_schema_no_normalizer():
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_system_prompt=True,
        supports_json_schema=True,
    )
    config = TargetConfiguration(capabilities=caps)
    assert config.pipeline.normalizers == ()


async def test_pipeline_strips_json_schema_for_non_schema_target():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)

    piece = MessagePiece(
        role="user",
        original_value="score this",
        prompt_metadata={"json_schema": {"type": "object"}, "response_format": "json"},
    )
    result = await config.pipeline.normalize_async(messages=[Message(message_pieces=[piece])])

    out_piece = result[0].message_pieces[0]
    metadata = out_piece.prompt_metadata
    assert "json_schema" not in metadata
    assert metadata["response_format"] == "json"
    # For a text piece, the JSON-schema normalizer also injects schema instructions
    # into the prompt text so the model is still nudged toward conforming JSON output.
    assert "### Response format" in out_piece.converted_value


async def test_pipeline_keeps_json_schema_for_schema_target():
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_system_prompt=True,
        supports_json_schema=True,
    )
    config = TargetConfiguration(capabilities=caps)

    piece = MessagePiece(
        role="user",
        original_value="score this",
        prompt_metadata={"json_schema": {"type": "object"}, "response_format": "json"},
    )
    result = await config.pipeline.normalize_async(messages=[Message(message_pieces=[piece])])

    metadata = result[0].message_pieces[0].prompt_metadata
    assert metadata["json_schema"] == {"type": "object"}


def test_init_sparse_policy_missing_json_schema_no_normalizer():
    # A custom policy's behaviors map is sparse: an omitted capability is treated
    # as RAISE (it is NOT merged with the default ADAPT policy). So a policy that
    # omits JSON_SCHEMA adds no strip normalizer, and construction does not raise.
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    policy = CapabilityHandlingPolicy(behaviors={CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE})
    config = TargetConfiguration(capabilities=caps, policy=policy)
    assert config.pipeline.normalizers == ()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_capabilities_property():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    assert config.capabilities is caps


# ---------------------------------------------------------------------------
# supports
# ---------------------------------------------------------------------------


def test_includes_returns_true_when_supported(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    assert config.includes(capability=CapabilityName.MULTI_TURN) is True


def test_includes_returns_false_when_unsupported(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    assert config.includes(capability=CapabilityName.MULTI_TURN) is False


# ---------------------------------------------------------------------------
# ensure_can_handle
# ---------------------------------------------------------------------------


def test_ensure_can_handle_passes_when_supported():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    # Should not raise
    config.ensure_can_handle(capability=CapabilityName.MULTI_TURN)


def test_ensure_can_handle_passes_when_adapt(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    # ADAPT policy → should not raise
    config.ensure_can_handle(capability=CapabilityName.MULTI_TURN)


def test_ensure_can_handle_raises_when_raise_policy():
    # Build with ADAPT so construction succeeds, then test ensure_can_handle() on a RAISE capability.
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=False)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    config = TargetConfiguration(capabilities=caps, policy=policy)
    # system_prompt is missing + ADAPT → ensure_can_handle passes
    config.ensure_can_handle(capability=CapabilityName.SYSTEM_PROMPT)
    # json_output is missing + RAISE → ensure_can_handle raises
    with pytest.raises(ValueError, match="RAISE"):
        config.ensure_can_handle(capability=CapabilityName.JSON_OUTPUT)


def test_ensure_can_handle_raises_when_capability_missing_from_policy():
    # A sparse policy that omits a capability the target lacks → ensure_can_handle rejects it.
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    policy = CapabilityHandlingPolicy(behaviors={CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE})
    config = TargetConfiguration(capabilities=caps, policy=policy)
    with pytest.raises(ValueError, match="no handling policy"):
        config.ensure_can_handle(capability=CapabilityName.JSON_SCHEMA)


def test_ensure_can_handle_raises_valueerror_for_non_normalizable_capability():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True, supports_editable_history=False)
    config = TargetConfiguration(capabilities=caps)
    with pytest.raises(ValueError, match="no handling policy"):
        config.ensure_can_handle(capability=CapabilityName.EDITABLE_HISTORY)


# ---------------------------------------------------------------------------
# normalize_async
# ---------------------------------------------------------------------------


async def test_normalize_async_passthrough_when_all_supported(adapt_all_policy, make_message):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    msgs = [make_message("user", "hello")]
    result = await config.normalize_async(messages=msgs)
    assert len(result) == 1
    assert result[0].message_pieces[0].converted_value == "hello"


async def test_normalize_async_adapts_system_prompt(adapt_all_policy, make_message):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)

    msgs = [
        make_message("system", "you are helpful"),
        make_message("user", "hello"),
    ]
    result = await config.normalize_async(messages=msgs)
    # System squash merges system into user messages — no system role left
    for msg in result:
        for piece in msg.message_pieces:
            assert piece.api_role != "system"


async def test_normalize_async_adapts_multi_turn(adapt_all_policy, make_message):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)

    msgs = [
        make_message("user", "turn 1"),
        make_message("assistant", "reply 1"),
        make_message("user", "turn 2"),
    ]
    result = await config.normalize_async(messages=msgs)
    # History squash collapses into a single message
    assert len(result) == 1
    assert "[Conversation History]" in result[0].message_pieces[0].converted_value
    assert "turn 2" in result[0].message_pieces[0].converted_value


# ---------------------------------------------------------------------------
# TargetConfiguration._capabilities_to_identifier_params
# ---------------------------------------------------------------------------


def test_capabilities_to_identifier_params_includes_all_fields():
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        supports_json_schema=False,
        supports_json_output=True,
        supports_editable_history=False,
        supports_system_prompt=True,
    )

    params = TargetConfiguration._capabilities_to_identifier_params(caps)

    # Every dataclass field on TargetCapabilities must appear in the params.
    assert set(params.keys()) == {f.name for f in fields(caps)}


def test_capabilities_to_identifier_params_scalar_fields_passthrough():
    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_json_output=False,
        supports_system_prompt=True,
    )

    params = TargetConfiguration._capabilities_to_identifier_params(caps)

    assert params["supports_multi_turn"] is True
    assert params["supports_json_output"] is False
    assert params["supports_system_prompt"] is True


def test_capabilities_to_identifier_params_modality_sets_are_sorted():
    caps = TargetCapabilities(
        input_modalities=frozenset({frozenset({"image_path", "text"}), frozenset({"text"}), frozenset({"image_path"})}),
        output_modalities=frozenset({frozenset({"text"})}),
    )

    params = TargetConfiguration._capabilities_to_identifier_params(caps)

    assert params["input_modalities"] == [["image_path"], ["image_path", "text"], ["text"]]
    assert params["output_modalities"] == [["text"]]


def test_capabilities_to_identifier_params_is_deterministic_across_calls():
    caps = TargetCapabilities(
        input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path"}), frozenset({"text", "image_path"})}),
    )

    first = TargetConfiguration._capabilities_to_identifier_params(caps)
    second = TargetConfiguration._capabilities_to_identifier_params(caps)

    assert first == second


def test_capabilities_to_identifier_params_equal_caps_produce_equal_params():
    caps_a = TargetCapabilities(
        supports_multi_turn=True,
        input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path"})}),
    )
    caps_b = TargetCapabilities(
        supports_multi_turn=True,
        input_modalities=frozenset({frozenset({"image_path"}), frozenset({"text"})}),
    )

    assert TargetConfiguration._capabilities_to_identifier_params(
        caps_a
    ) == TargetConfiguration._capabilities_to_identifier_params(caps_b)


def test_capabilities_to_identifier_params_differing_caps_produce_differing_params():
    caps_a = TargetCapabilities(supports_json_output=True)
    caps_b = TargetCapabilities(supports_json_output=False)

    assert TargetConfiguration._capabilities_to_identifier_params(
        caps_a
    ) != TargetConfiguration._capabilities_to_identifier_params(caps_b)
