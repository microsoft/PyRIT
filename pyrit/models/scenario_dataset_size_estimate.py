# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario dataset size estimates: selected seed groups before technique expansion."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pyrit.models.dataset_limit import ResolvedDatasetLimit


class ScenarioDatasetSizeEstimateKind(str, Enum):
    """Meaning of a population budget."""

    Bounded = "bounded"
    AllAvailable = "all_available"
    Indeterminate = "indeterminate"


class _BudgetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BoundedDatasetSize(_BudgetModel):
    """A finite upper limit, not a minimum or an observed count."""

    kind: Literal[ScenarioDatasetSizeEstimateKind.Bounded] = ScenarioDatasetSizeEstimateKind.Bounded
    value: int = Field(ge=0, strict=True)


class AllAvailableDatasetSize(_BudgetModel):
    """A finite source without a finite aggregate cap; child caps still apply."""

    kind: Literal[ScenarioDatasetSizeEstimateKind.AllAvailable] = ScenarioDatasetSizeEstimateKind.AllAvailable


class IndeterminateDatasetSize(_BudgetModel):
    """A population whose configuration is not known."""

    kind: Literal[ScenarioDatasetSizeEstimateKind.Indeterminate] = ScenarioDatasetSizeEstimateKind.Indeterminate
    detail: str = Field(default="Population configuration is not available.", min_length=1)


ScenarioDatasetSizeEstimate = Annotated[
    BoundedDatasetSize | AllAvailableDatasetSize | IndeterminateDatasetSize,
    Field(discriminator="kind"),
]


def scenario_dataset_size_from_limit(limit: ResolvedDatasetLimit) -> BoundedDatasetSize | AllAvailableDatasetSize:
    """Return a finite-source budget from an optional selection limit."""
    return AllAvailableDatasetSize() if limit == "all" else BoundedDatasetSize(value=limit)


class DatasetLimitState(str, Enum):
    """How the dataset-limit control obtains its initial value."""

    Value = "value"
    ScenarioDefault = "scenario_default"
    NotApplicable = "not_applicable"


class DatasetLimitInput(BaseModel):
    """Editable dataset limit, separate from combined or generated population budgets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: DatasetLimitState = DatasetLimitState.ScenarioDefault
    value: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_value(self) -> "DatasetLimitInput":
        """
        Require a numeric value only for the value state.

        Returns:
            DatasetLimitInput: Validated control metadata.

        Raises:
            ValueError: If the state and value disagree.
        """
        if (self.state is DatasetLimitState.Value) != (self.value is not None):
            raise ValueError("Only the value state requires a dataset limit value.")
        return self
