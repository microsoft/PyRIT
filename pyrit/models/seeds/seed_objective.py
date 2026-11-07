# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedObjective class for representing seed objectives.
"""

from __future__ import annotations

import logging
import uuid  # noqa: TC003  (runtime Pydantic annotation)
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from pyrit.common.path import PATHS_DICT
from pyrit.models.decomposition import (
    DecompositionNode,
    DecompositionPath,
    DecompositionTree,
    validate_own_decomposition,
)
from pyrit.models.score.condition import ConditionTuple  # noqa: TC001  (runtime Pydantic annotation)
from pyrit.models.seeds.seed import Seed

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class SeedObjective(Seed[Literal["text"]]):
    """
    Represents a seed objective with various attributes and metadata.

    An objective can be decomposed into child objectives. A child names its parent in
    ``parent_objective_id``, and the parent lists ``paths``: each an ordered chain of its direct
    children that together achieve it. Children can be decomposed further.
    """

    # Discriminator field for the polymorphic Seed union (see seed_group.SeedUnion).
    seed_type: Literal["objective"] = "objective"

    # Objectives are always text. Narrowing the base field rejects non-text values up-front
    # rather than silently dropping them downstream.
    data_type: Literal["text"] = "text"

    conditions: ConditionTuple = ()

    parent_objective_id: uuid.UUID | None = None

    paths: tuple[DecompositionPath, ...] = ()

    @property
    def decomposition_node(self) -> DecompositionNode | None:
        """The objective's decomposition relationships, or None when it has no ID."""
        if self.id is None:
            return None
        return DecompositionNode(id=self.id, parent_id=self.parent_objective_id, paths=self.paths)

    @model_validator(mode="after")
    def _validate_and_render(self) -> SeedObjective:
        """
        Post-initialization to render the template to replace existing values.

        Returns:
            SeedObjective: The validated, rendered objective.

        Raises:
            ValueError: If is_general_technique is True, or the decomposition fields are inconsistent.
        """
        if self.is_general_technique:
            raise ValueError("SeedObjective cannot be a general technique.")
        validate_own_decomposition(node_id=self.id, parent_id=self.parent_objective_id, paths=self.paths)
        # Only trusted templates are rendered through Jinja — see seed_prompt.py for details.
        if self.is_jinja_template:
            self.value = self._render_trusted_template_value(**PATHS_DICT)
        return self


def build_objective_decomposition_tree(objectives: Iterable[SeedObjective]) -> DecompositionTree:
    """
    Build the decomposition trees of objectives that have a parent, paths, or children.

    Args:
        objectives (Iterable[SeedObjective]): Objectives that include every member of each tree.

    Returns:
        DecompositionTree: The validated trees.

    Raises:
        ValueError: If a parent is missing, an objective appears twice, the parents form a cycle, or
            a path step is not a direct child of the path's owner.
    """
    objectives = list(objectives)
    parent_ids = {objective.parent_objective_id for objective in objectives if objective.parent_objective_id}
    nodes = [
        node
        for node in (objective.decomposition_node for objective in objectives)
        if node is not None and (node.is_member or node.id in parent_ids)
    ]
    return DecompositionTree(nodes)
