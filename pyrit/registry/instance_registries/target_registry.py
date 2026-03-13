# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target registry for discovering and managing PyRIT prompt targets.

Targets are registered explicitly via initializers as pre-configured instances.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pyrit.identifiers import ComponentIdentifier
from pyrit.registry.instance_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class TargetRegistry(BaseInstanceRegistry["PromptTarget", ComponentIdentifier]):
    """
    Registry for managing available prompt target instances.

    This registry stores pre-configured PromptTarget instances (not classes).
    Targets are registered explicitly via initializers after being instantiated
    with their required parameters (e.g., endpoint, API keys).

    Targets are identified by their snake_case name derived from the class name,
    or a custom name provided during registration.
    """

    @classmethod
    def get_registry_singleton(cls) -> TargetRegistry:
        """
        Get the singleton instance of the TargetRegistry.

        Returns:
            The singleton TargetRegistry instance.
        """
        return super().get_registry_singleton()  # type: ignore[return-value]

    def register_instance(
        self,
        target: PromptTarget,
        *,
        name: Optional[str] = None,
    ) -> None:
        """
        Register a target instance.

        Note: Unlike ScenarioRegistry and InitializerRegistry which register classes,
        TargetRegistry registers pre-configured instances.

        Args:
            target: The pre-configured target instance (not a class).
            name: Optional custom registry name. If not provided,
                derived from class name with identifier hash appended
                (e.g., OpenAIChatTarget -> openai_chat_abc123).
        """
        if name is None:
            name = target.get_identifier().unique_name

        self.register(target, name=name)
        logger.debug(f"Registered target instance: {name} ({target.__class__.__name__})")

    def get_instance_by_name(self, name: str) -> Optional[PromptTarget]:
        """
        Get a registered target instance by name.

        Note: This returns an already-instantiated target, not a class.

        Args:
            name: The registry name of the target.

        Returns:
            The target instance, or None if not found.
        """
        return self.get(name)

    def _build_metadata(self, name: str, instance: PromptTarget) -> ComponentIdentifier:
        """
        Build metadata for a target instance.

        Args:
            name: The registry name of the target.
            instance: The target instance.

        Returns:
            ComponentIdentifier: The target's identifier.
        """
        return instance.get_identifier()
