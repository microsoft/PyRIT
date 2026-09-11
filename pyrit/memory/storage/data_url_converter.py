# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common import get_mime_type
from pyrit.memory.storage.serializers import DataTypeSerializer, data_serializer_factory

# Supported image formats for OpenAI vision models.
# Animated GIFs are accepted, but only the first frame is processed.
# https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-with-vision
OPENAI_VISION_SUPPORTED_IMAGE_FORMATS = [".jpg", ".jpeg", ".png", ".webp", ".gif"]


async def convert_local_image_to_data_url_async(image_path: str) -> str:
    """
    Convert a local image file to a data URL encoded in base64.

    Args:
        image_path (str): The file system path to the image file.

    Returns:
        str: A string containing the MIME type and the base64-encoded data of the image, formatted as a data URL.

    Raises:
        FileNotFoundError: If no file is found at the specified `image_path`.
        ValueError: If the image format is unsupported.
    """
    ext = DataTypeSerializer.get_extension(image_path)
    if ext is None or ext.lower() not in OPENAI_VISION_SUPPORTED_IMAGE_FORMATS:
        raise ValueError(
            f"Unsupported image format: {ext}. Supported formats are: {OPENAI_VISION_SUPPORTED_IMAGE_FORMATS}"
        )

    mime_type = get_mime_type(image_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    image_serializer = data_serializer_factory(
        category="prompt-memory-entries", value=image_path, data_type="image_path", extension=ext
    )
    base64_encoded_data = await image_serializer.read_data_base64_async()
    # OpenAI API accepts base64-encoded images.
    return f"data:{mime_type};base64,{base64_encoded_data}"
