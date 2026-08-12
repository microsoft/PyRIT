# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote import (
    TurkishPromptInjectionFamily,
    TurkishPromptInjectionLabel,
    TurkishPromptInjectionSplit,
    _TurkishPromptInjectionDataset,
)
from pyrit.models import SeedDataset, SeedPrompt

FETCH_TARGET = "_fetch_from_huggingface_async"


def _row(
    *,
    identifier: int,
    label: int,
    split: str,
    attack_family: str = "tr_instruction_override_extraction",
    technique: str = "override",
    severity: str = "high",
) -> dict[str, Any]:
    is_attack = label == 1
    return {
        "id": identifier,
        "text": f"{'Saldiri' if is_attack else 'Mesru'} ornegi {identifier} icin yeterince uzun bir Turkce metin.",
        "label": label,
        "class": "injection" if is_attack else "benign",
        "attack_family": attack_family if is_attack else "benign",
        "technique": technique if is_attack else "hard_negative",
        "severity": severity if is_attack else "none",
        "language": "tr",
        "split": split,
    }


@pytest.fixture
def mock_rows_by_split() -> dict[str, list[dict[str, Any]]]:
    return {
        "train": [
            _row(identifier=1, label=1, split="train", attack_family="tr_instruction_override_extraction"),
            _row(identifier=2, label=0, split="train"),
            _row(identifier=3, label=1, split="train", attack_family="tr_jailbreak_persona"),
        ],
        "validation": [
            _row(identifier=4, label=1, split="validation", attack_family="tr_obfuscation_exfiltration"),
            _row(identifier=5, label=0, split="validation"),
        ],
        "test": [
            _row(identifier=6, label=1, split="test", attack_family="tr_agentic_toolabuse"),
            _row(identifier=7, label=0, split="test"),
        ],
    }


def _fetch_side_effect(rows_by_split: dict[str, list[dict[str, Any]]]):
    def _inner(*, dataset_name: str, config: str, split: str, cache: bool, revision: str):
        return list(rows_by_split.get(split, []))

    return _inner


@pytest.mark.asyncio
async def test_default_loads_attacks_from_all_splits(mock_rows_by_split):
    loader = _TurkishPromptInjectionDataset()
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert all(isinstance(seed, SeedPrompt) for seed in dataset.seeds)
    # 4 attack rows across all splits, no benign
    assert len(dataset.seeds) == 4
    assert all(seed.metadata["label"] == 1 for seed in dataset.seeds)
    assert all(seed.metadata["language"] == "tr" for seed in dataset.seeds)


@pytest.mark.asyncio
async def test_label_all_loads_both_classes(mock_rows_by_split):
    loader = _TurkishPromptInjectionDataset(label=TurkishPromptInjectionLabel.ALL)
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))):
        dataset = await loader.fetch_dataset_async()

    labels = sorted(seed.metadata["label"] for seed in dataset.seeds)
    assert labels == [0, 0, 0, 1, 1, 1, 1]


@pytest.mark.asyncio
async def test_benign_filter_loads_hard_negatives(mock_rows_by_split):
    loader = _TurkishPromptInjectionDataset(label=TurkishPromptInjectionLabel.BENIGN)
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 3
    assert all(seed.metadata["label"] == 0 for seed in dataset.seeds)
    assert all(seed.metadata["attack_family"] == "benign" for seed in dataset.seeds)


@pytest.mark.asyncio
async def test_family_filter_scopes_attacks(mock_rows_by_split):
    loader = _TurkishPromptInjectionDataset(attack_families=[TurkishPromptInjectionFamily.JAILBREAK_PERSONA])
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 1
    assert dataset.seeds[0].metadata["attack_family"] == "tr_jailbreak_persona"


@pytest.mark.asyncio
async def test_single_split(mock_rows_by_split):
    loader = _TurkishPromptInjectionDataset(
        label=TurkishPromptInjectionLabel.ALL, split=TurkishPromptInjectionSplit.TEST
    )
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(mock_rows_by_split))):
        dataset = await loader.fetch_dataset_async()

    assert len(dataset.seeds) == 2
    assert all(seed.metadata["split"] == "test" for seed in dataset.seeds)


def test_invalid_enum_type_raises():
    with pytest.raises(ValueError):
        _TurkishPromptInjectionDataset(label="attack")  # type: ignore[arg-type]


def test_empty_family_list_raises():
    with pytest.raises(ValueError):
        _TurkishPromptInjectionDataset(attack_families=[])


@pytest.mark.asyncio
async def test_validation_rejects_non_turkish_language(mock_rows_by_split):
    bad = {"train": [_row(identifier=1, label=1, split="train")]}
    bad["train"][0]["language"] = "en"
    loader = _TurkishPromptInjectionDataset(split=TurkishPromptInjectionSplit.TRAIN)
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(bad))):
        with pytest.raises(ValueError, match="non-Turkish"):
            await loader.fetch_dataset_async()


@pytest.mark.asyncio
async def test_validation_rejects_bad_label(mock_rows_by_split):
    bad = {"train": [_row(identifier=1, label=1, split="train")]}
    bad["train"][0]["label"] = 2
    loader = _TurkishPromptInjectionDataset(split=TurkishPromptInjectionSplit.TRAIN)
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(bad))):
        with pytest.raises(ValueError, match="Invalid label"):
            await loader.fetch_dataset_async()


@pytest.mark.asyncio
async def test_validation_rejects_bool_label(mock_rows_by_split):
    # True == 1, but type(True) is bool, not int — must be rejected.
    bad = {"train": [_row(identifier=1, label=1, split="train")]}
    bad["train"][0]["label"] = True
    loader = _TurkishPromptInjectionDataset(split=TurkishPromptInjectionSplit.TRAIN)
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(bad))):
        with pytest.raises(ValueError, match="Invalid label"):
            await loader.fetch_dataset_async()


@pytest.mark.asyncio
async def test_empty_after_filter_raises(mock_rows_by_split):
    # Only benign rows exist for this split, but we ask for attacks.
    rows = {"validation": [_row(identifier=5, label=0, split="validation")]}
    loader = _TurkishPromptInjectionDataset(
        label=TurkishPromptInjectionLabel.ATTACK, split=TurkishPromptInjectionSplit.VALIDATION
    )
    with patch.object(loader, FETCH_TARGET, new=AsyncMock(side_effect=_fetch_side_effect(rows))):
        with pytest.raises(ValueError, match="cannot be empty"):
            await loader.fetch_dataset_async()
