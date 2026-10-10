# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Awaitable, Callable
from typing import cast

from pyrit.auth.azure_auth import ensure_async_token_provider, get_azure_openai_auth, is_azure_openai_endpoint
from pyrit.common import default_values


def resolve_openai_auth(
    *,
    endpoint: str,
    api_key: str | Callable[[], str | Awaitable[str]] | None,
    api_key_environment_variable: str,
) -> str | Callable[[], Awaitable[str]]:
    """
    Resolve OpenAI authentication from a key, environment variable, or Azure Entra fallback.

    Args:
        endpoint (str): The OpenAI-compatible endpoint URL.
        api_key (str | Callable[[], str | Awaitable[str]] | None): The explicit API key or token provider.
        api_key_environment_variable (str): Environment variable to use when ``api_key`` is not provided.

    Returns:
        str | Callable[[], Awaitable[str]]: API key string or async-compatible token provider.

    Raises:
        ValueError: If no key is provided and the endpoint is not a recognized Azure OpenAI endpoint.
    """
    if api_key is not None and callable(api_key):
        return cast("str | Callable[[], Awaitable[str]]", ensure_async_token_provider(api_key))

    api_key_value = default_values.get_non_required_value(
        env_var_name=api_key_environment_variable, passed_value=api_key
    )
    if api_key_value:
        return api_key_value

    if is_azure_openai_endpoint(endpoint):
        return get_azure_openai_auth(endpoint)

    raise ValueError(
        f"Environment variable {api_key_environment_variable} is required for non-Azure endpoints. "
        "For recognized Azure OpenAI / AI Foundry endpoints, Entra ID authentication is used automatically."
    )
