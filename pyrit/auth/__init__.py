# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Authentication functionality for a variety of services.
"""

from pyrit.auth.authenticator import Authenticator
from pyrit.auth.azure_auth import (
    AsyncTokenProviderCredential,
    AzureAuth,
    TokenProviderCredential,
    ensure_async_token_provider,
    get_azure_async_token_provider,
    get_azure_openai_auth,
    get_azure_token_provider,
    get_default_azure_scope,
    is_azure_ml_endpoint,
    is_azure_openai_endpoint,
)
from pyrit.auth.azure_storage_auth import AzureStorageAuth
from pyrit.auth.copilot_authenticator import CopilotAuthenticator
from pyrit.auth.manual_copilot_authenticator import ManualCopilotAuthenticator
from pyrit.auth.openai_auth import resolve_openai_auth

__all__ = [
    "AsyncTokenProviderCredential",
    "Authenticator",
    "AzureAuth",
    "AzureStorageAuth",
    "CopilotAuthenticator",
    "ManualCopilotAuthenticator",
    "resolve_openai_auth",
    "TokenProviderCredential",
    "ensure_async_token_provider",
    "get_azure_token_provider",
    "get_azure_async_token_provider",
    "get_default_azure_scope",
    "get_azure_openai_auth",
    "is_azure_ml_endpoint",
    "is_azure_openai_endpoint",
]
