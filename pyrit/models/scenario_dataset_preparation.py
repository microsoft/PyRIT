# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Dataset-preparation state stored in scenario-run metadata."""

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field


class ScenarioDatasetPreparation(BaseModel):
    """
    Whether a run saved before dataset preparation has finished preparing its datasets.

    A run launched with deferred preparation is saved as pending, then marked prepared once its
    datasets are loaded and its plan is stored. A pending run has no plan, so it can't be resumed.
    """

    METADATA_KEY: ClassVar[str] = "scenario_dataset_preparation_v1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    pending: bool = Field(strict=True)

    def to_metadata(self) -> dict[str, Any]:
        """Return the namespaced scenario-run metadata."""
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> Self | None:
        """
        Read the preparation state, or return None when the run recorded none.

        Runs that prepared their datasets before being saved, and rows from older versions, record none.

        Args:
            metadata (dict[str, Any]): The scenario-result metadata.

        Returns:
            Self | None: The recorded state, or None if absent.

        Raises:
            ValueError: If the stored state is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])

    @classmethod
    def is_pending(cls, *, metadata: dict[str, Any]) -> bool:
        """
        Whether a run is still preparing its datasets, or stopped before finishing.

        Args:
            metadata (dict[str, Any]): The scenario-result metadata.

        Returns:
            bool: True if the run recorded pending preparation; False if it recorded none.
        """
        state = cls.from_metadata(metadata=metadata)
        return state is not None and state.pending
