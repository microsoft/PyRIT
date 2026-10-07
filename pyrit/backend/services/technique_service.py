# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Thin runtime technique service; construction belongs to the registry."""

import asyncio
from functools import lru_cache

from pyrit.backend.mappers.technique_mappers import technique_to_instance
from pyrit.backend.models.techniques import (
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueTypeEntry,
    TechniqueTypeResponse,
)
from pyrit.models import SeedPrompt, SeedSimulatedConversation
from pyrit.models.technique_definition import TechniqueDefinition, TechniqueFactoryOptions
from pyrit.registry.components import AttackRegistry, AttackTechniqueRegistry
from pyrit.registry.resolution import json_input_parameters


class TechniqueService:
    """List the active factory pool and register validated runtime definitions."""

    def __init__(self) -> None:
        """Bind to the runtime registry; service lifecycle clears this binding on reset."""
        self._registry = AttackTechniqueRegistry.get_registry_singleton()

    async def list_async(self) -> TechniqueListResponse:
        """
        List all factories, including advanced programmatic definitions.

        Returns:
            TechniqueListResponse: The active factory catalog.
        """
        return await asyncio.to_thread(self._list)

    def _list(self) -> TechniqueListResponse:
        return TechniqueListResponse(
            items=[
                technique_to_instance(name=entry.name, factory=entry.instance)
                for entry in self._registry.instances.get_all_instances()
            ]
        )

    async def get_async(self, name: str) -> TechniqueInstance | None:
        """
        Get safe settings for a named factory.

        Returns:
            TechniqueInstance | None: The factory settings, if registered.
        """
        factory = self._registry.instances.get(name)
        if factory is None:
            return None
        return await asyncio.to_thread(technique_to_instance, name=name, factory=factory)

    async def create_async(self, definition: TechniqueDefinition) -> TechniqueInstance:
        """
        Validate, project, and register without sending prompts or creating attacks.

        Returns:
            TechniqueInstance: Safe settings for the admitted factory.
        """
        return await asyncio.to_thread(self._create, definition)

    def _create(self, definition: TechniqueDefinition) -> TechniqueInstance:
        factory = self._registry.build_from_definition(definition)
        result = technique_to_instance(name=definition.name, factory=factory)
        self._registry.instances.register_definition(factory)
        return result

    async def types_async(self) -> TechniqueTypeResponse:
        """
        Discover metadata off the event loop.

        Returns:
            TechniqueTypeResponse: Declared construction inputs.
        """
        return await asyncio.to_thread(self._types)

    @staticmethod
    def _types() -> TechniqueTypeResponse:
        metadata = AttackRegistry.get_registry_singleton().get_all_registered_class_metadata()
        items = []
        for entry in metadata:
            names = {parameter.name for parameter in entry.parameters}
            items.append(
                TechniqueTypeEntry(
                    attack_type=entry.registry_name,
                    description=entry.class_description,
                    parameters=[
                        parameter.model_copy(update={"default": None})
                        if parameter.name == "attack_scoring_config"
                        else parameter
                        for parameter in entry.parameters
                        if parameter.name not in {"objective_target", "attack_adversarial_config"}
                    ],
                    supports_adversarial="attack_adversarial_config" in names,
                    supports_converters="attack_converter_config" in names,
                )
            )
        return TechniqueTypeResponse(
            items=items,
            definition_schema=TechniqueDefinition.model_json_schema(),
            factory_parameters=json_input_parameters(TechniqueFactoryOptions),
            seed_parameters={
                "SeedPrompt": json_input_parameters(SeedPrompt),
                "SeedSimulatedConversation": json_input_parameters(SeedSimulatedConversation),
            },
        )


@lru_cache(maxsize=1)
def get_technique_service() -> TechniqueService:
    """
    Get the runtime service.

    Returns:
        TechniqueService: The cached runtime service.
    """
    return TechniqueService()
