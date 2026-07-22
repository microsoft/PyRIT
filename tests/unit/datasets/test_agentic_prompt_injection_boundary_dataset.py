# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote import (
    AgenticPromptInjectionBoundaryAttackFamily,
    AgenticPromptInjectionBoundaryDataset,
    AgenticPromptInjectionBoundaryLabel,
    AgenticPromptInjectionBoundarySourceContext,
    AgenticPromptInjectionBoundarySplit,
)
from pyrit.models import SeedDataset, SeedPrompt


def _row(
    *,
    identifier: str,
    pair_id: str,
    scenario_id: str,
    label: int,
    split: str,
    pair_family: str = "tool_action_abuse",
    source_context: str = "agent_tool_request",
) -> dict[str, Any]:
    category = "prompt_injection" if label == 1 else "benign_boundary"
    return {
        "id": identifier,
        "pair_id": pair_id,
        "scenario_id": scenario_id,
        "text": f"A sufficiently detailed {'attack' if label else 'legitimate'} workflow request for {pair_id}.",
        "label": label,
        "category": category,
        "attack_family": pair_family if label == 1 else "none",
        "pair_family": pair_family,
        "source_context": source_context,
        "risk_domain": "finance",
        "target_boundary": "tool_permissions",
        "expected_action": "block_or_review" if label == 1 else "allow",
        "source_type": "synthetic_curated",
        "language": "en",
        "split": split,
    }


@pytest.fixture
def mock_rows_by_split() -> dict[str, list[dict[str, Any]]]:
    return {
        "train": [
            _row(identifier="apibp_0001_a", pair_id="pair_0001", scenario_id="scn_001", label=1, split="train"),
            _row(identifier="apibp_0001_b", pair_id="pair_0001", scenario_id="scn_001", label=0, split="train"),
        ],
        "validation": [
            _row(
                identifier="apibp_0002_a",
                pair_id="pair_0002",
                scenario_id="scn_002",
                label=1,
                split="validation",
                pair_family="direct_instruction_override",
                source_context="direct_user",
            ),
            _row(
                identifier="apibp_0002_b",
                pair_id="pair_0002",
                scenario_id="scn_002",
                label=0,
                split="validation",
                pair_family="direct_instruction_override",
                source_context="direct_user",
            ),
        ],
        "test": [
            _row(identifier="apibp_0003_a", pair_id="pair_0003", scenario_id="scn_003", label=1, split="test"),
            _row(identifier="apibp_0003_b", pair_id="pair_0003", scenario_id="scn_003", label=0, split="test"),
            _row(
                identifier="apibp_0004_a",
                pair_id="pair_0004",
                scenario_id="scn_004",
                label=1,
                split="test",
                pair_family="indirect_content_injection",
                source_context="retrieved_document",
            ),
            _row(
                identifier="apibp_0004_b",
                pair_id="pair_0004",
                scenario_id="scn_004",
                label=0,
                split="test",
                pair_family="indirect_content_injection",
                source_context="retrieved_document",
            ),
        ],
    }


def _fetch_side_effect(
    rows_by_split: dict[str, list[dict[str, Any]]],
) -> Callable[..., Any]:
    async def _fetch(**kwargs: Any) -> list[dict[str, Any]]:
        return rows_by_split[str(kwargs["split"])]

    return _fetch


def test_dataset_name() -> None:
    loader = AgenticPromptInjectionBoundaryDataset()
    assert loader.dataset_name == "agentic_prompt_injection_boundary_pairs"


async def test_default_loads_attacks_from_all_splits(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset()
    mock_fetch = AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))

    with patch.object(loader, "_fetch_from_huggingface_async", new=mock_fetch):
        dataset = await loader.fetch_dataset_async(cache=False)

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 4
    assert all(isinstance(seed, SeedPrompt) for seed in dataset.seeds)
    assert all(seed.metadata["label"] == 1 for seed in dataset.seeds)
    assert {call.kwargs["split"] for call in mock_fetch.await_args_list} == {"train", "validation", "test"}
    assert all(call.kwargs["config"] == "default" for call in mock_fetch.await_args_list)
    assert all(call.kwargs["cache"] is False for call in mock_fetch.await_args_list)
    assert all(
        call.kwargs["revision"] == AgenticPromptInjectionBoundaryDataset.HF_DATASET_REVISION
        for call in mock_fetch.await_args_list
    )

    first = dataset.seeds[0]
    assert first.name == "apibp_0001_a"
    assert first.prompt_group_alias is None
    assert first.metadata["pair_id"] == "pair_0001"
    assert first.metadata["scenario_id"] == "scn_001"
    assert first.metadata["target_boundary"] == "tool_permissions"
    assert first.metadata["dataset_version"] == "1.0.0"
    assert first.metadata["hf_revision"] == AgenticPromptInjectionBoundaryDataset.HF_DATASET_REVISION
    assert first.harm_categories == []
    assert first.source == ("https://huggingface.co/datasets/3nesdeniz/agentic-prompt-injection-boundary-pairs")


async def test_label_all_reconstructs_pairs(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(
        label=AgenticPromptInjectionBoundaryLabel.ALL,
        split=AgenticPromptInjectionBoundarySplit.TEST,
    )

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split)),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 4
    assert {seed.metadata["label"] for seed in dataset.seeds} == {0, 1}
    assert {seed.metadata["pair_id"] for seed in dataset.seeds} == {"pair_0003", "pair_0004"}
    assert all(seed.prompt_group_alias is None for seed in dataset.seeds)
    assert len(dataset.seed_groups) == len(dataset.seeds)


