# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer import GenericSystemSquashNormalizer, HistorySquashNormalizer
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


def test_init_missing_capability_raise_policy_raises():
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True)
    with pytest.raises(ValueError, match="RAISE"):
        TargetConfiguration(capabilities=caps)


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
    # JSON_SCHEMA is RAISE and unsupported — but it's not normalizable, so construction
    # doesn't try to build a normalizer for it. Use a custom policy where system_prompt
    # is ADAPT (so pipeline builds), but then call ensure_can_handle() on JSON_OUTPUT which is RAISE.
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


def test_ensure_can_handle_raises_valueerror_for_non_normalizable_capability():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True, supports_editable_history=False)
    config = TargetConfiguration(capabilities=caps)
    with pytest.raises(ValueError, match="no handling policy"):
        config.ensure_can_handle(capability=CapabilityName.EDITABLE_HISTORY)


# ---------------------------------------------------------------------------
# normalize_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_async_passthrough_when_all_supported(adapt_all_policy, make_message):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    msgs = [make_message("user", "hello")]
    result = await config.normalize_async(messages=msgs)
    assert len(result) == 1
    assert result[0].message_pieces[0].converted_value == "hello"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
