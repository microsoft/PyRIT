# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import MutableSequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.chat import ChatCompletion
from unit.mocks import get_sample_conversations, openai_chat_response_json_dict

from pyrit.executor.attack.core.attack_strategy import AttackStrategy
from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


@pytest.fixture
def sample_entries() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def openai_response_json() -> dict:
    return openai_chat_response_json_dict()


@pytest.fixture
def azure_openai_target(patch_central_database):
    return OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="test",
        api_key="test",
    )


@pytest.fixture
def mock_attack_strategy():
    """Create a mock attack strategy for testing"""
    strategy = MagicMock(spec=AttackStrategy)
    strategy.execute_async = AsyncMock()
    strategy.execute_with_context_async = AsyncMock()
    strategy.get_identifier.return_value = ComponentIdentifier(
        class_name="TestAttack",
        class_module="pyrit.executor.attack.test_attack",
    )
    return strategy


def test_set_system_prompt(azure_openai_target: OpenAIChatTarget, mock_attack_strategy: AttackStrategy):
    azure_openai_target.set_system_prompt(
        system_prompt="system prompt",
        conversation_id="1",
        attack_identifier=mock_attack_strategy.get_identifier(),
        labels={},
    )

    chats = azure_openai_target._memory.get_message_pieces(conversation_id="1")
    assert len(chats) == 1, f"Expected 1 chat, got {len(chats)}"
    assert chats[0].api_role == "system"
    assert chats[0].converted_value == "system prompt"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_set_system_prompt_adds_memory(
    azure_openai_target: OpenAIChatTarget, mock_attack_strategy: AttackStrategy
):
    azure_openai_target.set_system_prompt(
        system_prompt="system prompt",
        conversation_id="1",
        attack_identifier=mock_attack_strategy.get_identifier(),
        labels={},
    )

    chats = azure_openai_target._memory.get_message_pieces(conversation_id="1")
    assert len(chats) == 1, f"Expected 1 chats, got {len(chats)}"
    assert chats[0].api_role == "system"


@pytest.mark.asyncio
async def test_send_prompt_with_system_calls_chat_complete(
    azure_openai_target: OpenAIChatTarget,
    openai_response_json: dict,
    sample_entries: MutableSequence[MessagePiece],
    mock_attack_strategy: AttackStrategy,
):
    # Mock SDK response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.audio = None  # Explicitly set to avoid MagicMock auto-creation
    mock_message.tool_calls = None
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]

    with patch.object(
        azure_openai_target._async_client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        azure_openai_target.set_system_prompt(
            system_prompt="system prompt",
            conversation_id="1",
            attack_identifier=mock_attack_strategy.get_identifier(),
            labels={},
        )

        request = sample_entries[0]
        request.converted_value = "hi, I am a victim chatbot, how can I help?"
        request.conversation_id = "1"

        await azure_openai_target.send_prompt_async(message=Message(message_pieces=[request]))

        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_send_prompt_async_with_delay(
    azure_openai_target: OpenAIChatTarget,
    openai_response_json: dict,
    sample_entries: MutableSequence[MessagePiece],
):
    azure_openai_target._max_requests_per_minute = 10

    # Mock SDK response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.audio = None  # Explicitly set to avoid MagicMock auto-creation
    mock_message.tool_calls = None
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]

    with (
        patch.object(
            azure_openai_target._async_client.chat.completions, "create", new_callable=AsyncMock
        ) as mock_create,
        patch("asyncio.sleep") as mock_sleep,
    ):
        mock_create.return_value = mock_response

        request = sample_entries[0]
        request.converted_value = "hi, I am a victim chatbot, how can I help?"

        await azure_openai_target.send_prompt_async(message=Message(message_pieces=[request]))

        mock_create.assert_called_once()
        mock_sleep.assert_called_once_with(6)  # 60/max_requests_per_minute


# ---------------------------------------------------------------------------
# _propagate_lineage — metadata preservation after normalization
# ---------------------------------------------------------------------------

_LINEAGE_CONVERSATION_ID = "original-conv-id-12345"
_LINEAGE_LABELS = {"op_name": "test_op", "user_id": "user42"}
_LINEAGE_ATTACK_IDENTIFIER = ComponentIdentifier(class_name="TestAttack", class_module="tests.attacks")
_LINEAGE_PROMPT_TARGET_IDENTIFIER = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit")
_LINEAGE_PROMPT_METADATA = {"scenario": "test_scenario", "turn": 3}


