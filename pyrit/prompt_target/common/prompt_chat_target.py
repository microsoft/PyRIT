# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import MessagePiece
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


class PromptChatTarget(PromptTarget):
    """
    A prompt chat target is a target where you can explicitly set the conversation history using memory.

    Some algorithms require conversation to be modified (e.g. deleting the last message) or set explicitly.
    These algorithms will require PromptChatTargets be used.

    As a concrete example, OpenAI chat targets are PromptChatTargets. You can set made-up conversation history.
    Realtime chat targets or OpenAI completions are NOT PromptChatTargets. You don't send the conversation history.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
        )
    )

    def __init__(
        self,
        *,
        max_requests_per_minute: Optional[int] = None,
        endpoint: str = "",
        model_name: str = "",
        underlying_model: Optional[str] = None,
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the PromptChatTarget.

        Args:
            max_requests_per_minute (int, Optional): Maximum number of requests per minute.
            endpoint (str): The endpoint URL. Defaults to empty string.
            model_name (str): The model name. Defaults to empty string.
            underlying_model (str, Optional): The underlying model name (e.g., "gpt-4o") for
                identification purposes. This is useful when the deployment name in Azure differs
                from the actual model. Defaults to None.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration
                for this target instance. If None, uses the class-level defaults. Defaults to None.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint,
            model_name=model_name,
            underlying_model=underlying_model,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )

    def set_system_prompt(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
        attack_identifier: Optional[ComponentIdentifier] = None,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Set the system prompt for the prompt target. May be overridden by subclasses.

        Raises:
            RuntimeError: If the conversation already exists.
        """
        messages = self._memory.get_conversation(conversation_id=conversation_id)

        if messages:
            raise RuntimeError("Conversation already exists, system prompt needs to be set at the beginning")

        self._memory.add_message_to_memory(
            request=MessagePiece(
                role="system",
                conversation_id=conversation_id,
                original_value=system_prompt,
                converted_value=system_prompt,
                prompt_target_identifier=self.get_identifier(),
                attack_identifier=attack_identifier,
                labels=labels,
            ).to_message()
        )

    def is_response_format_json(self, message_piece: MessagePiece) -> bool:
        """
        Check if the response format is JSON and ensure the target supports it.

        Args:
            message_piece: A MessagePiece object with a `prompt_metadata` dictionary that may
                include a "response_format" key.

        Returns:
            bool: True if the response format is JSON, False otherwise.

        Raises:
            ValueError: If "json" response format is requested but unsupported.
        """
        config = self._get_json_response_config(message_piece=message_piece)
        return config.enabled

    def _get_json_response_config(self, *, message_piece: MessagePiece) -> _JsonResponseConfig:
        """
        Get the JSON response configuration from the message piece metadata.

        Args:
            message_piece: A MessagePiece object with a `prompt_metadata` dictionary that may
                include JSON response configuration.

        Returns:
            _JsonResponseConfig: The JSON response configuration.

        Raises:
            ValueError: If JSON response format is requested but unsupported.
        """
        config = _JsonResponseConfig.from_metadata(metadata=message_piece.prompt_metadata)

        if config.enabled and not self.capabilities.supports_json_output:
            target_name = self.get_identifier().class_name
            raise ValueError(f"This target {target_name} does not support JSON response format.")

        return config
