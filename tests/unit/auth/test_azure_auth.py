# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.auth.auth_config import REFRESH_TOKEN_BEFORE_MSEC
from pyrit.auth.azure_auth import (
    AzureAuth,
    get_azure_token_provider,
    get_speech_config,
    get_speech_config_from_default_azure_credential,
)

curr_epoch_time = int(time.time())
mock_token = "fake token"


def is_speechsdk_installed():
    try:
        import azure.cognitiveservices.speech  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


def test_get_token_on_init():
    with patch("azure.identity.AzureCliCredential.get_token") as mock_get_token:
        mock_get_token.return_value = MagicMock(token=mock_token)
        test_instance = AzureAuth(token_scope="https://mocked_endpoint.azure.com")
        assert test_instance.token == mock_token


def test_refresh_no_expiration():
    # Token not expired so not reset
    with patch("azure.identity.AzureCliCredential.get_token") as mock_get_token:
        mock_get_token.return_value = MagicMock(
            token=mock_token, expires_on=curr_epoch_time + REFRESH_TOKEN_BEFORE_MSEC
        )
        test_instance = AzureAuth(token_scope="https://mocked_endpoint.azure.com")
        token = test_instance.refresh_token()
        assert token == mock_token
        mock_get_token.assert_called()


def test_refresh_expiration():
    # Token expired and reset
    with patch("azure.identity.AzureCliCredential.get_token") as mock_get_token:
        mock_get_token.return_value = MagicMock(token=mock_token, expires_on=curr_epoch_time)
        test_instance = AzureAuth(token_scope="https://mocked_endpoint.azure.com")
        token = test_instance.refresh_token()
        assert token
        assert mock_get_token.call_count == 2


def test_get_azure_token_provider_get_token():
    with (
        patch("azure.identity.DefaultAzureCredential.get_token") as mock_default_cred,
        patch(
            "builtins.hasattr",
            side_effect=lambda obj, attr: False if attr == "get_token_info" else getattr(obj, attr, None) is not None,
        ),
    ):
        mock_default_cred.return_value = MagicMock(token=mock_token, expires_on=curr_epoch_time)
        token_provider = get_azure_token_provider(scope="https://mocked_endpoint.azure.com")
        assert token_provider() == mock_token


def test_get_azure_token_provider_get_token_info():
    with (
        patch("azure.identity.DefaultAzureCredential.get_token_info") as mock_default_cred,
        patch(
            "builtins.hasattr",
            side_effect=lambda obj, attr: True if attr == "get_token_info" else getattr(obj, attr, None) is not None,
        ),
    ):
        mock_default_cred.return_value = MagicMock(token=mock_token, expires_on=curr_epoch_time)
        token_provider = get_azure_token_provider(scope="https://mocked_endpoint.azure.com")
        assert token_provider() == mock_token


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
@patch("azure.cognitiveservices.speech.SpeechConfig")
@patch("pyrit.auth.azure_auth.AzureAuth")
def test_get_speech_config_from_default_azure_credential(mock_azure_auth_class: Any, mock_speech_config: Any) -> None:
    """Test get_speech_config_from_default_azure_credential creates proper SpeechConfig."""
    # Mock AzureAuth instance
    mock_azure_auth_instance = MagicMock()
    mock_azure_auth_instance.get_token.return_value = "test_token"
    mock_azure_auth_class.return_value = mock_azure_auth_instance

    # Mock SpeechConfig
    mock_config = MagicMock()
    mock_speech_config.return_value = mock_config

    # Call the function
    result = get_speech_config_from_default_azure_credential(resource_id="test_resource_id", region="test_region")

    # Verify AzureAuth was created with correct scope
    mock_azure_auth_class.assert_called_once_with(token_scope="https://cognitiveservices.azure.com/.default")

    # Verify get_token was called
    mock_azure_auth_instance.get_token.assert_called_once()

    # Verify SpeechConfig was created with auth_token and region
    expected_auth_token = "aad#test_resource_id#test_token"
    mock_speech_config.assert_called_once_with(auth_token=expected_auth_token, region="test_region")

    assert result == mock_config


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
@patch("azure.cognitiveservices.speech.SpeechConfig")
def test_get_speech_config_with_key_and_region(mock_speech_config: Any) -> None:
    """Test get_speech_config with key and region uses SpeechConfig directly."""
    mock_config = MagicMock()
    mock_speech_config.return_value = mock_config

    result = get_speech_config(resource_id=None, key="test_key", region="test_region")

    mock_speech_config.assert_called_once_with(subscription="test_key", region="test_region")
    assert result == mock_config


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
@patch("pyrit.auth.azure_auth.get_speech_config_from_default_azure_credential")
def test_get_speech_config_with_resource_id_and_region(mock_get_speech_config_from_cred: Any) -> None:
    """Test get_speech_config with resource_id and region uses credential auth."""
    mock_config = MagicMock()
    mock_get_speech_config_from_cred.return_value = mock_config

    result = get_speech_config(resource_id="test_resource_id", key=None, region="test_region")

    mock_get_speech_config_from_cred.assert_called_once_with(resource_id="test_resource_id", region="test_region")
    assert result == mock_config


@pytest.mark.skipif(not is_speechsdk_installed(), reason="Azure Speech SDK is not installed.")
def test_get_speech_config_insufficient_info_raises_error() -> None:
    """Test get_speech_config raises ValueError with insufficient information."""
    with pytest.raises(ValueError, match="Insufficient information provided for Azure Speech service"):
        get_speech_config(resource_id=None, key=None, region="test_region")


def test_get_access_token_from_interactive_login_returns_token():
    from pyrit.auth.azure_auth import get_access_token_from_interactive_login

    with (
        patch("pyrit.auth.azure_auth.InteractiveBrowserCredential") as mock_cred_cls,
        patch("pyrit.auth.azure_auth.get_bearer_token_provider") as mock_provider_fn,
    ):
        mock_provider_fn.return_value = MagicMock(return_value="test_token_123")
        result = get_access_token_from_interactive_login(scope="https://cognitiveservices.azure.com/.default")

    assert result == "test_token_123"
    mock_cred_cls.assert_called_once()
    mock_provider_fn.assert_called_once()


def test_get_access_token_from_interactive_login_propagates_exception():
    from pyrit.auth.azure_auth import get_access_token_from_interactive_login

    with (
        patch("pyrit.auth.azure_auth.InteractiveBrowserCredential"),
        patch("pyrit.auth.azure_auth.get_bearer_token_provider", side_effect=ValueError("auth failed")),
    ):
        with pytest.raises(ValueError, match="auth failed"):
            get_access_token_from_interactive_login(scope="https://test.scope")
