# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote.harmeval_dataset import (
    HarmEvalTopic,
    _HarmEvalDataset,
)
from pyrit.models import SeedDataset, SeedObjective


@pytest.fixture
def mock_harmeval_data():
    """Mock rows for the HarmEval dataset, mirroring the real HF schema.

    HarmEval is gated, so these ``Question`` strings are benign synthetic
    placeholders -- they must not be copied verbatim from the upstream data.
    The ``Topic`` values are the verbatim upstream labels.
    """
    return [
        {"Question": "placeholder question a", "Topic": "Illegal Activity"},
        {"Question": "placeholder question b", "Topic": "Child Abuse Content"},
        {"Question": "placeholder question c", "Topic": "Hate/Harass/Violence"},
        {"Question": "placeholder question d", "Topic": "Economic Harm"},
        {"Question": "placeholder question e", "Topic": "Privacy Violation Activity"},
        {"Question": "placeholder question f", "Topic": "Tailored Financial Advice"},
    ]


class TestHarmEvalDataset:
    """Test the HarmEval dataset loader."""

    def test_dataset_name(self):
        """Test dataset_name property."""
        assert _HarmEvalDataset().dataset_name == "harmeval"

    async def test_loader_is_discoverable(self):
        """Discovery walks remote/__init__.py's __all__, so a loader missing from it never reaches
        the catalog -- importing this module directly would otherwise hide that."""
        import pyrit.datasets.seed_datasets.remote as remote_datasets

        assert "_HarmEvalDataset" in remote_datasets.__all__
        assert "harmeval" in await SeedDatasetProvider.get_all_dataset_names_async()

    def test_topic_enum_is_re_exported(self):
        """Callers filter with HarmEvalTopic, so it must be exported from the remote package."""
        import pyrit.datasets.seed_datasets.remote as remote_datasets

        assert remote_datasets.HarmEvalTopic is HarmEvalTopic

    async def test_fetch_dataset_defaults_to_all_topics(self, mock_harmeval_data):
        """Default loader should return every row, unfiltered."""
        loader = _HarmEvalDataset()
        assert loader.topics is None

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmeval_data)):
            dataset = await loader.fetch_dataset_async()

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == len(mock_harmeval_data)
        assert all(isinstance(seed, SeedObjective) for seed in dataset.seeds)
        assert [seed.value for seed in dataset.seeds] == [row["Question"] for row in mock_harmeval_data]
        for seed in dataset.seeds:
            assert seed.dataset_name == "harmeval"
            assert seed.name == "HarmEval"
            assert seed.source == "https://huggingface.co/datasets/SoftMINER-Group/HarmEval"
            assert seed.authors is not None and "Somnath Banerjee" in seed.authors
            assert seed.groups is not None and "Indian Institute of Technology Kharagpur" in seed.groups

    async def test_source_topic_preserved_in_metadata(self, mock_harmeval_data):
        """The verbatim upstream Topic label is kept per seed for post-filtering."""
        loader = _HarmEvalDataset()

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmeval_data)):
            dataset = await loader.fetch_dataset_async()

        assert [seed.metadata["topic"] for seed in dataset.seeds if seed.metadata] == [
            row["Topic"] for row in mock_harmeval_data
        ]

    async def test_harm_categories_are_standardized(self, mock_harmeval_data):
        """Topics are mapped onto PyRIT's canonical taxonomy, not passed through raw."""
        loader = _HarmEvalDataset()

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmeval_data)):
            dataset = await loader.fetch_dataset_async()

        by_topic = {seed.metadata["topic"]: seed.harm_categories for seed in dataset.seeds if seed.metadata}
        assert by_topic["Illegal Activity"] == ["COORDINATION_HARM"]
        assert by_topic["Child Abuse Content"] == ["GROOMING", "SEXUAL_CONTENT", "CHILD_LEAKAGE"]
        assert by_topic["Hate/Harass/Violence"] == ["HATE_SPEECH", "HARASSMENT", "VIOLENT_CONTENT"]
        assert by_topic["Economic Harm"] == ["SCAMS", "DECEPTION"]
        assert by_topic["Privacy Violation Activity"] == ["PPI"]
        assert by_topic["Tailored Financial Advice"] == ["FINANCIAL_ADVICE"]

    @pytest.mark.parametrize("topic", list(HarmEvalTopic), ids=lambda t: t.name)
    def test_every_topic_has_an_override(self, topic):
        """Five of the eleven topics fall back to OTHER without an override; none may."""
        loader = _HarmEvalDataset()
        assert topic.value in _HarmEvalDataset.HARM_CATEGORY_ALIAS_OVERRIDES

        standardized = loader._standardize_harm_categories(
            topic.value, alias_overrides=_HarmEvalDataset.HARM_CATEGORY_ALIAS_OVERRIDES
        )
        assert standardized, f"{topic.value!r} standardized to nothing"
        assert "OTHER" not in standardized, f"{topic.value!r} standardized to OTHER"

    @pytest.mark.parametrize("topic", list(HarmEvalTopic), ids=lambda t: t.name)
    async def test_fetch_dataset_filters_by_each_topic(self, topic):
        """Each enum value selects exactly the rows carrying that upstream label."""
        rows = [{"Question": f"placeholder for {member.value}", "Topic": member.value} for member in HarmEvalTopic]
        loader = _HarmEvalDataset(topics=[topic])

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
            dataset = await loader.fetch_dataset_async()

        assert len(dataset.seeds) == 1
        assert dataset.seeds[0].metadata is not None
        assert dataset.seeds[0].metadata["topic"] == topic.value

    async def test_fetch_dataset_filters_by_multiple_topics(self, mock_harmeval_data):
        """Multiple topics are OR-ed together."""
        loader = _HarmEvalDataset(topics=[HarmEvalTopic.ILLEGAL_ACTIVITY, HarmEvalTopic.ECONOMIC_HARM])

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmeval_data)):
            dataset = await loader.fetch_dataset_async()

        assert {seed.metadata["topic"] for seed in dataset.seeds if seed.metadata} == {
            "Illegal Activity",
            "Economic Harm",
        }

    async def test_fetch_dataset_empty_after_filter_raises(self, mock_harmeval_data):
        """Filtering to a topic absent from the data raises, naming the filter that emptied it.

        Matching on the filter detail rather than the generic "SeedDataset cannot be empty"
        keeps this test from passing on SeedDataset's own validator if the loader stops checking.
        """
        loader = _HarmEvalDataset(topics=[HarmEvalTopic.MALWARE])

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=mock_harmeval_data)):
            with pytest.raises(ValueError, match=r"HarmEval filter: topics=\['Malware'\]"):
                await loader.fetch_dataset_async()

    async def test_rows_with_empty_question_are_skipped(self):
        """Blank questions are dropped rather than emitted as empty objectives."""
        loader = _HarmEvalDataset()
        rows = [
            {"Question": "   ", "Topic": "Malware"},
            {"Question": "placeholder question", "Topic": "Malware"},
        ]

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["placeholder question"]

    @pytest.mark.parametrize("dropped", ["Question", "Topic"])
    async def test_unexpected_schema_raises(self, dropped):
        """Issue #2770 flags the column names as unverified, so drift must be reported, not swallowed."""
        loader = _HarmEvalDataset()
        row = {"Question": "placeholder question", "Topic": "Malware"}
        del row[dropped]

        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=[row])):
            with pytest.raises(ValueError, match=rf"missing expected column\(s\) \['{dropped}'\]"):
                await loader.fetch_dataset_async()

    async def test_fetch_dataset_passes_token_and_split(self, mock_harmeval_data):
        """The loader forwards the token and the hardcoded 'train' split to HuggingFace."""
        loader = _HarmEvalDataset(token="hf_test_token")

        mock_fetch = AsyncMock(return_value=mock_harmeval_data)
        with patch.object(loader, "_fetch_from_huggingface_async", new=mock_fetch):
            await loader.fetch_dataset_async(cache=False)

        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        assert kwargs["dataset_name"] == "SoftMINER-Group/HarmEval"
        assert kwargs["split"] == "train"
        assert kwargs["cache"] is False
        assert kwargs["token"] == "hf_test_token"

    def test_token_defaults_to_env_var(self):
        """Token should fall back to HUGGINGFACE_TOKEN env var when not provided."""
        with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "env_token_value"}):
            assert _HarmEvalDataset().token == "env_token_value"

    def test_token_explicit_overrides_env_var(self):
        """Explicit token argument should override the env var."""
        with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "env_token_value"}):
            assert _HarmEvalDataset(token="explicit_token").token == "explicit_token"

    def test_invalid_topic_raises(self):
        """Passing a non-HarmEvalTopic value should raise."""
        with pytest.raises(ValueError, match="Expected HarmEvalTopic"):
            _HarmEvalDataset(topics=["Malware"])  # type: ignore[ty:invalid-argument-type]

    def test_empty_topics_list_raises(self):
        """An empty list is a caller mistake; None is the way to select everything."""
        with pytest.raises(ValueError, match="must be a non-empty list"):
            _HarmEvalDataset(topics=[])
