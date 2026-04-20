# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Dict, List, Literal, Optional, cast

from pyrit.common import get_non_required_value
from pyrit.message_normalizer.chat_message_normalizer import ChatMessageNormalizer
from pyrit.message_normalizer.message_normalizer import (
    MessageStringNormalizer,
    SystemMessageBehavior,
    apply_system_message_behavior,
)
from pyrit.models import Message

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = logging.getLogger(__name__)

# Extended behavior for tokenizer that also supports "developer" role translation
TokenizerSystemBehavior = Literal["keep", "squash", "ignore", "developer"]
"""
Extended system message behavior for tokenizer templates:
- "keep": Keep system messages as-is (default for most models)
- "squash": Merge system message into first user message
- "ignore": Drop system messages entirely
- "developer": Change system role to developer role (for newer OpenAI models)
"""


@dataclass
class TokenizerModelConfig:
    """Configuration for a HuggingFace model's chat template behavior."""

    model_name: str
    """The full HuggingFace model name (e.g., 'meta-llama/Meta-Llama-3-8B-Instruct')."""

    system_message_behavior: TokenizerSystemBehavior = "keep"
    """How to handle system messages. See TokenizerSystemBehavior for options."""


class TokenizerTemplateNormalizer(MessageStringNormalizer):
    """
    Enable application of the chat template stored in a Hugging Face tokenizer
    to a list of messages. For more details, see
    https://huggingface.co/docs/transformers/main/en/chat_templating.
    """

    # Alias mappings for common HuggingFace models
    MODEL_ALIASES: ClassVar[Dict[str, TokenizerModelConfig]] = {
        # No authentication required
        "chatml": TokenizerModelConfig(
            model_name="HuggingFaceH4/zephyr-7b-beta",
        ),
        "phi3": TokenizerModelConfig(
            model_name="microsoft/Phi-3-mini-4k-instruct",
        ),
        "qwen": TokenizerModelConfig(
            model_name="Qwen/Qwen2-7B-Instruct",
        ),
        "falcon": TokenizerModelConfig(
            model_name="tiiuae/falcon-7b-instruct",
        ),
        "openchat": TokenizerModelConfig(
            model_name="openchat/openchat-3.5-0106",
        ),
        "tinyllama": TokenizerModelConfig(
            model_name="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
        ),
        # Gated models (require token parameter)
        "llama3": TokenizerModelConfig(
            model_name="meta-llama/Meta-Llama-3-8B-Instruct",
        ),
        "llama2": TokenizerModelConfig(
            model_name="meta-llama/Llama-2-7b-chat-hf",
        ),
        "mistral": TokenizerModelConfig(
            model_name="mistralai/Mistral-7B-Instruct-v0.2",
        ),
        "gemma": TokenizerModelConfig(
            model_name="google/gemma-7b-it",
            system_message_behavior="squash",
        ),
        # Vision models
        "llama3-vision": TokenizerModelConfig(
            model_name="meta-llama/Llama-3.2-11B-Vision-Instruct",
        ),
    }

    def __init__(
        self,
        *,
        tokenizer: "PreTrainedTokenizerBase",
        system_message_behavior: TokenizerSystemBehavior = "keep",
    ) -> None:
        """
        Initialize an instance of the TokenizerTemplateNormalizer class.

        Args:
            tokenizer: A Hugging Face tokenizer with a chat template.
            system_message_behavior: How to handle system messages. Options:
                - "keep": Keep system messages as-is (default)
                - "squash": Merge system into first user message
                - "ignore": Drop system messages entirely
                - "developer": Change system role to developer role
        """
        self.tokenizer = tokenizer
        self.system_message_behavior = system_message_behavior

    @staticmethod
    def _load_tokenizer(model_name: str, token: Optional[str]) -> "PreTrainedTokenizerBase":
        """
        Load a tokenizer from HuggingFace.

        This is a separate method to make it easy to mock in tests.

        Args:
            model_name: The HuggingFace model name.
            token: Optional authentication token.

        Returns:
            The loaded tokenizer.
        """
        from transformers import AutoTokenizer, PreTrainedTokenizerBase

        return cast(
            PreTrainedTokenizerBase,
            AutoTokenizer.from_pretrained(model_name, token=token or None),  # type: ignore[no-untyped-call]
        )

    @classmethod
    def from_model(
        cls,
        model_name_or_alias: str,
        *,
        token: Optional[str] = None,
        system_message_behavior: Optional[TokenizerSystemBehavior] = None,
    ) -> "TokenizerTemplateNormalizer":
        """
        Create a normalizer from a model name or alias.

        This factory method simplifies creating a normalizer by handling tokenizer
        loading automatically. Use aliases for common models or provide a full
        HuggingFace model path.

        Args:
            model_name_or_alias: Either a full HuggingFace model name or an alias
                (e.g., 'chatml', 'phi3', 'llama3'). See MODEL_ALIASES for available aliases.
            token: Optional HuggingFace token for gated models. If not provided,
                falls back to HUGGINGFACE_TOKEN environment variable.
            system_message_behavior: Override how to handle system messages.
                If not provided, uses the model's default config.

        Returns:
            TokenizerTemplateNormalizer configured with the model's tokenizer.

        Raises:
            ValueError: If the tokenizer doesn't have a chat_template.
        """
        resolved_token = get_non_required_value(env_var_name="HUGGINGFACE_TOKEN", passed_value=token)
        if not resolved_token:
            logger.warning("No HuggingFace token provided. Gated models may fail to load without authentication.")

        # Get config from alias or create default config for custom model
        alias_key = model_name_or_alias.lower()
        if alias_key in cls.MODEL_ALIASES:
            config = cls.MODEL_ALIASES[alias_key]
            model_name = config.model_name
            default_behavior = config.system_message_behavior
        else:
            model_name = model_name_or_alias
            default_behavior = "keep"

        tokenizer = cls._load_tokenizer(model_name, resolved_token)

        if tokenizer.chat_template is None:
            raise ValueError(
                f"Tokenizer for '{model_name}' does not have a chat_template. "
                "Use a model with a built-in chat template or set one manually."
            )

        return cls(
            tokenizer=tokenizer,
            system_message_behavior=(
                system_message_behavior if system_message_behavior is not None else default_behavior
            ),
        )

    async def normalize_string_async(self, messages: List[Message]) -> str:
        """
        Apply the chat template stored in the tokenizer to a list of messages.

        Handles system messages based on the configured system_message_behavior:
        - "keep": Pass system messages as-is
        - "squash": Merge system into first user message
        - "ignore": Drop system messages entirely
        - "developer": Change system role to developer role

        Args:
            messages: A list of Message objects.

        Returns:
            The formatted chat messages as a string.
        """
        # Handle "developer" specially since it's not in base SystemMessageBehavior
        use_developer = self.system_message_behavior == "developer"

        # For squash/ignore/keep, use the shared helper; for developer, treat as "keep"
        base_behavior: SystemMessageBehavior = (
            "keep" if self.system_message_behavior == "developer" else self.system_message_behavior
        )
        processed_messages = await apply_system_message_behavior(messages, base_behavior)

        # Use ChatMessageNormalizer with developer role if needed
        chat_normalizer = ChatMessageNormalizer(use_developer_role=use_developer)
        chat_messages = await chat_normalizer.normalize_async(processed_messages)

        # Convert ChatMessage objects to dicts
        messages_list = [msg.model_dump(exclude_none=True) for msg in chat_messages]

        formatted_messages = str(
            self.tokenizer.apply_chat_template(
                messages_list,
                tokenize=False,
                add_generation_prompt=True,
            )
        )
        return formatted_messages
