# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path

from huggingface_hub import snapshot_download


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
