# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedObjective class for representing seed objectives.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import model_validator

from pyrit.common.path import PATHS_DICT
from pyrit.models.seeds.seed import Seed

logger = logging.getLogger(__name__)


class SeedObjective(Seed):
    """Represents a seed objective with various attributes and metadata."""

    # Discriminator field for the polymorphic Seed union (see seed_group.SeedUnion).
    seed_type: Literal["objective"] = "objective"

    # Objectives are always text. Narrowing the base field rejects non-text values up-front
    # rather than silently dropping them downstream.
    data_type: Literal["text"] = "text"

    @model_validator(mode="after")
    def _validate_and_render(self) -> SeedObjective:
        """
        Post-initialization to render the template to replace existing values.

        Returns:
            SeedObjective: The validated, rendered objective.

        Raises:
            ValueError: If is_general_technique is True.
        """
        if self.is_general_technique:
            raise ValueError("SeedObjective cannot be a general technique.")
        # Only trusted templates are rendered through Jinja — see seed_prompt.py for details.
        if self.is_jinja_template:
            self.value = self.render_template_value_silent(**PATHS_DICT)
        return self
