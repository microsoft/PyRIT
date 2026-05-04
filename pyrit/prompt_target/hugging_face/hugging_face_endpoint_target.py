# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import warnings

from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute, validate_temperature, validate_top_p

logger = logging.getLogger(__name__)


class HuggingFaceEndpointTarget(PromptTarget):
    """
    The HuggingFaceEndpointTarget interacts with HuggingFace models hosted on cloud endpoints.

    .. deprecated:: 0.13.0
        Use ``OpenAIChatTarget`` with ``endpoint="https://router.huggingface.co/v1"``
        and ``api_key=HUGGINGFACE_TOKEN`` instead. The HuggingFace Inference Providers API
        is OpenAI-compatible, making this target redundant. Will be removed in v0.15.0.
    """

    def __init__(
        self,
        *,
        hf_token: str,
        endpoint: str,
        model_id: str,
        max_tokens: int = 400,
        temperature: float = 1.0,
        top_p: float = 1.0,
        top_k: int | None = None,
        do_sample: bool | None = None,
        repetition_penalty: float | None = None,
        max_requests_per_minute: int | None = None,
        verbose: bool = False,
        custom_configuration: TargetConfiguration | None = None,
        custom_capabilities: TargetCapabilities | None = None,
    ) -> None:
        """
        Initialize the HuggingFaceEndpointTarget with API credentials and model parameters.

        Args:
            hf_token (str): The Hugging Face token for authenticating with the Hugging Face endpoint.
            endpoint (str): The endpoint URL for the Hugging Face model.
            model_id (str): The model ID to be used at the endpoint.
            max_tokens (int): The maximum number of tokens to generate. Defaults to 400.
            temperature (float): The sampling temperature to use. Defaults to 1.0.
            top_p (float): The cumulative probability for nucleus sampling. Defaults to 1.0.
            top_k (int | None): Top-K sampling parameter. Only used when do_sample is True.
                Defaults to None (uses model default).
            do_sample (bool | None): Whether to use sampling instead of greedy decoding.
                Defaults to None.
            repetition_penalty (float | None): Penalty for repeating tokens. Values > 1.0
                discourage repetition. Defaults to None (uses model default).
            max_requests_per_minute (int | None): The maximum number of requests per minute. Defaults to None.
            verbose (bool): Flag to enable verbose logging. Defaults to False.
            custom_configuration (TargetConfiguration | None): Custom configuration for this target instance.
            custom_capabilities (TargetCapabilities | None): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        print_deprecation_message(
            old_item=HuggingFaceEndpointTarget,
            new_item="OpenAIChatTarget with endpoint='https://router.huggingface.co/v1'",
            removed_in="v0.15.0",
        )

        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            verbose=verbose,
            endpoint=endpoint,
            model_name=model_id,
            custom_configuration=custom_configuration,
            custom_capabilities=custom_capabilities,
        )

        validate_temperature(temperature)
        validate_top_p(top_p)

        self.hf_token = hf_token
        self.endpoint = endpoint
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._top_k = top_k
        self._do_sample = do_sample
        self._repetition_penalty = repetition_penalty

        self._warn_if_sampling_params_without_do_sample()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with HuggingFace endpoint-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "temperature": self._temperature,
                "top_p": self._top_p,
                "top_k": self._top_k,
                "do_sample": self._do_sample,
                "repetition_penalty": self._repetition_penalty,
                "max_tokens": self.max_tokens,
            },
        )

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send a normalized prompt asynchronously to a cloud-based HuggingFace model endpoint.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response object with generated text pieces.

        Raises:
            ValueError: If the response from the Hugging Face API is not successful.
            Exception: If an error occurs during the HTTP request to the Hugging Face endpoint.
        """
        message = normalized_conversation[-1]
        request = message.message_pieces[0]
        headers = {"Authorization": f"Bearer {self.hf_token}"}
        parameters: dict[str, object] = {
            "max_tokens": self.max_tokens,
            "temperature": self._temperature,
            "top_p": self._top_p,
        }
        if self._top_k is not None:
            parameters["top_k"] = self._top_k
        if self._do_sample is not None:
            parameters["do_sample"] = self._do_sample
        if self._repetition_penalty is not None:
            parameters["repetition_penalty"] = self._repetition_penalty
        payload: dict[str, object] = {
            "inputs": request.converted_value,
            "parameters": parameters,
        }

        logger.info(f"Sending the following prompt to the cloud endpoint: {request.converted_value}")

        try:
            # Use the utility method to make the request
            response = await make_request_and_raise_if_error_async(
                endpoint_uri=self.endpoint,
                method="POST",
                request_body=payload,
                headers=headers,
                post_type="json",
            )

            response_data = response.json()

            # Check if the response is a list and handle appropriately
            if isinstance(response_data, list):
                # Access the first element if it's a list and extract 'generated_text' safely
                response_message = response_data[0].get("generated_text", "")
            else:
                response_message = response_data.get("generated_text", "")

            message = construct_response_from_request(
                request=request,
                response_text_pieces=[response_message],
                prompt_metadata={"model_id": self.model_id},
            )
            return [message]

        except Exception as e:
            logger.error(f"Error occurred during HTTP request to the Hugging Face endpoint: {e}")
            raise

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        """
        Validate the provided message.

        Args:
            normalized_conversation: The normalized conversation to validate.

        Raises:
            ValueError: If the request is not valid for this target.
        """
        message = normalized_conversation[-1]
        n_pieces = len(message.message_pieces)
        if n_pieces != 1:
            raise ValueError(f"This target only supports a single message piece. Received: {n_pieces} pieces.")

    def _warn_if_sampling_params_without_do_sample(self) -> None:
        """
        Emit a warning when sampling parameters are set but do_sample is not explicitly True.

        Sampling-specific parameters (temperature != 1.0, top_p != 1.0, top_k) are
        ignored by HuggingFace unless do_sample=True.
        """
        has_sampling_override = self._temperature != 1.0 or self._top_p != 1.0 or self._top_k is not None
        if has_sampling_override and self._do_sample is not True:
            warnings.warn(
                "Sampling parameters (temperature, top_p, top_k) are set but do_sample is not True. "
                "HuggingFace ignores these parameters during greedy decoding. "
                "Set do_sample=True to enable sampling.",
                UserWarning,
                stacklevel=3,
            )
