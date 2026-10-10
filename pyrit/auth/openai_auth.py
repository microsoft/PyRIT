# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Awaitable, Callable
from typing import cast

from pyrit.auth.azure_auth import ensure_async_token_provider, get_azure_openai_auth, is_azure_openai_endpoint
from pyrit.common import default_values
from pyrit.common.auth_mode import AuthMode
from pyrit.common.deprecation import print_deprecation_message


def resolve_openai_auth(
    *,
    endpoint: str,
    api_key: str | Callable[[], str | Awaitable[str]] | None,
    api_key_environment_variable: str,
    auth_mode: AuthMode = "api_key",
) -> str | Callable[[], Awaitable[str]]:
    """
    Resolve OpenAI authentication from an explicit identity choice, a key, or an environment variable.

    Args:
        endpoint (str): The OpenAI-compatible endpoint URL.
        api_key (str | Callable[[], str | Awaitable[str]] | None): The explicit API key or token provider.
        api_key_environment_variable (str): Environment variable to use when ``api_key`` is not provided.
        auth_mode (AuthMode): ``"identity"`` authenticates with a Microsoft Entra ID token minted for
            the endpoint; it ignores the API key environment variable and rejects an explicit
            ``api_key``. ``"api_key"`` (the default) resolves a token-provider callable, then an
            explicit key, then the environment variable, and finally falls back to Entra ID on a
            recognized Azure OpenAI endpoint. That last fallback is deprecated and is removed in
            1.4.0, after which identity auth must be requested explicitly.

    Returns:
        str | Callable[[], Awaitable[str]]: API key string or async-compatible token provider.

    Raises:
        ValueError: If identity auth is requested alongside an explicit ``api_key``, if identity auth
            is requested for an endpoint that is not a recognized Azure OpenAI endpoint, or if
            ``"api_key"`` auth is requested and no key is available for an endpoint that is not a
            recognized Azure OpenAI endpoint.
    """
    # Identity is an explicit caller choice, so it must never be silently downgraded to a key
    # that merely happens to be present in the environment.
    if auth_mode == "identity":
        if api_key is not None:
            raise ValueError(
                'auth_mode="identity" cannot be combined with an explicit api_key, because identity auth '
                "mints its own Microsoft Entra ID token and would silently ignore the key or token provider "
                "you supplied. Omit api_key to authenticate with an ambient Azure identity, or pass "
                'auth_mode="api_key" to authenticate with the key or token provider you supplied.'
            )
        if not is_azure_openai_endpoint(endpoint):
            raise ValueError(
                f"Identity-based authentication requires a recognized Azure OpenAI / AI Foundry endpoint, "
                f"but got '{endpoint}'. Pass auth_mode=\"api_key\" for this endpoint, supplying either a key "
                "or your own token provider callable as api_key."
            )
        return get_azure_openai_auth(endpoint)

    if api_key is not None and callable(api_key):
        return cast("str | Callable[[], Awaitable[str]]", ensure_async_token_provider(api_key))

    api_key_value = default_values.get_non_required_value(
        env_var_name=api_key_environment_variable, passed_value=api_key
    )
    if api_key_value:
        return api_key_value

    # Keyless configurations against a recognized Azure endpoint predate explicit auth modes, so the
    # implicit Entra fallback stays until 1.4.0 rather than breaking them at the next minor release.
    if is_azure_openai_endpoint(endpoint):
        print_deprecation_message(
            old_item=(
                "Falling back to Microsoft Entra ID authentication for Azure OpenAI endpoints when no API key "
                "is configured"
            ),
            new_item='auth_mode="identity"',
            removed_in="1.4.0",
        )
        return get_azure_openai_auth(endpoint)

    raise ValueError(
        f"No API key available for endpoint '{endpoint}'. Set the {api_key_environment_variable} environment "
        'variable, pass api_key explicitly, or pass auth_mode="identity" to authenticate with Microsoft '
        "Entra ID on a recognized Azure OpenAI / AI Foundry endpoint."
    )
