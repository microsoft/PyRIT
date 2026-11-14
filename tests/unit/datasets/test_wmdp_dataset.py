# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.datasets.executors.question_answer.wmdp_dataset import fetch_wmdp_dataset


class _EmptySplit:
    def __len__(self) -> int:
        return 0


def test_fetch_wmdp_dataset_rejects_empty_category():
    with patch(
        "pyrit.datasets.executors.question_answer.wmdp_dataset.load_dataset",
        return_value={"test": _EmptySplit()},
    ) as mock_load_dataset:
        with pytest.raises(ValueError, match="Invalid Parameter"):
            fetch_wmdp_dataset(category="")

    mock_load_dataset.assert_not_called()


class _FakeSplit:
    """Minimal stand-in for a Hugging Face split: row count via ``len`` and column access via ``[]``."""

    def __init__(self, *, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, column: str) -> list[Any]:
        return [row[column] for row in self._rows]


def _fake_load_dataset(name_to_rows: dict[str, list[dict[str, Any]]]) -> Callable[[str, str], dict[str, _FakeSplit]]:
    def _load(path: str, name: str) -> dict[str, _FakeSplit]:
        return {"test": _FakeSplit(rows=name_to_rows[name])}

    return _load


def _row(*, question: str, answer: int) -> dict[str, Any]:
    return {"question": question, "choices": ["a", "b", "c", "d"], "answer": answer}


def test_fetch_wmdp_dataset_loads_single_category_and_builds_entries() -> None:
    rows = {
        "wmdp-bio": [
            _row(question="bio question", answer=2),
            _row(question="bio question 2", answer=0),
        ]
    }

    with patch(
        "pyrit.datasets.executors.question_answer.wmdp_dataset.load_dataset",
        side_effect=_fake_load_dataset(rows),
    ) as mock_load_dataset:
        dataset = fetch_wmdp_dataset(category="bio")

    mock_load_dataset.assert_called_once_with("cais/wmdp", "wmdp-bio")
    assert dataset.name == "wmdp"
    assert [(entry.question, entry.correct_answer) for entry in dataset.questions] == [
        ("bio question", 2),
        ("bio question 2", 0),
    ]
    entry = dataset.questions[0]
    assert [(choice.index, choice.text) for choice in entry.choices] == [(0, "a"), (1, "b"), (2, "c"), (3, "d")]


def test_fetch_wmdp_dataset_loads_all_categories_when_none_specified() -> None:
    rows = {
        "wmdp-cyber": [_row(question="cyber question", answer=0)],
        "wmdp-bio": [_row(question="bio question", answer=1)],
        "wmdp-chem": [_row(question="chem question", answer=3)],
    }

    with patch(
        "pyrit.datasets.executors.question_answer.wmdp_dataset.load_dataset",
        side_effect=_fake_load_dataset(rows),
    ) as mock_load_dataset:
        dataset = fetch_wmdp_dataset()

    assert [call.args[1] for call in mock_load_dataset.call_args_list] == ["wmdp-cyber", "wmdp-bio", "wmdp-chem"]
    assert [entry.question for entry in dataset.questions] == ["cyber question", "bio question", "chem question"]


def test_fetch_wmdp_dataset_rejects_unknown_category() -> None:
    with patch("pyrit.datasets.executors.question_answer.wmdp_dataset.load_dataset") as mock_load_dataset:
        with pytest.raises(ValueError, match="Invalid Parameter"):
            fetch_wmdp_dataset(category="physics")

    mock_load_dataset.assert_not_called()
