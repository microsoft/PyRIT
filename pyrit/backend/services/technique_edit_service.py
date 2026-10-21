# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Safe projections and local edit admission. Factories own copying and seed semantics."""

from __future__ import annotations

from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue, TypeAdapter, ValidationError

from pyrit.backend.models.techniques import TechniqueConfiguration, TechniqueEditResponse
from pyrit.backend.services.technique_service import TechniqueService
from pyrit.common import get_global_default_values
from pyrit.executor.attack import AttackConverterConfig
from pyrit.models import AttackIdentifier, ComponentIdentifier, Parameter, SeedPrompt, SeedSimulatedConversation
from pyrit.models.parameter import ComponentType
from pyrit.models.technique_edit import SimulationChanges, TechniqueChanges, TechniqueOverride
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, ConverterRegistry, TargetRegistry
from pyrit.registry.resolution import resolve_constructor_args, resolve_reference_value

if TYPE_CHECKING:
    from pyrit.scenario.core import AttackTechniqueFactory


class TechniqueEditService:
    """Validate source-pinned edits without constructing attacks or updating a registry."""

    _DEFERRED = {"objective_target", "attack_scoring_config", "attack_adversarial_config", "attack_converter_config"}
    _PROMPTS = ("adversarial_system_prompt", "adversarial_seed_prompt", "adversarial_prompt_template")

    @staticmethod
    def _source(name: str) -> AttackTechniqueFactory:
        factory = AttackTechniqueRegistry.get_registry_singleton().instances.get(name)
        if factory is None:
            raise ValueError(f"Source technique '{name}' is no longer registered")
        return factory

    @classmethod
    def configuration(cls, name: str) -> TechniqueConfiguration:
        """
        Load effective settings without evaluating creation statements or sending prompts.

        Returns:
            TechniqueConfiguration: Safe, prefilled editor data.
        """
        factory = cls._source(name)
        return cls._project(name=name, source_identity=factory.get_configuration_identity(), factory=factory)

    @classmethod
    def validate(cls, specification: TechniqueOverride) -> TechniqueEditResponse:
        """
        Admit an edit locally and pin the references needed to replay it.

        Returns:
            TechniqueEditResponse: Validated settings and their replay specification.
        """
        factory, dependencies = cls._apply(specification)
        override = specification.model_copy(
            update={"dependencies": dependencies, "effective_identity": factory.get_configuration_identity()}
        )
        return TechniqueEditResponse(
            configuration=cls._project(
                name=specification.source_name, source_identity=specification.source_identity, factory=factory
            ),
            override=override,
        )

    @classmethod
    def resolve(cls, specification: TechniqueOverride) -> AttackTechniqueFactory:
        """
        Reconstruct exactly the admitted edit for estimates, launch and resume.

        Returns:
            AttackTechniqueFactory: A private factory with shared live dependencies.

        Raises:
            ValueError: If the source or a dependency differs from the admitted edit.
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
        projection = cls._project(
            name=specification.source_name, source_identity=specification.source_identity, factory=source
        )
        changes = specification.changes
        supplied = changes.model_fields_set - {"params"}
        unsupported = supplied - set(projection.editable_fields)
        if unsupported:
            raise ValueError(f"Technique settings are read-only: {', '.join(sorted(unsupported))}")
        if "simulation" in supplied and changes.simulation is None:
            raise ValueError("Simulation settings cannot be cleared")
        allowed = {parameter.name for parameter in projection.parameters}
        if unsupported_params := set(changes.params) - allowed:
            raise ValueError(f"Technique parameters are read-only or unknown: {', '.join(sorted(unsupported_params))}")
        metadata = AttackRegistry.get_registry_singleton().get_class_metadata(source.attack_class)
        TechniqueService.validate_parameters(parameters=metadata.parameters, params=changes.params)
        kwargs = resolve_constructor_args(
            cls=source.attack_class, raw_args=changes.params, identifier_type=AttackIdentifier, external_input=True
        )
        settings = source.get_effective_settings()
        settings.attack_kwargs.update(kwargs)
        registry = AttackRegistry.get_registry_singleton()
        registry.validate_constructor_parameters(attack_class=source.attack_class, params=settings.attack_kwargs)
        for name in supplied - {"simulation", "request_converters", "response_converters"}:
            value = getattr(changes, name)
            if name == "adversarial_chat" and value is not None:
                value = resolve_reference_value(
                    component_type=ComponentType.TARGET, value=value, owner="Technique", name=name
                )
            setattr(settings, name, value)
        config = settings.attack_kwargs.get("attack_converter_config") or AttackConverterConfig()
        converter_edits = {}
        for name in ("request_converters", "response_converters"):
            if name in supplied:
                names = getattr(changes, name)
                if names is None:
                    raise ValueError(f"Use an empty list to clear {name}")
                converters = [
                    resolve_reference_value(
                        component_type=ComponentType.CONVERTER, value=value, owner="Technique", name=name
                    )
                    for value in names
                ]
                converter_edits[name] = ConverterConfiguration.from_converters(converters=converters)
        if converter_edits:
            settings.attack_kwargs["attack_converter_config"] = replace(config, **converter_edits)
        factory = source.with_settings(settings=settings, simulation=changes.simulation)
        dependencies = cls._dependencies(factory=factory)
        return factory, dependencies

    @classmethod
    def _project(cls, *, name: str, source_identity: str, factory: AttackTechniqueFactory) -> TechniqueConfiguration:
        metadata = AttackRegistry.get_registry_singleton().get_class_metadata(factory.attack_class)
        settings = factory.get_effective_settings()
        values: dict[str, Any] = {"description": settings.description, "params": {}}
        editable = ["description"]
        readonly = {
            "advanced configuration": "Requirements, seed metadata, scorer and feedback policy stay unchanged.",
        }
        parameters = []
        for parameter in metadata.parameters:
            if parameter.name in cls._DEFERRED:
                continue
            value = settings.attack_kwargs.get(parameter.name, parameter.default)
            if parameter.name not in settings.attack_kwargs:
                found, default = get_global_default_values().get_default_value(
                    class_type=factory.attack_class, parameter_name=parameter.name
                )
                if found:
                    value = default
            try:
                projected = cls._parameter_value(parameter=parameter, value=value)
            except (ValueError, TypeError, ValidationError):
                readonly[parameter.name] = f"{type(value).__name__}: retained on the source; not editable."
                continue
            parameters.append(parameter)
            values["params"][parameter.name] = projected
        accepted = {parameter.name for parameter in metadata.parameters}
        if factory.uses_adversarial:
            target = cls._reference_name(component_type=ComponentType.TARGET, value=settings.adversarial_chat)
            if settings.adversarial_chat is None or target is not None:
                editable.append("adversarial_chat")
                values["adversarial_chat"] = target
            else:
                readonly["adversarial_chat"] = "Unregistered live target; retained unchanged."
        if "attack_adversarial_config" in accepted:
            for prompt_name in cls._PROMPTS:
                prompt = getattr(settings, prompt_name)
                if (
                    prompt is None
                    or isinstance(prompt, str)
                    or isinstance(prompt, SeedPrompt)
                    and prompt.data_type == "text"
                ):
                    values[prompt_name] = prompt.value if isinstance(prompt, SeedPrompt) else prompt
                    editable.append(prompt_name)
                else:
                    readonly[prompt_name] = "Opaque prompt; retained unchanged."
        if "attack_converter_config" in accepted:
            config = settings.attack_kwargs.get("attack_converter_config") or AttackConverterConfig()
            for key in ("request_converters", "response_converters"):
                names = cls._converter_names(getattr(config, key))
                if names is not None:
                    values[key] = names
                    editable.append(key)
                else:
                    readonly[key] = "Conditional or unregistered converter chain; order and settings stay unchanged."
        if factory.seed_technique is not None:
            simulation = next(
                (seed for seed in factory.seed_technique.seeds if isinstance(seed, SeedSimulatedConversation)), None
            )
            prompts = (
                (
                    simulation.adversarial_chat_system_prompt,
                    simulation.simulated_target_system_prompt,
                    simulation.next_message_system_prompt,
                )
                if simulation is not None
                else ()
            )
            if simulation is not None and all(prompt is None or prompt.data_type == "text" for prompt in prompts):
                final = [p for p in factory.seed_technique.prompts if p.sequence == simulation.sequence_range.stop]
                editable.append("simulation")
                values["simulation"] = SimulationChanges(
                    num_turns=simulation.num_turns,
                    adversarial_chat_system_prompt=simulation.adversarial_chat_system_prompt.value,
                    simulated_target_system_prompt=simulation.simulated_target_system_prompt.value,
                    next_message_system_prompt=(
                        simulation.next_message_system_prompt.value if simulation.next_message_system_prompt else None
                    ),
                    final_user_message=(
                        final[0].value
                        if len(final) == 1
                        and final[0].role == "user"
                        and final[0].data_type == "text"
                        and simulation.next_message_system_prompt is None
                        else None
                    ),
                )
            else:
                readonly["technique seeds"] = "Existing seed group and prompt templates stay unchanged."
        return TechniqueConfiguration(
            source_name=name,
            source_identity=source_identity,
            attack_type=metadata.registry_name,
            settings=TechniqueChanges.model_validate(values),
            parameters=parameters,
            editable_fields=editable,
            supplied_parameters=list(settings.attack_kwargs),
            read_only=readonly,
        )

    @classmethod
    def _parameter_value(cls, *, parameter: Parameter, value: Any) -> JsonValue:
        if parameter.reference is not None:
            if parameter.reference.component_type not in {ComponentType.TARGET, ComponentType.CONVERTER}:
                raise ValueError("Unsupported registry reference")
            if value is None:
                return None
            if isinstance(value, list):
                names = [
                    cls._reference_name(component_type=parameter.reference.component_type, value=item) for item in value
                ]
                if None in names:
                    raise ValueError("Unregistered reference")
                return [name for name in names if name is not None]
            name = cls._reference_name(component_type=parameter.reference.component_type, value=value)
            if name is None:
                raise ValueError("Unregistered reference")
            return name
        if parameter.variants:
            raise ValueError("Structured values need a lossless projection before they can be edited")
        if parameter.type_name not in TechniqueService._SCALAR_INPUT_TYPES and not parameter.choices:
            raise ValueError("Unsupported parameter")
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, list):
            value = [item.value if isinstance(item, Enum) else item for item in value]
        return TypeAdapter(JsonValue).validate_python(value, strict=True)

    @staticmethod
    def _reference_name(*, component_type: ComponentType, value: Any) -> str | None:
        registry = TargetRegistry if component_type == ComponentType.TARGET else ConverterRegistry
        return next(
            (
                entry.name
                for entry in registry.get_registry_singleton().instances.get_all_instances()
                if entry.instance is value
            ),
            None,
        )

    @classmethod
    def _converter_names(cls, configurations: list[ConverterConfiguration]) -> list[str] | None:
        result = []
        for config in configurations:
            if config.indexes_to_apply or config.prompt_data_types_to_apply:
                return None
            for converter in config.converters:
                name = cls._reference_name(component_type=ComponentType.CONVERTER, value=converter)
                if name is None:
                    return None
                result.append(name)
        return result

    @classmethod
    def _dependencies(cls, *, factory: AttackTechniqueFactory) -> dict[str, str]:
        settings = factory.get_effective_settings()
        result = {}
        for component_type, registry in (
            (ComponentType.TARGET, TargetRegistry),
            (ComponentType.CONVERTER, ConverterRegistry),
        ):
            for entry in registry.get_registry_singleton().instances.get_all_instances():
                # Record only referenced instances, not the entire runtime catalog.
                if cls._contains_reference(value=settings, reference=entry.instance):
                    identifier: ComponentIdentifier = entry.instance.get_identifier()
                    result[f"{component_type.value}:{entry.name}"] = identifier.hash
        return result

    @classmethod
    def _contains_reference(cls, *, value: Any, reference: Any) -> bool:
        from dataclasses import fields, is_dataclass

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
