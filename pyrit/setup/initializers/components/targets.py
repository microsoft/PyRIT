# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AIRT Target Initializer for registering pre-configured targets from environment variables.

This module provides the TargetInitializer class that registers available
targets into the TargetRegistry based on environment variable configuration.

Note: This module only includes PRIMARY endpoint configurations from .env_example.
      Alias configurations (those using ${...} syntax) are excluded since they
      reference other primary configurations.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pyrit.auth import get_azure_openai_auth, get_azure_token_provider
from pyrit.prompt_target import (
    AzureMLChatTarget,
    OpenAIChatTarget,
    OpenAICompletionTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    PromptShieldTarget,
    PromptTarget,
    RealtimeTarget,
)
from pyrit.registry import TargetRegistry
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


# Literal type for target tags
TargetTag = Literal["default", "scorer"]


@dataclass
class TargetConfig:
    """
    Configuration for a target to be registered.

    Attributes:
        registry_name: The name used to retrieve the target from the registry.
        target_class: The target class to instantiate.
        endpoint_var: The environment variable name for the endpoint URL.
        key_var: The environment variable name for the API key. Empty string means no auth required.
        model_var: The environment variable name for the model name.
        underlying_model_var: The environment variable name for the underlying model.
        temperature: Optional temperature override for the target.
        tags: Tags for filtering which targets to register.
    """

    registry_name: str
    target_class: type[PromptTarget]
    endpoint_var: str
    key_var: str = ""  # Empty string means no auth required
    model_var: Optional[str] = None
    underlying_model_var: Optional[str] = None
    temperature: Optional[float] = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)
    tags: list[TargetTag] = field(default_factory=lambda: ["default"])


