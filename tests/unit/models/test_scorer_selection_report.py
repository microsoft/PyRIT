# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scorer selection report validation."""

import pytest
from pydantic import ValidationError

from pyrit.models import ScorerCandidateVerdict, ScorerSelectionEntry, ScorerVerdictStatus


def _verdict(index: int, status: ScorerVerdictStatus) -> ScorerCandidateVerdict:
    return ScorerCandidateVerdict(candidate_index=index, candidate_label=f"c{index}", status=status)


def _entry(*statuses: ScorerVerdictStatus) -> ScorerSelectionEntry:
    return ScorerSelectionEntry(
        atomic_attack_name="group",
        display_group="group",
        candidates=[_verdict(index, status) for index, status in enumerate(statuses)],
    )


def test_an_entry_with_one_selected_candidate_is_valid():
    entry = _entry(ScorerVerdictStatus.REJECTED, ScorerVerdictStatus.SELECTED)

    assert entry.selected.candidate_index == 1


@pytest.mark.parametrize(
    ("statuses", "count"),
    [
        ((ScorerVerdictStatus.REJECTED,), 0),
        ((ScorerVerdictStatus.SELECTED, ScorerVerdictStatus.SELECTED), 2),
    ],
)
def test_an_entry_needs_exactly_one_selected_candidate(statuses, count):
    with pytest.raises(ValidationError, match=f"exactly one selected candidate; got {count}"):
        _entry(*statuses)
