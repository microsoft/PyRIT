# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Prompt targets for PyRIT.

Target implementations for interacting with different services and APIs,
for example sending prompts or transferring content (uploads).
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.prompt_target.azure_blob_storage_target import AzureBlobStorageTarget
    from pyrit.prompt_target.azure_ml_chat_target import AzureMLChatTarget
    from pyrit.prompt_target.common.conversation_normalization_pipeline import ConversationNormalizationPipeline
    from pyrit.prompt_target.common.discover_target_capabilities import discover_target_capabilities_async
    from pyrit.prompt_target.common.prompt_target import PromptTarget
    from pyrit.prompt_target.common.realtime_audio import ServerVadConfig
    from pyrit.prompt_target.common.target_capabilities import (
        CapabilityHandlingPolicy,
        CapabilityName,
        TargetCapabilities,
        UnsupportedCapabilityBehavior,
        get_known_capabilities,
    )
    from pyrit.prompt_target.common.target_configuration import TargetConfiguration
    from pyrit.prompt_target.common.target_requirements import CHAT_TARGET_REQUIREMENTS, TargetRequirements
    from pyrit.prompt_target.common.utils import limit_requests_per_minute
    from pyrit.prompt_target.gandalf_target import GandalfLevel, GandalfTarget
    from pyrit.prompt_target.http_target.http_target import HTTPTarget
    from pyrit.prompt_target.http_target.http_target_callback_functions import (
        get_http_target_json_response_callback_function,
        get_http_target_regex_matching_callback_function,
    )
    from pyrit.prompt_target.http_target.httpx_api_target import HTTPXAPITarget
    from pyrit.prompt_target.hugging_face.hugging_face_chat_target import HuggingFaceChatTarget
    from pyrit.prompt_target.litellm_chat_target import LiteLLMChatTarget
    from pyrit.prompt_target.openai.openai_chat_audio_config import OpenAIChatAudioConfig
    from pyrit.prompt_target.openai.openai_chat_target import OpenAIChatTarget
    from pyrit.prompt_target.openai.openai_completion_target import OpenAICompletionTarget
    from pyrit.prompt_target.openai.openai_image_target import OpenAIImageTarget
    from pyrit.prompt_target.openai.openai_realtime_target import RealtimeTarget
    from pyrit.prompt_target.openai.openai_response_target import OpenAIResponseTarget
    from pyrit.prompt_target.openai.openai_target import OpenAITarget
    from pyrit.prompt_target.openai.openai_tts_target import OpenAITTSTarget
    from pyrit.prompt_target.openai.openai_video_target import OpenAIVideoTarget
    from pyrit.prompt_target.playwright_copilot_target import CopilotType, PlaywrightCopilotTarget
    from pyrit.prompt_target.playwright_target import PlaywrightTarget
    from pyrit.prompt_target.prompt_shield_target import PromptShieldTarget
    from pyrit.prompt_target.round_robin_target import RoundRobinTarget
    from pyrit.prompt_target.text_target import TextTarget
    from pyrit.prompt_target.websocket_copilot_target import WebSocketCopilotTarget
    from pyrit.prompt_target.websocket_target import WebsocketTarget

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AzureBlobStorageTarget": "pyrit.prompt_target.azure_blob_storage_target",
    "AzureMLChatTarget": "pyrit.prompt_target.azure_ml_chat_target",
    "CapabilityName": "pyrit.prompt_target.common.target_capabilities",
    "CapabilityHandlingPolicy": "pyrit.prompt_target.common.target_capabilities",
    "CHAT_TARGET_REQUIREMENTS": "pyrit.prompt_target.common.target_requirements",
    "CopilotType": "pyrit.prompt_target.playwright_copilot_target",
    "ConversationNormalizationPipeline": "pyrit.prompt_target.common.conversation_normalization_pipeline",
    "GandalfLevel": "pyrit.prompt_target.gandalf_target",
    "GandalfTarget": "pyrit.prompt_target.gandalf_target",
    "get_http_target_json_response_callback_function": "pyrit.prompt_target.http_target.http_target_callback_functions",
    "get_http_target_regex_matching_callback_function": (
        "pyrit.prompt_target.http_target.http_target_callback_functions"
    ),
    "HTTPTarget": "pyrit.prompt_target.http_target.http_target",
    "HTTPXAPITarget": "pyrit.prompt_target.http_target.httpx_api_target",
    "HuggingFaceChatTarget": "pyrit.prompt_target.hugging_face.hugging_face_chat_target",
    "limit_requests_per_minute": "pyrit.prompt_target.common.utils",
    "LiteLLMChatTarget": "pyrit.prompt_target.litellm_chat_target",
    "OpenAICompletionTarget": "pyrit.prompt_target.openai.openai_completion_target",
    "OpenAIChatAudioConfig": "pyrit.prompt_target.openai.openai_chat_audio_config",
    "OpenAIChatTarget": "pyrit.prompt_target.openai.openai_chat_target",
    "OpenAIImageTarget": "pyrit.prompt_target.openai.openai_image_target",
    "OpenAIResponseTarget": "pyrit.prompt_target.openai.openai_response_target",
    "OpenAIVideoTarget": "pyrit.prompt_target.openai.openai_video_target",
    "OpenAITTSTarget": "pyrit.prompt_target.openai.openai_tts_target",
    "OpenAITarget": "pyrit.prompt_target.openai.openai_target",
    "PlaywrightTarget": "pyrit.prompt_target.playwright_target",
    "PlaywrightCopilotTarget": "pyrit.prompt_target.playwright_copilot_target",
    "PromptShieldTarget": "pyrit.prompt_target.prompt_shield_target",
    "PromptTarget": "pyrit.prompt_target.common.prompt_target",
    "RealtimeTarget": "pyrit.prompt_target.openai.openai_realtime_target",
    "ServerVadConfig": "pyrit.prompt_target.common.realtime_audio",
    "RoundRobinTarget": "pyrit.prompt_target.round_robin_target",
    "TargetCapabilities": "pyrit.prompt_target.common.target_capabilities",
    "TargetConfiguration": "pyrit.prompt_target.common.target_configuration",
    "TargetRequirements": "pyrit.prompt_target.common.target_requirements",
    "UnsupportedCapabilityBehavior": "pyrit.prompt_target.common.target_capabilities",
    "TextTarget": "pyrit.prompt_target.text_target",
    "WebsocketTarget": "pyrit.prompt_target.websocket_target",
    "discover_target_capabilities_async": "pyrit.prompt_target.common.discover_target_capabilities",
    "get_known_capabilities": "pyrit.prompt_target.common.target_capabilities",
    "WebSocketCopilotTarget": "pyrit.prompt_target.websocket_copilot_target",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
