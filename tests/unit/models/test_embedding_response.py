# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path

import pytest

from pyrit.models import EmbeddingData, EmbeddingResponse, EmbeddingUsageInformation


@pytest.fixture
def my_embedding() -> EmbeddingResponse:
    return EmbeddingResponse(
        model="test",
        object="test",
        usage=EmbeddingUsageInformation(prompt_tokens=0, total_tokens=0),
        data=[EmbeddingData(embedding=[0.0], index=0, object="embedding")],
    )


@pytest.fixture
def my_embedding_data() -> dict:
    return {
        "model": "test",
        "object": "test",
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
        "data": [{"embedding": [0.0], "index": 0, "object": "embedding"}],
    }


def test_can_save_embeddings(my_embedding: EmbeddingResponse):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_file = my_embedding.save_to_file(Path(tmp_dir))
        assert Path(output_file).exists()


def test_embedding_creation_is_idempotent(my_embedding: EmbeddingResponse, my_embedding_data: dict):
    new_embedding = EmbeddingResponse(**my_embedding_data)
    assert new_embedding == my_embedding


def test_save_load_loop_is_idempotent(my_embedding):
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_file = my_embedding.save_to_file(Path(tmp_dir))
        loaded_embedding = EmbeddingResponse.load_from_file(Path(output_file))
        assert my_embedding == loaded_embedding
