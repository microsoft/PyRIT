# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from typing_extensions import Self

StrategyResultT = TypeVar("StrategyResultT", bound="StrategyResult")


class StrategyResult(BaseModel, ABC):
    """Base class for all strategy results."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    def duplicate(self) -> Self:
        """
        Create a deep copy of the result.

        Returns:
            StrategyResult: A deep copy of the result.

        """
        return self.model_copy(deep=True)
