# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Source-pinned replay admission; shared components own construction and editing rules."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING, Any

from pyrit.backend.mappers.technique_mappers import technique_to_configuration
from pyrit.backend.models.techniques import TechniqueConfiguration, TechniqueEditResponse
from pyrit.models.parameter import ComponentType
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, TargetRegistry

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier
    from pyrit.models.technique_edit import TechniqueOverride
    from pyrit.scenario.core import AttackTechniqueFactory


class TechniqueEditService:
    """Check source/dependency drift and adapt the shared registry edit operations."""

    @staticmethod
    def _source(name: str) -> AttackTechniqueFactory:
        factory = AttackTechniqueRegistry.get_registry_singleton().instances.get(name)
        if factory is None:
            raise ValueError(f"Source technique '{name}' is no longer registered")
        return factory

    @classmethod
    def configuration(cls, name: str) -> TechniqueConfiguration:
        """
        Load safe settings without constructing an attack or resolving execution inputs.

        Returns:
            TechniqueConfiguration: The source-pinned editor snapshot.
        """
        factory = cls._source(name)
        return technique_to_configuration(
            name=name, source_identity=factory.get_configuration_identity(), factory=factory
        )

    @classmethod
    def validate(cls, specification: TechniqueOverride) -> TechniqueEditResponse:
        """
        Admit a private copy and record its replay identity.

        Returns:
            TechniqueEditResponse: The displayed settings and admitted patch.
        """
        factory, dependencies = cls._apply(specification)
        override = specification.model_copy(
            update={"dependencies": dependencies, "effective_identity": factory.get_configuration_identity()}
        )
        configuration = technique_to_configuration(
            name=specification.source_name, source_identity=specification.source_identity, factory=factory
        )
        for name in ("request_converters", "response_converters"):
            if name in specification.changes.model_fields_set:
                setattr(configuration.settings, name, getattr(specification.changes, name))
                if name not in configuration.editable_fields:
                    configuration.editable_fields.append(name)
                configuration.read_only.pop(name, None)
        return TechniqueEditResponse(
            configuration=configuration,
            override=override,
        )

    @classmethod
    def resolve(cls, specification: TechniqueOverride) -> AttackTechniqueFactory:
        """
        Replay the admitted copy for estimates, launch, retries, and resume.

        Returns:
            AttackTechniqueFactory: A private factory with shared live dependencies.

        Raises:
            ValueError: If the source, dependencies, or effective configuration changed.
        """
        factory, dependencies = cls._apply(specification)
        if specification.dependencies != dependencies:
            raise ValueError("Technique dependencies changed. Reload and apply the technique edit again.")
        if specification.effective_identity != factory.get_configuration_identity():
            raise ValueError("Edited technique configuration changed. Reload and apply the technique edit again.")
        return factory

    @classmethod
    def _apply(cls, specification: TechniqueOverride) -> tuple[AttackTechniqueFactory, dict[str, str]]:
        source = cls._source(specification.source_name)
        if source.get_configuration_identity() != specification.source_identity:
            raise ValueError("Source technique configuration changed. Reload the technique before editing or resuming.")
        factory = AttackTechniqueRegistry.get_registry_singleton().edit_factory(
            source=source, changes=specification.changes
        )
        return factory, cls._dependencies(factory=factory)

    @classmethod
    def _dependencies(cls, *, factory: AttackTechniqueFactory) -> dict[str, str]:
        settings = factory.get_effective_settings()
        result = {}
        for component_type, registry in (
            (ComponentType.TARGET, TargetRegistry),
            (ComponentType.CONVERTER, ConverterRegistry),
        ):
            for entry in registry.get_registry_singleton().instances.get_all_instances():
                if cls._contains_reference(value=settings, reference=entry.instance):
                    identifier: ComponentIdentifier = entry.instance.get_identifier()
                    result[f"{component_type.value}:{entry.name}"] = identifier.hash
        return result

    @classmethod
    def _contains_reference(cls, *, value: Any, reference: Any) -> bool:
        if value is reference:
            return True
        if is_dataclass(value) and not isinstance(value, type):
            return any(
                cls._contains_reference(value=getattr(value, field.name), reference=reference)
                for field in fields(value)
            )
        if isinstance(value, dict):
            return any(cls._contains_reference(value=item, reference=reference) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_reference(value=item, reference=reference) for item in value)
        return False
