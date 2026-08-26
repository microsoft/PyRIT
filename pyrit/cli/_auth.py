# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Authentication helpers for the thin PyRIT REST clients."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

AuthMode = Literal["auto", "azure_cli", "device_code", "none"]
AUTH_MODES: tuple[AuthMode, ...] = ("auto", "azure_cli", "device_code", "none")
_GRAPH_USER_READ_SCOPE = "https://graph.microsoft.com/User.Read"
_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_REFRESH_BUFFER_SECONDS = 300
_AZURE_CLI_WARNING = (
    "Warning: azure_cli mode sends the Azure CLI application's Microsoft Graph token to the backend. "
    "That token can contain permissions beyond User.Read. Prefer auto or device_code."
)


class CliAuthenticationError(Exception):
    """Raised when the CLI cannot authenticate to a protected backend."""


@dataclass(frozen=True)
class BackendAuthConfig:
    """Public Entra configuration advertised by the PyRIT backend."""

    enabled: bool
    tenant_id: str
    client_id: str
    scopes: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Any) -> BackendAuthConfig:
        """
        Validate an ``/api/auth/config`` response.

        Args:
            payload: Parsed response payload.

        Returns:
            Validated backend authentication configuration.

        Raises:
            CliAuthenticationError: If the server returns an unsupported contract.
        """
        if not isinstance(payload, dict):
            raise CliAuthenticationError(
                "The server returned an unsupported authentication contract. "
                "Upgrade the PyRIT backend so /api/auth/config includes 'enabled' and 'scopes'."
            )

        tenant_id = payload.get("tenantId", "")
        client_id = payload.get("clientId", "")
        if not isinstance(tenant_id, str) or not isinstance(client_id, str):
            raise CliAuthenticationError("The server returned invalid Entra tenant or client configuration.")
        if "enabled" not in payload and not tenant_id.strip() and not client_id.strip():
            return cls(enabled=False, tenant_id="", client_id="", scopes=())
        if not isinstance(payload.get("enabled"), bool):
            raise CliAuthenticationError(
                "The server returned an unsupported authentication contract. "
                "Upgrade the PyRIT backend so /api/auth/config includes 'enabled' and 'scopes'."
            )

        enabled = payload["enabled"]
        raw_scopes = payload.get("scopes", [])
        if not isinstance(raw_scopes, list) or not all(
            isinstance(scope, str) and scope.strip() for scope in raw_scopes
        ):
            raise CliAuthenticationError("The server returned invalid delegated authentication scopes.")

        scopes = tuple(scope.strip() for scope in raw_scopes)
        if enabled and scopes != (_GRAPH_USER_READ_SCOPE,):
            raise CliAuthenticationError(
                "The server requested an unsupported authentication scope. "
                f"Only {_GRAPH_USER_READ_SCOPE} is allowed."
            )
        tenant_id = tenant_id.strip()
        client_id = client_id.strip()
        if enabled and (not tenant_id or not client_id or not scopes):
            raise CliAuthenticationError(
                "The server reports authentication enabled but its Entra configuration is incomplete."
            )

        return cls(
            enabled=enabled,
            tenant_id=tenant_id,
            client_id=client_id,
            scopes=scopes,
        )


class TokenProvider(Protocol):
    """Supply and refresh access tokens for backend requests."""

    async def get_token_async(self) -> str:
        """Return a current bearer token."""

    async def close_async(self) -> None:
        """Release credential resources."""


class _AzureIdentityTokenProvider:
    """Adapt an asynchronous Azure Identity credential to the CLI token protocol."""

    def __init__(self, *, credential: Any, auth_config: BackendAuthConfig, mode: AuthMode) -> None:
        self._credential = credential
        self._auth_config = auth_config
        self._mode = mode
        self._access_token: Any = None

    async def get_token_async(self) -> str:
        """
        Acquire a delegated Microsoft Graph access token.

        Returns:
            Access token text.

        Raises:
            CliAuthenticationError: If Azure Identity cannot authenticate the user.
        """
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import CredentialUnavailableError

        cached_token = self._get_current_token()
        if cached_token is not None:
            return cached_token

        try:
            access_token = await self._credential.get_token(_GRAPH_DEFAULT_SCOPE)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            if self._mode == "azure_cli":
                hint = f"Run 'az login --tenant {self._auth_config.tenant_id}' and try again."
            else:
                hint = "Confirm that device-code authentication is enabled for the CoPyRIT Entra application."
            raise CliAuthenticationError(f"Entra authentication failed. {hint}") from exc

        token = getattr(access_token, "token", "")
        if not isinstance(token, str) or not token:
            raise CliAuthenticationError("Entra authentication returned an empty access token.")
        self._access_token = access_token
        return token

    def _get_current_token(self) -> str | None:
        """Return a cached token that is valid beyond the refresh buffer."""
        if self._access_token is None:
            return None
        expires_on = getattr(self._access_token, "expires_on", 0)
        token = getattr(self._access_token, "token", "")
        if (
            isinstance(expires_on, int | float)
            and expires_on > time.time() + _TOKEN_REFRESH_BUFFER_SECONDS
            and isinstance(token, str)
            and token
        ):
            return token
        return None

    async def close_async(self) -> None:
        """Close the underlying Azure Identity credential."""
        await self._credential.close()


