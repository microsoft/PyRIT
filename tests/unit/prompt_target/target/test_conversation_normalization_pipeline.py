# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.message_normalizer import GenericSystemSquashNormalizer, HistorySquashNormalizer, MessageListNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole
from pyrit.prompt_target.common.conversation_normalization_pipeline import ConversationNormalizationPipeline
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)


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
def raise_all_policy():
    return CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
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
# Construction — from_capabilities
# ---------------------------------------------------------------------------


def test_from_capabilities_all_supported_empty_tuple(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=adapt_all_policy)
    assert pipeline.normalizers == ()


def test_from_capabilities_none_supported_has_two_normalizers(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=adapt_all_policy)
    assert len(pipeline.normalizers) == 2
    assert isinstance(pipeline.normalizers[0], GenericSystemSquashNormalizer)
    assert isinstance(pipeline.normalizers[1], HistorySquashNormalizer)


def test_from_capabilities_missing_system_prompt_only():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=False)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=policy)
    assert len(pipeline.normalizers) == 1
    assert isinstance(pipeline.normalizers[0], GenericSystemSquashNormalizer)


def test_from_capabilities_missing_multi_turn_only():
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=policy)
    assert len(pipeline.normalizers) == 1
    assert isinstance(pipeline.normalizers[0], HistorySquashNormalizer)


def test_from_capabilities_normalizers_is_tuple(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=adapt_all_policy)
    assert isinstance(pipeline.normalizers, tuple)


# ---------------------------------------------------------------------------
# from_capabilities — RAISE policy (deferred to ensure_can_handle)
# ---------------------------------------------------------------------------


def test_from_capabilities_skips_normalizer_when_system_prompt_missing_and_policy_raise(raise_all_policy):
    caps = TargetCapabilities(supports_system_prompt=False, supports_multi_turn=True)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=raise_all_policy)
    # RAISE policy should not add normalizers — validation is deferred.
    assert len(pipeline.normalizers) == 0


def test_from_capabilities_skips_normalizer_when_multi_turn_missing_and_policy_raise(raise_all_policy):
    caps = TargetCapabilities(supports_system_prompt=True, supports_multi_turn=False)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=raise_all_policy)
    # RAISE policy should not add normalizers — validation is deferred.
    assert len(pipeline.normalizers) == 0


# ---------------------------------------------------------------------------
# from_capabilities — custom overrides
# ---------------------------------------------------------------------------


def test_from_capabilities_uses_override_normalizer():
    mock_normalizer = MagicMock(spec=MessageListNormalizer)
    caps = TargetCapabilities(supports_system_prompt=False, supports_multi_turn=True)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    pipeline = ConversationNormalizationPipeline.from_capabilities(
        capabilities=caps,
        policy=policy,
        normalizer_overrides={CapabilityName.SYSTEM_PROMPT: mock_normalizer},
    )
    assert len(pipeline.normalizers) == 1
    assert pipeline.normalizers[0] is mock_normalizer


# ---------------------------------------------------------------------------
# normalize_async — pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_passthrough_when_empty_pipeline(make_message):
    pipeline = ConversationNormalizationPipeline()
    messages = [make_message("system", "sys"), make_message("user", "hi")]
    result = await pipeline.normalize_async(messages=messages)

    assert len(result) == 2
    assert result[0].get_value() == "sys"
    assert result[1].get_value() == "hi"


# ---------------------------------------------------------------------------
# normalize_async — ADAPT system prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_adapts_system_prompt(make_message):
    caps = TargetCapabilities(supports_system_prompt=False, supports_multi_turn=True)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=policy)

    messages = [make_message("system", "be nice"), make_message("user", "hello")]
    result = await pipeline.normalize_async(messages=messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    assert "be nice" in result[0].get_value()
    assert "hello" in result[0].get_value()


# ---------------------------------------------------------------------------
# normalize_async — ADAPT multi-turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_adapts_multi_turn(make_message):
    caps = TargetCapabilities(supports_system_prompt=True, supports_multi_turn=False)
    policy = CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
        }
    )
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=policy)

    messages = [
        make_message("user", "hello"),
        make_message("assistant", "hi"),
        make_message("user", "how are you?"),
    ]
    result = await pipeline.normalize_async(messages=messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    text = result[0].get_value()
    assert "hello" in text
    assert "hi" in text
    assert "how are you?" in text


# ---------------------------------------------------------------------------
# normalize_async — both adapts in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_adapts_system_then_multi_turn(adapt_all_policy, make_message):
    """System squash runs first, then history squash."""
    caps = TargetCapabilities(supports_system_prompt=False, supports_multi_turn=False)
    pipeline = ConversationNormalizationPipeline.from_capabilities(capabilities=caps, policy=adapt_all_policy)

    messages = [
        make_message("system", "be nice"),
        make_message("user", "hello"),
        make_message("assistant", "hi"),
        make_message("user", "bye"),
    ]
    result = await pipeline.normalize_async(messages=messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    text = result[0].get_value()
    assert "be nice" in text
    assert "bye" in text


# ---------------------------------------------------------------------------
# normalize_async — custom normalizer via mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalize_uses_custom_normalizer(make_message):
    mock_normalizer = MagicMock(spec=MessageListNormalizer)
    expected = [make_message("user", "custom")]
    mock_normalizer.normalize_async = AsyncMock(return_value=expected)

    pipeline = ConversationNormalizationPipeline(normalizers=(mock_normalizer,))

    messages = [make_message("system", "sys"), make_message("user", "hi")]
    result = await pipeline.normalize_async(messages=messages)

    assert result == expected
    mock_normalizer.normalize_async.assert_called_once()
