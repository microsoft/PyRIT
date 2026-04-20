# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import re
from abc import abstractmethod
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from openai import (
    AsyncOpenAI,
    BadRequestError,
    ContentFilterFinishReasonError,
    RateLimitError,
)
from openai._exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)

from pyrit.common import default_values
from pyrit.exceptions.exception_classes import (
    RateLimitException,
    handle_bad_request_exception,
)
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_chat_target import PromptChatTarget
from pyrit.prompt_target.openai.openai_error_handling import (
    _extract_error_payload,
    _extract_request_id_from_exception,
    _extract_retry_after_from_exception,
)

logger = logging.getLogger(__name__)


class OpenAITarget(PromptChatTarget):
    """
    Abstract base class for OpenAI-based prompt targets.

    This class provides common functionality for interacting with OpenAI API
    endpoints, handling authentication, rate limiting, and request/response processing.

    Read more about the various models here:
    https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models.
    """

    ADDITIONAL_REQUEST_HEADERS: str = "OPENAI_ADDITIONAL_REQUEST_HEADERS"

    model_name_environment_variable: str
    endpoint_environment_variable: str
    api_key_environment_variable: str
    underlying_model_environment_variable: str

    _async_client: Optional[AsyncOpenAI] = None

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str | Callable[[], str | Awaitable[str]]] = None,
        headers: Optional[str] = None,
        max_requests_per_minute: Optional[int] = None,
        httpx_client_kwargs: Optional[dict[str, Any]] = None,
        underlying_model: Optional[str] = None,
    ) -> None:
        """
        Initialize an instance of OpenAITarget.

        Args:
            model_name (str, Optional): The name of the model (or name of deployment in Azure).
                If no value is provided, the environment variable will be used (set by subclass).
            endpoint (str, Optional): The target URL for the OpenAI service.
            api_key (str | Callable[[], str], Optional): The API key for accessing the OpenAI service,
                or a callable that returns an access token. For Azure endpoints with Entra authentication,
                pass a token provider from pyrit.auth (e.g., get_azure_openai_auth(endpoint)).
                Defaults to the target-specific API key environment variable.
            headers (str, Optional): Extra headers of the endpoint (JSON).
            max_requests_per_minute (int, Optional): Number of requests the target can handle per
                minute before hitting a rate limit. The number of requests sent to the target
                will be capped at the value provided.
            httpx_client_kwargs (dict, Optional): Additional kwargs to be passed to the
                `httpx.AsyncClient()` constructor.
            underlying_model (str, Optional): The underlying model name (e.g., "gpt-4o") used solely for
                target identifier purposes. This is useful when the deployment name in Azure differs
                from the actual model. If not provided, will attempt to fetch from environment variable.
                If it is not there either, the identifier "model_name" attribute will use the model_name.
                Defaults to None.
        """
        self._headers: dict[str, str] = {}
        self._httpx_client_kwargs = httpx_client_kwargs or {}

        request_headers = default_values.get_non_required_value(
            env_var_name=self.ADDITIONAL_REQUEST_HEADERS, passed_value=headers
        )

        if request_headers and isinstance(request_headers, str):
            self._headers = json.loads(request_headers)

        self._set_openai_env_configuration_vars()

        self._model_name: str = default_values.get_required_value(
            env_var_name=self.model_name_environment_variable, passed_value=model_name
        )
        endpoint_value = default_values.get_required_value(
            env_var_name=self.endpoint_environment_variable, passed_value=endpoint
        )

        # Get underlying_model from passed value or environment variable
        underlying_model_value = default_values.get_non_required_value(
            env_var_name=self.underlying_model_environment_variable, passed_value=underlying_model
        )

        # Initialize parent with endpoint and model_name
        PromptChatTarget.__init__(
            self,
            max_requests_per_minute=max_requests_per_minute,
            endpoint=endpoint_value,
            model_name=self._model_name,
            underlying_model=underlying_model_value,
        )

        # API key is required - either from parameter or environment variable
        self._api_key = default_values.get_required_value(
            env_var_name=self.api_key_environment_variable, passed_value=api_key
        )

        self._initialize_openai_client()

    def _extract_deployment_from_azure_url(self, url: str) -> str:
        """
        Extract deployment/model name from Azure OpenAI URL.

        Azure URLs have formats like:
        - https://{resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions
        - https://{resource}.openai.azure.com/openai/deployments/{deployment}/responses

        Args:
            url: The Azure endpoint URL.

        Returns:
            The deployment name, or empty string if not found.
        """
        # Match /deployments/{deployment_name}/
        match = re.search(r"/deployments/([^/]+)/", url)
        if match:
            deployment = match.group(1)
            logger.info(f"Extracted deployment name from URL: {deployment}")
            return deployment

        return ""

    def _warn_old_azure_url_format(self, url: str) -> None:
        """
        Warn users about old Azure URL format without modifying the URL.

        Old formats that trigger warnings:
        - Deployment in path: /openai/deployments/{deployment}/...
        - API version in query: ?api-version=X

        These can appear independently or together.

        Recommended new format: https://{resource}.openai.azure.com/openai/v1
        Pass deployment name as model_name parameter.

        Args:
            url: The Azure endpoint URL to validate.
        """
        parsed = urlparse(url)
        suggested_url = f"{parsed.scheme}://{parsed.netloc}/openai/v1"

        # Check for both deployment in path and api-version
        deployment = self._extract_deployment_from_azure_url(url)
        has_api_version = "api-version" in parsed.query

        # Build the specific issue description
        if deployment and has_api_version:
            issue_desc = "with deployment in path and api-version parameter"
            recommendation = (
                f"with deployment '{deployment}' passed as model_name parameter and api-version parameter removed"
            )
        elif deployment:
            issue_desc = "with deployment in path"
            recommendation = f"with deployment '{deployment}' passed as model_name parameter"
        elif has_api_version:
            issue_desc = "with api-version parameter"
            recommendation = "without api-version parameter"
        else:
            return  # No issues found

        logger.warning(
            f"Old Azure URL format detected {issue_desc}. "
            f"Current URL: {url}. "
            f"Recommended format: {suggested_url} {recommendation}. "
            f"Old format URLs will be deprecated in a future release. "
            f"See https://learn.microsoft.com/en-us/azure/ai-services/openai/api-version-deprecation "
            "for more information."
        )

    @abstractmethod
    def _get_target_api_paths(self) -> list[str]:
        """
        Return list of API paths that should not be in the URL for this target.

        The SDK automatically appends these paths, so they shouldn't be in the base URL.

        Returns:
            List of API paths (e.g., ["/chat/completions", "/v1/chat/completions"])
        """
        pass

    @abstractmethod
    def _get_provider_examples(self) -> dict[str, str]:
        """
        Return provider-specific example URLs for this target.

        Used in warnings to show users the correct format.

        Returns:
            Dict mapping provider patterns to example URLs
            (e.g., {".openai.azure.com": "https://{resource}.openai.azure.com/openai/v1"})
        """
        pass

    def _validate_url_for_target(self, endpoint_url: str) -> None:
        """
        Validate the URL format for this specific target and warn about issues.

        Checks for:
        - API-specific paths that should not be in the URL
        - Query parameters like api-version

        This method does NOT modify the URL - it only logs warnings.

        Args:
            endpoint_url: The endpoint URL to validate.
        """
        # Check for API paths that shouldn't be in the URL
        api_paths = self._get_target_api_paths()
        provider_examples = self._get_provider_examples()

        for api_path in api_paths:
            if api_path in endpoint_url:
                self._warn_url_with_api_path(endpoint_url, api_path, provider_examples)
                break  # Only warn once

        # Warn if query parameters are present
        self._warn_url_with_query_params(endpoint_url)

    def _warn_azure_url_path_issues(self, endpoint_url: str) -> None:
        """
        Warn about Azure URL path structure issues without modifying the URL.

        Expected formats:
        - Azure OpenAI: https://{resource}.openai.azure.com/openai/v1
        - Azure Foundry: https://{resource}.models.ai.azure.com (no /openai/v1 needed)

        Args:
            endpoint_url: The Azure endpoint URL to validate.
        """
        parsed = urlparse(endpoint_url)

        if ".openai.azure.com" in endpoint_url:
            # Check for various issues with Azure OpenAI URLs
            path = parsed.path.rstrip("/")

            if not path or path == "":
                logger.warning(
                    f"Azure OpenAI URL is missing path structure. "
                    f"Current: {endpoint_url}. "
                    f"Recommended: {endpoint_url.rstrip('/')}/openai/v1"
                )
            elif path == "/openai":
                logger.warning(
                    f"Azure OpenAI URL is missing /v1 suffix. "
                    f"Current: {endpoint_url}. "
                    f"Recommended: {endpoint_url.rstrip('/')}/v1"
                )
            elif not path.endswith("/openai/v1") and not path.startswith("/openai/v1"):
                # Check if it has an API extension that should be removed
                if any(
                    api_path in path
                    for api_path in [
                        "/chat/completions",
                        "/responses",
                        "/completions",
                        "/videos",
                        "/images/generations",
                        "/audio/speech",
                    ]
                ):
                    # This is handled by target-specific validation
                    pass
                elif "/openai" not in path:
                    logger.warning(
                        f"Azure OpenAI URL should include /openai/v1 path. "
                        f"Current: {endpoint_url}. "
                        f"Recommended: {parsed.scheme}://{parsed.netloc}/openai/v1"
                    )

    def _initialize_openai_client(self) -> None:
        """
        Initialize the OpenAI client using AsyncOpenAI.

        Validates the URL format and warns about potential issues, but does NOT modify
        the user-provided URL. This allows flexibility for custom endpoints and non-standard
        providers while helping users identify common configuration mistakes.

        Supported formats:
        - Platform OpenAI: https://api.openai.com/v1
        - Azure OpenAI: https://{resource}.openai.azure.com/openai/v1
        - Azure Foundry: https://{resource}.models.ai.azure.com
        - Anthropic: https://api.anthropic.com/v1
        - Google Gemini: https://generativelanguage.googleapis.com/v1beta/openai
        - Custom endpoints: Any format (warnings may be shown but URL is not modified)
        """
        # Merge custom headers with httpx_client_kwargs
        httpx_kwargs = self._httpx_client_kwargs.copy()
        if self._headers:
            httpx_kwargs.setdefault("default_headers", {}).update(self._headers)

        # Determine if this is Azure OpenAI based on the endpoint
        is_azure = "azure" in self._endpoint.lower() if self._endpoint else False

        # Warn about old Azure format but don't modify
        warned_old_format = False
        if is_azure:
            parsed_url = urlparse(self._endpoint)
            # Check if it has api-version query parameter OR /deployments/ in path
            has_api_version = "api-version" in parsed_url.query
            has_deployments = "/deployments/" in parsed_url.path

            if has_deployments or has_api_version:
                self._warn_old_azure_url_format(self._endpoint)
                warned_old_format = True

        # Validate URL format for target-specific issues
        # Skip if we already warned about old format (to avoid duplicate warnings)
        if not warned_old_format:
            self._validate_url_for_target(self._endpoint)

        # Warn about Azure path structure issues
        if is_azure:
            self._warn_azure_url_path_issues(self._endpoint)

        # Use endpoint as-is - the user knows their provider best
        base_url = self._endpoint

        # Pass api_key directly to the SDK - it handles both strings and callables
        self._async_client = AsyncOpenAI(
            base_url=base_url,
            api_key=self._api_key,
            **httpx_kwargs,
        )

    async def _handle_openai_request(
        self,
        *,
        api_call: Callable[..., Any],
        request: Message,
    ) -> Message:
        """
        Unified error handling wrapper for all OpenAI SDK requests.

        This method wraps any OpenAI SDK call and handles all common error scenarios:
        - Content filtering (both proactive checks and SDK exceptions)
        - Bad request errors (400s with content filter detection)
        - Rate limiting (429s with retry-after extraction)
        - API status errors (other HTTP errors)
        - Transient errors (timeouts, connection issues)
        - Authentication errors

        Automatically detects the response type and applies appropriate validation and content
        filter checks via abstract methods. On success, constructs and returns a Message object.

        Args:
            api_call: Async callable that invokes the OpenAI SDK method.
            request: The Message representing the user's request (for error responses).

        Returns:
            Message: The constructed message response (success or error).

        Raises:
            RateLimitException: For 429 rate limit errors.
            APIStatusError: For other API status errors.
            APITimeoutError: For transient infrastructure errors.
            APIConnectionError: For transient infrastructure errors.
            AuthenticationError: For authentication failures.
        """
        try:
            # Execute the API call
            response = await api_call()

            # Extract MessagePiece for validation and construction (most targets use single piece)
            request_piece = request.message_pieces[0] if request.message_pieces else None

            # Check for content filter via subclass implementation
            if self._check_content_filter(response):
                return self._handle_content_filter_response(response, request_piece)

            # Validate response via subclass implementation
            error_message = self._validate_response(response, request_piece)
            if error_message:
                return error_message

            # Construct and return Message from validated response
            return await self._construct_message_from_response(response, request_piece)

        except ContentFilterFinishReasonError as e:
            # Content filter error raised by SDK during parse/structured output flows
            request_id = _extract_request_id_from_exception(e)
            logger.error(f"Content filter error (SDK raised). request_id={request_id} error={e}")

            # Convert exception to response-like object for consistent handling
            error_str = str(e)

            class _ErrorResponse:
                def model_dump_json(self) -> str:
                    return error_str

            request_piece = request.message_pieces[0] if request.message_pieces else None
            return self._handle_content_filter_response(_ErrorResponse(), request_piece)
        except BadRequestError as e:
            # Handle 400 errors - includes input policy filters and some Azure output-filter 400s
            payload, is_content_filter = _extract_error_payload(e)
            request_id = _extract_request_id_from_exception(e)

            # Safely serialize payload for logging
            try:
                payload_str = payload if isinstance(payload, str) else json.dumps(payload)[:200]
            except (TypeError, ValueError):
                # If JSON serialization fails (e.g., contains non-serializable objects), use str()
                payload_str = str(payload)[:200]

            logger.warning(
                f"BadRequestError request_id={request_id} is_content_filter={is_content_filter} payload={payload_str}"
            )

            request_piece = request.message_pieces[0] if request.message_pieces else None
            return handle_bad_request_exception(
                response_text=str(payload),
                request=request_piece,
                error_code=400,
                is_content_filter=is_content_filter,
            )
        except RateLimitError as e:
            # SDK's RateLimitError (429)
            request_id = _extract_request_id_from_exception(e)
            retry_after = _extract_retry_after_from_exception(e)
            logger.warning(f"RateLimitError request_id={request_id} retry_after={retry_after} error={e}")
            raise RateLimitException()
        except APIStatusError as e:
            # Other API status errors - check for 429 here as well
            request_id = _extract_request_id_from_exception(e)
            if getattr(e, "status_code", None) == 429:
                retry_after = _extract_retry_after_from_exception(e)
                logger.warning(f"429 via APIStatusError request_id={request_id} retry_after={retry_after}")
                raise RateLimitException()
            else:
                logger.exception(
                    f"APIStatusError request_id={request_id} status={getattr(e, 'status_code', None)} error={e}"
                )
                raise
        except (APITimeoutError, APIConnectionError) as e:
            # Transient infrastructure errors - these are retryable
            request_id = _extract_request_id_from_exception(e)
            logger.warning(f"Transient API error ({e.__class__.__name__}) request_id={request_id} error={e}")
            raise
        except AuthenticationError as e:
            # Authentication errors - non-retryable, surface quickly
            request_id = _extract_request_id_from_exception(e)
            logger.error(f"Authentication error request_id={request_id} error={e}")
            raise

    @abstractmethod
    async def _construct_message_from_response(self, response: Any, request: MessagePiece) -> Message:
        """
        Construct a Message from the OpenAI SDK response.

        This method extracts the relevant data from the SDK response object and
        constructs a Message with appropriate message pieces. It may include
        async operations like saving files for image/audio/video responses.

        Args:
            response: The response object from OpenAI SDK (e.g., ChatCompletion, Response, etc.).
            request: The original request MessagePiece.

        Returns:
            Message: Constructed message with extracted content.
        """
        pass

    def _check_content_filter(self, response: Any) -> bool:
        """
        Check if the response indicates content filtering.

        Override this method in subclasses that need content filter detection.
        Default implementation returns False (no content filter).

        Args:
            response: The response object from OpenAI SDK.

        Returns:
            bool: True if content filter detected, False otherwise.
        """
        return False

    def _handle_content_filter_response(self, response: Any, request: MessagePiece) -> Message:
        """
        Handle content filter errors by creating a proper error Message.

        Args:
            response: The response object from OpenAI SDK.
            request: The original request message piece.

        Returns:
            Message object with error type indicating content was filtered.
        """
        logger.warning("Output content filtered by content policy.")
        return handle_bad_request_exception(
            response_text=response.model_dump_json(),
            request=request,
            error_code=200,
            is_content_filter=True,
        )

    def _validate_response(self, response: Any, request: MessagePiece) -> Optional[Message]:
        """
        Validate the response and return error Message if needed.

        Override this method in subclasses that need custom response validation.
        Default implementation returns None (no validation errors).

        Args:
            response: The response object from OpenAI SDK.
            request: The original request MessagePiece.

        Returns:
            Optional[Message]: Error Message if validation fails, None otherwise.

        Raises:
            Various exceptions for validation failures.
        """
        return None

    @abstractmethod
    def _set_openai_env_configuration_vars(self) -> None:
        """
        Set deployment_environment_variable, endpoint_environment_variable,
        and api_key_environment_variable which are read from .env file.
        """
        raise NotImplementedError

    def _warn_url_with_api_path(
        self, endpoint_url: str, api_path: str, provider_examples: dict[str, str] = None
    ) -> None:
        """
        Warn if URL includes API-specific path that should be handled by the SDK.

        Args:
            endpoint_url: The endpoint URL to check.
            api_path: The API path to check for (e.g., "/chat/completions", "/responses").
            provider_examples: Optional dict mapping provider patterns to example base URLs.
        """
        if api_path in endpoint_url:
            parsed = urlparse(endpoint_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.replace(api_path, '')}"

            message = (
                f"URL includes API path '{api_path}' which the OpenAI SDK handles automatically. "
                f"Current URL: {endpoint_url}. "
                f"Recommended: Remove '{api_path}' from the URL. "
            )

            # Add provider-specific guidance
            if provider_examples:
                for pattern, example in provider_examples.items():
                    if pattern in endpoint_url:
                        message += f"Example: {example}. "
                        break
            else:
                message += f"Suggested: {base_url}. "

            logger.warning(message)

    def _warn_url_with_query_params(self, endpoint_url: str) -> None:
        """
        Warn if URL includes query parameters like api-version.

        Args:
            endpoint_url: The endpoint URL to check.
        """
        parsed = urlparse(endpoint_url)
        if parsed.query:
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            logger.warning(
                f"URL includes query parameters '{parsed.query}' which should be removed. "
                f"Current URL: {endpoint_url}. "
                f"Recommended: {base_url}"
            )

    def _warn_if_irregular_endpoint(self, expected_url_regex: list[str]) -> None:
        """
        Validate that the endpoint URL ends with one of the expected routes for this OpenAI target.

        Args:
            expected_url_regex: Expected regex pattern(s) for this target. Should be a list of regex strings.

        Prints a warning if the endpoint doesn't match any of the expected routes.
        This validation helps ensure the endpoint is configured correctly for the specific API.
        """
        if not self._endpoint or not expected_url_regex:
            return

        # Use urllib to extract the path part and normalize it
        parsed_url = urlparse(self._endpoint)
        normalized_route = parsed_url.path.lower().rstrip("/")

        # Check if the endpoint matches any of the expected regex patterns
        for regex_pattern in expected_url_regex:
            if re.search(regex_pattern, normalized_route):
                return

        # No matches found, log warning
        if len(expected_url_regex) == 1:
            # Convert regex back to human-readable format for the warning
            pattern_str = expected_url_regex[0].replace(r"[^/]+", "*").replace("$", "")
            expected_routes_str = pattern_str
        else:
            # Convert all regex patterns to human-readable format
            readable_patterns = [p.replace(r"[^/]+", "*").replace("$", "") for p in expected_url_regex]
            expected_routes_str = f"one of: {', '.join(readable_patterns)}"

        logger.warning(
            f"The provided endpoint URL {parsed_url} does not match any of the expected formats: {expected_routes_str}."
            f"This may be intentional, especially if you are using an endpoint other than Azure or OpenAI."
            f"For more details and guidance, please see the .env_example file in the repository."
        )

    @abstractmethod
    def is_json_response_supported(self) -> bool:
        """
        Abstract method to determine if JSON response format is supported by the target.

        Returns:
            bool: True if JSON response is supported, False otherwise.
        """
        pass
