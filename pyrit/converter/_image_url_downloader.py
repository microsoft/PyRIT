# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import aiohttp


async def _download_image_from_url_async(url: str) -> bytes:
    """
    Download image data from a URL and return it as bytes.

    Args:
        url (str): The URL to download the image from.

    Returns:
        bytes: The content of the image as bytes.

    Raises:
        RuntimeError: If an aiohttp client error occurs during the download process.
    """
    try:
        async with aiohttp.ClientSession() as session, session.get(url) as response:
            response.raise_for_status()
            return await response.read()
    except aiohttp.ClientError as e:
        raise RuntimeError(f"Failed to download content from URL {url}: {str(e)}") from e
