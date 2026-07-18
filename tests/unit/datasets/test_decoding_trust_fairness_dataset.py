# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.decoding_trust_fairness_dataset import (
    _BASE_URL,
    DecodingTrustFairnessSubset,
    _DecodingTrustFairnessDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


def _record(*, text: str = "some input text", label: str = "yes", option: list | None = None) -> dict:
    return {
        "option": option if option is not None else ["yes", "no"],
        "input": text,
        "label": label,
        "examples": [],
    }


@pytest.fixture
def sample_records():
    return [
        _record(text="Person A description...", label="yes"),
        _record(text="Person B description...", label="no"),
    ]


class TestDecodingTrustFairnessDataset:
    """Test the DecodingTrust Fairness dataset loader."""

    async def test_default_subset_is_adult_0_8(self, sample_records):
        loader = _DecodingTrustFairnessDataset()
        assert loader.subset is DecodingTrustFairnessSubset.ADULT_0_8

        expected_url = f"{_BASE_URL}adult_0_8.jsonl"
        with patch.object(loader, "_fetch_from_url", return_value=sample_records) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=expected_url, source_type="public_url", cache=True)
        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 2

    async def test_custom_subset_builds_correct_url(self, sample_records):
        loader = _DecodingTrustFairnessDataset(subset=DecodingTrustFairnessSubset.ADULT_32_200_TRAIN_BASE_RATE_0_5)
        expected_url = f"{_BASE_URL}adult_32_200_train_base_rate_0.5.jsonl"

        with patch.object(loader, "_fetch_from_url", return_value=sample_records) as mock_fetch:
            await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=expected_url, source_type="public_url", cache=True)

    def test_invalid_subset_raises_value_error(self):
        with pytest.raises(ValueError, match="DecodingTrustFairnessSubset"):
            _DecodingTrustFairnessDataset(subset="adult_0_8")  # type: ignore[arg-type]

    async def test_raises_on_non_dict_record(self):
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=["not a dict"]):
            with pytest.raises(ValueError, match="JSON objects"):
                await loader.fetch_dataset_async()

    async def test_skips_records_missing_input(self):
        records = [
            _record(text="ok"),
            {"option": ["yes", "no"], "label": "no", "examples": []},
            {"option": ["yes", "no"], "input": "", "label": "no", "examples": []},
        ]
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()
        assert [seed.value for seed in dataset.seeds] == ["ok"]

    async def test_raises_when_result_empty(self):
        records = [{"option": ["yes", "no"], "input": "", "label": "no", "examples": []}]
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=records):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset_async()

    async def test_metadata_round_trip(self):
        records = [_record(text="x", label="yes", option=["yes", "no"])]
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        meta = dataset.seeds[0].metadata
        assert meta is not None
        assert meta["label"] == "yes"
        assert meta["option"] == "yes,no"

    async def test_metadata_option_missing_or_malformed(self):
        records = [{"input": "x", "label": "yes", "examples": []}]
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()
        assert dataset.seeds[0].metadata["option"] == ""

    async def test_per_seed_metadata(self, sample_records):
        loader = _DecodingTrustFairnessDataset()
        with patch.object(loader, "_fetch_from_url", return_value=sample_records):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert seed.dataset_name == "decoding_trust_fairness"
        assert seed.data_type == "text"
        assert seed.description is not None and "DecodingTrust" in seed.description
        assert seed.authors is not None and "Boxin Wang" in seed.authors
        assert seed.groups is not None and "University of Illinois Urbana-Champaign" in seed.groups
        assert seed.harm_categories == ["fairness"]

    def test_dataset_name(self):
        assert _DecodingTrustFairnessDataset().dataset_name == "decoding_trust_fairness"

    def test_class_level_metadata(self):
        assert _DecodingTrustFairnessDataset.harm_categories == ["fairness"]
        assert _DecodingTrustFairnessDataset.modalities == ["text"]
        assert _DecodingTrustFairnessDataset.size == "small"
        assert _DecodingTrustFairnessDataset.tags == {"default", "safety", "bias"}

    def test_pinned_commit_in_url(self):
        from pyrit.datasets.seed_datasets.remote import decoding_trust_fairness_dataset as mod

        assert mod._DECODING_TRUST_COMMIT == "bfb1251"
        assert mod._DECODING_TRUST_COMMIT in _BASE_URL
        assert _BASE_URL.endswith("/data/fairness/fairness_data/")
