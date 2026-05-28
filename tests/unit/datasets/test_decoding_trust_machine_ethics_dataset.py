# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.decoding_trust_machine_ethics_dataset import (
    _CSV_URL,
    _DecodingTrustMachineEthicsDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


def _row(
    *,
    description: str,
    morality: str,
    file: str = "actions.zil",
    line: str = "42",
    neighboring_text: str = "<TELL .STROPN>",
) -> dict[str, str]:
    """Build a CSV-shaped row mirroring jiminy_subset.csv."""
    return {
        "File": file,
        "Line": line,
        "Neighboring text": neighboring_text,
        "Description": description,
        "Review": "",
        "Conditioning": "",
        "Morality": morality,
        "Python Filters": "",
    }


@pytest.fixture
def mixed_rows():
    """Mix of bad / good / neutral rows with assorted intensities."""
    return [
        _row(description="brush teeth with glue", morality="bad, self, 3"),
        _row(description="break a window", morality="bad, others, 1"),
        _row(description="help an old lady", morality="good, others, 1"),
        _row(description="walk to the door", morality=""),  # neutral
        _row(description="steal AND hurt", morality="bad, others, 2\nbad, self, 2"),
        _row(description="rescue and self-care", morality="good, others, 1\ngood, self, 1"),
    ]


class TestDecodingTrustMachineEthicsDataset:
    """Test the DecodingTrust Machine Ethics dataset loader."""

    async def test_default_morality_is_bad_only(self, mixed_rows):
        """Default constructor (morality='bad') drops good-only and neutral rows."""
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=_CSV_URL, source_type="public_url", cache=True)
        assert isinstance(dataset, SeedDataset)
        values = [seed.value for seed in dataset.seeds]
        assert values == ["brush teeth with glue", "break a window", "steal AND hurt"]

    async def test_morality_good_returns_only_good_labels(self, mixed_rows):
        loader = _DecodingTrustMachineEthicsDataset(morality="good")

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows):
            dataset = await loader.fetch_dataset_async()

        values = [seed.value for seed in dataset.seeds]
        assert values == ["help an old lady", "rescue and self-care"]

    async def test_morality_neutral_returns_only_unlabeled_rows(self, mixed_rows):
        # min_intensity=0 to allow the neutral row through (its max intensity is 0).
        loader = _DecodingTrustMachineEthicsDataset(morality="neutral", min_intensity=0)

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["walk to the door"]

    async def test_morality_all_returns_everything_including_neutrals(self, mixed_rows):
        loader = _DecodingTrustMachineEthicsDataset(morality="all", min_intensity=0)

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows):
            dataset = await loader.fetch_dataset_async()

        assert len(dataset.seeds) == len(mixed_rows)

    async def test_morality_all_with_default_min_intensity_drops_neutrals(self, mixed_rows):
        """morality='all' with default min_intensity=1 still drops the neutral row (intensity 0)."""
        loader = _DecodingTrustMachineEthicsDataset(morality="all")

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows):
            dataset = await loader.fetch_dataset_async()

        assert "walk to the door" not in [seed.value for seed in dataset.seeds]

    async def test_multi_label_row_emits_multiple_categories(self):
        rows = [_row(description="steal AND hurt", morality="bad, others, 2\nbad, self, 2")]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert seed.harm_categories is not None
        assert sorted(seed.harm_categories) == ["bad_to_others", "bad_to_self"]
        assert seed.metadata is not None
        assert seed.metadata["max_intensity"] == 2

    async def test_min_intensity_filter(self, mixed_rows):
        """min_intensity=3 keeps only rows whose max intensity reaches 3."""
        loader = _DecodingTrustMachineEthicsDataset(min_intensity=3)

        with patch.object(loader, "_fetch_from_url", return_value=mixed_rows):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["brush teeth with glue"]

    async def test_harm_category_vocabulary(self):
        """Intensity is not folded into the category name."""
        rows = [_row(description="x", morality="bad, self, 3")]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].harm_categories == ["bad_to_self"]

    async def test_metadata_preserves_source_traceability(self):
        rows = [
            _row(
                description="x",
                morality="bad, self, 2",
                file="actions.zil",
                line="34",
                neighboring_text="<TELL .STROPN>",
            )
        ]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        meta = dataset.seeds[0].metadata
        assert meta is not None
        assert meta["source_file"] == "actions.zil"
        assert meta["source_line"] == 34
        assert isinstance(meta["source_line"], int)
        assert meta["morality_raw"] == "bad, self, 2"
        assert meta["max_intensity"] == 2
        assert meta["neighboring_text"] == "<TELL .STROPN>"

    async def test_skips_rows_missing_description(self):
        """Rows with empty or whitespace-only Description are skipped, not failed."""
        rows = [
            _row(description="keep me", morality="bad, self, 1"),
            _row(description="", morality="bad, self, 1"),
            _row(description="   ", morality="bad, self, 1"),
        ]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["keep me"]

    async def test_raises_on_missing_required_columns(self):
        """Missing Morality column on the first row raises ValueError."""
        rows = [{"File": "x", "Line": "1", "Description": "x"}]  # no Morality
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            with pytest.raises(ValueError, match="Morality"):
                await loader.fetch_dataset_async()

    async def test_malformed_morality_label_does_not_fail_row(self):
        """A row with one valid label and one garbage label still yields a SeedPrompt."""
        rows = [_row(description="x", morality="bad, self, 1\nweird-thing")]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].harm_categories == ["bad_to_self"]

    async def test_per_seed_metadata(self):
        rows = [_row(description="x", morality="bad, self, 1")]
        loader = _DecodingTrustMachineEthicsDataset()

        with patch.object(loader, "_fetch_from_url", return_value=rows):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert isinstance(seed, SeedPrompt)
        assert seed.dataset_name == "decoding_trust_machine_ethics"
        assert seed.source == _CSV_URL
        assert seed.data_type == "text"
        assert seed.description is not None
        assert "DecodingTrust" in seed.description
        assert "Jiminy" in seed.description
        assert seed.authors is not None
        assert "Boxin Wang" in seed.authors  # DecodingTrust lead
        assert "Andy Zou" in seed.authors  # Jiminy Cricket author
        assert seed.groups is not None
        assert "University of Illinois Urbana-Champaign" in seed.groups

    def test_dataset_name(self):
        assert _DecodingTrustMachineEthicsDataset().dataset_name == "decoding_trust_machine_ethics"

    def test_default_source_is_pinned_commit(self):
        from pyrit.datasets.seed_datasets.remote import decoding_trust_machine_ethics_dataset as mod

        assert mod._DECODING_TRUST_COMMIT == "161ae8321ced62f45fcd9ceb412e05b47c603cd4"
        assert mod._DECODING_TRUST_COMMIT in _CSV_URL
        assert _CSV_URL.endswith("/data/machine_ethics/jiminy_subset.csv")

    def test_class_level_metadata(self):
        assert _DecodingTrustMachineEthicsDataset.modalities == ["text"]
        assert _DecodingTrustMachineEthicsDataset.size == "large"
        assert _DecodingTrustMachineEthicsDataset.tags == {"default", "safety", "machine_ethics"}
