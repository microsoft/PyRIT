# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader
from pyrit.models import SeedDataset, SeedPrompt


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

    async def test_fetch_dataset_offloads_file_read(self, tmp_path: Path) -> None:
        """Dataset file loading runs outside the event loop thread."""
        file_path = tmp_path / "test.yaml"
        loader = _LocalDatasetLoader.__new__(_LocalDatasetLoader)
        loader.file_path = file_path
        loader._dataset_name = "test_dataset"
        expected = SeedDataset(
            dataset_name="test_dataset",
            seeds=[SeedPrompt(value="test prompt", data_type="text")],
        )
        to_thread_mock = AsyncMock(return_value=expected)

        with (
            patch.object(SeedDataset, "from_yaml_file") as load_mock,
            patch(
                "pyrit.datasets.seed_datasets.local.local_dataset_loader.asyncio.to_thread",
                new=to_thread_mock,
            ),
        ):
            dataset = await loader.fetch_dataset_async()

        assert dataset is expected
        to_thread_mock.assert_awaited_once_with(load_mock, file_path)
        load_mock.assert_not_called()

    async def test_parse_metadata_offloads_file_read(self, tmp_path: Path) -> None:
        """Metadata YAML parsing runs outside the event loop thread."""
        file_path = tmp_path / "test.yaml"
        loader = _LocalDatasetLoader.__new__(_LocalDatasetLoader)
        loader.file_path = file_path
        loader._dataset_name = "test_dataset"
        read_yaml_mock = MagicMock(
            return_value={
                "dataset_name": "test_dataset",
                "harm_categories": ["violence"],
            }
        )
        to_thread_mock = AsyncMock(return_value=read_yaml_mock.return_value)

        with (
            patch.object(loader, "_read_yaml", new=read_yaml_mock),
            patch(
                "pyrit.datasets.seed_datasets.local.local_dataset_loader.asyncio.to_thread",
                new=to_thread_mock,
            ),
        ):
            metadata = await loader._parse_metadata_async()

        assert metadata is not None
        assert metadata.harm_categories == {"violence"}
        to_thread_mock.assert_awaited_once_with(read_yaml_mock)
        read_yaml_mock.assert_not_called()

    async def test_fetch_dataset_file_not_found(self):
        loader = _LocalDatasetLoader(file_path=Path("non_existent.yaml"))
        with pytest.raises(Exception):
            await loader.fetch_dataset_async()
