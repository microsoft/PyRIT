# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer registry for discovering and managing PyRIT scorers.

scorers are registered explicitly via initializers as pre-configured instances.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pyrit.identifiers import ScorerIdentifier
from pyrit.identifiers.class_name_utils import class_name_to_snake_case
from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


class ScorerRegistry(BaseInstanceRegistry["Scorer", ScorerIdentifier]):
    """
    Registry for managing available scorer instances.

    This registry stores pre-configured Scorer instances (not classes).
    Scorers are registered explicitly via initializers after being instantiated
    with their required parameters (e.g., chat_target).

    Scorers are identified by their snake_case name derived from the class name,
    or a custom name provided during registration.
    """

    @classmethod
    def get_registry_singleton(cls) -> "ScorerRegistry":
        """
        Get the singleton instance of the ScorerRegistry.

        Returns:
            The singleton ScorerRegistry instance.
        """
        return super().get_registry_singleton()  # type: ignore[return-value]

    def register_instance(
        self,
        scorer: "Scorer",
        *,
        name: Optional[str] = None,
    ) -> None:
        """
        Register a scorer instance.

        Note: Unlike ScenarioRegistry and InitializerRegistry which register classes,
        ScorerRegistry registers pre-configured instances.

        Args:
            scorer: The pre-configured scorer instance (not a class).
            name: Optional custom registry name. If not provided,
                derived from class name with identifier hash appended
                (e.g., SelfAskRefusalScorer -> self_ask_refusal_abc123).
        """
        if name is None:
            base_name = class_name_to_snake_case(scorer.__class__.__name__, suffix="Scorer")
            # Append identifier hash if available for uniqueness
            identifier_hash = scorer.get_identifier().hash[:8]
            name = f"{base_name}_{identifier_hash}"

        self.register(scorer, name=name)
        logger.debug(f"Registered scorer instance: {name} ({scorer.__class__.__name__})")

    def get_instance_by_name(self, name: str) -> Optional["Scorer"]:
        """
        Get a registered scorer instance by name.

        Note: This returns an already-instantiated scorer, not a class.

        Args:
            name: The registry name of the scorer.

        Returns:
            The scorer instance, or None if not found.
        """
        return self.get(name)

    def _build_metadata(self, name: str, instance: "Scorer") -> ScorerIdentifier:
        """
        Build metadata for a scorer instance.

        Args:
            name: The registry name of the scorer.
            instance: The scorer instance.

        Returns:
            ScorerIdentifier: The scorer's identifier
        """
        return instance.get_identifier()
