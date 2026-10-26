# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Safe creation-call display for registered technique factories."""

from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pyrit.backend.models.techniques import TechniqueConfiguration, TechniqueInstance
from pyrit.models import Identifiable, SeedPrompt
from pyrit.models.technique_edit import TechniqueSettings
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry
from pyrit.registry.resolution import project_parameter_value
from pyrit.scenario.core import AttackTechniqueFactory


def _format_creation_value(value: Any) -> str:
    """
    Format creation inputs without reading live component settings or credentials.

    Returns:
        str: Python-style display text; live objects without captured inputs use an ellipsis.
    """
    if isinstance(value, Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if value is None or isinstance(value, (str, int, float, bool)):
        return repr(value)
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, Path):
        return f"Path({str(value)!r})"
    if isinstance(value, Identifiable):
        return f"{type(value).__name__}(...)"
    if isinstance(value, SeedPrompt):
        arguments = {
            name: getattr(value, name)
            for name in ("value", "data_type", "parameters", "response_json_schema", "is_jinja_template")
            if getattr(value, name) is not None and getattr(value, name) != type(value).model_fields[name].default
        }
        return _format_creation_object(name=type(value).__name__, arguments=arguments)
    if is_dataclass(value) and not isinstance(value, type):
        arguments = {}
        for field in fields(value):
            item = getattr(value, field.name)
            if not field.init or item is None:
                continue
            if field.default is not MISSING and isinstance(item, (str, int, float, bool)) and item == field.default:
                continue
            if (
                field.default_factory in (list, dict, tuple, set)
                and isinstance(item, (list, dict, tuple, set))
                and not item
            ):
                continue
            arguments[field.name] = item
        return _format_creation_object(name=type(value).__name__, arguments=arguments)
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_format_creation_value(key)}: {_format_creation_value(item)}" for key, item in value.items())
            + "}"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_format_creation_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        contents = ", ".join(_format_creation_value(item) for item in value)
        return f"({contents}{',' if len(value) == 1 else ''})"
    return f"{type(value).__name__}(...)"


def _format_creation_object(*, name: str, arguments: dict[str, Any]) -> str:
    """Return a nested constructor display without storage-model wrappers."""
    return f"{name}(" + ", ".join(f"{key}={_format_creation_value(value)}" for key, value in arguments.items()) + ")"


def _format_creation_calls(factory: AttackTechniqueFactory) -> str:
    """Return a safe display of factory creation inputs, not executable or lossless source."""
    calls = []
    for call, kwargs in factory.get_creation_calls():
        arguments = "".join(f"    {name}={_format_creation_value(value)},\n" for name, value in kwargs.items())
        calls.append(f"{call}(\n{arguments})")
    return ".".join(calls)


def technique_to_instance(*, name: str, factory: AttackTechniqueFactory) -> TechniqueInstance:
    """
    Map a real factory without constructing an attack or resolving default targets.

    Returns:
        TechniqueInstance: Catalog metadata and the supplied factory creation inputs.
    """
    return TechniqueInstance(
        name=name,
        description=factory.description,
        attack_type=factory.attack_class.__name__,
        tags=factory.technique_tags,
        uses_adversarial=factory.uses_adversarial,
        uses_default_adversarial_target=factory.uses_default_adversarial_target,
        creation_statement=_format_creation_calls(factory),
    )


def technique_to_configuration(
    *, name: str, source_identity: str, factory: AttackTechniqueFactory
) -> TechniqueConfiguration:
    """
    Map the shared edit contract onto the existing editor response without reconstructing opaque state.

    Returns:
        TechniqueConfiguration: Safe settings and display-only read-only explanations.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    metadata = AttackRegistry.get_registry_singleton().get_class_metadata(factory.attack_class)
    parameters = registry.get_editable_parameters(factory)
    values = registry.get_editable_settings(factory)
    effective = factory.get_effective_attack_kwargs()
    values["params"] = {
        parameter.name: project_parameter_value(
            parameter=parameter, value=effective.get(parameter.name, parameter.default)
        )
        for parameter in parameters
    }
    readonly = {"advanced configuration": "Requirements, seed metadata, scorer and feedback policy stay unchanged."}
    parameter_names = {parameter.name for parameter in parameters}
    for parameter in registry.get_constructor_parameters(factory):
        if parameter.name not in parameter_names:
            value = effective.get(parameter.name, parameter.default)
            readonly[parameter.name] = f"{type(value).__name__}: retained on the source; not editable."
    accepted = {parameter.name for parameter in metadata.parameters}
    if factory.uses_adversarial and "adversarial_chat" not in values:
        readonly["adversarial_chat"] = "Unregistered live target; retained unchanged."
    if "attack_adversarial_config" in accepted:
        for key in ("adversarial_system_prompt", "adversarial_seed_prompt", "adversarial_prompt_template"):
            if key not in values:
                readonly[key] = "Opaque prompt; retained unchanged."
    if "attack_converter_config" in accepted:
        for key in ("request_converters", "response_converters"):
            if key not in values:
                readonly[key] = "Conditional or unregistered converter chain; order and settings stay unchanged."
    if factory.seed_technique is not None and "simulation" not in values:
        readonly["technique seeds"] = "Existing seed group and prompt templates stay unchanged."
    return TechniqueConfiguration(
        source_name=name,
        source_identity=source_identity,
        attack_type=metadata.registry_name,
        settings=TechniqueSettings.model_validate(values),
        parameters=parameters,
        editable_fields=[key for key in values if key != "params"],
        supplied_parameters=list(factory.get_effective_settings().attack_kwargs),
        read_only=readonly,
        supports_prepended_conversation=factory.supports_prepended_conversation,
    )
