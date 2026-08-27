# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Authentication helpers for the thin PyRIT REST clients."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

AuthMode = Literal["auto", "azure_cli", "device_code", "none"]
AUTH_MODES: tuple[AuthMode, ...] = ("auto", "azure_cli", "device_code", "none")
_GRAPH_USER_READ_SCOPE = "https://graph.microsoft.com/User.Read"
_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_REFRESH_BUFFER_SECONDS = 300
_CACHE_ERROR_MARKERS = ("cache encryption", "persistent cache", "libsecret", "keychain")
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
                f"The server requested an unsupported authentication scope. Only {_GRAPH_USER_READ_SCOPE} is allowed."
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


class _AzureCliTokenProvider:
    """Adapt Azure CLI credentials to the CLI token protocol."""

    def __init__(self, *, credential: Any, auth_config: BackendAuthConfig) -> None:
        self._credential = credential
        self._auth_config = auth_config
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
            raise CliAuthenticationError(
                f"Entra authentication failed. Run 'az login --tenant {self._auth_config.tenant_id}' and try again."
            ) from exc

        token = _get_access_token_text(access_token)
        self._access_token = access_token
        return token

    def _get_current_token(self) -> str | None:
        """Return a cached token that is valid beyond the refresh buffer."""
        return _get_current_token_text(self._access_token)

    async def close_async(self) -> None:
        """Close the underlying Azure Identity credential."""
        await self._credential.close()


class _DeviceCodeTokenProvider:
    """Adapt the synchronous device-code credential without blocking the event loop."""

    def __init__(
        self,
        *,
        credential: Any,
        auth_config: BackendAuthConfig,
        authentication_record_path: Path,
        has_authentication_record: bool,
    ) -> None:
        self._credential = credential
        self._auth_config = auth_config
        self._authentication_record_path = authentication_record_path
        self._has_authentication_record = has_authentication_record
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
        from azure.identity import AuthenticationRequiredError, CredentialUnavailableError

        cached_token = self._get_current_token()
        if cached_token is not None:
            return cached_token

        try:
            if not self._has_authentication_record:
                await self._authenticate_async()
            try:
                access_token = await asyncio.to_thread(self._credential.get_token, *self._auth_config.scopes)
            except AuthenticationRequiredError:
                await self._authenticate_async()
                access_token = await asyncio.to_thread(self._credential.get_token, *self._auth_config.scopes)
        except (ClientAuthenticationError, CredentialUnavailableError) as exc:
            if _is_persistent_cache_error(exc):
                raise CliAuthenticationError(
                    "Encrypted token caching is unavailable. Configure the platform credential store "
                    "(for example, libsecret on Linux) and try again."
                ) from exc
            raise CliAuthenticationError(
                "Entra authentication failed. Confirm that device-code authentication "
                "is enabled for the CoPyRIT Entra application."
            ) from exc

        token = _get_access_token_text(access_token)
        self._access_token = access_token
        return token

    async def _authenticate_async(self) -> None:
        """Authenticate interactively and persist the resulting account metadata."""
        authentication_record = await asyncio.to_thread(
            self._credential.authenticate,
            scopes=self._auth_config.scopes,
        )
        await asyncio.to_thread(
            _save_authentication_record,
            authentication_record=authentication_record,
            path=self._authentication_record_path,
        )
        self._has_authentication_record = True

    def _get_current_token(self) -> str | None:
        """Return a cached token that is valid beyond the refresh buffer."""
        return _get_current_token_text(self._access_token)

    async def close_async(self) -> None:
        """Close the underlying synchronous Azure Identity credential."""
        await asyncio.to_thread(self._credential.close)


def _is_interactive() -> bool:
    """Return whether authentication may safely prompt this process."""
    return bool(sys.stdin.isatty() and sys.stderr.isatty())


def _get_access_token_text(access_token: Any) -> str:
    """
    Return validated token text from an Azure Identity access token.

    Returns:
        The non-empty access token text.

    Raises:
        CliAuthenticationError: If Azure Identity returns an empty token.
    """
    token = getattr(access_token, "token", "")
    if not isinstance(token, str) or not token:
        raise CliAuthenticationError("Entra authentication returned an empty access token.")
    return token


