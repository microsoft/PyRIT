# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import warnings
from unittest.mock import patch

import pytest

from pyrit.auth.openai_auth import resolve_openai_auth

AZURE_ENDPOINT = "https://my-resource.openai.azure.com/openai/v1"
NON_AZURE_ENDPOINT = "https://api.openai.com/v1"
API_KEY_ENV_VAR = "OPENAI_CHAT_API_KEY"


@pytest.fixture
def minted_provider():
    async def _provider() -> str:
        return "entra-token"

    with patch("pyrit.auth.openai_auth.get_azure_openai_auth", return_value=_provider) as mock_auth:
        yield _provider, mock_auth


def test_identity_ignores_env_var_api_key(minted_provider):
    """An explicit identity choice must not be downgraded to a key sitting in the environment."""
    provider, mock_auth = minted_provider
    with patch.dict(os.environ, {API_KEY_ENV_VAR: "sk-SECRET-FROM-DOTENV"}):
        resolved = resolve_openai_auth(
            endpoint=AZURE_ENDPOINT,
            api_key=None,
            api_key_environment_variable=API_KEY_ENV_VAR,
            auth_mode="identity",
        )

    assert resolved is provider
    mock_auth.assert_called_once_with(AZURE_ENDPOINT)


@pytest.mark.parametrize(
    "explicit_key",
    ["sk-explicit", lambda: "caller-supplied-token"],
    ids=["key_string", "token_provider"],
)
def test_identity_with_explicit_api_key_raises(minted_provider, explicit_key):
    """Identity plus an explicit credential is contradictory. Silently dropping a caller's token
    provider would authenticate as a different principal than the one they supplied."""
    _, mock_auth = minted_provider
    with pytest.raises(ValueError, match="cannot be combined with an explicit api_key"):
        resolve_openai_auth(
            endpoint=AZURE_ENDPOINT,
            api_key=explicit_key,
            api_key_environment_variable=API_KEY_ENV_VAR,
            auth_mode="identity",
        )

    mock_auth.assert_not_called()


def test_identity_raises_for_non_azure_endpoint():
    with pytest.raises(ValueError, match="Identity-based authentication requires a recognized Azure"):
        resolve_openai_auth(
            endpoint=NON_AZURE_ENDPOINT,
            api_key=None,
            api_key_environment_variable=API_KEY_ENV_VAR,
            auth_mode="identity",
        )


def test_default_auth_mode_uses_env_var():
    with patch.dict(os.environ, {API_KEY_ENV_VAR: "sk-from-env"}):
        resolved = resolve_openai_auth(
            endpoint=AZURE_ENDPOINT,
            api_key=None,
            api_key_environment_variable=API_KEY_ENV_VAR,
        )

    assert resolved == "sk-from-env"


def test_api_key_mode_prefers_explicit_key_over_env_var():
    with patch.dict(os.environ, {API_KEY_ENV_VAR: "sk-from-env"}):
        resolved = resolve_openai_auth(
            endpoint=AZURE_ENDPOINT,
            api_key="sk-explicit",
            api_key_environment_variable=API_KEY_ENV_VAR,
            auth_mode="api_key",
        )

    assert resolved == "sk-explicit"


def test_api_key_mode_wraps_callable_before_reading_env_var():
    def sync_provider() -> str:
        return "callable-token"

    with patch.dict(os.environ, {API_KEY_ENV_VAR: "sk-from-env"}):
        resolved = resolve_openai_auth(
            endpoint=AZURE_ENDPOINT,
            api_key=sync_provider,
            api_key_environment_variable=API_KEY_ENV_VAR,
        )

    assert callable(resolved)
    assert resolved is not sync_provider


def test_api_key_mode_falls_back_to_entra_with_deprecation_warning(minted_provider):
    """Keyless Azure configurations predate explicit auth modes, so the fallback survives to 1.4.0 --
    but it now announces itself instead of happening silently."""
    provider, mock_auth = minted_provider
    with patch.dict(os.environ, {API_KEY_ENV_VAR: ""}):
        with pytest.warns(DeprecationWarning, match="1.4.0"):
            resolved = resolve_openai_auth(
                endpoint=AZURE_ENDPOINT,
                api_key=None,
                api_key_environment_variable=API_KEY_ENV_VAR,
            )

    assert resolved is provider
    mock_auth.assert_called_once_with(AZURE_ENDPOINT)


def test_identity_does_not_emit_a_deprecation_warning(minted_provider):
    """Identity is the replacement the warning points at, so it must not warn itself."""
    provider, _ = minted_provider
    with patch.dict(os.environ, {API_KEY_ENV_VAR: ""}):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            resolved = resolve_openai_auth(
                endpoint=AZURE_ENDPOINT,
                api_key=None,
                api_key_environment_variable=API_KEY_ENV_VAR,
                auth_mode="identity",
            )

    assert resolved is provider


def test_api_key_mode_raises_for_non_azure_endpoint_without_key():
    with patch.dict(os.environ, {API_KEY_ENV_VAR: ""}):
        with pytest.raises(ValueError, match="No API key available"):
            resolve_openai_auth(
                endpoint=NON_AZURE_ENDPOINT,
                api_key=None,
                api_key_environment_variable=API_KEY_ENV_VAR,
            )


def test_api_key_mode_error_names_the_identity_migration():
    """A caller who cannot fall back needs the error to name the supported alternative."""
    with patch.dict(os.environ, {API_KEY_ENV_VAR: ""}):
        with pytest.raises(ValueError) as exc_info:
            resolve_openai_auth(
                endpoint=NON_AZURE_ENDPOINT,
                api_key=None,
                api_key_environment_variable=API_KEY_ENV_VAR,
            )

    message = str(exc_info.value)
    assert 'auth_mode="identity"' in message
    assert API_KEY_ENV_VAR in message
