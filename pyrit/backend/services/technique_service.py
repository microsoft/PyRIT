# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Thin runtime technique service; construction belongs to the registry."""

import asyncio
from functools import lru_cache
from typing import Any, get_args

from pydantic import TypeAdapter

from pyrit.backend.mappers.technique_mappers import technique_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.techniques import (
    CreateTechniqueRequest,
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueTypeEntry,
    TechniqueTypeResponse,
)
from pyrit.registry.components import AttackRegistry, AttackTechniqueRegistry


class TechniqueService:
    """List the active factory pool and register basic runtime configurations."""

    _SCALAR_INPUT_TYPES = {"str", "int", "float", "bool", "list[str]", "list[int]", "list[float]", "list[bool]"}

    def __init__(self) -> None:
        """Bind to the runtime registry; service lifecycle clears this binding on reset."""
        self._registry = AttackTechniqueRegistry.get_registry_singleton()

    async def list_async(self, *, limit: int = 50, cursor: str | None = None) -> TechniqueListResponse:
        """
        List factories in registry-name order, including advanced programmatic definitions.

        Returns:
            TechniqueListResponse: A page and its pagination metadata.
        """
        return await asyncio.to_thread(self._list, limit=limit, cursor=cursor)

    def _list(self, *, limit: int, cursor: str | None) -> TechniqueListResponse:
        entries = self._registry.instances.get_all_instances()
        start = next((index + 1 for index, entry in enumerate(entries) if entry.name == cursor), 0)
        page = entries[start : start + limit]
        has_more = len(entries) > start + limit
        return TechniqueListResponse(
            items=[technique_to_instance(name=entry.name, factory=entry.instance) for entry in page],
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=page[-1].name if page and has_more else None,
                prev_cursor=cursor,
            ),
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

    async def create_async(self, request: CreateTechniqueRequest) -> TechniqueInstance:
        """
        Validate, project, and register without sending prompts or creating attacks.

        Returns:
            TechniqueInstance: Safe settings for the admitted factory.
        """
        return await asyncio.to_thread(self._create, request)

    def _create(self, request: CreateTechniqueRequest) -> TechniqueInstance:
        self._validate_params(request)
        args = request.model_dump(exclude_unset=True, exclude={"name", "type", "tags"})
        factory = self._registry.create_factory(
            name=request.name, attack_type=request.type, technique_tags=request.tags, **args
        )
        result = technique_to_instance(name=request.name, factory=factory)
        self._registry.instances.register_runtime(factory)
        return result

    @classmethod
    def _validate_params(cls, request: CreateTechniqueRequest) -> None:
        """Reject REST values that need live Python objects before factory registration."""
        registry = AttackRegistry.get_registry_singleton()
        if request.type not in registry:
            raise ValueError(f"Attack type '{request.type}' is not registered")
        declared = {
            parameter.name: parameter
            for parameter in registry.get_class_metadata(registry.get_class(request.type)).parameters
        }
        for name, value in request.params.items():
            parameter = declared.get(name)
            if parameter is None:
                raise ValueError(f"Unknown parameter '{name}' for '{request.type}'")
            if parameter.variants is not None:
                continue
            annotation: Any = parameter.param_type
            if parameter.reference is not None:
                annotation = list[str] if parameter.is_list else str
                if type(None) in get_args(parameter.reference.annotation):
                    annotation = annotation | None
            elif parameter.type_name not in cls._SCALAR_INPUT_TYPES and not parameter.choices:
                raise ValueError(f"Parameter '{name}' requires a Python value; it is not supported through REST")
            TypeAdapter(annotation).validate_python(
                parameter.coerce_value(value) if parameter.choices else value, strict=True
            )

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
                        parameter
                        for parameter in entry.parameters
                        if parameter.name
                        not in {
                            "objective_target",
                            "attack_adversarial_config",
                            "attack_converter_config",
                            "attack_scoring_config",
                        }
                    ],
                    supports_adversarial="attack_adversarial_config" in names,
                    supports_converters="attack_converter_config" in names,
                )
            )
        return TechniqueTypeResponse(items=items)


@lru_cache(maxsize=1)
def get_technique_service() -> TechniqueService:
    """
    Get the runtime service.

    Returns:
        TechniqueService: The cached runtime service.
    """
    return TechniqueService()