def _get_current_token_text(access_token: Any) -> str | None:
    """Return token text when an access token remains valid beyond the refresh buffer."""
    if access_token is None:
        return None
    expires_on = getattr(access_token, "expires_on", 0)
    token = getattr(access_token, "token", "")
    if (
        isinstance(expires_on, int | float)
        and expires_on > time.time() + _TOKEN_REFRESH_BUFFER_SECONDS
        and isinstance(token, str)
        and token
    ):
        return token
    return None


def _authentication_cache_key(*, auth_config: BackendAuthConfig) -> str:
    """Return a path-safe identifier for one tenant and client pair."""
    cache_identity = f"{auth_config.tenant_id}:{auth_config.client_id}"
    return hashlib.sha256(cache_identity.encode()).hexdigest()[:24]


def _authentication_record_path(*, auth_config: BackendAuthConfig) -> Path:
    """Return the local path for non-secret Azure Identity account metadata."""
    cache_key = _authentication_cache_key(auth_config=auth_config)
    return Path.home() / ".pyrit" / ".pyrit_cache" / f"copyrit-auth-{cache_key}.json"


def _load_authentication_record(*, auth_config: BackendAuthConfig, path: Path) -> Any | None:
    """
    Load account metadata required to reuse the encrypted token cache.

    Returns:
        The stored Azure Identity authentication record, or ``None`` when absent.

    Raises:
        CliAuthenticationError: If the record is invalid or belongs to another app.
    """
    from azure.identity import AuthenticationRecord

    if not path.exists():
        return None
    try:
        authentication_record = AuthenticationRecord.deserialize(path.read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError) as exc:
        raise CliAuthenticationError(
            f"Could not read the CoPyRIT authentication record at {path}: {exc}. Remove the file and try again."
        ) from exc
    if authentication_record.client_id != auth_config.client_id:
        raise CliAuthenticationError(f"The CoPyRIT authentication record at {path} does not match the server.")
    return authentication_record


def _save_authentication_record(*, authentication_record: Any, path: Path) -> None:
    """
    Atomically store non-secret account metadata for later cache access.

    Raises:
        CliAuthenticationError: If the record cannot be serialized or saved.
    """
    temporary_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(authentication_record.serialize(), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    except (OSError, ValueError) as exc:
        raise CliAuthenticationError(f"Could not save the CoPyRIT authentication record at {path}: {exc}") from exc


def _print_device_code_prompt(verification_uri: str, user_code: str, expires_on: datetime) -> None:
    """Write device-code instructions to stderr so stdout remains redirectable."""
    print(
        f"To sign in, open {verification_uri} and enter code {user_code}. "
        f"The code expires at {expires_on.isoformat()}.",
        file=sys.stderr,
    )


def _is_persistent_cache_error(exc: BaseException) -> bool:
    """Return whether an exception chain identifies a platform token-cache failure."""
    current: BaseException | None = exc
    while current is not None:
        message = str(current).lower()
        if any(marker in message for marker in _CACHE_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _create_azure_cli_provider(*, auth_config: BackendAuthConfig) -> TokenProvider:
    from azure.identity.aio import AzureCliCredential

    credential = AzureCliCredential(tenant_id=auth_config.tenant_id)
    return _AzureCliTokenProvider(credential=credential, auth_config=auth_config)


async def _create_device_code_provider_async(*, auth_config: BackendAuthConfig) -> TokenProvider:
    from azure.identity import DeviceCodeCredential, TokenCachePersistenceOptions

    cache_key = _authentication_cache_key(auth_config=auth_config)
    authentication_record_path = _authentication_record_path(auth_config=auth_config)
    authentication_record = await asyncio.to_thread(
        _load_authentication_record,
        auth_config=auth_config,
        path=authentication_record_path,
    )
    cache_options = TokenCachePersistenceOptions(name=f"pyrit-copyrit-{cache_key}")
    credential = DeviceCodeCredential(
        tenant_id=auth_config.tenant_id,
        client_id=auth_config.client_id,
        authentication_record=authentication_record,
        cache_persistence_options=cache_options,
        disable_automatic_authentication=True,
        prompt_callback=_print_device_code_prompt,
    )
    return _DeviceCodeTokenProvider(
        credential=credential,
        auth_config=auth_config,
        authentication_record_path=authentication_record_path,
        has_authentication_record=authentication_record is not None,
    )


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
    provider = await _create_device_code_provider_async(auth_config=auth_config)
    return await _verify_provider_async(provider=provider)
