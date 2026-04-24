# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import warnings
from typing import Any

from httpx import HTTPStatusError

from pyrit.common import default_values, net_utility
from pyrit.exceptions import (
    EmptyResponseException,
    RateLimitException,
    handle_bad_request_exception,
    pyrit_target_retry,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.message_normalizer import ChatMessageNormalizer, MessageListNormalizer
from pyrit.models import (
    Message,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p

logger = logging.getLogger(__name__)


class AzureMLChatTarget(PromptChatTarget):
    """
    A prompt target for Azure Machine Learning chat endpoints.

    This class works with most chat completion Instruct models deployed on Azure AI Machine Learning
    Studio endpoints (including but not limited to: mistralai-Mixtral-8x7B-Instruct-v01,
    mistralai-Mistral-7B-Instruct-v01, Phi-3.5-MoE-instruct, Phi-3-mini-4k-instruct,
    Llama-3.2-3B-Instruct, and Meta-Llama-3.1-8B-Instruct).

    Please create or adjust environment variables (endpoint and key) as needed for the model you are using.
    """

    endpoint_uri_environment_variable: str = "AZURE_ML_MANAGED_ENDPOINT"
    api_key_environment_variable: str = "AZURE_ML_KEY"

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            supports_editable_history=True,
            supports_multi_turn=True,
            supports_system_prompt=True,
        )
    )

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        model_name: str = "",
        message_normalizer: MessageListNormalizer[Any] | None = None,
        max_new_tokens: int = 400,
        temperature: float = 1.0,
        top_p: float = 1.0,
        repetition_penalty: float = 1.0,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
        custom_capabilities: TargetCapabilities | None = None,
        **param_kwargs: Any,
    ) -> None:
        """
        Initialize an instance of the AzureMLChatTarget class.

        Args:
            endpoint (str | None): The endpoint URL for the deployed Azure ML model.
                Defaults to the value of the AZURE_ML_MANAGED_ENDPOINT environment variable.
            api_key (str | None): The API key for accessing the Azure ML endpoint.
                Defaults to the value of the `AZURE_ML_KEY` environment variable.
            model_name (str): The name of the model being used (e.g., "Llama-3.2-3B-Instruct").
                Used for identification purposes. Defaults to empty string.
            message_normalizer (MessageListNormalizer[Any] | None): **Deprecated.** Use
                ``custom_configuration`` with ``CapabilityHandlingPolicy`` instead. Previously used for
                models that do not allow system prompts.
                Will be removed in v0.15.0.
            max_new_tokens (int): The maximum number of tokens to generate in the response.
                Defaults to 400.
            temperature (float): The temperature for generating diverse responses. 1.0 is most random,
                0.0 is least random. Defaults to 1.0.
            top_p (float): The top-p value for generating diverse responses. It represents
                the cumulative probability of the top tokens to keep. Defaults to 1.0.
            repetition_penalty (float): The repetition penalty for generating diverse responses.
                1.0 means no penalty with a greater value (up to 2.0) meaning more penalty for repeating tokens.
                Defaults to 1.2.
            max_requests_per_minute (int | None): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            custom_configuration (TargetConfiguration | None): Override the default configuration for this target
                instance. Useful for targets whose capabilities depend on deployment configuration.
            custom_capabilities (TargetCapabilities | None): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.15.0.
            **param_kwargs: Additional parameters to pass to the model for generating responses. Example
                parameters can be found here: https://huggingface.co/docs/api-inference/tasks/text-generation.
                Note that the link above may not be comprehensive, and specific acceptable parameters may be
                model-dependent. If a model does not accept a certain parameter that is passed in, it will be skipped
                without throwing an error.

        Raises:
            ValueError: If both `message_normalizer` and `custom_configuration` are provided,
                since `message_normalizer` is deprecated and the two configurations may conflict.
        """
        endpoint_value = default_values.get_required_value(
            env_var_name=self.endpoint_uri_environment_variable, passed_value=endpoint
        )

        # Translate legacy message_normalizer into TargetConfiguration
        if message_normalizer is not None:
            if custom_configuration is not None:
                raise ValueError(
                    "Cannot specify both 'message_normalizer' and 'custom_configuration'. "
                    "Use 'custom_configuration' only; 'message_normalizer' is deprecated and "
                    "will be removed in v0.15.0."
                )
            warnings.warn(
                "Passing message_normalizer is deprecated. Use custom_configuration with "
                "CapabilityHandlingPolicy instead. Will be removed in v0.15.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            # The legacy message_normalizer was primarily used to handle system prompts
            # for models that don't support them (e.g. GenericSystemSquashNormalizer).
            # We translate it into a TargetConfiguration that marks system_prompt as
            # unsupported + ADAPT so the pipeline invokes the user's normalizer.
            default_caps = self._DEFAULT_CONFIGURATION.capabilities
            default_behaviors = dict(self._DEFAULT_CONFIGURATION.policy.behaviors)
            default_behaviors[CapabilityName.SYSTEM_PROMPT] = UnsupportedCapabilityBehavior.ADAPT
            custom_configuration = TargetConfiguration(
                capabilities=TargetCapabilities(
                    supports_multi_message_pieces=default_caps.supports_multi_message_pieces,
                    supports_editable_history=default_caps.supports_editable_history,
                    supports_multi_turn=default_caps.supports_multi_turn,
                    supports_system_prompt=False,
                ),
                policy=CapabilityHandlingPolicy(behaviors=default_behaviors),
                normalizer_overrides={CapabilityName.SYSTEM_PROMPT: message_normalizer},
            )

        PromptChatTarget.__init__(
            self,
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint_value,
            model_name=model_name,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )

        self._initialize_vars(endpoint=endpoint, api_key=api_key)

        validate_temperature(temperature)
        validate_top_p(top_p)

        self._max_new_tokens = max_new_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._repetition_penalty = repetition_penalty
        self._extra_parameters = param_kwargs

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with Azure ML-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "temperature": self._temperature,
                "top_p": self._top_p,
                "max_new_tokens": self._max_new_tokens,
                "repetition_penalty": self._repetition_penalty,
            },
        )

    def _initialize_vars(self, endpoint: str | None = None, api_key: str | None = None) -> None:
        """
        Set the endpoint and key for accessing the Azure ML model. Use this function to manually
        pass in your own endpoint uri and api key. Defaults to the values in the .env file for the variables
        stored in self.endpoint_uri_environment_variable and self.api_key_environment_variable (which default to
        "AZURE_ML_MANAGED_ENDPOINT" and "AZURE_ML_KEY" respectively). It is recommended to set these variables
        in the .env file and call _set_env_configuration_vars rather than passing the uri and key directly to
        this function or the target constructor.

        Args:
            endpoint (str, optional): The endpoint uri for the deployed Azure ML model.
            api_key (str, optional): The API key for accessing the Azure ML endpoint.
        """
        self._endpoint = default_values.get_required_value(
            env_var_name=self.endpoint_uri_environment_variable, passed_value=endpoint
        )
        self._api_key = default_values.get_required_value(
            env_var_name=self.api_key_environment_variable, passed_value=api_key
        )

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously send a message to the Azure ML chat target.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response from the prompt target.

        Raises:
            EmptyResponseException: If the response from the chat is empty.
            RateLimitException: If the target rate limit is exceeded.
            HTTPStatusError: For any other HTTP errors during the process.
        """
        message = normalized_conversation[-1]
        request = message.message_pieces[0]

        logger.info(f"Sending the following prompt to the prompt target: {request}")

        try:
            resp_text = await self._complete_chat_async(
                messages=normalized_conversation,
            )

            if not resp_text:
                raise EmptyResponseException(message="The chat returned an empty response.")

            response_entry = construct_response_from_request(request=request, response_text_pieces=[resp_text])
        except HTTPStatusError as hse:
            if hse.response.status_code == 400:
                # Handle Bad Request
                response_entry = handle_bad_request_exception(response_text=hse.response.text, request=request)
            elif hse.response.status_code == 429:
                raise RateLimitException from hse
            else:
                raise hse

        logger.info("Received the following response from the prompt target" + f"{response_entry.get_value()}")
        return [response_entry]

    @pyrit_target_retry
    async def _complete_chat_async(
        self,
        messages: list[Message],
    ) -> str:
        """
        Completes a chat interaction by generating a response to the given input prompt.

        This is a synchronous wrapper for the asynchronous _generate_and_extract_response method.

        Args:
            messages (list[Message]): The message objects containing the role and content.

        Raises:
            EmptyResponseException: If the response from the chat is empty.
            Exception: For any other errors during the process.

        Returns:
            str: The generated response message.
        """
        headers = self._get_headers()
        payload = await self._construct_http_body_async(messages)

        response = await net_utility.make_request_and_raise_if_error_async(
            endpoint_uri=self._endpoint, method="POST", request_body=payload, headers=headers
        )

        try:
            return str(response.json()["output"])
        except Exception as e:
            if response.json() == {}:
                raise EmptyResponseException(message="The chat returned an empty response.") from e
            raise type(e)(
                f"Exception obtaining response from the target. Returned response: {response.json()}. "
                f"Exception: {str(e)}"
            ) from e

    async def _construct_http_body_async(
        self,
        messages: list[Message],
    ) -> dict[str, Any]:
        """
        Construct the HTTP request body for the AML online endpoint.

        Args:
            messages: List of chat messages to include in the request body.

        Returns:
            dict: The constructed HTTP request body.
        """
        wire_format = ChatMessageNormalizer()
        messages_dict = await wire_format.normalize_to_dicts_async(messages)

        # Parameters include additional ones passed in through **kwargs. Those not accepted by the model will
        # be ignored. We only include commonly supported parameters here - model-specific parameters like
        # stop sequences should be passed via **param_kwargs since different models use different EOS tokens.
        return {
            "input_data": {
                "input_string": messages_dict,
                "parameters": {
                    "max_new_tokens": self._max_new_tokens,
                    "temperature": self._temperature,
                    "top_p": self._top_p,
                    "repetition_penalty": self._repetition_penalty,
                }
                | self._extra_parameters,
            }
        }

    def _get_headers(self) -> dict[str, str]:
        """
        Headers for accessing inference endpoint deployed in AML.

        Returns:
            headers(dict): contains bearer token as AML key and content-type: JSON
        """
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": ("Bearer " + self._api_key),
        }

        return headers

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass
