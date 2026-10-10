# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset import (
    _NONTOXIC_URL,
    _PERSPECTIVE_SCORE_KEYS,
    _TOXIC_URL,
    DecodingTrustToxicitySubset,
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

        assert loader.subset is DecodingTrustToxicitySubset.TOXIC

        with patch.object(loader, "_fetch_from_url", return_value=toxic_records) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        assert mock_fetch.call_count == 1
        mock_fetch.assert_called_once_with(source=_TOXIC_URL, source_type="public_url", cache=True)
        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 2

    async def test_subset_nontoxic_only(self, nontoxic_records):
        """subset=NONTOXIC fetches only the nontoxic URL."""
        loader = _DecodingTrustToxicityDataset(subset=DecodingTrustToxicitySubset.NONTOXIC)

        with patch.object(loader, "_fetch_from_url", return_value=nontoxic_records) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=_NONTOXIC_URL, source_type="public_url", cache=True)
        assert len(dataset.seeds) == 1

    async def test_subset_all_fetches_both_urls(self, toxic_records, nontoxic_records):
        """subset=ALL fetches both URLs and concatenates."""
        loader = _DecodingTrustToxicityDataset(subset=DecodingTrustToxicitySubset.ALL)

        with patch.object(
            loader,
            "_fetch_from_url",
            side_effect=[toxic_records, nontoxic_records],
        ) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        assert mock_fetch.call_count == 2
        assert {call.kwargs["source"] for call in mock_fetch.call_args_list} == {_TOXIC_URL, _NONTOXIC_URL}
        assert len(dataset.seeds) == len(toxic_records) + len(nontoxic_records)

    def test_invalid_subset_raises_value_error(self):
        """A raw string for subset is rejected by _validate_enum."""
        with pytest.raises(ValueError, match="DecodingTrustToxicitySubset"):
            _DecodingTrustToxicityDataset(subset="toxic")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_threshold", [-1.0, -0.01, 1.01, 1.5, float("inf"), float("-inf")])
    def test_invalid_toxicity_threshold_raises_value_error(self, bad_threshold):
        """toxicity_threshold outside [0.0, 1.0] is rejected at construction time."""
        with pytest.raises(ValueError, match="toxicity_threshold must be in"):
            _DecodingTrustToxicityDataset(toxicity_threshold=bad_threshold)

    @pytest.mark.parametrize("ok_threshold", [0.0, 0.5, 1.0])
    def test_valid_toxicity_threshold_accepted(self, ok_threshold):
        """Boundary values 0.0 and 1.0 plus mid-range are accepted."""
        loader = _DecodingTrustToxicityDataset(toxicity_threshold=ok_threshold)
        assert loader.toxicity_threshold == ok_threshold

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
        loader = _DecodingTrustToxicityDataset(
            subset=DecodingTrustToxicitySubset.NONTOXIC,
            toxicity_threshold=0.99,
        )

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

    async def test_raises_when_filters_leave_zero_seeds(self):
        """challenging_only on a subset with no challenging records → empty result raises."""
        records = [
            _record(text="a", challenging=False),
            _record(text="b", challenging=False),
        ]
        loader = _DecodingTrustToxicityDataset(challenging_only=True)

        with patch.object(loader, "_fetch_from_url", return_value=records):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset_async()

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
        assert seed.groups is not None and "University of Illinois at Urbana-Champaign" in seed.groups

    async def test_metadata_round_trip(self):
        """Per-row challenging flag and 8 Perspective scores land in SeedPrompt.metadata."""
        records = [
            _record(
                text="x",
                challenging=True,
                toxicity=0.9066653,
                severe_toxicity=0.71939063,
                profanity=0.913447,
                sexually_explicit=0.83578736,
                identity_attack=0.27345368,
                threat=0.27313367,
                insult=0.924118,
                flirtation=0.400925,
            ),
        ]
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        meta = dataset.seeds[0].metadata
        assert meta is not None
        # challenging stored as bool (bool ≤ int so dict[str, str | int] accepts it)
        assert meta["challenging"] is True
        # All eight Perspective scores stringified at full precision
        for key in _PERSPECTIVE_SCORE_KEYS:
            assert key in meta
            assert meta[key] == str(records[0]["prompt"][key])

    async def test_metadata_challenging_defaults_false_when_missing(self):
        """Records without a 'challenging' key produce metadata['challenging'] == False."""
        records = [
            {
                "prompt": {
                    "text": "x",
                    "toxicity": 0.9,
                },
            },
        ]
        loader = _DecodingTrustToxicityDataset()

        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        meta = dataset.seeds[0].metadata
        assert meta is not None
        assert meta["challenging"] is False

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
        """harm_categories/modalities/size/tags are set for the dataset-discovery parser.

        Class-level ``harm_categories`` deliberately excludes ``flirtation`` because
        Perspective API exposes it as a tone signal, not a harm — including it in
        dataset-discovery indexing would misrepresent this dataset's purpose
        (see PR #1821 review thread). Per-row derivation still emits flirtation when
        the per-prompt score crosses the threshold; see
        ``test_per_row_derivation_still_emits_flirtation``.
        """
        expected = [k for k in _PERSPECTIVE_SCORE_KEYS if k != "flirtation"]
        assert _DecodingTrustToxicityDataset.harm_categories == expected
        assert "flirtation" not in _DecodingTrustToxicityDataset.harm_categories
        assert _DecodingTrustToxicityDataset.modalities == ["text"]
        assert _DecodingTrustToxicityDataset.size == "large"
        assert _DecodingTrustToxicityDataset.tags == {"default", "safety", "toxicity"}

    async def test_per_row_derivation_still_emits_flirtation(self):
        """Per-row harm_categories still include flirtation when the per-prompt score crosses the threshold.

        This guards the class-level / per-row asymmetry: flirtation is dropped from
        class-level discovery indexing, but a specific prompt that scores high on
        flirtation still gets it tagged in its per-row harm_categories so callers can
        filter on it via metadata.
        """
        records = [
            _record(
                text="flirty prompt",
                challenging=True,
                toxicity=0.2,
                severe_toxicity=0.1,
                profanity=0.1,
                sexually_explicit=0.1,
                identity_attack=0.1,
                threat=0.1,
                insult=0.1,
                flirtation=0.9,  # only flirtation crosses threshold
            ),
        ]
        loader = _DecodingTrustToxicityDataset(toxicity_threshold=0.5)

        with patch.object(loader, "_fetch_from_url", return_value=records):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].harm_categories == ["flirtation"]
        # ...and metadata still carries the full flirtation score string
        assert dataset.seeds[0].metadata is not None
        assert dataset.seeds[0].metadata["flirtation"] == "0.9"
