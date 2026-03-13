# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset


class ConcreteRemoteLoader(_RemoteDatasetLoader):
    @property
    def dataset_name(self):
        return "test_remote"

    async def fetch_dataset(self):
        return SeedDataset(prompts=[])


class TestRemoteDatasetLoader:
    def test_get_cache_file_name(self):
        loader = ConcreteRemoteLoader()
        name = loader._get_cache_file_name(source="http://example.com", file_type="json")
        assert name.endswith(".json")
        # MD5 of "http://example.com"
        assert name.startswith("a9b9f04336ce0181a08e774e01113b31")

    def test_get_cache_file_name_deterministic(self):
        """Test that same source produces same cache name."""
        loader = ConcreteRemoteLoader()
        source = "https://example.com/data.csv"

        name1 = loader._get_cache_file_name(source=source, file_type="csv")
        name2 = loader._get_cache_file_name(source=source, file_type="csv")

        assert name1 == name2

    def test_read_cache_json(self):
        loader = ConcreteRemoteLoader()
        mock_file = mock_open(read_data='[{"key": "value"}]')
        with patch("pathlib.Path.open", mock_file):
            data = loader._read_cache(cache_file=Path("test.json"), file_type="json")
            assert data == [{"key": "value"}]

    def test_read_cache_invalid_type(self):
        loader = ConcreteRemoteLoader()
        with patch("pathlib.Path.open", mock_open()), pytest.raises(ValueError, match="Invalid file_type"):
            loader._read_cache(cache_file=Path("test.xyz"), file_type="xyz")

    def test_write_cache_json(self, tmp_path):
        loader = ConcreteRemoteLoader()
        cache_file = tmp_path / "test.json"
        data = [{"key": "value"}]

        loader._write_cache(cache_file=cache_file, examples=data, file_type="json")

        assert cache_file.exists()
        with open(cache_file, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == data

    def test_write_cache_creates_directories(self, tmp_path):
        loader = ConcreteRemoteLoader()
        cache_file = tmp_path / "subdir" / "test.json"
        data = [{"key": "value"}]

        loader._write_cache(cache_file=cache_file, examples=data, file_type="json")

        assert cache_file.exists()
