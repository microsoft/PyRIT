# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock, patch

import pytest

from pyrit.datasets import _remote_fetch


class TestValidateFileType:
    def test_accepts_supported_types(self):
        for file_type in ("json", "jsonl", "csv", "txt"):
            _remote_fetch.validate_file_type(file_type)

    def test_rejects_unsupported_type(self):
        with pytest.raises(ValueError, match="Invalid file_type"):
            _remote_fetch.validate_file_type("xml")


class TestCacheFileName:
    def test_deterministic(self):
        a = _remote_fetch.get_cache_file_name(source="https://example.com/data.jsonl", file_type="jsonl")
        b = _remote_fetch.get_cache_file_name(source="https://example.com/data.jsonl", file_type="jsonl")
        assert a == b
        assert a.endswith(".jsonl")

    def test_distinct_sources_produce_distinct_names(self):
        a = _remote_fetch.get_cache_file_name(source="https://a.example/data.csv", file_type="csv")
        b = _remote_fetch.get_cache_file_name(source="https://b.example/data.csv", file_type="csv")
        assert a != b

    def test_get_cache_file_applies_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        path = _remote_fetch.get_cache_file(
            source="https://example.com/x.json", file_type="json", cache_subdir="my-subdir"
        )
        assert path.parent == tmp_path / "my-subdir"
        assert path.suffix == ".json"


class TestReadWriteCache:
    def test_roundtrip_json(self, tmp_path):
        cache_file = tmp_path / "data.json"
        payload = [{"k": "v"}, {"k2": "v2"}]
        _remote_fetch.write_cache(cache_file=cache_file, examples=payload, file_type="json")
        assert cache_file.exists()
        assert _remote_fetch.read_cache(cache_file=cache_file, file_type="json") == payload

    def test_roundtrip_jsonl(self, tmp_path):
        cache_file = tmp_path / "data.jsonl"
        payload = [{"a": 1}, {"a": 2}]
        _remote_fetch.write_cache(cache_file=cache_file, examples=payload, file_type="jsonl")
        assert _remote_fetch.read_cache(cache_file=cache_file, file_type="jsonl") == payload

    def test_write_creates_parent_dirs(self, tmp_path):
        cache_file = tmp_path / "nested" / "deeper" / "data.json"
        _remote_fetch.write_cache(cache_file=cache_file, examples=[{"k": "v"}], file_type="json")
        assert cache_file.exists()

    def test_invalid_file_type_raises(self, tmp_path):
        cache_file = tmp_path / "data.xyz"
        cache_file.write_text("{}")
        with pytest.raises(ValueError, match="Invalid file_type"):
            _remote_fetch.read_cache(cache_file=cache_file, file_type="xyz")


class TestFetchFromPublicUrl:
    @patch.object(_remote_fetch, "requests")
    def test_parses_json_response(self, mock_requests):
        payload = [{"key": "value"}]
        resp = MagicMock()
        resp.status_code = 200
        resp.text = json.dumps(payload)
        mock_requests.get.return_value = resp

        result = _remote_fetch.fetch_from_public_url(source="https://example.com/x.json", file_type="json")
        assert result == payload

    @patch.object(_remote_fetch, "requests")
    def test_non_200_raises(self, mock_requests):
        resp = MagicMock()
        resp.status_code = 500
        mock_requests.get.return_value = resp
        with pytest.raises(Exception, match="Status code: 500"):
            _remote_fetch.fetch_from_public_url(source="https://example.com/x.json", file_type="json")


class TestFetchWithCache:
    @patch.object(_remote_fetch, "requests")
    def test_cache_hit_short_circuits_network(self, mock_requests, monkeypatch, tmp_path):
        """If the cache file exists, we must not hit the network."""
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        source = "https://example.com/data.jsonl"

        cache_file = _remote_fetch.get_cache_file(
            source=source, file_type="jsonl", cache_subdir="unit-test-cache"
        )
        payload = [{"cached": True}]
        _remote_fetch.write_cache(cache_file=cache_file, examples=payload, file_type="jsonl")

        result = _remote_fetch.fetch_with_cache(source=source, cache_subdir="unit-test-cache")
        assert result == payload
        mock_requests.get.assert_not_called()

    @patch.object(_remote_fetch, "requests")
    def test_cache_miss_writes_to_cache(self, mock_requests, monkeypatch, tmp_path):
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        source = "https://example.com/data.jsonl"
        payload = [{"fresh": True}]

        resp = MagicMock()
        resp.status_code = 200
        resp.text = "\n".join(json.dumps(row) for row in payload)
        mock_requests.get.return_value = resp

        result = _remote_fetch.fetch_with_cache(source=source, cache_subdir="unit-test-cache")
        assert result == payload

        cache_file = _remote_fetch.get_cache_file(
            source=source, file_type="jsonl", cache_subdir="unit-test-cache"
        )
        assert cache_file.exists()

    @patch.object(_remote_fetch, "requests")
    def test_cache_false_does_not_persist(self, mock_requests, monkeypatch, tmp_path):
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        source = "https://example.com/data.jsonl"
        payload = [{"x": 1}]

        resp = MagicMock()
        resp.status_code = 200
        resp.text = json.dumps(payload[0])
        mock_requests.get.return_value = resp

        _remote_fetch.fetch_with_cache(source=source, cache_subdir="unit-test-cache", cache=False)

        cache_file = _remote_fetch.get_cache_file(
            source=source, file_type="jsonl", cache_subdir="unit-test-cache"
        )
        assert not cache_file.exists()

    def test_invalid_file_type_inferred_raises(self):
        with pytest.raises(ValueError, match="Invalid file_type"):
            _remote_fetch.fetch_with_cache(source="https://example.com/data.xml", cache_subdir="x")

    def test_invalid_source_type_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        with pytest.raises(ValueError, match="Invalid source_type"):
            _remote_fetch.fetch_with_cache(
                source="https://example.com/data.json",
                cache_subdir="x",
                source_type="ftp",  # type: ignore[arg-type]
            )

    @patch.object(_remote_fetch, "requests")
    def test_cache_subdir_routes_to_separate_namespaces(self, mock_requests, monkeypatch, tmp_path):
        """Same source, different cache_subdir → different cache files."""
        monkeypatch.setattr(_remote_fetch, "DB_DATA_PATH", tmp_path)
        source = "https://example.com/data.jsonl"
        payload = [{"k": "v"}]

        resp = MagicMock()
        resp.status_code = 200
        resp.text = json.dumps(payload[0])
        mock_requests.get.return_value = resp

        _remote_fetch.fetch_with_cache(source=source, cache_subdir="seed-prompt-entries")
        _remote_fetch.fetch_with_cache(source=source, cache_subdir="anthropic-evals-cache")

        assert (tmp_path / "seed-prompt-entries").exists()
        assert (tmp_path / "anthropic-evals-cache").exists()