async def test_benign_filter_loads_hard_negative_surface(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(
        label=AgenticPromptInjectionBoundaryLabel.BENIGN,
        split=AgenticPromptInjectionBoundarySplit.TRAIN,
    )

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split)),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].harm_categories == []
    assert dataset.seeds[0].metadata["category"] == "benign_boundary"
    assert dataset.seeds[0].metadata["expected_action"] == "allow"


async def test_family_filter_uses_pair_family_and_keeps_both_sides(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(
        label=AgenticPromptInjectionBoundaryLabel.ALL,
        split=AgenticPromptInjectionBoundarySplit.TEST,
        attack_families=[AgenticPromptInjectionBoundaryAttackFamily.INDIRECT_CONTENT_INJECTION],
    )

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split)),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    assert {seed.metadata["label"] for seed in dataset.seeds} == {0, 1}
    assert all(seed.metadata["pair_family"] == "indirect_content_injection" for seed in dataset.seeds)


async def test_source_context_filter(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(
        label=AgenticPromptInjectionBoundaryLabel.ALL,
        split=AgenticPromptInjectionBoundarySplit.TEST,
        source_contexts=[AgenticPromptInjectionBoundarySourceContext.RETRIEVED_DOCUMENT],
    )

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split)),
    ):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    assert all(seed.metadata["source_context"] == "retrieved_document" for seed in dataset.seeds)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"label": "attack"}, "Expected AgenticPromptInjectionBoundaryLabel"),
        ({"split": "test"}, "Expected AgenticPromptInjectionBoundarySplit"),
        ({"attack_families": []}, "non-empty"),
        ({"source_contexts": []}, "non-empty"),
        ({"attack_families": ["tool_action_abuse"]}, "Expected AgenticPromptInjectionBoundaryAttackFamily"),
        ({"source_contexts": ["direct_user"]}, "Expected AgenticPromptInjectionBoundarySourceContext"),
    ],
)
def test_invalid_filters_raise(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AgenticPromptInjectionBoundaryDataset(**kwargs)


async def test_empty_after_filter_raises(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(
        split=AgenticPromptInjectionBoundarySplit.TRAIN,
        attack_families=[AgenticPromptInjectionBoundaryAttackFamily.APPROVAL_WORKFLOW_BYPASS],
    )

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split)),
    ):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


async def test_missing_required_key_raises(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    malformed = dict(mock_rows_by_split["train"][0])
    malformed.pop("pair_id")
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TRAIN)

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=[malformed]),
    ):
        with pytest.raises(ValueError, match="pair_id"):
            await loader.fetch_dataset_async()


async def test_conflicting_split_metadata_raises(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    mismatched = dict(mock_rows_by_split["test"][0])
    mismatched["split"] = "train"
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TEST)

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=[mismatched]),
    ):
        with pytest.raises(ValueError, match="does not match fetched split"):
            await loader.fetch_dataset_async()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("label", "1", "Invalid label"),
        ("label", 1.5, "Invalid label"),
        ("label", True, "Invalid label"),
        ("label", 2, "Invalid label"),
        ("text", "", "invalid `text` value"),
        ("text", "   ", "invalid `text` value"),
        ("category", "benign_boundary", "category"),
        ("attack_family", "none", "attack_family"),
        ("expected_action", "allow", "expected_action"),
        ("pair_family", "unknown_family", "Invalid pair family"),
        ("source_context", "unknown_context", "Invalid source context"),
        ("source_type", "customer_data", "source_type"),
        ("language", "tr", "language"),
    ],
)
async def test_invalid_row_invariants_raise(
    mock_rows_by_split: dict[str, list[dict[str, Any]]],
    field: str,
    value: Any,
    message: str,
) -> None:
    malformed = dict(mock_rows_by_split["train"][0])
    malformed[field] = value
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TRAIN)

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=[malformed])):
        with pytest.raises(ValueError, match=message):
            await loader.fetch_dataset_async()


async def test_incomplete_pair_raises(mock_rows_by_split: dict[str, list[dict[str, Any]]]) -> None:
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TRAIN)

    with patch.object(
        loader,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=[mock_rows_by_split["train"][0]]),
    ):
        with pytest.raises(ValueError, match="exactly labels 0 and 1"):
            await loader.fetch_dataset_async()


async def test_mismatched_pair_metadata_raises(mock_rows_by_split: dict[str, list[dict[str, Any]]]) -> None:
    pair_rows = [dict(row) for row in mock_rows_by_split["train"]]
    pair_rows[1]["risk_domain"] = "healthcare"
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TRAIN)

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=pair_rows)):
        with pytest.raises(ValueError, match="mismatched `risk_domain`"):
            await loader.fetch_dataset_async()


async def test_duplicate_id_raises(mock_rows_by_split: dict[str, list[dict[str, Any]]]) -> None:
    pair_rows = [dict(row) for row in mock_rows_by_split["train"]]
    pair_rows[1]["id"] = pair_rows[0]["id"]
    loader = AgenticPromptInjectionBoundaryDataset(split=AgenticPromptInjectionBoundarySplit.TRAIN)

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=pair_rows)):
        with pytest.raises(ValueError, match="Duplicate Agentic Boundary Pairs entry ID: apibp_0001_a"):
            await loader.fetch_dataset_async()
