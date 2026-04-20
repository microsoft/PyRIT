# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timedelta
from urllib.parse import urlparse

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import (
    ContainerSasPermissions,
    UserDelegationKey,
    generate_container_sas,
)
from azure.storage.blob.aio import BlobServiceClient


class AzureStorageAuth:
    """
    A utility class for Azure Storage authentication, providing methods to generate SAS tokens
    using user delegation keys.
    """

    @staticmethod
    async def get_user_delegation_key(blob_service_client: BlobServiceClient) -> UserDelegationKey:
        """
        Retrieve a user delegation key valid for one day.

        Args:
            blob_service_client (BlobServiceClient): An instance of BlobServiceClient to interact
            with Azure Blob Storage.

        Returns:
            UserDelegationKey: A user delegation key valid for one day.
        """
        delegation_key_start_time = datetime.now()
        delegation_key_expiry_time = delegation_key_start_time + timedelta(days=1)

        user_delegation_key = await blob_service_client.get_user_delegation_key(
            key_start_time=delegation_key_start_time, key_expiry_time=delegation_key_expiry_time
        )

        return user_delegation_key

    @staticmethod
    async def get_sas_token(container_url: str) -> str:
        """
        Generate a SAS token for the specified blob using a user delegation key.

        Args:
            container_url (str): The URL of the Azure Blob Storage container.

        Returns:
            str: The generated SAS token.

        Raises:
            ValueError: If container_url is empty or invalid.
        """
        if not container_url:
            raise ValueError(
                "Azure Storage Container URL is not provided. The correct format "
                "is 'https://storageaccountname.core.windows.net/containername'."
            )

        parsed_url = urlparse(container_url)

        if not parsed_url.scheme or not parsed_url.netloc or not parsed_url.path:
            raise ValueError(
                "Invalid Azure Storage Container URL."
                " The correct format is 'https://storageaccountname.core.windows.net/containername'."
            )

        account_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        credential = DefaultAzureCredential()

        try:
            async with BlobServiceClient(account_url=account_url, credential=credential) as blob_service_client:
                user_delegation_key = await AzureStorageAuth.get_user_delegation_key(
                    blob_service_client=blob_service_client
                )
                container_name = parsed_url.path.lstrip("/")
                storage_account_name = parsed_url.netloc.split(".")[0]

                # Set start_time 5 minutes before the current time to account for any clock skew
                start_time = datetime.now() - timedelta(minutes=5)
                expiry_time = start_time + timedelta(days=1)

                sas_token = generate_container_sas(
                    account_name=storage_account_name,
                    container_name=container_name,
                    user_delegation_key=user_delegation_key,
                    permission=ContainerSasPermissions(read=True, write=True, create=True, list=True, delete=True),  # type: ignore[no-untyped-call, unused-ignore]
                    expiry=expiry_time,
                    start=start_time,
                )
        finally:
            await credential.close()

        return sas_token  # type: ignore[return-value, no-any-return, unused-ignore]
