# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
CSV → goals/targets loader for the GCG attack.

Decoupled from ``GCGGenerator`` so that callers with goals and targets
already in memory can pass them straight into ``execute_async`` without going
through ``pandas`` or any filesystem access.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (
    get_goals_and_targets as _legacy_loader,
)

if TYPE_CHECKING:
    from pyrit.executor.promptgen.gcg.config import GCGDataConfig


def load_goals_and_targets(
    *, data: GCGDataConfig, random_seed: int = 42
) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Load training and held-out goal/target lists from CSV(s).

    Wraps the existing pandas-based ``get_goals_and_targets`` so the caller
    sees a typed signature instead of a free-form params object.

    Args:
        data (GCGDataConfig): Train/test CSV paths plus row counts. Empty
            ``train_data`` falls back to whatever default the legacy loader
            returns (an empty list today).
        random_seed (int): Seed used to shuffle the training rows. Defaults
            to ``42`` to match ``GCGAlgorithmConfig``'s default.

    Returns:
        tuple[list[str], list[str], list[str], list[str]]:
            ``(train_goals, train_targets, test_goals, test_targets)``.

    Raises:
        ValueError: If goals and targets in either split have mismatched
            lengths (re-raised from the underlying loader).
    """
    params = SimpleNamespace(
        train_data=data.train_data,
        test_data=data.test_data,
        n_train_data=data.n_train_data,
        n_test_data=data.n_test_data,
        random_seed=random_seed,
    )
    return _legacy_loader(params)
