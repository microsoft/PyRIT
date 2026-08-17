# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
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

    async def test_fetch_dataset(self, tmp_path, valid_yaml_content):
        file_path = tmp_path / "test.yaml"
        file_path.write_text(valid_yaml_content, encoding="utf-8")

        loader = _LocalDatasetLoader(file_path=file_path)
        dataset = await loader.fetch_dataset_async()

        assert isinstance(dataset, SeedDataset)
        assert dataset.dataset_name == "test_dataset"
        assert len(dataset.prompts) == 1
        assert dataset.prompts[0].value == "test prompt"

    async def test_fetch_dataset_warns_that_in_memory_edits_are_not_written_to_disk(
        self,
        *,
        tmp_path: Path,
        valid_yaml_content: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        file_path = tmp_path / "test.yaml"
        file_path.write_text(valid_yaml_content, encoding="utf-8")
        loader = _LocalDatasetLoader(file_path=file_path)

        with caplog.at_level(
            logging.WARNING,
            logger="pyrit.datasets.seed_datasets.local.local_dataset_loader",
        ):
            await loader.fetch_dataset_async()

        assert str(file_path) in caplog.text
        assert "not written back to disk" in caplog.text
        assert "save edits to the source file before reloading or they will be lost" in caplog.text

    async def test_fetch_dataset_file_not_found_does_not_log_success_warning(
        self,
        *,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        loader = _LocalDatasetLoader(file_path=Path("non_existent.yaml"))
        with caplog.at_level(
            logging.WARNING,
            logger="pyrit.datasets.seed_datasets.local.local_dataset_loader",
        ):
            with pytest.raises(Exception):
                await loader.fetch_dataset_async()

        assert "Local dataset provider loaded" not in caplog.text
