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

    def test_write_cache_csv_allows_empty_examples(self, tmp_path):
        loader = ConcreteRemoteLoader()
        cache_file = tmp_path / "empty.csv"

        loader._write_cache(cache_file=cache_file, examples=[], file_type="csv")

        assert cache_file.exists()
        assert cache_file.read_text(encoding="utf-8") == ""
        assert loader._read_cache(cache_file=cache_file, file_type="csv") == []

    def test_get_file_type_strips_query_string(self):
        loader = ConcreteRemoteLoader()
        assert loader._get_file_type(source="https://example.com/data.json?download=1") == "json"

    def test_get_file_type_strips_fragment(self):
        loader = ConcreteRemoteLoader()
        assert loader._get_file_type(source="https://example.com/data.csv#row5") == "csv"

    def test_get_file_type_lowercases_extension(self):
        loader = ConcreteRemoteLoader()
        assert loader._get_file_type(source="https://example.com/data.JSONL") == "jsonl"

    def test_get_file_type_local_path(self):
        loader = ConcreteRemoteLoader()
        assert loader._get_file_type(source="/tmp/data.txt") == "txt"

    def test_get_file_type_returns_empty_for_no_extension(self):
        loader = ConcreteRemoteLoader()
        assert loader._get_file_type(source="https://example.com/data") == ""

    @patch.object(_RemoteDatasetLoader, "_fetch_from_public_url", return_value=[{"key": "value"}])
    def test_fetch_from_url_supports_query_string_file_type(self, mock_fetch_from_public_url):
        loader = ConcreteRemoteLoader()

        result = loader._fetch_from_url(
            source="https://example.com/data.json?download=1",
            source_type="public_url",
            cache=False,
        )

        assert result == [{"key": "value"}]
        mock_fetch_from_public_url.assert_called_once_with(
            source="https://example.com/data.json?download=1",
            file_type="json",
        )

    @patch.object(_RemoteDatasetLoader, "_fetch_from_public_url", return_value=[{"key": "value"}])
    def test_fetch_from_url_supports_uppercase_file_type(self, mock_fetch_from_public_url):
        loader = ConcreteRemoteLoader()

        result = loader._fetch_from_url(
            source="https://example.com/data.JSON",
            source_type="public_url",
            cache=False,
        )

        assert result == [{"key": "value"}]
        mock_fetch_from_public_url.assert_called_once_with(
            source="https://example.com/data.JSON",
            file_type="json",
        )
