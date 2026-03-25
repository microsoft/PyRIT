# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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
