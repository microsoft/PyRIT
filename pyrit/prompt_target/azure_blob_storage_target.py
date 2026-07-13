# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import ClassVar
from urllib.parse import urlparse

from azure.core.exceptions import ClientAuthenticationError
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import ContainerClient as AsyncContainerClient

from pyrit.common import default_values
from pyrit.models import ComponentIdentifier, Message, construct_response_from_request
from pyrit.prompt_target.common.prompt_target import AuthMode, PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


class SupportedContentType(Enum):
    """
    All supported content types for uploading blobs to provided storage account container.
    See all options here: https://www.iana.org/assignments/media-types/media-types.xhtml.
    """

    PLAIN_TEXT = "text/plain"
    HTML = "text/html"


class AzureBlobStorageTarget(PromptTarget):
    """
    The AzureBlobStorageTarget takes prompts, saves the prompts to a file, and stores them as a blob in a provided
    storage account container.

    Args:
        container_url (str): URL to the Azure Blob Storage Container.
        sas_token (optional[str]): Optional Blob SAS token needed to authenticate blob operations. If not provided,
            ``DefaultAzureCredential`` is used directly, which requires the caller to hold a data-plane role such
            as Storage Blob Data Contributor on the storage account.
        blob_content_type (SupportedContentType): Expected Content Type of the blob, chosen from the
            SupportedContentType enum. Set to PLAIN_TEXT by default.
        max_requests_per_minute (int, Optional): Number of requests the target can handle per
            minute before hitting a rate limit. The number of requests sent to the target
            will be capped at the value provided.
    """

    AZURE_STORAGE_CONTAINER_ENVIRONMENT_VARIABLE: str = "AZURE_STORAGE_ACCOUNT_CONTAINER_URL"
    SAS_TOKEN_ENVIRONMENT_VARIABLE: str = "AZURE_STORAGE_ACCOUNT_SAS_TOKEN"

    # A SAS token is the "api_key"; with no token the target falls back to
    # ``DefaultAzureCredential`` (identity-based auth).
    supported_auth_modes: ClassVar[tuple[AuthMode, ...]] = ("api_key", "identity")

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            input_modalities=frozenset(
                {
                    frozenset(["text"]),
                    frozenset(["url"]),
                }
            ),
            output_modalities=frozenset(
                {
                    frozenset(["url"]),
                }
            ),
        )
    )

    def __init__(
        self,
        *,
        container_url: str | None = None,
        sas_token: str | None = None,
        blob_content_type: SupportedContentType = SupportedContentType.PLAIN_TEXT,
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Initialize the Azure Blob Storage target.

        Args:
            container_url (str, Optional): The Azure Storage container URL.
                Defaults to the AZURE_STORAGE_ACCOUNT_CONTAINER_URL environment variable.
            sas_token (str, Optional): The SAS token for authentication.
                Defaults to the AZURE_STORAGE_ACCOUNT_SAS_TOKEN environment variable.
            blob_content_type (SupportedContentType): The content type for blobs.
                Defaults to PLAIN_TEXT.
            max_requests_per_minute (int, Optional): Maximum number of requests per minute.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
        """
        self._blob_content_type: str = blob_content_type.value

        self._container_url: str = default_values.get_required_value(
            env_var_name=self.AZURE_STORAGE_CONTAINER_ENVIRONMENT_VARIABLE, passed_value=container_url
        )

        self._sas_token: str | None = sas_token
        self._client_async: AsyncContainerClient | None = None
        self._credential: DefaultAzureCredential | None = None

        super().__init__(
            endpoint=self._container_url,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with Azure Blob Storage-specific parameters.

        Returns:
            ComponentIdentifier: The identifier for this target instance.
        """
        return self._create_identifier(
            params={
                "container_url": self._container_url,
                "blob_content_type": self._blob_content_type,
            },
        )

    async def _create_container_client_async(self) -> None:
        """
        Create an asynchronous ContainerClient for Azure Storage. If a SAS token is provided via the
        AZURE_STORAGE_ACCOUNT_SAS_TOKEN environment variable or the init sas_token parameter, it will be used
        for authentication. Otherwise, ``DefaultAzureCredential`` is used directly, which requires the caller
        to hold a data-plane role such as Storage Blob Data Contributor on the storage account.
        """
        container_url, _ = self._parse_url()
        try:
            sas_token: str = default_values.get_required_value(
                env_var_name=self.SAS_TOKEN_ENVIRONMENT_VARIABLE, passed_value=self._sas_token
            )
        except ValueError:
            logger.info("SAS token not provided. Using DefaultAzureCredential for direct Entra ID authentication.")
            account_url, _, container_name = container_url.rpartition("/")
            self._credential = DefaultAzureCredential()
            self._client_async = AsyncContainerClient(
                account_url=account_url,
                container_name=container_name,
                credential=self._credential,
            )
            return

        logger.info("Using SAS token from environment variable or passed parameter.")
        self._client_async = AsyncContainerClient.from_container_url(
            container_url=container_url,
            credential=sas_token,
        )

    async def _close_client_async(self) -> None:
        """Close the container client and credential, resetting both to None."""
        client, self._client_async = self._client_async, None
        credential, self._credential = self._credential, None
        try:
            if client:
                await client.close()
        finally:
            if credential:
                await credential.close()

    async def _upload_blob_async(self, file_name: str, data: bytes, content_type: str) -> None:
        """
        (Async) Handles uploading blob to given storage container.

        Args:
            file_name (str): File name to assign to uploaded blob.
            data (bytes): Byte representation of content to upload to container.
            content_type (str): Content type to upload.

        Raises:
            RuntimeError: If blob storage client is not initialized.
        """
        content_settings = ContentSettings(content_type=f"{content_type}")
        logger.info(msg="\nUploading to Azure Storage as blob:\n\t" + file_name)

        # Parse the Azure Storage Blob URL to extract components
        _, blob_prefix = self._parse_url()
        # If a blob prefix is provided, prepend it to the file name.
        # If not, the file will be put in the root of the container.
        blob_path = f"{blob_prefix}/{file_name}" if blob_prefix else file_name
        try:
            if not self._client_async:
                await self._create_container_client_async()
            if self._client_async is None:
                raise RuntimeError("Blob storage client not initialized")
            blob_client = self._client_async.get_blob_client(blob=blob_path)
            if await blob_client.exists():
                logger.info(msg=f"Blob {blob_path} already exists. Deleting it before uploading a new version.")
                await blob_client.delete_blob()
            logger.info(msg=f"Uploading new blob to {blob_path}")
            await blob_client.upload_blob(data=data, content_settings=content_settings)
        except Exception as exc:
            if isinstance(exc, ClientAuthenticationError):
                logger.exception(
                    msg="Authentication failed. Please check that the container exists in the "
                    "Azure Storage Account. If using a SAS token, ensure it is valid. Otherwise, "
                    "ensure you are logged in via `az login` and hold a data-plane role such as "
                    "Storage Blob Data Contributor on the storage account."
                )
                raise
            logger.exception(msg=f"An unexpected error occurred: {exc}")
            raise
        finally:
            await self._close_client_async()

    def _parse_url(self) -> tuple[str, str]:
        """
        Parse the Azure Storage Blob URL to extract components.

        Returns:
            tuple: A tuple containing the container URL and blob prefix.

        Raises:
            ValueError: If the container URL does not include a container name in its path.
        """
        parsed_url = urlparse(self._container_url)
        path_parts = parsed_url.path.split("/")
        if len(path_parts) < 2 or not path_parts[1]:
            raise ValueError(
                f"Invalid Azure Storage container URL '{self._container_url}': expected a container name in the "
                "path, e.g. https://<account>.blob.core.windows.net/<container>."
            )
        container_name = path_parts[1]
        blob_prefix = "/".join(path_parts[2:])
        container_url = f"https://{parsed_url.netloc}/{container_name}"
        return container_url, blob_prefix

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        (Async) Sends prompt to target, which creates a file and uploads it as a blob
        to the provided storage container.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: A list containing the response with the Blob URL.
        """
        message = normalized_conversation[-1]
        request = message.message_pieces[0]

        # default file name is <conversation_id>.txt, but can be overridden by prompt metadata
        file_name = f"{request.conversation_id}.txt"
        if request.prompt_metadata.get("file_name"):
            file_name = str(request.prompt_metadata["file_name"])

        data = str.encode(request.converted_value)
        blob_url = self._container_url + "/" + file_name

        await self._upload_blob_async(file_name=file_name, data=data, content_type=self._blob_content_type)

        response = construct_response_from_request(
            request=request, response_text_pieces=[blob_url], response_type="url"
        )

        return [response]
