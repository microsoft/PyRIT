# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.decoding_trust_adv_glue_dataset import (
    _BASE_URL,
    _DECODING_TRUST_COMMIT,
    DecodingTrustAdvGLUEModel,
    DecodingTrustAdvGLUETask,
    _DecodingTrustAdvGLUEDataset,
)
from pyrit.datasets.seed_datasets.seed_metadata import RECOMMENDED_TAGS
from pyrit.models import SeedDataset, SeedPrompt


def _record(
    *,
    sentence: str,
    original_sentence: str = "clean version",
    label: int = 1,
    method: str = "textfooler",
    data_construction: str = "word",
    idx: int = 0,
) -> dict:
    """Build one AdvGLUE++-shaped record."""
    return {
        "idx": idx,
        "sentence": sentence,
        "original_sentence": original_sentence,
        "label": label,
        "method": method,
        "data_construction": data_construction,
    }


def _file(**tasks) -> dict:
    """Build a source-file object {task: [records]} from keyword task lists."""
    return dict(tasks)


@pytest.fixture
def alpaca_file():
    return _file(
        sst2=[_record(sentence="adv sst2 one", label=1), _record(sentence="adv sst2 two", label=0)],
        mnli=[_record(sentence="adv mnli one", label=2)],
    )


class TestDecodingTrustAdvGLUEDataset:
    """Test the DecodingTrust AdvGLUE++ (adversarial robustness) dataset loader."""

    async def test_default_source_model_is_alpaca(self, alpaca_file):
        """Default constructor fetches only alpaca.json (a single file)."""
        loader = _DecodingTrustAdvGLUEDataset()

        assert loader.source_model is DecodingTrustAdvGLUEModel.ALPACA

        with patch.object(loader, "_fetch_from_url", return_value=alpaca_file) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        mock_fetch.assert_called_once_with(source=f"{_BASE_URL}alpaca.json", source_type="public_url", cache=True)
        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 3

    async def test_source_model_all_fetches_three_files(self, alpaca_file):
        """source_model=ALL fetches alpaca, vicuna and stable-vicuna and concatenates."""
        loader = _DecodingTrustAdvGLUEDataset(source_model=DecodingTrustAdvGLUEModel.ALL)

        with patch.object(loader, "_fetch_from_url", side_effect=[alpaca_file, alpaca_file, alpaca_file]) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        assert mock_fetch.call_count == 3
        fetched = {call.kwargs["source"] for call in mock_fetch.call_args_list}
        assert fetched == {
            f"{_BASE_URL}alpaca.json",
            f"{_BASE_URL}vicuna.json",
            f"{_BASE_URL}stable-vicuna.json",
        }
        assert len(dataset.seeds) == 9  # 3 records x 3 files

    async def test_task_filter_restricts_tasks(self, alpaca_file):
        """A tasks filter keeps only records from the selected GLUE tasks."""
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.MNLI])

        with patch.object(loader, "_fetch_from_url", return_value=alpaca_file):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["adv mnli one"]

    def test_invalid_source_model_raises(self):
        """A raw string for source_model is rejected by _validate_enum."""
        with pytest.raises(ValueError, match="DecodingTrustAdvGLUEModel"):
            _DecodingTrustAdvGLUEDataset(source_model="alpaca")  # type: ignore[arg-type]

    def test_invalid_task_raises(self):
        """A raw string inside tasks is rejected by _validate_enums."""
        with pytest.raises(ValueError, match="DecodingTrustAdvGLUETask"):
            _DecodingTrustAdvGLUEDataset(tasks=["sst2"])  # type: ignore[list-item]

    async def test_prompt_value_is_adversarial_sentence(self, alpaca_file):
        """The SeedPrompt value is the perturbed adversarial `sentence`."""
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=alpaca_file):
            dataset = await loader.fetch_dataset_async()

        first = dataset.seeds[0]
        assert isinstance(first, SeedPrompt)
        assert first.value == "adv sst2 one"
        # Robustness probes carry no harm categories.
        assert first.harm_categories == []

    async def test_per_seed_metadata(self, alpaca_file):
        """Provenance fields land in metadata, including the human-readable label name."""
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=alpaca_file):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert seed.dataset_name == "decoding_trust_adv_glue_plus_plus"
        assert seed.source == f"{_BASE_URL}alpaca.json"
        assert seed.authors is not None and "Boxin Wang" in seed.authors
        meta = seed.metadata
        assert meta is not None
        assert meta["task"] == "sst2"
        assert meta["source_model"] == "alpaca"
        assert meta["label"] == "1"
        assert meta["label_name"] == "positive"  # sst2 label 1
        assert meta["method"] == "textfooler"
        assert meta["data_construction"] == "word"
        assert meta["original_sentence"] == "clean version"
        assert meta["idx"] == "0"

    async def test_mnli_label_name_mapping(self, alpaca_file):
        """MNLI label 2 maps to 'contradiction'."""
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.MNLI])

        with patch.object(loader, "_fetch_from_url", return_value=alpaca_file):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].metadata["label_name"] == "contradiction"

    async def test_unknown_label_omits_label_name(self):
        """A label with no mapping stores label but not label_name."""
        file = _file(sst2=[_record(sentence="x", label=7)])
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        meta = dataset.seeds[0].metadata
        assert meta["label"] == "7"
        assert "label_name" not in meta

    async def test_component_form_mnli_composed(self):
        """MNLI records that store premise/hypothesis separately are composed, not dropped."""
        file = _file(
            mnli=[
                {
                    "idx": 5,
                    "premise": "the cat sat",
                    "hypothesis": "a feline rested",
                    "original_hypothesis": "a cat rested",
                    "label": 1,
                    "method": "bertattack",
                    "data_construction": "word",
                }
            ]
        )
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.MNLI])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        seed = dataset.seeds[0]
        assert seed.value == "premise: the cat sat hypothesis: a feline rested"
        assert seed.metadata["label_name"] == "neutral"
        # Clean counterpart preserved even though there's no `original_sentence`.
        assert seed.metadata["original_hypothesis"] == "a cat rested"

    async def test_component_form_rte_maps_to_premise_hypothesis(self):
        """RTE sentence1/sentence2 map to premise/hypothesis in the composed text."""
        file = _file(rte=[{"idx": 9, "sentence1": "A big storm hit", "sentence2": "It was calm", "label": 1}])
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.RTE])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].value == "premise: A big storm hit hypothesis: It was calm"

    async def test_component_form_qqp_composed(self):
        """QQP question1/question2 records are composed."""
        file = _file(qqp=[{"idx": 2, "question1": "How to X?", "question2": "how do i x?", "label": 1}])
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.QQP])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].value == "question1: How to X? question2: how do i x?"

    async def test_qnli_component_vs_precomposed(self):
        """QNLI: a record with `question` is composed; one without is used as-is.

        The `sentence` field is overloaded for QNLI — it is the passage in
        component form but the full pre-joined input otherwise — so presence of
        `question` is what disambiguates.
        """
        file = _file(
            qnli=[
                {"idx": 1, "question": "Who?", "sentence": "Alice went home.", "label": 0},
                {"idx": 2, "sentence": "question: Who? sentence: Bob went home.", "label": 1},
            ]
        )
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.QNLI])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        values = [seed.value for seed in dataset.seeds]
        assert values == [
            "question: Who? sentence: Alice went home.",
            "question: Who? sentence: Bob went home.",
        ]

    async def test_incomplete_component_record_skipped(self):
        """A component-form record missing its second field is skipped, not half-composed."""
        file = _file(mnli=[{"idx": 1, "premise": "only premise", "label": 0}])
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.MNLI])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset_async()

    async def test_skips_records_missing_sentence(self):
        """Records with missing/empty sentence or non-dict shape are skipped, not fatal."""
        file = _file(
            sst2=[
                _record(sentence="keep"),
                {"label": 1},  # missing sentence
                {"sentence": ""},  # empty sentence
                "not a dict",  # malformed record
            ]
        )
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=file):
            dataset = await loader.fetch_dataset_async()

        assert [seed.value for seed in dataset.seeds] == ["keep"]

    async def test_raises_when_task_value_not_list(self):
        """A task mapping to a non-list is a hard error."""
        file = {"sst2": {"not": "a list"}}
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=file):
            with pytest.raises(ValueError, match="to map to a list"):
                await loader.fetch_dataset_async()

    async def test_raises_when_file_not_object(self):
        """A source file that is not a JSON object is a hard error."""
        loader = _DecodingTrustAdvGLUEDataset()

        with patch.object(loader, "_fetch_from_url", return_value=["not", "an", "object"]):
            with pytest.raises(ValueError, match="JSON object keyed by task"):
                await loader.fetch_dataset_async()

    async def test_raises_when_filters_leave_zero_seeds(self):
        """A task filter that matches nothing in the file yields an empty result and raises."""
        file = _file(mnli=[_record(sentence="only mnli")])
        loader = _DecodingTrustAdvGLUEDataset(tasks=[DecodingTrustAdvGLUETask.RTE])

        with patch.object(loader, "_fetch_from_url", return_value=file):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset_async()

    def test_dataset_name(self):
        """dataset_name returns the canonical id."""
        assert _DecodingTrustAdvGLUEDataset().dataset_name == "decoding_trust_adv_glue_plus_plus"

    def test_source_urls_are_pinned_commit(self):
        """Source URLs must reference the pinned commit SHA and the AdvGLUE++ path."""
        loader = _DecodingTrustAdvGLUEDataset(source_model=DecodingTrustAdvGLUEModel.ALL)
        urls = loader._model_urls()

        assert len(urls) == 3
        for url in urls:
            assert _DECODING_TRUST_COMMIT in url
            assert "/data/adv-glue-plus-plus/data/" in url
            assert url.endswith(".json")

    def test_single_model_url(self):
        """A single source_model resolves to exactly one file URL."""
        loader = _DecodingTrustAdvGLUEDataset(source_model=DecodingTrustAdvGLUEModel.VICUNA)
        assert loader._model_urls() == [f"{_BASE_URL}vicuna.json"]

    def test_class_level_metadata(self):
        """Class metadata drives dataset discovery and must use the canonical vocab."""
        assert _DecodingTrustAdvGLUEDataset.modalities == ["text"]
        assert _DecodingTrustAdvGLUEDataset.size == "huge"
        assert _DecodingTrustAdvGLUEDataset.tags == {"safety", "synthetic"}
        # Tags must be drawn from the recommended vocabulary (soft contract).
        assert _DecodingTrustAdvGLUEDataset.tags <= RECOMMENDED_TAGS
        # Adversarial robustness prompts are benign NLU sentences: no harm categories.
        assert not hasattr(_DecodingTrustAdvGLUEDataset, "harm_categories")
