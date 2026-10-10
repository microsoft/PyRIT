# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.common.download_hf_model import download_specific_files_async

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
    with patch.dict(os.environ, {"HUGGINGFACE_TOKEN": "mocked_token"}):
        token = os.getenv("HUGGINGFACE_TOKEN")
        yield token


async def test_download_specific_files_async(setup_environment):
    """Test downloading specific files"""
    token = setup_environment  # Get the token from the fixture
    cache_dir = Path("model-cache")

    with (
        patch("pathlib.Path.mkdir"),
        patch("pyrit.common.download_hf_model.snapshot_download") as snapshot_download_mock,
    ):
        await download_specific_files_async(MODEL_ID, FILE_PATTERNS, token, cache_dir)

    snapshot_download_mock.assert_called_once_with(
        repo_id=MODEL_ID,
        allow_patterns=FILE_PATTERNS,
        token=token,
        local_dir=cache_dir,
    )
