# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import functions to test from local application files
from pyrit.common.download_hf_model import (
    download_chunk,
    download_chunk_async,
    download_file,
    download_file_async,
    download_files,
    download_files_async,
    download_specific_files,
    download_specific_files_async,
)

# Define constants for testing
MODEL_ID = "microsoft/Phi-3-mini-4k-instruct"
FILE_PATTERNS = [
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "config.json",
    "tokenizer.json",
    "tokenizer.model",
    "special_tokens_map.json",
    "generation_config.json",
]


@pytest.fixture(scope="module")
def setup_environment():
    """Fixture to set up the environment for Hugging Face downloads."""
    # Check for Hugging Face token
    with patch.dict(os.environ, {"HUGGINGFACE_TOKEN": "mocked_token"}):
        token = os.getenv("HUGGINGFACE_TOKEN")
        yield token


async def test_download_specific_files_async(setup_environment):
    """Test downloading specific files"""
    token = setup_environment  # Get the token from the fixture

    with patch("os.makedirs"), patch("pyrit.common.download_hf_model.download_files_async"):
        await download_specific_files_async(MODEL_ID, FILE_PATTERNS, token, Path(""))


async def test_deprecated_alias_emits_warning_and_delegates(setup_environment):
    token = setup_environment

    with patch("os.makedirs"), patch("pyrit.common.download_hf_model.download_files_async"):
        with pytest.warns(DeprecationWarning, match="download_specific_files"):
            await download_specific_files(MODEL_ID, FILE_PATTERNS, token, Path(""))


async def test_download_chunk_deprecated_alias_emits_warning_and_delegates():
    client = MagicMock()
    seen: dict[str, tuple] = {}

    async def fake_chunk_async(url, headers, start, end, c):
        seen["args"] = (url, headers, start, end, c)
        return b"data"

    with patch("pyrit.common.download_hf_model.download_chunk_async", new=fake_chunk_async):
        with pytest.warns(DeprecationWarning, match="download_chunk"):
            result = await download_chunk("https://example/file", {"k": "v"}, 0, 9, client)

    assert seen["args"] == ("https://example/file", {"k": "v"}, 0, 9, client)
    assert result == b"data"


async def test_download_file_deprecated_alias_emits_warning_and_delegates():
    seen: dict[str, tuple] = {}

    async def fake_file_async(url, token, download_dir, num_splits):
        seen["args"] = (url, token, download_dir, num_splits)

    with patch("pyrit.common.download_hf_model.download_file_async", new=fake_file_async):
        with pytest.warns(DeprecationWarning, match="download_file"):
            await download_file("https://example/file", "token", Path(""), 3)

    assert seen["args"] == ("https://example/file", "token", Path(""), 3)


async def test_download_files_deprecated_alias_emits_warning_and_delegates():
    seen: dict[str, tuple] = {}

    async def fake_files_async(urls, token, download_dir, num_splits, parallel_downloads):
        seen["args"] = (urls, token, download_dir, num_splits, parallel_downloads)

    with patch("pyrit.common.download_hf_model.download_files_async", new=fake_files_async):
        with pytest.warns(DeprecationWarning, match="download_files"):
            await download_files(["https://example/file"], "token", Path(""), 3, 4)

    assert seen["args"] == (["https://example/file"], "token", Path(""), 3, 4)


async def test_download_files_async_dispatches_one_call_per_url():
    """Exercise the nested download_with_limit_async helper plus asyncio.gather."""
    seen_urls: list[str] = []

    async def fake_file_async(url, token, download_dir, num_splits):
        seen_urls.append(url)

    with patch("pyrit.common.download_hf_model.download_file_async", new=fake_file_async):
        urls = ["https://example/a", "https://example/b", "https://example/c"]
        await download_files_async(urls, "token", Path("/tmp"), num_splits=2, parallel_downloads=2)

    assert sorted(seen_urls) == sorted(urls)


async def test_download_file_async_schedules_one_chunk_per_split(tmp_path):
    """Exercise the chunk-task assembly inside download_file_async."""
    num_splits = 3
    file_size = 30
    chunk_bytes = b"abcdefghij"

    head_response = MagicMock()
    head_response.raise_for_status = MagicMock()
    head_response.headers = {"Content-Length": str(file_size)}

    client_instance = MagicMock()
    client_instance.head = AsyncMock(return_value=head_response)

    class _ClientCM:
        async def __aenter__(self):
            return client_instance

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with (
        patch("pyrit.common.download_hf_model.httpx.AsyncClient", return_value=_ClientCM()),
        patch("pyrit.common.download_hf_model.download_chunk_async", new_callable=AsyncMock) as mock_chunk_async,
    ):
        mock_chunk_async.return_value = chunk_bytes
        await download_file_async("https://example/myfile.bin", "token", tmp_path, num_splits)

    assert mock_chunk_async.await_count == num_splits
    assert (tmp_path / "myfile.bin").read_bytes() == chunk_bytes * num_splits


async def test_download_chunk_async_returns_response_content():
    """Sanity-check the real download_chunk_async implementation."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.content = b"chunk-payload"
    client = MagicMock()
    client.get = AsyncMock(return_value=response)

    result = await download_chunk_async("https://example/file", {"Authorization": "Bearer t"}, 0, 9, client)

    assert result == b"chunk-payload"
    client.get.assert_awaited_once()
