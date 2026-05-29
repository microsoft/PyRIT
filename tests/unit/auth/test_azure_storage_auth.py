# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.storage.blob import UserDelegationKey
from azure.storage.blob.aio import BlobServiceClient

from pyrit.auth import AzureStorageAuth

# Mock data
MOCK_CONTAINER_URL = "https://storageaccountname.blob.core.windows.net/containername"


async def test_get_user_delegation_key():
    mock_blob_service_client = AsyncMock(spec=BlobServiceClient)
    expected_start_time = datetime.now(tz=timezone.utc)
    expected_expiry_time = expected_start_time + timedelta(days=1)

    mock_user_delegation_key = UserDelegationKey()
    mock_user_delegation_key.signed_oid = "test_oid"
    mock_user_delegation_key.signed_tid = "test_tid"
    mock_user_delegation_key.signed_start = expected_start_time.isoformat()
    mock_user_delegation_key.signed_expiry = expected_expiry_time.isoformat()
    mock_user_delegation_key.signed_service = "b"
    mock_user_delegation_key.signed_version = "2020-02-10"

    mock_blob_service_client.get_user_delegation_key.return_value = mock_user_delegation_key

    actual_delegation_key = await AzureStorageAuth.get_user_delegation_key(mock_blob_service_client)

    assert actual_delegation_key.signed_oid == mock_user_delegation_key.signed_oid
    assert actual_delegation_key.signed_tid == mock_user_delegation_key.signed_tid
    assert actual_delegation_key.signed_start == mock_user_delegation_key.signed_start
    assert actual_delegation_key.signed_expiry == mock_user_delegation_key.signed_expiry
    assert actual_delegation_key.signed_service == mock_user_delegation_key.signed_service
    assert actual_delegation_key.signed_version == mock_user_delegation_key.signed_version


@patch("pyrit.auth.AzureStorageAuth.get_user_delegation_key", new_callable=AsyncMock)
@patch("azure.storage.blob.aio.BlobServiceClient")
@patch("azure.storage.blob.aio.ContainerClient")
@patch("azure.storage.blob._shared_access_signature.BlobSharedAccessSignature")
async def test_get_sas_token(
    mock_blob_sas, mock_container_client, mock_blob_service_client, mock_get_user_delegation_key
):
    # Mocking the user delegation key
    mock_user_delegation_key = UserDelegationKey()
    mock_get_user_delegation_key.return_value = mock_user_delegation_key

    # Mocking the container URL
    container_url = "https://mockaccount.blob.core.windows.net/mockcontainer"
    # Use MagicMock with spec to avoid AsyncMock behavior for sync properties
    mock_container_client_instance = MagicMock(spec=["container_name", "account_name"])
    mock_container_client.from_container_url.return_value = mock_container_client_instance
    mock_container_client_instance.container_name = "mock_container"
    mock_container_client_instance.account_name = "mock_account"

    # Mocking the generate_container return value
    mock_sas_instance = mock_blob_sas.return_value
    mock_sas_instance.generate_container.return_value = "mock_sas_token"

    sas_token = await AzureStorageAuth.get_sas_token(container_url)

    # Assertions
    assert sas_token == "mock_sas_token"
    mock_get_user_delegation_key.assert_awaited_once()
    mock_blob_sas.assert_called_once()
    mock_sas_instance.generate_container.assert_called_once()


async def test_get_sas_token_no_url():
    # Test with no container URL
    with pytest.raises(
        ValueError,
        match="Azure Storage Container URL is not provided."
        " The correct format is 'https://storageaccountname.core.windows.net/containername'.",
    ):
        await AzureStorageAuth.get_sas_token("")


async def test_get_sas_token_invalid_url_scheme():
    # Test with invalid container URL (no scheme)
    invalid_url = "mockaccount.blob.core.windows.net/mockcontainer"
    with pytest.raises(
        ValueError,
        match="Invalid Azure Storage Container URL."
        " The correct format is 'https://storageaccountname.core.windows.net/containername'.",
    ):
        await AzureStorageAuth.get_sas_token(invalid_url)


async def test_get_sas_token_invalid_url_netloc():
    # Test with invalid container URL (no netloc)
    invalid_url = "https:///mockcontainer"
    with pytest.raises(
        ValueError,
        match="Invalid Azure Storage Container URL."
        " The correct format is 'https://storageaccountname.core.windows.net/containername'.",
    ):
        await AzureStorageAuth.get_sas_token(invalid_url)


async def test_get_sas_token_invalid_url_path():
    # Test with invalid container URL (no path)
    invalid_url = "https://storageaccountname.core.windows.net"
    with pytest.raises(
        ValueError,
        match="Invalid Azure Storage Container URL."
        " The correct format is 'https://storageaccountname.core.windows.net/containername'.",
    ):
        await AzureStorageAuth.get_sas_token(invalid_url)
