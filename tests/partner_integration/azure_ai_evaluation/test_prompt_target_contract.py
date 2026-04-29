# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for PromptTarget interface used by azure-ai-evaluation.

The azure-ai-evaluation red team module extends PromptTarget in four places:
- _CallbackChatTarget (wraps user callbacks)
- AzureRAIServiceTarget (sends prompts to RAI service)
- RAIServiceEvalChatTarget (evaluation-specific RAI target)
- _rai_service_target.py (multi-turn jailbreak target)

These tests ensure the base class interface remains stable.
"""

import uuid

import pytest

from pyrit.models import Message, MessagePiece, construct_response_from_request
from pyrit.prompt_target import PromptTarget


class _MinimalTarget(PromptTarget):
    """Minimal concrete PromptTarget for contract testing."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return []

    def _validate_request(self, *, normalized_conversation) -> None:
        pass


class TestPromptTargetContract:
    """Validate PromptTarget base class interface stability."""

    def test_prompt_target_is_abstract(self):
        """PromptTarget should not be directly instantiable (has abstract methods)."""
        with pytest.raises(TypeError):
            PromptTarget()

    def test_prompt_target_has_send_prompt_async(self):
        """azure-ai-evaluation overrides send_prompt_async in all subclasses."""
        assert hasattr(PromptTarget, "send_prompt_async")

    def test_prompt_target_subclassable_with_send_prompt_async(self):
        """azure-ai-evaluation creates subclasses that implement send_prompt_async."""
        target = _MinimalTarget()
        assert isinstance(target, PromptTarget)

    def test_prompt_target_init_accepts_keyword_args(self):
        """PromptTarget.__init__ should accept max_requests_per_minute."""
        target = _MinimalTarget(max_requests_per_minute=60)
        assert target is not None

    def test_construct_response_from_request_is_callable(self):
        """AzureRAIServiceTarget uses construct_response_from_request to build responses."""
        assert callable(construct_response_from_request)

    def test_construct_response_from_request_returns_message(self):
        """Verify construct_response_from_request produces a Message from a MessagePiece."""
        request_piece = MessagePiece(
            role="user",
            original_value="test prompt",
            conversation_id=str(uuid.uuid4()),
        )
        response = construct_response_from_request(
            request=request_piece,
            response_text_pieces=["test response"],
        )
        assert isinstance(response, Message)
        assert len(response.message_pieces) == 1
        assert response.message_pieces[0].converted_value == "test response"
        assert response.message_pieces[0].api_role == "assistant"

    def test_prompt_target_has_memory_attribute(self):
        """azure-ai-evaluation accesses self._memory on PromptTarget subclasses."""
        target = _MinimalTarget()
        # _memory is set during initialization or via property
        assert hasattr(target, "_memory")


class TestOpenAIChatTargetContract:
    """Validate OpenAIChatTarget importability and interface.

    strategy_utils.py imports OpenAIChatTarget for get_chat_target() and
    converter strategy instantiation (e.g., TenseConverter needs a chat target).
    """

    def test_openai_chat_target_importable(self):
        """OpenAIChatTarget must be importable from pyrit.prompt_target."""
        from pyrit.prompt_target import OpenAIChatTarget

        assert OpenAIChatTarget is not None

    def test_openai_chat_target_extends_prompt_target(self):
        """OpenAIChatTarget must be a PromptTarget subclass."""
        from pyrit.prompt_target import OpenAIChatTarget

        assert issubclass(OpenAIChatTarget, PromptTarget)
