# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from pathlib import Path

import aiofiles
import httpx
from huggingface_hub import HfApi, snapshot_download

logger = logging.getLogger(__name__)


def get_available_files(model_id: str, token: str) -> list[str]:
    """
    Fetch available files for a model from the Hugging Face repository.

    Returns:
        List of available file names.

    Raises:
        ValueError: If no files are found for the model.
    """
    api = HfApi()
    try:
        model_info = api.model_info(model_id, token=token)
        available_files = [file.rfilename for file in (model_info.siblings or [])]

        # Perform simple validation: raise a ValueError if no files are available
        if not len(available_files):
            raise ValueError(f"No available files found for the model: {model_id}")

        return available_files
    except Exception as e:
        logger.info(f"Error fetching model files for {model_id}: {e}")
        return []


async def download_specific_files_async(
    model_id: str, file_patterns: list[str] | None, token: str, cache_dir: Path
) -> None:
    """
    Download a Hugging Face model snapshot without blocking the event loop.

    If file_patterns is None, downloads all files.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        snapshot_download,
        repo_id=model_id,
        allow_patterns=file_patterns,
        token=token,
        local_dir=cache_dir,
    )


async def download_chunk_async(
    url: str, headers: dict[str, str], start: int, end: int, client: httpx.AsyncClient
) -> bytes:
    """
    Download a chunk of the file with a specified byte range.

    Returns:
        The content of the downloaded chunk.
    """
    range_header = {"Range": f"bytes={start}-{end}", **headers}
    response = await client.get(url, headers=range_header)
    response.raise_for_status()
    return response.content


async def download_file_async(url: str, token: str, download_dir: Path, num_splits: int) -> None:
    """Download a file in multiple segments (splits) using byte-range requests."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Get the file size to determine chunk size
        response = await client.head(url, headers=headers)
        response.raise_for_status()
        file_size = int(response.headers["Content-Length"])
        chunk_size = file_size // num_splits

        # Prepare tasks for each chunk
        tasks = []
        file_name = url.split("/")[-1]
        file_path = Path(download_dir, file_name)

        for i in range(num_splits):
            start = i * chunk_size
            end = start + chunk_size - 1 if i < num_splits - 1 else file_size - 1
            tasks.append(download_chunk_async(url, headers, start, end, client))

        # Download all chunks concurrently
        chunks = await asyncio.gather(*tasks)

        # Write chunks to the file in order
        async with aiofiles.open(file_path, "wb") as f:
            for chunk in chunks:
                await f.write(chunk)
        logger.info(f"Downloaded {file_name} to {file_path}")


async def download_files_async(
    urls: list[str], token: str, download_dir: Path, num_splits: int = 3, parallel_downloads: int = 4
) -> None:
    """Download multiple files with parallel downloads and segmented downloading."""
    # Limit the number of parallel downloads
    semaphore = asyncio.Semaphore(parallel_downloads)

    async def download_with_limit_async(url: str) -> None:
        async with semaphore:
            await download_file_async(url, token, download_dir, num_splits)

    # Run downloads concurrently, but limit to parallel_downloads at a time
    await asyncio.gather(*(download_with_limit_async(url) for url in urls))