# Define all supported target configurations.
# Only PRIMARY configurations are included here - alias configurations that use ${...}
# syntax in .env_example are excluded since they reference other primary configurations.
ENV_TARGET_CONFIGS: list[TargetConfig] = [
    # ============================================
    # OpenAI Chat Targets (OpenAIChatTarget)
    # ============================================
    TargetConfig(
        registry_name="platform_openai_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="PLATFORM_OPENAI_CHAT_ENDPOINT",
        key_var="PLATFORM_OPENAI_CHAT_API_KEY",
        model_var="PLATFORM_OPENAI_CHAT_GPT4O_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_gpt4o",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_KEY",
        model_var="AZURE_OPENAI_GPT4O_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_integration_test",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_INTEGRATION_TEST_ENDPOINT",
        key_var="AZURE_OPENAI_INTEGRATION_TEST_KEY",
        model_var="AZURE_OPENAI_INTEGRATION_TEST_MODEL",
        underlying_model_var="AZURE_OPENAI_INTEGRATION_TEST_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_gpt35_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT3_5_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT3_5_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT3_5_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT3_5_CHAT_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_gpt4_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4_CHAT_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat2",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY2",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL2",
    ),
    TargetConfig(
        registry_name="azure_foundry_deepseek",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_FOUNDRY_DEEPSEEK_ENDPOINT",
        key_var="AZURE_FOUNDRY_DEEPSEEK_KEY",
        model_var="AZURE_FOUNDRY_DEEPSEEK_MODEL",
    ),
    TargetConfig(
        registry_name="azure_foundry_phi4",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_FOUNDRY_PHI4_ENDPOINT",
        key_var="AZURE_CHAT_PHI4_KEY",
        model_var="AZURE_FOUNDRY_PHI4_MODEL",
    ),
    TargetConfig(
        registry_name="azure_foundry_mistral_large",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_FOUNDRY_MISTRAL_LARGE_ENDPOINT",
        key_var="AZURE_FOUNDRY_MISTRAL_LARGE_KEY",
        model_var="AZURE_FOUNDRY_MISTRAL_LARGE_MODEL",
    ),
    TargetConfig(
        registry_name="groq",
        target_class=OpenAIChatTarget,
        endpoint_var="GROQ_ENDPOINT",
        key_var="GROQ_KEY",
        model_var="GROQ_LLAMA_MODEL",
    ),
    TargetConfig(
        registry_name="open_router",
        target_class=OpenAIChatTarget,
        endpoint_var="OPEN_ROUTER_ENDPOINT",
        key_var="OPEN_ROUTER_KEY",
        model_var="OPEN_ROUTER_CLAUDE_MODEL",
    ),
    TargetConfig(
        registry_name="ollama",
        target_class=OpenAIChatTarget,
        endpoint_var="OLLAMA_CHAT_ENDPOINT",
        model_var="OLLAMA_MODEL",
    ),
    TargetConfig(
        registry_name="google_gemini",
        target_class=OpenAIChatTarget,
        endpoint_var="GOOGLE_GEMINI_ENDPOINT",
        key_var="GOOGLE_GEMINI_API_KEY",
        model_var="GOOGLE_GEMINI_MODEL",
    ),
    # ============================================
    # OpenAI Responses Targets (OpenAIResponseTarget)
    # ============================================
    TargetConfig(
        registry_name="azure_openai_gpt5_responses",
        target_class=OpenAIResponseTarget,
        endpoint_var="AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT",
        key_var="AZURE_OPENAI_GPT5_KEY",
        model_var="AZURE_OPENAI_GPT5_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT5_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_gpt5_responses_high_reasoning",
        target_class=OpenAIResponseTarget,
        endpoint_var="AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT",
        key_var="AZURE_OPENAI_GPT5_KEY",
        model_var="AZURE_OPENAI_GPT5_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT5_UNDERLYING_MODEL",
        extra_kwargs={"extra_body_parameters": {"reasoning": {"effort": "high"}}},
    ),
    TargetConfig(
        registry_name="platform_openai_responses",
        target_class=OpenAIResponseTarget,
        endpoint_var="PLATFORM_OPENAI_RESPONSES_ENDPOINT",
        key_var="PLATFORM_OPENAI_RESPONSES_KEY",
        model_var="PLATFORM_OPENAI_RESPONSES_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_responses",
        target_class=OpenAIResponseTarget,
        endpoint_var="AZURE_OPENAI_RESPONSES_ENDPOINT",
        key_var="AZURE_OPENAI_RESPONSES_KEY",
        model_var="AZURE_OPENAI_RESPONSES_MODEL",
        underlying_model_var="AZURE_OPENAI_RESPONSES_UNDERLYING_MODEL",
    ),
    # ============================================
    # Realtime Targets (RealtimeTarget)
    # ============================================
    TargetConfig(
        registry_name="platform_openai_realtime",
        target_class=RealtimeTarget,
        endpoint_var="PLATFORM_OPENAI_REALTIME_ENDPOINT",
        key_var="PLATFORM_OPENAI_REALTIME_API_KEY",
        model_var="PLATFORM_OPENAI_REALTIME_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_realtime",
        target_class=RealtimeTarget,
        endpoint_var="AZURE_OPENAI_REALTIME_ENDPOINT",
        key_var="AZURE_OPENAI_REALTIME_API_KEY",
        model_var="AZURE_OPENAI_REALTIME_MODEL",
        underlying_model_var="AZURE_OPENAI_REALTIME_UNDERLYING_MODEL",
    ),
    # ============================================
    # Image Targets (OpenAIImageTarget)
    # ============================================
    TargetConfig(
        registry_name="openai_image_azure",
        target_class=OpenAIImageTarget,
        endpoint_var="OPENAI_IMAGE_ENDPOINT1",
        key_var="OPENAI_IMAGE_API_KEY1",
        model_var="OPENAI_IMAGE_MODEL1",
        underlying_model_var="OPENAI_IMAGE_UNDERLYING_MODEL1",
    ),
    TargetConfig(
        registry_name="openai_image_platform",
        target_class=OpenAIImageTarget,
        endpoint_var="OPENAI_IMAGE_ENDPOINT2",
        key_var="OPENAI_IMAGE_API_KEY2",
        model_var="OPENAI_IMAGE_MODEL2",
        underlying_model_var="OPENAI_IMAGE_UNDERLYING_MODEL2",
    ),
    # ============================================
    # TTS Targets (OpenAITTSTarget)
    # ============================================
    TargetConfig(
        registry_name="openai_tts_azure",
        target_class=OpenAITTSTarget,
        endpoint_var="OPENAI_TTS_ENDPOINT1",
        key_var="OPENAI_TTS_KEY1",
        model_var="OPENAI_TTS_MODEL1",
        underlying_model_var="OPENAI_TTS_UNDERLYING_MODEL1",
    ),
    TargetConfig(
        registry_name="openai_tts_platform",
        target_class=OpenAITTSTarget,
        endpoint_var="OPENAI_TTS_ENDPOINT2",
        key_var="OPENAI_TTS_KEY2",
        model_var="OPENAI_TTS_MODEL2",
        underlying_model_var="OPENAI_TTS_UNDERLYING_MODEL2",
    ),
    # ============================================
    # Video Targets (OpenAIVideoTarget)
    # ============================================
    TargetConfig(
        registry_name="azure_openai_video",
        target_class=OpenAIVideoTarget,
        endpoint_var="AZURE_OPENAI_VIDEO_ENDPOINT",
        key_var="AZURE_OPENAI_VIDEO_KEY",
        model_var="AZURE_OPENAI_VIDEO_MODEL",
        underlying_model_var="AZURE_OPENAI_VIDEO_UNDERLYING_MODEL",
    ),
    # ============================================
    # Completion Targets (OpenAICompletionTarget)
    # ============================================
    TargetConfig(
        registry_name="openai_completion",
        target_class=OpenAICompletionTarget,
        endpoint_var="OPENAI_COMPLETION_ENDPOINT",
        key_var="OPENAI_COMPLETION_API_KEY",
        model_var="OPENAI_COMPLETION_MODEL",
    ),
    # ============================================
    # Azure ML Targets (AzureMLChatTarget)
    # ============================================
    TargetConfig(
        registry_name="azure_ml_phi",
        target_class=AzureMLChatTarget,
        endpoint_var="AZURE_ML_PHI_ENDPOINT",
        key_var="AZURE_ML_PHI_KEY",
    ),
    # ============================================
    # Safety Targets (PromptShieldTarget)
    # ============================================
    TargetConfig(
        registry_name="azure_content_safety",
        target_class=PromptShieldTarget,
        endpoint_var="AZURE_CONTENT_SAFETY_API_ENDPOINT",
        key_var="AZURE_CONTENT_SAFETY_API_KEY",
    ),
]

