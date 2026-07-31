# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset


class ConcreteRemoteLoader(_RemoteDatasetLoader):
    @property
    def dataset_name(self):
        return "test_remote"

    async def fetch_dataset_async(self):
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

    def test_standardize_harm_categories_supports_one_to_many_alias(self):
        loader = ConcreteRemoteLoader()

        standardized = loader._standardize_harm_categories("sexual violence")

        assert standardized == ["SEXUAL_CONTENT", "VIOLENT_CONTENT"]

    def test_standardize_harm_categories_supports_dataset_overrides(self):
        loader = ConcreteRemoteLoader()

        standardized = loader._standardize_harm_categories(
            "ableism",
            alias_overrides={"ableism": ["HATE_SPEECH", "REPRESENTATIONAL"]},
        )

        assert standardized == ["HATE_SPEECH", "REPRESENTATIONAL"]

    def test_fetch_from_url_invalid_file_type_raises(self):
        loader = ConcreteRemoteLoader()
        with pytest.raises(ValueError, match="Invalid file_type"):
            loader._fetch_from_url(
                source="https://example.com/data.xyz",
                source_type="public_url",
                cache=False,
            )

    def test_fetch_from_public_url_non_json_file_type(self):
        loader = ConcreteRemoteLoader()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "header1,header2\nvalue1,value2\n"
        with patch("requests.get", return_value=mock_response):
            result = loader._fetch_from_public_url(source="https://example.com/data.csv", file_type="csv")
        assert result == [{"header1": "value1", "header2": "value2"}]

    def test_fetch_from_public_url_invalid_file_type_raises(self):
        loader = ConcreteRemoteLoader()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        with patch("requests.get", return_value=mock_response), pytest.raises(ValueError, match="Invalid file_type"):
            loader._fetch_from_public_url(source="https://example.com/data.xyz", file_type="xyz")

    def test_fetch_from_file_json(self, tmp_path):
        loader = ConcreteRemoteLoader()
        source = tmp_path / "data.json"
        source.write_text('[{"key": "value"}]', encoding="utf-8")
        assert loader._fetch_from_file(source=str(source), file_type="json") == [{"key": "value"}]

    def test_fetch_from_file_invalid_file_type_raises(self, tmp_path):
        loader = ConcreteRemoteLoader()
        source = tmp_path / "data.xyz"
        source.write_text("anything", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid file_type"):
            loader._fetch_from_file(source=str(source), file_type="xyz")


class TestFetchZipFromUrl:
    SOURCE = "https://example.com/data.zip"

    def _make_zip_bytes(self, members: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def _mock_streaming_response(self, content: bytes) -> MagicMock:
        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.raise_for_status = MagicMock()
        response.iter_content = MagicMock(return_value=[content])
        return response

    async def test_parses_multiple_inner_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH",
            tmp_path,
        )
        rows_a = '{"a": 1}\n{"a": 2}\n'
        rows_b = '{"b": 3}\n'
        zip_bytes = self._make_zip_bytes({"folder/a.jsonl": rows_a, "folder/b.jsonl": rows_b})

        with patch(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get",
            return_value=self._mock_streaming_response(zip_bytes),
        ):
            loader = ConcreteRemoteLoader()
            result = await loader._fetch_zip_from_url_async(
                source=self.SOURCE,
                inner_files=["folder/a.jsonl", "folder/b.jsonl"],
                cache=True,
            )

        assert result["folder/a.jsonl"] == [{"a": 1}, {"a": 2}]
        assert result["folder/b.jsonl"] == [{"b": 3}]

    async def test_caches_zip_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH",
            tmp_path,
        )
        zip_bytes = self._make_zip_bytes({"x.json": '[{"k": "v"}]'})

        mock_get = MagicMock(return_value=self._mock_streaming_response(zip_bytes))
        with patch(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get",
            mock_get,
        ):
            loader = ConcreteRemoteLoader()
            await loader._fetch_zip_from_url_async(source=self.SOURCE, inner_files=["x.json"], cache=True)
            await loader._fetch_zip_from_url_async(source=self.SOURCE, inner_files=["x.json"], cache=True)

        assert mock_get.call_count == 1
        # Cache file is keyed by md5(source) under seed-prompt-entries/
        cached = list((tmp_path / "seed-prompt-entries").glob("*.zip"))
        assert len(cached) == 1

    async def test_cache_false_does_not_persist_zip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH",
            tmp_path,
        )
        zip_bytes = self._make_zip_bytes({"x.json": '[{"k": "v"}]'})

        with patch(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get",
            return_value=self._mock_streaming_response(zip_bytes),
        ):
            loader = ConcreteRemoteLoader()
            await loader._fetch_zip_from_url_async(source=self.SOURCE, inner_files=["x.json"], cache=False)

        assert not (tmp_path / "seed-prompt-entries").exists()

    async def test_missing_inner_file_raises_valueerror(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH",
            tmp_path,
        )
        zip_bytes = self._make_zip_bytes({"exists.jsonl": "{}\n"})

        with patch(
            "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get",
            return_value=self._mock_streaming_response(zip_bytes),
        ):
            loader = ConcreteRemoteLoader()
            with pytest.raises(ValueError, match="missing.jsonl"):
                await loader._fetch_zip_from_url_async(source=self.SOURCE, inner_files=["missing.jsonl"], cache=False)

    async def test_unsupported_inner_extension_raises_valueerror(self):
        loader = ConcreteRemoteLoader()
        with pytest.raises(ValueError, match="Invalid file_type"):
            await loader._fetch_zip_from_url_async(source=self.SOURCE, inner_files=["bad.parquet"], cache=False)


class TestFetchFromHuggingFaceDownloadMode:
    """The cache flag must drive the HuggingFace download_mode so cache=False re-downloads."""

    async def test_cache_true_reuses_dataset(self):
        from datasets import DownloadMode

        loader = ConcreteRemoteLoader()
        with patch("pyrit.datasets.seed_datasets.remote.remote_dataset_loader.load_dataset") as mock_load:
            await loader._fetch_from_huggingface_async(dataset_name="owner/ds", split="train", cache=True)

        assert mock_load.call_args.kwargs["download_mode"] == DownloadMode.REUSE_DATASET_IF_EXISTS
        assert mock_load.call_args.kwargs["cache_dir"] is not None

    async def test_cache_false_forces_redownload(self):
        from datasets import DownloadMode

        loader = ConcreteRemoteLoader()
        with patch("pyrit.datasets.seed_datasets.remote.remote_dataset_loader.load_dataset") as mock_load:
            await loader._fetch_from_huggingface_async(dataset_name="owner/ds", split="train", cache=False)

        assert mock_load.call_args.kwargs["download_mode"] == DownloadMode.FORCE_REDOWNLOAD
        assert mock_load.call_args.kwargs["cache_dir"] is None
