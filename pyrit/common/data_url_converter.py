# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import DataTypeSerializer, data_serializer_factory

# Supported image formats for Azure OpenAI GPT-4o,
# https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/use-your-image-data
AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif"]


async def convert_local_image_to_data_url(image_path: str) -> str:
    """
    Convert a local image file to a data URL encoded in base64.

    Args:
        image_path (str): The file system path to the image file.

    Raises:
        FileNotFoundError: If no file is found at the specified `image_path`.
        ValueError: If the image file's extension is not in the supported formats list.

    Returns:
        str: A string containing the MIME type and the base64-encoded data of the image, formatted as a data URL.
    """
    ext = DataTypeSerializer.get_extension(image_path)
    if ext.lower() not in AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS:
        raise ValueError(
            f"Unsupported image format: {ext}. Supported formats are: {AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS}"
        )

    mime_type = DataTypeSerializer.get_mime_type(image_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    image_serializer = data_serializer_factory(
        category="prompt-memory-entries", value=image_path, data_type="image_path", extension=ext
    )
    base64_encoded_data = await image_serializer.read_data_base64()
    # Azure OpenAI documentation doesn't specify the local image upload format for API.
    # GPT-4o image upload format is determined using "view code" functionality in Azure OpenAI deployments
    # The image upload format is same as GPT-4 Turbo.
    # Construct the data URL, as per Azure OpenAI GPT-4 Turbo local image format
    # https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/gpt-with-vision?tabs=rest%2Csystem-assigned%2Cresource#call-the-chat-completion-apis
    return f"data:{mime_type};base64,{base64_encoded_data}"
