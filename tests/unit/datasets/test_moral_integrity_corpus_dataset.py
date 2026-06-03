# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.moral_integrity_corpus_dataset import _MICDataset


class TestMICDataset:
    SPLIT_KEYS = [f"MIC/{split}.jsonl" for split in ["train", "dev", "test"]]

    def _split_payload(self, rows: list[dict]) -> dict[str, list[dict]]:
        """Return the same rows under each split key so all three splits are exercised."""
        return {key: list(rows) for key in self.SPLIT_KEYS}

    def test_dataset_name(self):
        """dataset_name property returns the snake_case name."""
        assert _MICDataset().dataset_name == "moral_integrity_corpus"

    def test_init_default(self):
        """Default initialization sets the canonical MIC.zip source URL."""
        dataset = _MICDataset()
        assert dataset.source == "https://huggingface.co/datasets/SALT-NLP/MIC/resolve/main/MIC.zip"

    async def test_fetch_dataset_async(self):
        """Happy path: rows across splits are loaded and metadata is set on each seed."""
        rows = [
            {"Q": "Is lying okay?", "moral": "fairness"},
            {"Q": "Am I a bad boyfriend?", "moral": "loyalty"},
            {"Q": "Can murder be justified?", "moral": "care|liberty"},
        ]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            result = await _MICDataset().fetch_dataset_async()

        # 3 unique Q strings; dedup across the three identical splits.
        assert len(result.seeds) == 3
        assert result.dataset_name == "moral_integrity_corpus"
        assert result.seeds[0].value == "Is lying okay?"
        assert result.seeds[0].data_type == "text"
        assert result.seeds[0].harm_categories == ["fairness"]
        assert result.seeds[2].harm_categories == ["care", "liberty"]
        assert "Caleb Ziems" in result.seeds[0].authors

    async def test_fetch_dataset_deduplicates(self):
        """Repeated questions across splits collapse to one seed."""
        rows = [
            {"Q": "Is lying okay?", "moral": "fairness"},
            {"Q": "Is lying okay?", "moral": "loyalty"},
            {"Q": "Different question?", "moral": "care"},
        ]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            result = await _MICDataset().fetch_dataset_async()
        assert len(result.seeds) == 2

    async def test_fetch_dataset_skips_empty_questions(self):
        """Empty and whitespace-only Q values are skipped."""
        rows = [
            {"Q": "Valid question?", "moral": "care"},
            {"Q": "", "moral": "fairness"},
            {"Q": "   ", "moral": "loyalty"},
        ]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            result = await _MICDataset().fetch_dataset_async()
        assert len(result.seeds) == 1

    async def test_fetch_dataset_empty_raises(self):
        """An archive that yields no usable rows raises ValueError."""
        rows = [{"Q": "", "moral": "care"}]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            with pytest.raises(ValueError, match="empty"):
                await _MICDataset().fetch_dataset_async()

    async def test_fetch_dataset_nan_moral(self):
        """Non-string `moral` values (e.g. NaN floats from JSON) yield empty categories."""
        rows = [{"Q": "Valid question?", "moral": float("nan")}]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            result = await _MICDataset().fetch_dataset_async()
        assert len(result.seeds) == 1
        assert result.seeds[0].harm_categories == []

    async def test_fetch_dataset_non_string_q(self):
        """Non-string Q values (e.g. null) are skipped without crashing."""
        rows = [
            {"Q": None, "moral": "care"},
            {"Q": 42, "moral": "fairness"},
            {"Q": "Real question?", "moral": "loyalty"},
        ]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            result = await _MICDataset().fetch_dataset_async()
        assert len(result.seeds) == 1

    async def test_fetch_dataset_passes_cache_flag(self):
        """`cache` is forwarded to the helper."""
        rows = [{"Q": "anything?", "moral": "care"}]
        mock_fetch = AsyncMock(return_value=self._split_payload(rows))
        with patch.object(_MICDataset, "_fetch_zip_from_url_async", mock_fetch):
            await _MICDataset().fetch_dataset_async(cache=False)
        kwargs = mock_fetch.call_args.kwargs
        assert kwargs["cache"] is False
        assert kwargs["source"] == "https://huggingface.co/datasets/SALT-NLP/MIC/resolve/main/MIC.zip"
        assert kwargs["inner_files"] == [
            "MIC/train.jsonl",
            "MIC/dev.jsonl",
            "MIC/test.jsonl",
        ]
