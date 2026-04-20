# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt targets for PyRIT.

Target implementations for interacting with different services and APIs,
for example sending prompts or transferring content (uploads).
"""

from pyrit.prompt_target.azure_blob_storage_target import AzureBlobStorageTarget
from pyrit.prompt_target.azure_ml_chat_target import AzureMLChatTarget
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.utils import limit_requests_per_minute
from pyrit.prompt_target.crucible_target import CrucibleTarget
from pyrit.prompt_target.gandalf_target import GandalfLevel, GandalfTarget
from pyrit.prompt_target.http_target.http_target import HTTPTarget
from pyrit.prompt_target.http_target.http_target_callback_functions import (
    get_http_target_json_response_callback_function,
    get_http_target_regex_matching_callback_function,
)
from pyrit.prompt_target.http_target.httpx_api_target import HTTPXAPITarget
from pyrit.prompt_target.hugging_face.hugging_face_chat_target import HuggingFaceChatTarget
from pyrit.prompt_target.hugging_face.hugging_face_endpoint_target import HuggingFaceEndpointTarget
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
from pyrit.prompt_target.text_target import TextTarget

__all__ = [
    "AzureBlobStorageTarget",
    "AzureMLChatTarget",
    "CopilotType",
    "CrucibleTarget",
    "GandalfLevel",
    "GandalfTarget",
    "get_http_target_json_response_callback_function",
    "get_http_target_regex_matching_callback_function",
    "HTTPTarget",
    "HTTPXAPITarget",
    "HuggingFaceChatTarget",
    "HuggingFaceEndpointTarget",
    "limit_requests_per_minute",
    "OpenAICompletionTarget",
    "OpenAIChatAudioConfig",
    "OpenAIChatTarget",
    "OpenAIImageTarget",
    "OpenAIResponseTarget",
    "OpenAIVideoTarget",
    "OpenAITTSTarget",
    "OpenAITarget",
    "PlaywrightTarget",
    "PlaywrightCopilotTarget",
    "PromptChatTarget",
    "PromptShieldTarget",
    "PromptTarget",
    "RealtimeTarget",
    "TextTarget",
]
