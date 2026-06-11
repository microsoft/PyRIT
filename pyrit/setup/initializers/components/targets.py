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
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pyrit.auth import get_azure_openai_auth, get_azure_token_provider
from pyrit.common.parameter import Parameter
from pyrit.models.identifiers import TARGET_EVAL_PARAM_FALLBACKS, TARGET_EVAL_PARAMS
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
    RoundRobinTarget,
)
from pyrit.registry import TargetRegistry
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class TargetInitializerTags(str, Enum):
    """Tags used by TargetInitializer for filtering which targets to register."""

    DEFAULT = "default"
    SCORER = "scorer"
    ALL = "all"
    DEFAULT_OBJECTIVE_TARGET = "default_objective_target"


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
        default_objective_target: If True, tags this target as DEFAULT_OBJECTIVE_TARGET in the registry.
    """

    registry_name: str
    target_class: type[PromptTarget]
    endpoint_var: str
    key_var: str = ""  # Empty string means no auth required
    model_var: str | None = None
    underlying_model_var: str | None = None
    temperature: float | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)
    tags: list[TargetInitializerTags] = field(default_factory=lambda: [TargetInitializerTags.DEFAULT])
    default_objective_target: bool = False


# Define all supported target configurations.
# Only PRIMARY configurations are included here - alias configurations that use ${...}
# syntax in .env_example are excluded since they reference other primary configurations.
ENV_TARGET_CONFIGS: list[TargetConfig] = [
    # ============================================
    # Default Objective Target (generic OPENAI_CHAT_* env vars)
    # ============================================
    TargetConfig(
        registry_name="openai_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="OPENAI_CHAT_ENDPOINT",
        key_var="OPENAI_CHAT_KEY",
        model_var="OPENAI_CHAT_MODEL",
        underlying_model_var="OPENAI_CHAT_UNDERLYING_MODEL",
        default_objective_target=True,
    ),
    # ============================================
    # OpenAI Chat Targets (OpenAIChatTarget)
    # ============================================
    TargetConfig(
        registry_name="platform_openai_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="PLATFORM_OPENAI_CHAT_ENDPOINT",
        key_var="PLATFORM_OPENAI_CHAT_KEY",
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
        registry_name="azure_openai_gpt4o2",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT2",
        key_var="AZURE_OPENAI_GPT4O_KEY2",
        model_var="AZURE_OPENAI_GPT4O_MODEL2",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2",
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
        registry_name="azure_openai_gpt5_4",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT5_4_ENDPOINT",
        key_var="AZURE_OPENAI_GPT5_4_KEY",
        model_var="AZURE_OPENAI_GPT5_4_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT5_4_UNDERLYING_MODEL",
    ),
    TargetConfig(
        registry_name="azure_openai_gpt5_1",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT5_COMPLETIONS_ENDPOINT",
        key_var="AZURE_OPENAI_GPT5_COMPLETIONS_KEY",
        model_var="AZURE_OPENAI_GPT5_COMPLETIONS_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT5_COMPLETIONS_UNDERLYING_MODEL",
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
        registry_name="mai_target",
        target_class=OpenAIChatTarget,
        endpoint_var="MAI_CHAT_ENDPOINT",
        key_var="MAI_CHAT_KEY",
        model_var="MAI_CHAT_MODEL",
        underlying_model_var="MAI_CHAT_UNDERLYING_MODEL",
        extra_kwargs={"httpx_client_kwargs": {"default_query": {"api-version": "2024-05-01-preview"}}},
    ),
    # ============================================
    # Adversarial Chat Target (for scenario attack techniques)
    # ============================================
    TargetConfig(
        registry_name="adversarial_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="ADVERSARIAL_CHAT_ENDPOINT",
        key_var="ADVERSARIAL_CHAT_KEY",
        model_var="ADVERSARIAL_CHAT_MODEL",
        underlying_model_var="ADVERSARIAL_CHAT_UNDERLYING_MODEL",
        temperature=1.2,
    ),
    TargetConfig(
        registry_name="adversarial_chat_singleturn",
        target_class=AzureMLChatTarget,
        endpoint_var="ADVERSARIAL_CHAT_SINGLETURN_ENDPOINT",
        key_var="ADVERSARIAL_CHAT_SINGLETURN_KEY",
        model_var="ADVERSARIAL_CHAT_SINGLETURN_MODEL",
        temperature=1.2,
    ),
    TargetConfig(
        registry_name="adversarial_chat_multiturn",
        target_class=AzureMLChatTarget,
        endpoint_var="ADVERSARIAL_CHAT_MULTITURN_ENDPOINT",
        key_var="ADVERSARIAL_CHAT_MULTITURN_KEY",
        model_var="ADVERSARIAL_CHAT_MULTITURN_MODEL",
        temperature=1.2,
    ),
    TargetConfig(
        registry_name="adversarial_chat_reasoning",
        target_class=AzureMLChatTarget,
        endpoint_var="ADVERSARIAL_CHAT_REASONING_ENDPOINT",
        key_var="ADVERSARIAL_CHAT_REASONING_KEY",
        model_var="ADVERSARIAL_CHAT_REASONING_MODEL",
        temperature=1.2,
    ),
    TargetConfig(
        registry_name="objective_scorer_chat",
        target_class=OpenAIChatTarget,
        endpoint_var="OBJECTIVE_SCORER_CHAT_ENDPOINT",
        key_var="OBJECTIVE_SCORER_CHAT_KEY",
        model_var="OBJECTIVE_SCORER_CHAT_MODEL",
        underlying_model_var="OBJECTIVE_SCORER_CHAT_UNDERLYING_MODEL",
        tags=[TargetInitializerTags.DEFAULT, TargetInitializerTags.SCORER],
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

# Temperature variant targets for scorers.
# These reuse the same endpoints as their base targets but with different temperatures.
# The temp9 variants are tagged DEFAULT because the default scale and task_achieved
# scorers depend on them. The temp0 variants remain SCORER-only.
SCORER_TARGET_CONFIGS: list[TargetConfig] = [
    TargetConfig(
        registry_name="azure_openai_gpt4o_temp0",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_KEY",
        model_var="AZURE_OPENAI_GPT4O_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL",
        temperature=0.0,
        tags=[TargetInitializerTags.SCORER],
    ),
    TargetConfig(
        registry_name="azure_openai_gpt4o_temp9",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_KEY",
        model_var="AZURE_OPENAI_GPT4O_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNDERLYING_MODEL",
        temperature=0.9,
        tags=[TargetInitializerTags.DEFAULT],
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat_temp0",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL",
        temperature=0.0,
        tags=[TargetInitializerTags.SCORER],
    ),
    TargetConfig(
        registry_name="azure_gpt4o_unsafe_chat_temp9",
        target_class=OpenAIChatTarget,
        endpoint_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
        key_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
        model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
        underlying_model_var="AZURE_OPENAI_GPT4O_UNSAFE_CHAT_UNDERLYING_MODEL",
        temperature=0.9,
        tags=[TargetInitializerTags.DEFAULT],
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

    Supported Parameters:
        tags: Target tags to register (list of strings).
            "default" registers the base environment targets.
            "scorer" registers scorer-specific temperature variant targets.
            "all" registers all targets regardless of tag.
            If not provided, only "default" targets are registered.
        auto_group: Whether to automatically create round-robin groups from
            targets with matching behavioral eval params (underlying model,
            temperature, top_p). Defaults to True.

    Supported Endpoints by Category:

    **OpenAI Chat Targets (OpenAIChatTarget):**
    - PLATFORM_OPENAI_CHAT_* - Platform OpenAI Chat API
    - AZURE_OPENAI_GPT4O_* - Azure OpenAI GPT-4o
    - AZURE_OPENAI_INTEGRATION_TEST_* - Integration test endpoint
    - AZURE_OPENAI_GPT3_5_CHAT_* - Azure OpenAI GPT-3.5
    - AZURE_OPENAI_GPT4_CHAT_* - Azure OpenAI GPT-4
    - AZURE_OPENAI_GPT5_4_* - Azure OpenAI GPT-5.4
    - AZURE_OPENAI_GPT5_COMPLETIONS_* - Azure OpenAI GPT-5.1
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
        initializer.params = {"tags": ["default", "scorer"]}
        await initializer.initialize_async()
    """

    def __init__(self) -> None:
        """Initialize the TargetInitializer."""
        super().__init__()
        # Tracks registry names registered by this initializer so that
        # _auto_group_targets only groups targets it owns — not targets
        # that other code may have registered directly into the registry.
        self._registered_names: list[str] = []

    @property
    def supported_parameters(self) -> list[Parameter]:
        """Get the list of parameters this initializer accepts."""
        return [
            Parameter(
                name="tags",
                description="Target tags to register (e.g., ['default'], ['default', 'scorer'], or ['all'])",
                default=["default"],
            ),
            Parameter(
                name="auto_group",
                description="Auto-create round-robin groups from targets with matching behavioral eval params",
                default=True,
            ),
        ]

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

        When ``auto_group`` is True (the default), targets that share the
        same behavioral eval params (underlying model, temperature, top_p)
        and target class are automatically grouped into ``RoundRobinTarget``
        instances for rate-limit distribution and fault tolerance.
        """
        tags = self.params.get("tags", ["default"])
        if TargetInitializerTags.ALL in tags:
            tags = [tag for tag in TargetInitializerTags if tag != TargetInitializerTags.ALL]

        auto_group = self.params.get("auto_group", True)
        # Normalize: params arrive as bool (direct), str, or list[str] (YAML).
        if not isinstance(auto_group, bool):
            value = auto_group[0] if isinstance(auto_group, list) else auto_group
            auto_group = str(value).lower() not in ("false", "0", "no")

        self._registered_names: list[str] = []

        for config in TARGET_CONFIGS:
            if not any(tag in tags for tag in config.tags):
                continue
            self._register_target(config)

        if auto_group:
            self._auto_group_targets()

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

        # Guard against silent fallback to a global OPENAI_CHAT_MODEL default when the
        # declared per-config model env var is unset. Without this skip, the target
        # registers cleanly but sends requests to the wrong model at runtime.
        if config.model_var and not model_name:
            logger.warning(
                "Skipping target '%s': %s is not set. "
                "All declared env vars (endpoint, key, model) must be present for this target to register.",
                config.registry_name,
                config.model_var,
            )
            return

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
        if config.tags:
            registry.add_tags(name=config.registry_name, tags=list(config.tags))
        if config.default_objective_target:
            registry.add_tags(name=config.registry_name, tags=[TargetInitializerTags.DEFAULT_OBJECTIVE_TARGET])
        self._registered_names.append(config.registry_name)
        logger.info(f"Registered target: {config.registry_name}")

    def _auto_group_targets(self) -> None:
        """
        Automatically create round-robin groups from registered targets with
        matching behavioral eval params.

        Groups targets by ``(class_name, underlying_model_name, temperature,
        top_p)`` — the same ``TARGET_EVAL_PARAMS`` checked by
        ``RoundRobinTarget._validate_behavioral_consistency``. For each group
        with 2+ members, creates a ``RoundRobinTarget`` wrapping them.

        The ``RoundRobinTarget`` constructor validates both behavioral AND
        configuration consistency, so any group that would fail validation
        (e.g. different ``TargetConfiguration``) is caught and skipped.
        """
        registry = TargetRegistry.get_registry_singleton()

        # Group registered targets by behavioral key.
        groups: dict[tuple, list[tuple[str, PromptTarget]]] = defaultdict(list)
        for name in self._registered_names:
            target = registry.get_instance_by_name(name)
            if target is None:
                continue
            key = get_behavioral_key(target)
            groups[key].append((name, target))

        for key, members in groups.items():
            if len(members) < 2:
                continue

            # Deduplicate: targets with identical ComponentIdentifier hashes have
            # the exact same config (endpoint, model, api_version, etc.) so including
            # both in a round-robin just wastes a rotation slot. Keep the first
            # occurrence of each hash.
            seen_hashes: set[str | None] = set()
            unique_members: list[tuple[str, PromptTarget]] = []
            for name, target in members:
                target_hash = target.get_identifier().hash
                if target_hash in seen_hashes:
                    logger.debug(f"Skipping duplicate target '{name}' (hash {target_hash}) in auto-group for key {key}")
                    continue
                seen_hashes.add(target_hash)
                unique_members.append((name, target))

            if len(unique_members) < 2:
                continue

            member_names = [name for name, _ in unique_members]
            member_targets = [target for _, target in unique_members]

            try:
                rr_target = RoundRobinTarget(targets=member_targets)
            except ValueError as ex:
                logger.debug(f"Skipping auto-group for behavioral key {key}: {ex}")
                continue

            rr_name = generate_rr_name(key)

            if rr_name in registry:
                logger.debug(f"Skipping auto-group {rr_name}: name already exists in registry")
                continue

            registry.register_instance(rr_target, name=rr_name)

            logger.info(f"Auto-grouped round-robin target: {rr_name} (members: {member_names})")


def get_behavioral_key(target: PromptTarget) -> tuple:
    """
    Extract a hashable behavioral grouping key from a target's identifier.

    Uses ``TARGET_EVAL_PARAMS`` with ``TARGET_EVAL_PARAM_FALLBACKS`` — the
    same params that ``RoundRobinTarget._validate_behavioral_consistency``
    checks. Prepends ``class_name`` so different target types never mix.

    Args:
        target: The target to extract the key from.

    Returns:
        A hashable tuple of ``(class_name, (param, value), ...)``.
    """
    identifier = target.get_identifier()
    parts: list[Any] = [identifier.class_name]
    for param in sorted(TARGET_EVAL_PARAMS):
        value = identifier.params.get(param)
        if (value is None or value == "") and param in TARGET_EVAL_PARAM_FALLBACKS:
            value = identifier.params.get(TARGET_EVAL_PARAM_FALLBACKS[param])
        parts.append((param, value))
    return tuple(parts)


def generate_rr_name(key: tuple) -> str:
    """
    Generate a registry name for an auto-grouped round-robin target.

    Produces names like ``OpenAIChatTarget_gpt-4o_rr`` or
    ``OpenAIChatTarget_gpt-4o_temperature0.0_rr``. Dynamically includes
    all non-None behavioral params from the key.

    Args:
        key: The behavioral grouping key tuple from ``get_behavioral_key``.

    Returns:
        A sanitized registry name string.
    """
    class_name = key[0]
    param_dict = dict(key[1:])

    underlying_model = param_dict.pop("underlying_model_name", None) or "unknown"
    parts = [class_name, underlying_model]

    for param, value in sorted(param_dict.items()):
        if value is None:
            continue
        parts.append(f"{param}{value}")

    parts.append("rr")

    return "_".join(parts)
