# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Union, cast

import msal
from azure.core.credentials import AccessToken
from azure.identity import (
    AzureCliCredential,
    DefaultAzureCredential,
    InteractiveBrowserCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from azure.identity.aio import (
    get_bearer_token_provider as get_async_bearer_token_provider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import azure.cognitiveservices.speech as speechsdk

from pyrit.auth.auth_config import REFRESH_TOKEN_BEFORE_MSEC
from pyrit.auth.authenticator import Authenticator

logger = logging.getLogger(__name__)


class TokenProviderCredential:
    """
    Wrapper to convert a token provider callable into an Azure TokenCredential.

    This class bridges the gap between token provider functions (like those returned by
    get_azure_token_provider) and Azure SDK clients that require a TokenCredential object.
    """

    def __init__(self, token_provider: Callable[[], Union[str, Callable[..., Any]]]) -> None:
        """
        Initialize TokenProviderCredential.

        Args:
            token_provider: A callable that returns either a token string or an awaitable that returns a token string.
        """
        self._token_provider = token_provider

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        """
        Get an access token.

        Args:
            scopes: Token scopes (ignored as the scope is already configured in the token provider).
            kwargs: Additional arguments (ignored).

        Returns:
            AccessToken: The access token with expiration time.
        """
        token = self._token_provider()
        # Set expiration far in the future - the provider handles refresh
        expires_on = int(time.time()) + 3600
        return AccessToken(str(token), expires_on)


class AzureAuth(Authenticator):
    """
    Azure CLI Authentication.
    """

    access_token: AccessToken
    _token_scope: str

    def __init__(self, token_scope: str, tenant_id: str = ""):
        """
        Initialize Azure authentication.

        Args:
            token_scope (str): The token scope for authentication.
            tenant_id (str, optional): The tenant ID. Defaults to "".
        """
        self._tenant_id = tenant_id
        self._token_scope = token_scope
        self._set_default_token()

    def _set_default_token(self) -> None:
        """
        Set up default Azure credentials and retrieve access token.
        """
        self.azure_creds = DefaultAzureCredential()
        self.access_token = self.azure_creds.get_token(self._token_scope)
        self.token = self.access_token.token

    def refresh_token(self) -> str:
        """
        Refresh the access token if it is expired.

        Returns:
            str: A token
        """
        curr_epoch_time_in_ms = int(time.time()) * 1_000
        access_token_epoch_expiration_time_in_ms = int(self.access_token.expires_on) * 1_000
        # Adjust the expiration time to be before the actual expiration time so that user can use the token
        # for a while before it expires. This improves user experience. The token is refreshed REFRESH_TOKEN_BEFORE_MSEC
        # before it expires.
        token_expires_on_in_ms = access_token_epoch_expiration_time_in_ms - REFRESH_TOKEN_BEFORE_MSEC
        if token_expires_on_in_ms <= curr_epoch_time_in_ms:
            # Token is expired, generate a new one
            self._set_default_token()
        return self.token

    def get_token(self) -> str:
        """
        Get the current token.

        Returns:
            str: current token
        """
        return self.token


def get_access_token_from_azure_cli(*, scope: str, tenant_id: str = "") -> str:
    """
    Get access token from Azure CLI.

    Args:
        scope (str): The scope to request.
        tenant_id (str, optional): The tenant ID. Defaults to "".

    Returns:
        str: The access token.
    """
    try:
        credential = AzureCliCredential(tenant_id=tenant_id)
        token = credential.get_token(scope)
        return cast("str", token.token)
    except Exception as e:
        logger.error(f"Failed to obtain token for '{scope}' with tenant ID '{tenant_id}': {e}")
        raise


def get_access_token_from_azure_msi(*, client_id: str, scope: str) -> str:
    """
    Connect to an AOAI endpoint via managed identity credential attached to an Azure resource.
    For proper setup and configuration of MSI
    https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview.

    Args:
        client_id (str): The client ID of the service
        scope (str): The scope to request

    Returns:
        str: Authentication token
    """
    try:
        credential = ManagedIdentityCredential(client_id=client_id)
        token = credential.get_token(scope)
        return cast("str", token.token)
    except Exception as e:
        logger.error(f"Failed to obtain token for '{scope}' with client ID '{client_id}': {e}")
        raise


def get_access_token_from_msa_public_client(*, client_id: str, scope: str) -> str:
    """
    Use MSA account to connect to an AOAI endpoint via interactive login. A browser window
    will open and ask for login credentials.

    Args:
        client_id (str): The client ID of the service
        scope (str): The scope to request

    Returns:
        str: Authentication token
    """
    try:
        app = msal.PublicClientApplication(client_id)
        result = app.acquire_token_interactive(scopes=[scope])
        return cast("str", result["access_token"])
    except Exception as e:
        logger.error(f"Failed to obtain token for '{scope}' with client ID '{client_id}': {e}")
        raise


def get_access_token_from_interactive_login(scope: str) -> str:
    """
    Connect to an OpenAI endpoint with an interactive login from Azure. A browser window will
    open and ask for login credentials.  The token will be scoped for Azure Cognitive services.

    Args:
        scope (str): The scope to request

    Returns:
        str: Authentication token
    """
    try:
        token_provider = get_bearer_token_provider(InteractiveBrowserCredential(), scope)
        return token_provider()
    except Exception as e:
        logger.error(f"Failed to obtain token for '{scope}': {e}")
        raise


def get_azure_token_provider(scope: str) -> Callable[[], str]:
    """
    Get a synchronous Azure token provider using DefaultAzureCredential.

    Returns a callable that returns a bearer token string. The callable handles
    automatic token refresh.

    Args:
        scope (str): The Azure token scope (e.g., 'https://cognitiveservices.azure.com/.default').

    Returns:
        Callable[[], str]: A token provider function that returns bearer tokens.

    Example:
        >>> token_provider = get_azure_token_provider('https://cognitiveservices.azure.com/.default')
        >>> token = token_provider()  # Get current token
    """
    try:
        return get_bearer_token_provider(DefaultAzureCredential(), scope)
    except Exception as e:
        logger.error(f"Failed to obtain token provider for '{scope}': {e}")
        raise


def get_azure_async_token_provider(scope: str):  # type: ignore[no-untyped-def]
    """
    Get an asynchronous Azure token provider using AsyncDefaultAzureCredential.

    Returns an async callable suitable for use with async clients like OpenAI's AsyncOpenAI.
    The callable handles automatic token refresh.

    Args:
        scope (str): The Azure token scope (e.g., 'https://cognitiveservices.azure.com/.default').

    Returns:
        Async callable that returns bearer tokens.

    Example:
        >>> token_provider = get_azure_async_token_provider('https://cognitiveservices.azure.com/.default')
        >>> token = await token_provider()  # Get current token (in async context)
    """
    try:
        return get_async_bearer_token_provider(AsyncDefaultAzureCredential(), scope)
    except Exception as e:
        logger.error(f"Failed to obtain async token provider for '{scope}': {e}")
        raise


def get_default_azure_scope(endpoint: str) -> str:
    """
    Determine the appropriate Azure token scope based on the endpoint URL.

    The Cognitive Services scope is accepted by all Azure AI endpoints including
    Azure OpenAI (*.openai.azure.com) and AI Foundry (*.ai.azure.com).

    Args:
        endpoint (str): The Azure endpoint URL.

    Returns:
        str: The token scope 'https://cognitiveservices.azure.com/.default'.

    Example:
        >>> scope = get_default_azure_scope('https://myresource.openai.azure.com')
        >>> # Returns 'https://cognitiveservices.azure.com/.default'
    """
    return "https://cognitiveservices.azure.com/.default"


def get_azure_openai_auth(endpoint: str):  # type: ignore[no-untyped-def]
    """
    Get an async Azure token provider for OpenAI endpoints.

    Automatically determines the correct scope based on the endpoint URL and returns
    an async token provider suitable for use with AsyncOpenAI clients.

    Args:
        endpoint (str): The Azure OpenAI endpoint URL.

    Returns:
        Async callable that returns bearer tokens.

    Example:
        >>> from pyrit.prompt_target import OpenAIChatTarget
        >>> target = OpenAIChatTarget(
        ...     endpoint='https://myresource.openai.azure.com',
        ...     api_key=get_azure_openai_auth('https://myresource.openai.azure.com')
        ... )
    """
    scope = get_default_azure_scope(endpoint)
    return get_azure_async_token_provider(scope)


def get_speech_config(resource_id: Union[str, None], key: Union[str, None], region: str) -> speechsdk.SpeechConfig:
    """
    Get the speech config using key/region pair (for key auth scenarios) or resource_id/region pair
    (for Entra auth scenarios).

    Args:
        resource_id (Union[str, None]): The resource ID to get the token for.
        key (Union[str, None]): The Azure Speech key
        region (str): The region to get the token for.

    Returns:
        speechsdk.SpeechConfig: The speech config based on passed in args

    Raises:
        ModuleNotFoundError: If azure.cognitiveservices.speech is not installed.
        ValueError: If neither key/region nor resource_id/region is provided.
    """
    try:
        import azure.cognitiveservices.speech as speechsdk  # noqa: F811
    except ModuleNotFoundError as e:
        logger.error(
            "Could not import azure.cognitiveservices.speech. "
            "You may need to install it via 'pip install pyrit[speech]'"
        )
        raise e

    if key and region:
        return speechsdk.SpeechConfig(
            subscription=key,
            region=region,
        )
    if resource_id and region:
        return get_speech_config_from_default_azure_credential(
            resource_id=resource_id,
            region=region,
        )
    raise ValueError("Insufficient information provided for Azure Speech service.")


def get_speech_config_from_default_azure_credential(resource_id: str, region: str) -> speechsdk.SpeechConfig:
    """
    Get the speech config for the given resource ID and region.

    Args:
        resource_id (str): The resource ID to get the token for.
        region (str): The region to get the token for.

    Returns:
        The speech config for the given resource ID and region.

    Raises:
        ModuleNotFoundError: If azure.cognitiveservices.speech is not installed.
    """
    try:
        import azure.cognitiveservices.speech as speechsdk  # noqa: F811
    except ModuleNotFoundError as e:
        logger.error(
            "Could not import azure.cognitiveservices.speech. "
            "You may need to install it via 'pip install pyrit[speech]'"
        )
        raise e

    try:
        azure_auth = AzureAuth(token_scope=get_default_azure_scope(""))
        token = azure_auth.get_token()
        authorization_token = "aad#" + resource_id + "#" + token
        return speechsdk.SpeechConfig(
            auth_token=authorization_token,
            region=region,
        )
    except Exception as e:
        logger.error(f"Failed to get speech config for resource ID '{resource_id}' and region '{region}': {e}")
        raise