# Scorer-specific temperature variant targets.
# These reuse the same endpoints as their base targets but with different temperatures.
SCORER_TARGET_CONFIGS: list[TargetConfig] = [
    TargetConfig(
        registry_name="azure_openai_gpt4o_temp0",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_KEY",
        model_var="AZURE_OPENAI_GPT4O_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL",
        temperature=0.0,
        tags=["scorer"],
    ),
    TargetConfig(
        registry_name="azure_openai_gpt4o_temp9",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_KEY",
        model_var="AZURE_OPENAI_GPT4O_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL",
        temperature=0.9,
        tags=["scorer"],
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat_temp0",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL",
        temperature=0.0,
        tags=["scorer"],
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat_temp9",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL",
        temperature=0.9,
        tags=["scorer"],
    ),
]

# Combined list of all target configurations.
TARGET_CONFIGS: list[TargetConfig] = ENV_TARGET_CONFIGS + SCORER_TARGET_CONFIGS


class TargetInitializer(PyRITInitializer):
    """
    Target Initializer for registering pre-configured targets.

    This initializer scans for known endpoint environment variables and registers
    the corresponding targets into the TargetRegistry. Targets can be filtered
    by tags to control which targets are registered.

    Args:
        tags: List of tags to filter which targets to register.
            "default" registers the base environment targets.
            "scorer" registers scorer-specific temperature variant targets.
            Pass multiple tags to register targets matching any tag.
            If not provided, only "default" targets are registered.

    Supported Endpoints by Category:

    **OpenAI Chat Targets (OpenAIChatTarget):**
    - PLATFORM_OPENAI_CHAT_* - Platform OpenAI Chat API
    - AZURE_OPENAI_GPT4O_* - Azure OpenAI GPT-4o
    - AZURE_OPENAI_INTEGRATION_TEST_* - Integration test endpoint
    - AZURE_OPENAI_GPT3_5_CHAT_* - Azure OpenAI GPT-3.5
    - AZURE_OPENAI_GPT4_CHAT_* - Azure OpenAI GPT-4
    - AZURE_OPENAI_GPT4O_UNSAFE_CHAT_* - Azure OpenAI GPT-4o unsafe
    - AZURE_OPENAI_GPT4O_UNSAFE_CHAT_*2 - Azure OpenAI GPT-4o unsafe secondary
    - AZURE_FOUNDRY_DEEPSEEK_* - Azure AI Foundry DeepSeek
    - AZURE_FOUNDRY_PHI4_* - Azure AI Foundry Phi-4
    - AZURE_FOUNDRY_MISTRAL_LARGE_* - Azure AI Foundry Mistral Large
    - GROQ_* - Groq API
    - OPEN_ROUTER_* - OpenRouter API
    - OLLAMA_* - Ollama local
    - GOOGLE_GEMINI_* - Google Gemini (OpenAI-compatible)

    **OpenAI Responses Targets (OpenAIResponseTarget):**
    - AZURE_OPENAI_GPT5_RESPONSES_* - Azure OpenAI GPT-5 Responses
    - AZURE_OPENAI_GPT5_RESPONSES_* (high reasoning) - Azure OpenAI GPT-5 Responses with high reasoning effort
    - PLATFORM_OPENAI_RESPONSES_* - Platform OpenAI Responses
    - AZURE_OPENAI_RESPONSES_* - Azure OpenAI Responses

    **Realtime Targets (RealtimeTarget):**
    - PLATFORM_OPENAI_REALTIME_* - Platform OpenAI Realtime
    - AZURE_OPENAI_REALTIME_* - Azure OpenAI Realtime

    **Image Targets (OpenAIImageTarget):**
    - OPENAI_IMAGE_*1 - Azure OpenAI Image
    - OPENAI_IMAGE_*2 - Platform OpenAI Image

    **TTS Targets (OpenAITTSTarget):**
    - OPENAI_TTS_*1 - Azure OpenAI TTS
    - OPENAI_TTS_*2 - Platform OpenAI TTS

    **Video Targets (OpenAIVideoTarget):**
    - AZURE_OPENAI_VIDEO_* - Azure OpenAI Video

    **Completion Targets (OpenAICompletionTarget):**
    - OPENAI_COMPLETION_* - OpenAI Completion

    **Azure ML Targets (AzureMLChatTarget):**
    - AZURE_ML_PHI_* - Azure ML Phi

    **Safety Targets (PromptShieldTarget):**
    - AZURE_CONTENT_SAFETY_* - Azure Content Safety

    Example:
        initializer = TargetInitializer()
        await initializer.initialize_async()

        # Register scorer temperature variants too
        initializer = TargetInitializer(tags=["default", "scorer"])
        await initializer.initialize_async()
    """

    def __init__(self, *, tags: list[TargetTag] | None = None) -> None:
        """
        Initialize the Target Initializer.

        Args:
            tags (list[TargetTag] | None): Tags to filter which targets to register.
                If None, only "default" targets are registered.
        """
        super().__init__()
        self._tags = tags if tags is not None else ["default"]

    @property
    def name(self) -> str:
        """Get the name of this initializer."""
        return "Target Initializer"

    @property
    def execution_order(self) -> int:
        """Get the execution order. Runs before ScorerInitializer (order=2)."""
        return 1

    @property
    def description(self) -> str:
        """Get the description of this initializer."""
        return (
            "Instantiates a collection of targets from "
            "available environment variables and adds them to the TargetRegistry"
        )

    @property
    def required_env_vars(self) -> list[str]:
        """
        Get list of required environment variables.

        Returns empty list since this initializer is optional - it registers
        whatever endpoints are available without requiring any.
        """
        return []

    async def initialize_async(self) -> None:
        """
        Register available targets based on environment variables.

        Scans for known endpoint environment variables and registers the
        corresponding targets into the TargetRegistry. Only targets with
        tags matching the configured tags are registered.
        """
        for config in TARGET_CONFIGS:
            if not any(tag in self._tags for tag in config.tags):
                continue
            self._register_target(config)

    def _register_target(self, config: TargetConfig) -> None:
        """
        Register a target if its required environment variables are set.

        Args:
            config: The target configuration specifying env vars and target class.
        """
        endpoint = os.getenv(config.endpoint_var)
        if not endpoint:
            return

        # Try API key first, fall back to Entra auth for Azure endpoints
        if config.key_var:
            api_key: Any = os.getenv(config.key_var)
            if not api_key and "azure" in endpoint.lower():
                if config.target_class is PromptShieldTarget:
                    api_key = get_azure_token_provider("https://cognitiveservices.azure.com/.default")
                else:
                    api_key = get_azure_openai_auth(endpoint)
            elif not api_key:
                return
        elif "azure" in endpoint.lower():
            if config.target_class is PromptShieldTarget:
                api_key = get_azure_token_provider("https://cognitiveservices.azure.com/.default")
            else:
                api_key = get_azure_openai_auth(endpoint)
        else:
            api_key = "not-needed"

        model_name = os.getenv(config.model_var) if config.model_var else None
        underlying_model = os.getenv(config.underlying_model_var) if config.underlying_model_var else None

        # Build kwargs for the target constructor
        kwargs: dict[str, Any] = {
            "endpoint": endpoint,
            "api_key": api_key,
        }

        # Only add model_name if the target supports it (PromptShieldTarget doesn't)
        if model_name is not None:
            kwargs["model_name"] = model_name

        # Add underlying_model if specified (for Azure deployments where name differs from model)
        if underlying_model is not None:
            kwargs["underlying_model"] = underlying_model

        # Add temperature if specified (for scorer-specific temperature variants)
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature

        # Add any extra constructor kwargs (e.g. extra_body_parameters for reasoning).
        # NOTE: extra_kwargs are defined in TARGET_CONFIGS (code-controlled, not user input),
        # so there is no risk of untrusted data overriding safety-critical parameters.
        if config.extra_kwargs:
            kwargs.update(config.extra_kwargs)

        target = config.target_class(**kwargs)
        registry = TargetRegistry.get_registry_singleton()
        registry.register_instance(target, name=config.registry_name)
        logger.info(f"Registered target: {config.registry_name}")
