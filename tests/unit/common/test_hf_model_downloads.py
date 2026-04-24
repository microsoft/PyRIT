# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from unittest.mock import patch

import pytest

# Import functions to test from local application files
from pyrit.common.download_hf_model import download_specific_files

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


@pytest.mark.asyncio
async def test_download_specific_files(setup_environment):
    """Test downloading specific files"""
    token = setup_environment  # Get the token from the fixture

    with patch("os.makedirs"), patch("pyrit.common.download_hf_model.download_files"):
        await download_specific_files(MODEL_ID, FILE_PATTERNS, token, Path(""))
