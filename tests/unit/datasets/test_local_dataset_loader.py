# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.local.local_dataset_loader import _LocalDatasetLoader
from pyrit.memory import MemoryInterface
from pyrit.models import SeedDataset, SeedOrigin, SeedPrompt


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
    origin: local
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
        assert dataset.seeds[0].origin is SeedOrigin.LOCAL

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

    async def test_fetch_dataset_rejects_conflicting_origin_async(self, tmp_path: Path) -> None:
        file_path = tmp_path / "conflicting.yaml"
        await asyncio.to_thread(
            file_path.write_text,
            "dataset_name: conflict\nseeds:\n  - value: test\n    origin: remote\n",
            encoding="utf-8",
        )
        loader = await asyncio.to_thread(_LocalDatasetLoader, file_path=file_path)
        with pytest.raises(ValueError, match="declares origin 'remote'"):
            await loader.fetch_dataset_async()

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


@pytest.mark.usefixtures("patch_central_database")
class TestLocalDatasetFilenameFallback:
    """A filename-derived dataset name must reach the seeds before they are persisted (#3740)."""

    filename_only_yaml = """
source: http://example.com
seeds:
  - value: filename fallback prompt
    data_type: text
    origin: local
  - value: per seed named prompt
    data_type: text
    origin: local
    dataset_name: per_seed_dataset
"""

    explicitly_named_yaml = """
dataset_name: explicit_dataset
source: http://example.com
seeds:
  - value: explicitly named prompt
    data_type: text
    origin: local
"""

    async def test_filename_fallback_is_assigned_to_seeds_and_queryable_async(
        self, tmp_path: Path, sqlite_instance: MemoryInterface
    ) -> None:
        file_path = tmp_path / "filename_only.yaml"
        file_path.write_text(self.filename_only_yaml, encoding="utf-8")

        dataset = await _LocalDatasetLoader(file_path=file_path).fetch_dataset_async()

        assert dataset.dataset_name == "filename_only"
        assert dataset.seeds[0].dataset_name == "filename_only"
        assert dataset.seeds[1].dataset_name == "per_seed_dataset"

        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="test")

        stored = await sqlite_instance.get_seeds_async(dataset_name="filename_only")
        assert [seed.value for seed in stored] == ["filename fallback prompt"]
        assert set(await sqlite_instance.get_seed_dataset_names_async()) == {
            "filename_only",
            "per_seed_dataset",
        }

    async def test_explicit_dataset_name_is_unchanged_async(
        self, tmp_path: Path, sqlite_instance: MemoryInterface
    ) -> None:
        file_path = tmp_path / "explicit.yaml"
        file_path.write_text(self.explicitly_named_yaml, encoding="utf-8")

        dataset = await _LocalDatasetLoader(file_path=file_path).fetch_dataset_async()

        assert dataset.dataset_name == "explicit_dataset"
        assert dataset.seeds[0].dataset_name == "explicit_dataset"

        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="test")
        assert len(await sqlite_instance.get_seeds_async(dataset_name="explicit_dataset")) == 1
        assert "explicit_dataset" in await sqlite_instance.get_seed_dataset_names_async()
