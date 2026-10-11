# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Safe creation-call display for registered technique factories."""

from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pyrit.backend.models.techniques import TechniqueInstance
from pyrit.models import Identifiable, SeedPrompt
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