def _make_lineage_piece(*, role: str, content: str) -> MessagePiece:
    return MessagePiece(
        role=role,
        conversation_id=_LINEAGE_CONVERSATION_ID,
        original_value=content,
        converted_value=content,
        original_value_data_type="text",
        converted_value_data_type="text",
        labels=dict(_LINEAGE_LABELS),
        prompt_target_identifier=_LINEAGE_PROMPT_TARGET_IDENTIFIER,
        attack_identifier=_LINEAGE_ATTACK_IDENTIFIER,
        prompt_metadata=dict(_LINEAGE_PROMPT_METADATA),
    )


def _make_lineage_message(*, role: str, content: str) -> Message:
    return Message(message_pieces=[_make_lineage_piece(role=role, content=content)])


def _make_mock_chat_completion(content: str = "response") -> MagicMock:
    mock = MagicMock(spec=ChatCompletion)
    mock.choices = [MagicMock()]
    mock.choices[0].finish_reason = "stop"
    mock.choices[0].message.content = content
    mock.choices[0].message.audio = None
    mock.choices[0].message.tool_calls = None
    mock.model_dump_json.return_value = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    )
    return mock


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_history_squash_preserves_metadata_on_normalized_message():
    """
    After history squash, _propagate_lineage should restore the original request's
    metadata (conversation_id, labels, attack_identifier) onto the squashed message.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_system_prompt=True,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        ),
    )

    history_msg = _make_lineage_message(role="assistant", content="previous answer")
    user_msg = _make_lineage_message(role="user", content="follow-up question")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = [history_msg]
    target._memory = mock_memory

    normalized = await target._get_normalized_conversation_async(message=user_msg)

    assert len(normalized) == 1

    normalized_piece = normalized[0].message_pieces[0]

    assert normalized_piece.conversation_id == _LINEAGE_CONVERSATION_ID
    assert normalized_piece.labels == _LINEAGE_LABELS
    assert normalized_piece.attack_identifier == _LINEAGE_ATTACK_IDENTIFIER
    assert normalized_piece.prompt_target_identifier == _LINEAGE_PROMPT_TARGET_IDENTIFIER
    assert normalized_piece.prompt_metadata == _LINEAGE_PROMPT_METADATA


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_response_preserves_metadata_after_history_squash():
    """
    End-to-end: after history squash the response must carry the original
    request's conversation_id, labels, and attack_identifier — not the
    random values created by the normalizer.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_system_prompt=True,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        ),
    )

    history_msg = _make_lineage_message(role="assistant", content="previous answer")
    user_msg = _make_lineage_message(role="user", content="follow-up question")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = [history_msg]
    target._memory = mock_memory

    mock_completion = _make_mock_chat_completion("target response")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    response_messages = await target.send_prompt_async(message=user_msg)

    assert len(response_messages) == 1
    response_piece = response_messages[0].message_pieces[0]

    assert response_piece.conversation_id == _LINEAGE_CONVERSATION_ID
    assert response_piece.labels == _LINEAGE_LABELS
    assert response_piece.attack_identifier == _LINEAGE_ATTACK_IDENTIFIER
    assert response_piece.prompt_target_identifier == _LINEAGE_PROMPT_TARGET_IDENTIFIER
    assert response_piece.prompt_metadata == _LINEAGE_PROMPT_METADATA


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_system_squash_preserves_metadata():
    """
    GenericSystemSquashNormalizer also creates messages via Message.from_prompt.
    _propagate_lineage should restore the original metadata after system squash too.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        ),
    )

    system_msg = _make_lineage_message(role="system", content="be helpful")
    user_msg = _make_lineage_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = [system_msg]
    target._memory = mock_memory

    normalized = await target._get_normalized_conversation_async(message=user_msg)

    assert len(normalized) == 1
    assert "be helpful" in normalized[0].get_value()

    normalized_piece = normalized[0].message_pieces[0]

    assert normalized_piece.conversation_id == _LINEAGE_CONVERSATION_ID
    assert normalized_piece.labels == _LINEAGE_LABELS
    assert normalized_piece.attack_identifier == _LINEAGE_ATTACK_IDENTIFIER
    assert normalized_piece.prompt_target_identifier == _LINEAGE_PROMPT_TARGET_IDENTIFIER
    assert normalized_piece.prompt_metadata == _LINEAGE_PROMPT_METADATA


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_history_squash_propagates_lineage_to_all_pieces():
    """
    When the squashed message contains multiple pieces, _propagate_lineage
    must stamp every piece — not just the first one.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_system_prompt=True,
                supports_multi_message_pieces=True,
                input_modalities=frozenset({frozenset(["text"])}),
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        ),
    )

    history_msg = _make_lineage_message(role="assistant", content="previous answer")
    # Build a user message with two pieces to exercise multi-piece stamping.
    user_msg = Message(
        message_pieces=[
            _make_lineage_piece(role="user", content="first part"),
            _make_lineage_piece(role="user", content="second part"),
        ]
    )

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = [history_msg]
    target._memory = mock_memory

    normalized = await target._get_normalized_conversation_async(message=user_msg)

    assert len(normalized) == 1

    for piece in normalized[0].message_pieces:
        assert piece.conversation_id == _LINEAGE_CONVERSATION_ID
        assert piece.labels == _LINEAGE_LABELS
        assert piece.attack_identifier == _LINEAGE_ATTACK_IDENTIFIER
        assert piece.prompt_target_identifier == _LINEAGE_PROMPT_TARGET_IDENTIFIER
        assert piece.prompt_metadata == _LINEAGE_PROMPT_METADATA


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_conversation_id_stamped_on_all_but_full_lineage_only_on_last():
    """
    conversation_id is stamped on every normalized message (including new ones
    created by the normalizer).  Full lineage (labels, attack_identifier, etc.)
    is only propagated to the last message.  Earlier messages keep their own
    metadata.  A warning is logged when the normalizer increases message count.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    history_msg = _make_lineage_message(role="assistant", content="previous answer")
    # Give history distinct metadata to verify it's preserved.
    history_msg.message_pieces[0].labels = {"original": "history_labels"}
    history_msg.message_pieces[0].prompt_metadata = {"original": "history_meta"}

    user_msg = _make_lineage_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = [history_msg]
    target._memory = mock_memory

    # Simulate a normalizer that inserts a new message with a random conversation_id.
    new_piece = MessagePiece(
        role="user",
        conversation_id="random-normalizer-uuid",
        original_value="injected",
        converted_value="injected",
        original_value_data_type="text",
        converted_value_data_type="text",
    )
    new_msg = Message(message_pieces=[new_piece])

    with patch.object(target.configuration, "normalize_async", new_callable=AsyncMock) as mock_normalize:
        mock_normalize.return_value = [history_msg, new_msg, user_msg]

        import logging

        with patch.object(logging.getLogger("pyrit.prompt_target.common.prompt_target"), "warning") as mock_warn:
            normalized = await target._get_normalized_conversation_async(message=user_msg)

        # All messages should carry the correct conversation_id.
        for msg in normalized:
            for piece in msg.message_pieces:
                assert piece.conversation_id == _LINEAGE_CONVERSATION_ID

        # History message's other metadata should be untouched.
        assert normalized[0].message_pieces[0].labels == {"original": "history_labels"}
        assert normalized[0].message_pieces[0].prompt_metadata == {"original": "history_meta"}

        # New middle message should NOT have full lineage overwritten.
        assert normalized[1].message_pieces[0].labels == {}

        # Last message should carry full lineage.
        last_piece = normalized[-1].message_pieces[0]
        assert last_piece.labels == _LINEAGE_LABELS
        assert last_piece.attack_identifier == _LINEAGE_ATTACK_IDENTIFIER
        assert last_piece.prompt_target_identifier == _LINEAGE_PROMPT_TARGET_IDENTIFIER
        assert last_piece.prompt_metadata == _LINEAGE_PROMPT_METADATA

        # Warning should fire because message count increased (2 → 3).
        mock_warn.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_no_warning_when_message_count_unchanged():
    """
    No warning is logged when the normalizer does not increase the message count.
    """
    target = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    user_msg = _make_lineage_message(role="user", content="hello")

    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = []
    target._memory = mock_memory

    with patch.object(target.configuration, "normalize_async", new_callable=AsyncMock) as mock_normalize:
        mock_normalize.return_value = [user_msg]

        import logging

        with patch.object(logging.getLogger("pyrit.prompt_target.common.prompt_target"), "warning") as mock_warn:
            await target._get_normalized_conversation_async(message=user_msg)

        mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# _create_identifier — target configuration in the identifier
# ---------------------------------------------------------------------------


def _make_identifier_target(
    *,
    capabilities: TargetCapabilities | None = None,
    policy: CapabilityHandlingPolicy | None = None,
) -> OpenAIChatTarget:
    kwargs: dict[str, Any] = {}
    if capabilities is not None or policy is not None:
        kwargs["custom_configuration"] = TargetConfiguration(
            capabilities=capabilities or TargetCapabilities(),
            policy=policy,
        )
    return OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        **kwargs,
    )


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_includes_capability_params():
    target = _make_identifier_target(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_json_schema=True,
            supports_json_output=True,
            supports_editable_history=False,
            supports_system_prompt=True,
        ),
    )

    params = target.get_identifier().params
    target_config = params["target_configuration"]
    capabilities = target_config["capabilities"]

    # Config-derived fields are nested under ``target_configuration``, not
    # spread at the top level — guards against accidental re-flattening.
    assert "supports_multi_turn" not in params
    assert set(target_config.keys()) == {"capabilities", "capability_policy", "normalization_pipeline"}

    assert capabilities["supports_multi_turn"] is True
    assert capabilities["supports_multi_message_pieces"] is True
    assert capabilities["supports_json_schema"] is True
    assert capabilities["supports_json_output"] is True
    assert capabilities["supports_editable_history"] is False
    assert capabilities["supports_system_prompt"] is True
    assert capabilities["input_modalities"] == [["text"]]
    assert capabilities["output_modalities"] == [["text"]]
    assert isinstance(target_config["capability_policy"], dict)
    assert isinstance(target_config["normalization_pipeline"], list)


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_differs_when_capabilities_differ():
    a = _make_identifier_target(capabilities=TargetCapabilities(supports_json_schema=False))
    b = _make_identifier_target(capabilities=TargetCapabilities(supports_json_schema=True))

    assert a.get_identifier().hash != b.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_differs_when_policy_differs():
    capabilities = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    a = _make_identifier_target(
        capabilities=capabilities,
        policy=CapabilityHandlingPolicy(
            behaviors={
                CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            }
        ),
    )
    b = _make_identifier_target(
        capabilities=capabilities,
        policy=CapabilityHandlingPolicy(
            behaviors={
                CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
            }
        ),
    )

    assert a.get_identifier().hash != b.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_is_deterministic_across_instances():
    capabilities = TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        input_modalities=frozenset({frozenset(["text"]), frozenset(["image_path"])}),
        output_modalities=frozenset({frozenset(["text"])}),
    )

    a = _make_identifier_target(capabilities=capabilities)
    b = _make_identifier_target(capabilities=capabilities)

    assert a.get_identifier().hash == b.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_differs_when_normalizer_overrides_differ():
    from pyrit.message_normalizer import GenericSystemSquashNormalizer, MessageListNormalizer
    from pyrit.models import Message
    from pyrit.prompt_target.common.target_capabilities import CapabilityName

    class _CustomSystemSquash(MessageListNormalizer[Message]):
        async def normalize_async(self, messages):  # pragma: no cover - not exercised
            return messages

    capabilities = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=False)
    policy = CapabilityHandlingPolicy(behaviors={CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT})

    default_cfg = TargetConfiguration(
        capabilities=capabilities,
        policy=policy,
        normalizer_overrides={CapabilityName.SYSTEM_PROMPT: GenericSystemSquashNormalizer()},
    )
    custom_cfg = TargetConfiguration(
        capabilities=capabilities,
        policy=policy,
        normalizer_overrides={CapabilityName.SYSTEM_PROMPT: _CustomSystemSquash()},
    )

    a = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=default_cfg,
    )
    b = OpenAIChatTarget(
        model_name="gpt-4o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        custom_configuration=custom_cfg,
    )

    assert a.get_identifier().hash != b.get_identifier().hash
