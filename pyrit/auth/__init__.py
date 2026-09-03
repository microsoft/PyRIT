# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Authentication functionality for a variety of services.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
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

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AsyncTokenProviderCredential": "pyrit.auth.azure_auth",
    "Authenticator": "pyrit.auth.authenticator",
    "AzureAuth": "pyrit.auth.azure_auth",
    "AzureStorageAuth": "pyrit.auth.azure_storage_auth",
    "CopilotAuthenticator": "pyrit.auth.copilot_authenticator",
    "ManualCopilotAuthenticator": "pyrit.auth.manual_copilot_authenticator",
    "resolve_openai_auth": "pyrit.auth.openai_auth",
    "TokenProviderCredential": "pyrit.auth.azure_auth",
    "ensure_async_token_provider": "pyrit.auth.azure_auth",
    "get_azure_token_provider": "pyrit.auth.azure_auth",
    "get_azure_async_token_provider": "pyrit.auth.azure_auth",
    "get_default_azure_scope": "pyrit.auth.azure_auth",
    "get_azure_openai_auth": "pyrit.auth.azure_auth",
    "is_azure_ml_endpoint": "pyrit.auth.azure_auth",
    "is_azure_openai_endpoint": "pyrit.auth.azure_auth",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
