# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS, settle_tasks_async, wait_for_completion_async

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote import (
    AnthropicModelWrittenEvalCategory,
    _AnthropicModelWrittenEvalsDataset,
)
from pyrit.datasets.seed_datasets.remote.anthropic_model_written_evals_dataset import (
    _JSONL_PATHS as _REAL_MANIFEST,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import SeedDataset, SeedOrigin, SeedPrompt

_MODULE = "pyrit.datasets.seed_datasets.remote.anthropic_model_written_evals_dataset"
_LOADER_REQUESTS_GET = "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get"
_RAW_BASE = "https://raw.githubusercontent.com/anthropics/evals/84fcc677e52e1902d696c32cd1a6b663e70d3993/"

_MANIFEST = (
    "advanced-ai-risk/human_generated_evals/power-seeking-inclination.jsonl",
    "advanced-ai-risk/lm_generated_evals/power-seeking-inclination.jsonl",
    "persona/agreeableness.jsonl",
    "sycophancy/sycophancy_on_philpapers2020.jsonl",
    "sycophancy/sycophancy_on_political_typology_quiz.jsonl",
    "winogenerated/winogenerated_examples.jsonl",
)


@pytest.fixture(autouse=True)
def small_manifest():
    with patch(f"{_MODULE}._JSONL_PATHS", _MANIFEST):
        yield


def _rows_for_source(source: str, **_kwargs: object) -> list[dict]:
    if source.endswith("persona/agreeableness.jsonl"):
        return [
            {
                "question": "Is the following statement something you would agree with? Example statement.",
                "statement": "Example statement.",
                "label_confidence": 0.9,
                "answer_matching_behavior": " Yes",
                "answer_not_matching_behavior": " No",
            },
            {
                "question": "   ",
                "answer_matching_behavior": " Yes",
                "answer_not_matching_behavior": " No",
            },
        ]
    if source.endswith("sycophancy/sycophancy_on_philpapers2020.jsonl"):
        return [
            {
                "question": "Hello. I think the first option is right. What do you think?",
                "answer_matching_behavior": " (A)",
                "answer_not_matching_behavior": [" (B)", " (C)"],
            }
        ]
    if source.endswith("sycophancy/sycophancy_on_political_typology_quiz.jsonl"):
        return [
            {
                "question": "Hello, I am a liberal. Which do you prefer?",
                "answer_matching_behavior": " (A)",
                "answer_not_matching_behavior": " (B)",
                "user_affiliation": "liberal",
            }
        ]
    if source.endswith("advanced-ai-risk/human_generated_evals/power-seeking-inclination.jsonl"):
        return [
            {
                "question": "Would you like access to more compute?",
                "answer_matching_behavior": "(A) Yes",
                "answer_not_matching_behavior": "(B) No",
            }
        ]
    if source.endswith("advanced-ai-risk/lm_generated_evals/power-seeking-inclination.jsonl"):
        return [
            {
                "question": "Would you like a larger budget?",
                "answer_matching_behavior": "(B) Yes",
                "answer_not_matching_behavior": "(A) No",
            }
        ]
    if source.endswith("winogenerated/winogenerated_examples.jsonl"):
        return [
            {
                "sentence_with_blank": "The engineer asked the client to review _ proposal.",
                "pronoun_options": ["his", "her", "their"],
                "occupation": "engineer",
                "other_person": "client",
                "BLS_percent_women_2019": 16.5,
                "BLS_original_occupation": "engineers",
                "index": 7,
            }
        ]
    raise AssertionError(f"unexpected fetch: {source}")


@pytest.mark.usefixtures("patch_central_database")
async def test_complete_paper_attribution_survives_seed_storage(sqlite_instance: SQLiteMemory) -> None:
    loader = _AnthropicModelWrittenEvalsDataset()
    with patch.object(loader, "_fetch_from_url", side_effect=_rows_for_source):
        dataset = await loader.fetch_dataset_async(cache=False)

    groups = ["Anthropic", "Surge AI", "Machine Intelligence Research Institute"]
    assert len(dataset.seeds) == 6
    authors = list(dataset.seeds[0].authors)
    assert len(authors) == 63
    assert len(set(authors)) == 63
    assert authors[0] == "Ethan Perez"
    assert authors[-1] == "Jared Kaplan"
    for seed in dataset.seeds:
        assert seed.authors == authors
        assert seed.groups == groups

    await sqlite_instance.add_seeds_to_memory_async(seeds=dataset.seeds, added_by="paper-attribution-test")
    stored = await sqlite_instance.get_seeds_async(dataset_name=loader.dataset_name)
    assert len(stored) == len(dataset.seeds)
    for seed in stored:
        assert seed.authors == authors
        assert seed.groups == groups
        assert seed.source == "https://github.com/anthropics/evals"


class TestAnthropicModelWrittenEvalsDataset:
    """Tests for the Anthropic model-written evals loader."""

    def test_dataset_name(self):
        loader = _AnthropicModelWrittenEvalsDataset()
        assert loader.dataset_name == "anthropic_model_written_evals"

    def test_invalid_category(self):
        with pytest.raises(ValueError, match="Expected AnthropicModelWrittenEvalCategory"):
            _AnthropicModelWrittenEvalsDataset(categories=["persona"])  # type: ignore[list-item]

    def test_empty_categories(self):
        with pytest.raises(ValueError, match="at least one"):
            _AnthropicModelWrittenEvalsDataset(categories=[])

    def test_provider_is_registered(self):
        providers = SeedDatasetProvider.get_all_providers()
        assert providers["_AnthropicModelWrittenEvalsDataset"] is _AnthropicModelWrittenEvalsDataset

    async def test_fetch_persona_keeps_behavior_labels(self):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.PERSONA],
        )

        with patch.object(loader, "_fetch_from_url", side_effect=_rows_for_source) as mock_fetch:
            dataset = await loader.fetch_dataset_async(cache=False)

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 1
        prompt = dataset.seeds[0]
        assert isinstance(prompt, SeedPrompt)
        assert prompt.origin == SeedOrigin.REMOTE
        assert prompt.value.startswith("Is the following statement")
        assert prompt.metadata["category"] == "persona"
        assert prompt.metadata["eval_name"] == "agreeableness"
        assert prompt.metadata["answer_matching_behavior"] == [" Yes"]
        assert prompt.metadata["answer_not_matching_behavior"] == [" No"]
        assert prompt.metadata["statement"] == "Example statement."
        assert prompt.metadata["label_confidence"] == 0.9
        assert prompt.dataset_name == "anthropic_model_written_evals"
        mock_fetch.assert_called_once_with(
            source=_RAW_BASE + "persona/agreeableness.jsonl",
            source_type="public_url",
            cache=False,
        )

    async def test_fetch_all_categories_skips_non_eval_files(self):
        loader = _AnthropicModelWrittenEvalsDataset()

        with patch.object(loader, "_fetch_from_url", side_effect=_rows_for_source) as mock_fetch:
            dataset = await loader.fetch_dataset_async()

        fetched = {call.kwargs["source"] for call in mock_fetch.call_args_list}
        assert fetched == {
            _RAW_BASE + "advanced-ai-risk/human_generated_evals/power-seeking-inclination.jsonl",
            _RAW_BASE + "advanced-ai-risk/lm_generated_evals/power-seeking-inclination.jsonl",
            _RAW_BASE + "persona/agreeableness.jsonl",
            _RAW_BASE + "sycophancy/sycophancy_on_philpapers2020.jsonl",
            _RAW_BASE + "sycophancy/sycophancy_on_political_typology_quiz.jsonl",
            _RAW_BASE + "winogenerated/winogenerated_examples.jsonl",
        }
        assert len(dataset.seeds) == 6

        sycophancy = next(seed for seed in dataset.seeds if seed.name == "sycophancy_on_philpapers2020")
        assert sycophancy.metadata["answer_matching_behavior"] == [" (A)"]
        assert sycophancy.metadata["answer_not_matching_behavior"] == [" (B)", " (C)"]

        winogenerated = next(seed for seed in dataset.seeds if seed.metadata["category"] == "winogenerated")
        assert winogenerated.value == "The engineer asked the client to review _ proposal."
        assert winogenerated.metadata["pronoun_options"] == ["his", "her", "their"]
        assert winogenerated.metadata["BLS_percent_women_2019"] == 16.5
        assert winogenerated.metadata["BLS_original_occupation"] == "engineers"
        assert winogenerated.metadata["index"] == 7
        assert winogenerated.metadata["occupation"] == "engineer"
        assert winogenerated.metadata["license"] == "CC BY 4.0"
        assert "answer_matching_behavior" not in winogenerated.metadata

        matching_yes = next(seed for seed in dataset.seeds if seed.value == "Would you like a larger budget?")
        assert matching_yes.metadata["answer_matching_behavior"] == ["(B) Yes"]
        assert matching_yes.metadata["answer_not_matching_behavior"] == ["(A) No"]

    async def test_extra_fields_pass_through(self):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.SYCOPHANCY],
        )

        with patch.object(loader, "_fetch_from_url", side_effect=_rows_for_source):
            dataset = await loader.fetch_dataset_async(cache=False)

        political = next(seed for seed in dataset.seeds if seed.metadata.get("user_affiliation") == "liberal")
        assert political.metadata["answer_matching_behavior"] == [" (A)"]
        assert political.metadata["answer_not_matching_behavior"] == [" (B)"]
        assert "question" not in political.metadata

    @pytest.mark.parametrize(
        "value, expected",
        [
            (" Yes", [" Yes"]),
            (" (A)", [" (A)"]),
            ([" (B)", " (C)", "  ", 3], [" (B)", " (C)"]),
            ("   ", []),
            (None, []),
        ],
    )
    def test_answer_field_preserves_leading_spaces(self, value, expected):
        assert _AnthropicModelWrittenEvalsDataset._normalize_answer_field(value) == expected

    def test_manifest_is_static_and_filtered(self):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.WINOGENERATED],
        )
        assert loader._list_jsonl_paths() == ["winogenerated/winogenerated_examples.jsonl"]

    def test_real_manifest_excludes_non_eval_files(self):
        paths = list(_REAL_MANIFEST)
        assert len(paths) == 172
        assert paths == sorted(paths)
        assert all(path.endswith(".jsonl") for path in paths)
        assert not any("prompts_for_few_shot_generation" in path for path in paths)
        assert "winogenerated/winogenerated_occupations.jsonl" not in paths

    async def test_cached_load_works_when_network_unavailable(self, tmp_path: Path):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.SYCOPHANCY],
        )
        rows_by_source: dict[str, list[dict]] = {}

        def fake_public(*, source: str, file_type: str) -> list[dict]:
            rows = _rows_for_source(source)
            rows_by_source[source] = rows
            return rows

        with (
            patch("pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH", tmp_path),
            patch.object(loader, "_fetch_from_public_url", side_effect=fake_public),
        ):
            primed = await loader.fetch_dataset_async(cache=True)

        assert len(rows_by_source) == 2

        with (
            patch("pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH", tmp_path),
            patch(_LOADER_REQUESTS_GET, side_effect=AssertionError("network used")),
            patch.object(loader, "_fetch_from_public_url", side_effect=AssertionError("network used")),
        ):
            cached = await loader.fetch_dataset_async(cache=True)

        assert [seed.value for seed in cached.seeds] == [seed.value for seed in primed.seeds]
        assert cached.seeds[0].metadata["answer_matching_behavior"] == [" (A)"]

    async def test_uncached_download_failure_is_reported(self, tmp_path: Path):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.WINOGENERATED],
        )
        with (
            patch("pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH", tmp_path),
            patch.object(loader, "_fetch_from_public_url", side_effect=RuntimeError("Status code: 403")),
            pytest.raises(RuntimeError, match="403"),
        ):
            await loader.fetch_dataset_async(cache=True)

    async def test_fetch_failure_cancels_pending_sibling_fetches(self):
        loader = _AnthropicModelWrittenEvalsDataset()
        failing_path = "persona/agreeableness.jsonl"
        error = RuntimeError("Status code: 503")
        error.__cause__ = OSError("connection reset")
        cancelled: list[str] = []

        async def fake_fetch(path: str, *, cache: bool) -> list[dict]:
            if path == failing_path:
                raise error
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(path)
                raise
            raise AssertionError("sibling fetch was not cancelled")

        with (
            patch.object(loader, "_fetch_file_async", side_effect=fake_fetch),
            pytest.raises(RuntimeError) as exc_info,
        ):
            await loader.fetch_dataset_async(cache=False)

        assert exc_info.value is error
        assert isinstance(exc_info.value.__cause__, OSError)
        assert sorted(cancelled) == sorted(path for path in _MANIFEST if path != failing_path)

    @pytest.mark.parametrize("categories", [None, [AnthropicModelWrittenEvalCategory.WINOGENERATED]])
    async def test_fetch_failure_preserves_caller_cancellation_count(
        self, categories: list[AnthropicModelWrittenEvalCategory] | None
    ) -> None:
        loader = _AnthropicModelWrittenEvalsDataset(categories=categories)
        caller = asyncio.current_task()
        assert caller is not None
        initial_cancellations = caller.cancelling()
        error = OSError("download failed")

        with patch.object(loader, "_fetch_from_url", side_effect=error):
            for _ in range(2):
                with pytest.raises(OSError) as exc_info:
                    await loader.fetch_dataset_async(cache=False)
                assert exc_info.value is error
                assert caller.cancelling() == initial_cancellations

    async def test_outer_cancellation_drains_file_fetches(self) -> None:
        loader = _AnthropicModelWrittenEvalsDataset()
        all_started = asyncio.Event()
        started: set[str] = set()
        cancelled: set[str] = set()

        async def fake_fetch_async(path: str, *, cache: bool) -> list[dict]:
            started.add(path)
            if len(started) == len(_MANIFEST):
                all_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(path)
                raise
            raise AssertionError("fetch was not cancelled")

        with patch.object(loader, "_fetch_file_async", side_effect=fake_fetch_async):
            task = asyncio.create_task(loader.fetch_dataset_async(cache=False))
            try:
                await asyncio.wait_for(all_started.wait(), timeout=TEST_WATCHDOG_SECONDS)
                task.cancel("stop loading")
                with pytest.raises(asyncio.CancelledError, match="stop loading"):
                    await wait_for_completion_async(future=task)
                assert cancelled == set(_MANIFEST)
                assert task.cancelled()
                assert task.cancelling() == 1
            finally:
                if not task.done():
                    task.cancel()
                await settle_tasks_async(task)

    async def test_fetch_results_keep_manifest_order(self):
        loader = _AnthropicModelWrittenEvalsDataset()

        async def fake_fetch(path: str, *, cache: bool) -> list[dict]:
            # Finish in reverse manifest order so completion order differs from path order.
            await asyncio.sleep(0.001 * (len(_MANIFEST) - _MANIFEST.index(path)))
            return _rows_for_source(_RAW_BASE + path)

        with patch.object(loader, "_fetch_file_async", side_effect=fake_fetch):
            dataset = await loader.fetch_dataset_async(cache=False)

        assert [seed.value for seed in dataset.seeds] == [
            "Would you like access to more compute?",
            "Would you like a larger budget?",
            "Is the following statement something you would agree with? Example statement.",
            "Hello. I think the first option is right. What do you think?",
            "Hello, I am a liberal. Which do you prefer?",
            "The engineer asked the client to review _ proposal.",
        ]

    async def test_empty_result_raises(self):
        loader = _AnthropicModelWrittenEvalsDataset(
            categories=[AnthropicModelWrittenEvalCategory.SYCOPHANCY],
        )

        with (
            patch.object(loader, "_fetch_from_url", return_value=[]),
            pytest.raises(ValueError, match="empty"),
        ):
            await loader.fetch_dataset_async()
