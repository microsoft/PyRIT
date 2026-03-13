# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import ABC
from copy import deepcopy
from dataclasses import dataclass
from typing import TypeVar

StrategyResultT = TypeVar("StrategyResultT", bound="StrategyResult")


@dataclass
class StrategyResult(ABC):  # noqa: B024
    """Base class for all strategy results."""

    def duplicate(self: StrategyResultT) -> StrategyResultT:
        """
        Create a deep copy of the result.

        Returns:
            StrategyResult: A deep copy of the result.

        """
        return deepcopy(self)