class _DeviceCodeTokenProvider:
    """Adapt the synchronous device-code credential without blocking the event loop."""

    def __init__(self, *, credential: Any, auth_config: BackendAuthConfig) -> None:
        self._credential = credential
        self._auth_config = auth_config
        self._access_token: Any = None

    async def get_token_async(self) -> str:
        """
        Acquire a delegated Microsoft Graph token through device-code login.

        Returns:
            Access token text.

        Raises:
            CliAuthenticationError: If Entra rejects device-code authentication.
        """
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import CredentialUnavailableError

        cached_token = self._get_current_token()
        if cached_token is not None:
            return cached_token

        try:
            access_token = await asyncio.to_thread(self._credential.get_token, *self._auth_config.scopes)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            raise CliAuthenticationError(
                "Entra authentication failed. Confirm that device-code authentication "
                "is enabled for the CoPyRIT Entra application."
            ) from exc

        token = getattr(access_token, "token", "")
        if not isinstance(token, str) or not token:
            raise CliAuthenticationError("Entra authentication returned an empty access token.")
        self._access_token = access_token
        return token

    def _get_current_token(self) -> str | None:
        """Return a cached token that is valid beyond the refresh buffer."""
        if self._access_token is None:
            return None
        expires_on = getattr(self._access_token, "expires_on", 0)
        token = getattr(self._access_token, "token", "")
        if (
            isinstance(expires_on, int | float)
            and expires_on > time.time() + _TOKEN_REFRESH_BUFFER_SECONDS
            and isinstance(token, str)
            and token
        ):
            return token
        return None

    async def close_async(self) -> None:
        """Close the underlying synchronous Azure Identity credential."""
        await asyncio.to_thread(self._credential.close)


def _is_interactive() -> bool:
    """Return whether authentication may safely prompt this process."""
    return bool(sys.stdin.isatty() and sys.stderr.isatty())


def _create_azure_cli_provider(*, auth_config: BackendAuthConfig) -> TokenProvider:
    from azure.identity.aio import AzureCliCredential

    credential = AzureCliCredential(tenant_id=auth_config.tenant_id)
    return _AzureIdentityTokenProvider(credential=credential, auth_config=auth_config, mode="azure_cli")


def _create_device_code_provider(*, auth_config: BackendAuthConfig) -> TokenProvider:
    from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

    cache_options = TokenCachePersistenceOptions(name=f"pyrit-copyrit-{auth_config.client_id}")
    credential = DeviceCodeCredential(
        tenant_id=auth_config.tenant_id,
        client_id=auth_config.client_id,
        cache_persistence_options=cache_options,
    )
    return _DeviceCodeTokenProvider(credential=credential, auth_config=auth_config)


async def _verify_provider_async(*, provider: TokenProvider) -> TokenProvider:
    """
    Acquire an initial token so selection fails before the first API operation.

    Returns:
        The verified provider.

    Raises:
        CliAuthenticationError: If the provider cannot acquire a token.
    """
    try:
        await provider.get_token_async()
    except CliAuthenticationError:
        await provider.close_async()
        raise
    return provider


async def create_token_provider_async(
    *,
    auth_config: BackendAuthConfig,
    auth_mode: AuthMode,
    interactive: bool | None = None,
) -> TokenProvider | None:
    """
    Select and verify a token provider for the backend.

    Args:
        auth_config: Authentication requirements discovered from the backend.
        auth_mode: Requested credential selection behavior.
        interactive: Optional terminal-interactivity override for tests.

    Returns:
        A verified token provider, or ``None`` when authentication is disabled.

    Raises:
        CliAuthenticationError: If the requested authentication flow cannot run.
    """
    if auth_mode == "none" or not auth_config.enabled:
        return None

    if auth_mode == "azure_cli":
        print(_AZURE_CLI_WARNING, file=sys.stderr)
        return await _verify_provider_async(provider=_create_azure_cli_provider(auth_config=auth_config))

    can_prompt = _is_interactive() if interactive is None else interactive
    if not can_prompt:
        raise CliAuthenticationError(
            "Device-code authentication requires an interactive terminal. "
            "Use azure_cli only when you accept sending the Azure CLI Graph token to the backend."
        )
    return await _verify_provider_async(provider=_create_device_code_provider(auth_config=auth_config))
