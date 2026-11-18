# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Execution ownership token stored in scenario-run metadata."""

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field


class ScenarioRunExecutionToken(BaseModel):
    """The unique identity of one scheduled scenario-run execution."""

    METADATA_KEY: ClassVar[str] = "scenario_run_execution_token_v1"
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(pattern=r"^[0-9a-f]{32}$")

    def to_metadata(self) -> dict[str, str]:
        """Return the namespaced scenario-run metadata."""
        return {self.METADATA_KEY: self.token}

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> Self | None:
        """
        Read the execution token, or return None when it is absent.

        Args:
            metadata (dict[str, Any]): The scenario-result metadata.

        Returns:
            Self | None: The token, or None when no token is present.
        """
        token = metadata.get(cls.METADATA_KEY)
        return None if token is None else cls(token=token)
