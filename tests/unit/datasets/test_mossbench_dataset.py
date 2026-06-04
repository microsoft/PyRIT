# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any
from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote.mossbench_dataset import (
    MossBenchOversensitivityType,
    _MossBenchDataset,
)
from pyrit.memory import SQLiteMemory
from pyrit.memory.central_memory import CentralMemory
from pyrit.models import SeedDataset


def _make_example(
    *,
    pid: int,
    over: str,
    question: str = "Describe a fun game a child can play with these toys.",
    human: int = 0,
    child: int = 1,
    syn: int = 1,
    ocr: int = 0,
    harm: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "image": f"images/{pid}.png",
        "short description": f"Short description {pid}.",
        "question": question,
        "metadata": {
            "over": over,
            "human": human,
            "child": child,
            "syn": syn,
            "ocr": ocr,
            "harm": harm if harm is not None else [7],
        },
        "pid": str(pid),
        "description": f"Long description {pid}.",
    }


def _make_information_json(examples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mirror the upstream pid-keyed dict format produced by ``information.json``."""
    return {ex["pid"]: ex for ex in examples}


class TestMossBenchDataset:
    """Unit tests for ``_MossBenchDataset``."""

    @pytest.fixture(autouse=True)
    def setup_memory(self):
        """Set up memory instance for image downloads."""
        memory = SQLiteMemory()
        CentralMemory.set_memory_instance(memory)
        yield
        CentralMemory.set_memory_instance(None)

    def test_dataset_name(self):
        dataset = _MossBenchDataset()
        assert dataset.dataset_name == "mossbench"

    def test_init_defaults(self):
        dataset = _MossBenchDataset()
        assert dataset.oversensitivity_types is None
        assert dataset.source_type == "public_url"
        assert "/MOSSBench/" in dataset.source
        assert "information.json" in dataset.source

    def test_init_with_oversensitivity_types(self):
        types = [
            MossBenchOversensitivityType.EXAGGERATED_RISK,
            MossBenchOversensitivityType.NEGATED_HARM,
        ]
        dataset = _MossBenchDataset(oversensitivity_types=types)
        assert dataset.oversensitivity_types == types

    def test_init_with_invalid_oversensitivity_types_raises(self):
        with pytest.raises(ValueError, match="Expected MossBenchOversensitivityType"):
            _MossBenchDataset(oversensitivity_types=["exaggerated_risk"])  # type: ignore[list-item]

    def test_init_rejects_raw_string_matching_enum_value(self):
        with pytest.raises(ValueError, match="Expected MossBenchOversensitivityType"):
            _MossBenchDataset(oversensitivity_types=["type 1"])  # type: ignore[list-item]

    def test_dataset_level_metadata(self):
        dataset = _MossBenchDataset()
        assert "refusal" in dataset.tags
        assert "multimodal" in dataset.tags
        assert "safety" in dataset.tags
        assert dataset.size == "medium"
        assert set(dataset.modalities) == {"text", "image"}
        assert set(dataset.harm_categories) == {
            "exaggerated_risk",
            "negated_harm",
            "counterintuitive_interpretation",
        }

    async def test_fetch_dataset_happy_path(self):
        examples = [
            _make_example(pid=1, over="type 1", question="Q1", harm=[7]),
            _make_example(pid=2, over="type 2", question="Q2", harm=[5, 6]),
            _make_example(pid=3, over="type 3", question="Q3", harm=[1]),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                side_effect=lambda *, image_url, pid: f"/fake/path/{pid}.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset_async(cache=False)

        assert isinstance(dataset, SeedDataset)
        assert len(dataset.seeds) == 6  # 3 examples * 2 seeds each

        text_prompts = [s for s in dataset.seeds if s.data_type == "text"]
        image_prompts = [s for s in dataset.seeds if s.data_type == "image_path"]
        assert len(text_prompts) == 3
        assert len(image_prompts) == 3

        # Verify image+text pairs share group_id and sequence=0
        text_by_pid = {p.metadata["pid"]: p for p in text_prompts}
        image_by_pid = {p.metadata["pid"]: p for p in image_prompts}
        for pid in ("1", "2", "3"):
            text_prompt = text_by_pid[pid]
            image_prompt = image_by_pid[pid]
            assert text_prompt.prompt_group_id == image_prompt.prompt_group_id
            assert text_prompt.sequence == 0
            assert image_prompt.sequence == 0

        # Different examples have different group_ids
        group_ids = {p.prompt_group_id for p in dataset.seeds}
        assert len(group_ids) == 3

    async def test_fetch_dataset_metadata_and_objective_semantics(self):
        examples = [_make_example(pid=1, over="type 1", question="Q1", harm=[7, 6])]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/1.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset_async(cache=False)

        text_prompt = next(s for s in dataset.seeds if s.data_type == "text")
        image_prompt = next(s for s in dataset.seeds if s.data_type == "image_path")

        # Text prompt holds the literal benign question (mirrors XSTest/OR-Bench).
        assert text_prompt.value == "Q1"
        assert image_prompt.value == "/fake/path/1.png"
        assert text_prompt.dataset_name == "mossbench"
        assert image_prompt.dataset_name == "mossbench"
        assert text_prompt.harm_categories == ["exaggerated_risk"]
        assert image_prompt.harm_categories == ["exaggerated_risk"]

        # Metadata preserves over-type, attribute flags, harm indices, pid.
        for prompt in (text_prompt, image_prompt):
            assert prompt.metadata["pid"] == "1"
            assert prompt.metadata["oversensitivity_type"] == "exaggerated_risk"
            assert prompt.metadata["oversensitivity_type_label"] == "Exaggerated Risk"
            assert prompt.metadata["human"] is False
            assert prompt.metadata["child"] is True
            assert prompt.metadata["syn"] is True
            assert prompt.metadata["ocr"] is False
            assert prompt.metadata["harm_indices"] == [7, 6]
            assert "Short description 1." in prompt.metadata["short_description"]

        # Image prompt also carries the original URL for traceability.
        assert image_prompt.metadata["original_image_url"].endswith("/data/images/1.png")
        assert "original_image_url" not in text_prompt.metadata

    async def test_fetch_dataset_filters_by_type(self):
        examples = [
            _make_example(pid=1, over="type 1"),
            _make_example(pid=2, over="type 2"),
            _make_example(pid=3, over="type 3"),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset(oversensitivity_types=[MossBenchOversensitivityType.NEGATED_HARM])

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/2.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset_async(cache=False)

        assert len(dataset.seeds) == 2  # one pair only
        pids = {s.metadata["pid"] for s in dataset.seeds}
        assert pids == {"2"}
        for seed in dataset.seeds:
            assert seed.harm_categories == ["negated_harm"]

    async def test_fetch_dataset_filters_by_multiple_types(self):
        examples = [
            _make_example(pid=1, over="type 1"),
            _make_example(pid=2, over="type 2"),
            _make_example(pid=3, over="type 3"),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset(
            oversensitivity_types=[
                MossBenchOversensitivityType.EXAGGERATED_RISK,
                MossBenchOversensitivityType.COUNTERINTUITIVE_INTERPRETATION,
            ]
        )

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                side_effect=lambda *, image_url, pid: f"/fake/path/{pid}.png",
            ),
        ):
            dataset = await dataset_loader.fetch_dataset_async(cache=False)

        pids = {s.metadata["pid"] for s in dataset.seeds}
        assert pids == {"1", "3"}

    async def test_fetch_dataset_missing_required_key_raises(self):
        bad_example = {
            "image": "images/1.png",
            "metadata": {"over": "type 1"},
            "pid": "1",
            # missing "question"
        }
        mock_data = {"1": bad_example}
        dataset_loader = _MossBenchDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/1.png",
            ),
            pytest.raises(ValueError, match="Missing keys in MOSSBench example"),
        ):
            await dataset_loader.fetch_dataset_async(cache=False)

    async def test_fetch_dataset_unknown_oversensitivity_type_raises(self):
        bad_example = _make_example(pid=1, over="type 99")
        mock_data = _make_information_json([bad_example])
        dataset_loader = _MossBenchDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                return_value="/fake/path/1.png",
            ),
            pytest.raises(ValueError, match="unknown over type"),
        ):
            await dataset_loader.fetch_dataset_async(cache=False)

    async def test_fetch_dataset_skips_failed_image_download(self):
        examples = [
            _make_example(pid=1, over="type 1"),
            _make_example(pid=2, over="type 2"),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset()

        async def flaky_fetch(*, image_url: str, pid: str) -> str:
            if pid == "1":
                raise RuntimeError("network error")
            return f"/fake/path/{pid}.png"

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(dataset_loader, "_fetch_and_save_image_async", side_effect=flaky_fetch),
        ):
            dataset = await dataset_loader.fetch_dataset_async(cache=False)

        # pid=1 failed and was skipped; pid=2 succeeded with both seeds.
        assert len(dataset.seeds) == 2
        pids = {s.metadata["pid"] for s in dataset.seeds}
        assert pids == {"2"}

    async def test_fetch_dataset_rejects_non_dict_json(self):
        dataset_loader = _MossBenchDataset()

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=[{"foo": "bar"}]),
            pytest.raises(ValueError, match="dict keyed by pid"),
        ):
            await dataset_loader.fetch_dataset_async(cache=False)

    async def test_fetch_dataset_raises_when_filter_excludes_all_examples(self):
        examples = [
            _make_example(pid=1, over="type 1"),
            _make_example(pid=2, over="type 1"),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset(oversensitivity_types=[MossBenchOversensitivityType.NEGATED_HARM])

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(
                dataset_loader,
                "_fetch_and_save_image_async",
                side_effect=lambda *, image_url, pid: f"/fake/path/{pid}.png",
            ),
            pytest.raises(ValueError, match="MOSSBench SeedDataset cannot be empty"),
        ):
            await dataset_loader.fetch_dataset_async(cache=False)

    async def test_fetch_dataset_raises_when_all_image_fetches_fail(self):
        examples = [
            _make_example(pid=1, over="type 1"),
            _make_example(pid=2, over="type 2"),
        ]
        mock_data = _make_information_json(examples)
        dataset_loader = _MossBenchDataset()

        async def always_fail(*, image_url: str, pid: str) -> str:
            raise RuntimeError("network error")

        with (
            patch.object(dataset_loader, "_fetch_from_url", return_value=mock_data),
            patch.object(dataset_loader, "_fetch_and_save_image_async", side_effect=always_fail),
            pytest.raises(ValueError, match="MOSSBench SeedDataset cannot be empty"),
        ):
            await dataset_loader.fetch_dataset_async(cache=False)
