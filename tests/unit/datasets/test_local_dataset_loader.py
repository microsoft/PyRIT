# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest

from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader
from pyrit.models import SeedDataset


class TestLocalDatasetLoader:
    @pytest.fixture
    def valid_yaml_content(self):
        return """
dataset_name: test_dataset
source: http://example.com
description: Test description
seeds:
  - value: test prompt
    data_type: text
"""

    def test_init(self, tmp_path, valid_yaml_content):
        file_path = tmp_path / "test.yaml"
        file_path.write_text(valid_yaml_content, encoding="utf-8")

        loader = _LocalDatasetLoader(file_path=file_path)
        assert loader.dataset_name == "test_dataset"
        assert loader.file_path == file_path

    def test_init_invalid_yaml(self, tmp_path):
        file_path = tmp_path / "test.yaml"
        file_path.write_text("invalid: yaml: content: :", encoding="utf-8")

        loader = _LocalDatasetLoader(file_path=file_path)
        # Should fallback to filename stem
        assert loader.dataset_name == "test"

    @pytest.mark.asyncio
    async def test_fetch_dataset(self, tmp_path, valid_yaml_content):
        file_path = tmp_path / "test.yaml"
        file_path.write_text(valid_yaml_content, encoding="utf-8")

        loader = _LocalDatasetLoader(file_path=file_path)
        dataset = await loader.fetch_dataset()

        assert isinstance(dataset, SeedDataset)
        assert dataset.dataset_name == "test_dataset"
        assert len(dataset.prompts) == 1
        assert dataset.prompts[0].value == "test prompt"

    @pytest.mark.asyncio
    async def test_fetch_dataset_file_not_found(self):
        loader = _LocalDatasetLoader(file_path=Path("non_existent.yaml"))
        with pytest.raises(Exception):  # noqa: B017
            await loader.fetch_dataset()
