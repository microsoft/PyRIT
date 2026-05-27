# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset import (
    _NONTOXIC_URL,
    _TOXIC_URL,
    _DecodingTrustToxicityDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


def _record(
    *,
    text: str,
    challenging: bool = True,
    toxicity: float = 0.9,
    severe_toxicity: float = 0.7,
    profanity: float = 0.8,
    sexually_explicit: float = 0.2,
    identity_attack: float = 0.1,
    threat: float = 0.1,
    insult: float = 0.6,
    flirtation: float = 0.1,
) -> dict:
    """Build a DecodingTrust-shaped record for use in test fixtures."""
    return {
        "filename": "fake.txt",
        "begin": 0,
        "end": len(text),
        "challenging": challenging,
        "prompt": {
            "text": text,
            "toxicity": toxicity,
            "severe_toxicity": severe_toxicity,
            "profanity": profanity,
            "sexually_explicit": sexually_explicit,
            "identity_attack": identity_attack,
            "threat": threat,
            "insult": insult,
            "flirtation": flirtation,
        },
        "continuation": {"text": " continuation"},
    }


@pytest.fixture
def toxic_records():
    return [
        _record(text="toxic one", toxicity=0.91, profanity=0.85, insult=0.7),
        _record(text="toxic two", toxicity=0.95, profanity=0.4, insult=0.3),
    ]


@pytest.fixture
def nontoxic_records():
    return [
        _record(
            text="nontoxic one",
            challenging=False,
            toxicity=0.1,
            severe_toxicity=0.05,
            profanity=0.05,
            sexually_explicit=0.05,
            identity_attack=0.05,
            threat=0.05,
            insult=0.05,
            flirtation=0.05,
        ),
    ]


class TestDecodingTrustToxicityDataset:
    """Test the DecodingTrust Toxicity dataset loader."""

    async def test_default_subset_is_toxic_only(self, toxic_records):
        """Default constructor must fetch only toxic.jsonl (maintainer-requested default)."""
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=toxic_records) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        assert mock_fetch.call_count == 1
        mock_fetch.assert_called_once_with(source=_TOXIC_URL, source_type="public_url", cache=True)
        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 2

    async def test_subset_nontoxic_only(self, nontoxic_records):
        """subset='nontoxic' fetches only the nontoxic URL."""
        loader = _DecodingTrustToxicityDataset(subset="nontoxic")

        with patch.object(loader, "_fetch_from_url", return_value=nontoxic_records) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=_NONTOXIC_URL, source_type="public_url", cache=True)
        assert len(dataset.seeds) == 1

    async def test_subset_all_fetches_both_urls(self, toxic_records, nontoxic_records):
        """subset='all' fetches both URLs and concatenates."""
        loader = _DecodingTrustToxicityDataset(subset="all")

        with patch.object(
            loader,
            "_fetch_from_url",
            side_effect=[toxic_records, nontoxic_records],
        ) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        assert mock_fetch.call_count == 2
        assert {call.kwargs["source"] for call in mock_fetch.call_args_list} == {_TOXIC_URL, _NONTOXIC_URL}
        assert len(dataset.seeds) == len(toxic_records) + len(nontoxic_records)

    async def test_harm_category_mapping_threshold(self, toxic_records):
        """Each Perspective score >= threshold contributes its key to harm_categories."""
        loader = _DecodingTrustToxicityDataset(toxicity_threshold=0.5)

        with patch.object(loader, "_fetch_from_url", return_value=toxic_records):
            dataset = await loader.fetch_dataset_async()

        first = dataset.seeds[0]
        assert isinstance(first, SeedPrompt)
        # toxicity=0.91, severe_toxicity=0.7, profanity=0.85, insult=0.7 → all in
        # sexually_explicit=0.2, identity_attack=0.1, threat=0.1, flirtation=0.1 → out
        assert first.harm_categories is not None
        assert sorted(first.harm_categories) == ["insult", "profanity", "severe_toxicity", "toxicity"]
        # Second record has lower profanity/insult — only toxicity & severe_toxicity stay
        second = dataset.seeds[1]
        assert second.harm_categories is not None
        assert sorted(second.harm_categories) == ["severe_toxicity", "toxicity"]

    async def test_high_threshold_yields_empty_categories(self, nontoxic_records):
        """At a very high threshold, nontoxic prompts get empty harm_categories."""
        loader = _DecodingTrustToxicityDataset(subset="nontoxic", toxicity_threshold=0.99)

        with patch.object(loader, "_fetch_from_url", return_value=nontoxic_records):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].harm_categories == []

    async def test_challenging_only_filter(self):
        """challenging_only=True drops records with challenging=False."""
        records = [
            _record(text="keep me", challenging=True),
            _record(text="drop me", challenging=False),
        ]
        loader = _DecodingTrustToxicityDataset(challenging_only=True)

        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["keep me"]

    async def test_skips_records_missing_prompt_text(self):
        """Records with missing or empty prompt.text are skipped, not failed."""
        records = [
            _record(text="ok"),
            {"challenging": True, "prompt": {}},  # missing text entirely
            {"challenging": True, "prompt": {"text": ""}},  # empty string
            {"challenging": True, "prompt": "not a dict"},  # malformed prompt field
        ]
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["ok"]

    async def test_raises_on_non_dict_record(self):
        """Top-level non-dict records (string, number, list) are a hard error."""
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=["this should be a dict"]):
            with pytest.raises(ValueError, match="JSON objects"):
                await loader.fetch_dataset_async()

    async def test_per_seed_metadata(self, toxic_records):
        """Every SeedPrompt carries dataset_name, source, description, authors, groups."""
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=toxic_records):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert seed.dataset_name == "decoding_trust_toxicity"
        assert seed.source == _TOXIC_URL
        assert seed.data_type == "text"
        assert seed.description is not None and "DecodingTrust" in seed.description
        assert seed.authors is not None and "Boxin Wang" in seed.authors  # DecodingTrust lead
        assert "Samuel Gehman" in seed.authors  # RealToxicityPrompts lead
        assert seed.groups is not None and "University of Illinois Urbana-Champaign" in seed.groups

    def test_dataset_name(self):
        """dataset_name property returns the canonical id."""
        assert _DecodingTrustToxicityDataset().dataset_name == "decoding_trust_toxicity"

    def test_default_source_is_pinned_commit(self):
        """The fetcher URLs must reference a pinned commit SHA, not main."""
        from pyrit.datasets.seed_datasets.remote import decoding_trust_toxicity_dataset as mod

        assert mod._DECODING_TRUST_COMMIT == "161ae8321ced62f45fcd9ceb412e05b47c603cd4"
        assert mod._DECODING_TRUST_COMMIT in _TOXIC_URL
        assert mod._DECODING_TRUST_COMMIT in _NONTOXIC_URL
        assert _TOXIC_URL.endswith("/data/toxicity/user_prompts/toxic.jsonl")
        assert _NONTOXIC_URL.endswith("/data/toxicity/user_prompts/nontoxic.jsonl")

    def test_class_level_metadata(self):
        """modalities/size/tags are set for the metadata parser."""
        assert _DecodingTrustToxicityDataset.modalities == ["text"]
        assert _DecodingTrustToxicityDataset.size == "large"
        assert _DecodingTrustToxicityDataset.tags == {"default", "safety", "toxicity"}
