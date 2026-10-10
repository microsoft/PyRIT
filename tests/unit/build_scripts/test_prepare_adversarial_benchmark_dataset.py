# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from build_scripts.prepare_adversarial_benchmark_dataset import (
    _BEHAVIOR_IDS,
    DATASET_NAME,
    _build_balanced_dataset,
)
from pyrit.models import SeedDataset, SeedObjective


def _source_dataset(*, behavior_ids: list[str]) -> SeedDataset:
    return SeedDataset(
        dataset_name="harmbench",
        seeds=[
            SeedObjective(
                value=f"Objective for {behavior_id}",
                dataset_name="harmbench",
                metadata={"BehaviorID": behavior_id},
            )
            for behavior_id in behavior_ids
        ],
    )


def test_build_balanced_dataset_selects_expected_order() -> None:
    source = _source_dataset(behavior_ids=["extra", *reversed(_BEHAVIOR_IDS)])

    result = _build_balanced_dataset(source_dataset=source)

    assert result.dataset_name == DATASET_NAME
    assert [seed.metadata["BehaviorID"] for seed in result.objectives] == list(_BEHAVIOR_IDS)
    assert all(seed.dataset_name == DATASET_NAME for seed in result.objectives)


def test_build_balanced_dataset_does_not_mutate_source() -> None:
    source = _source_dataset(behavior_ids=list(_BEHAVIOR_IDS))

    _build_balanced_dataset(source_dataset=source)

    assert all(seed.dataset_name == "harmbench" for seed in source.objectives)


def test_build_balanced_dataset_raises_for_missing_behavior() -> None:
    source = _source_dataset(behavior_ids=list(_BEHAVIOR_IDS[:-1]))

    with pytest.raises(ValueError, match=_BEHAVIOR_IDS[-1]):
        _build_balanced_dataset(source_dataset=source)
