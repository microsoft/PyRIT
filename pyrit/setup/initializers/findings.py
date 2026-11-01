# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Base initializer for a deployment's findings."""

from abc import abstractmethod
from typing import Any

from pyrit.models import FindingFields
from pyrit.setup.finding_fields import set_active_finding_fields
from pyrit.setup.pyrit_initializer import PyRITInitializer


class FindingsInitializer(PyRITInitializer):
    """Set a deployment's finding fields from ``get_finding_fields``."""

    @abstractmethod
    def get_finding_fields(self) -> FindingFields:
        """
        Return the deployment's finding fields, the same whatever the environment variables.

        A live apply checks these fields before it applies the new environment.

        Returns:
            FindingFields: The fields that setup makes active.
        """

    async def initialize_async(self) -> None:
        """Make the fields from ``get_finding_fields`` the active finding fields."""
        set_active_finding_fields(finding_fields=self.get_finding_fields(), source=type(self).__name__)

    async def get_dynamic_default_values_info_async(self) -> dict[str, Any]:
        """
        Report no default values or global variables without running ``initialize_async``.

        Returns:
            dict[str, Any]: Empty ``default_values`` and ``global_variables`` lists.
        """
        return {"default_values": [], "global_variables": []}
