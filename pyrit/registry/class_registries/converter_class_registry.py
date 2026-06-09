# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter class registry for PyRIT.

Discovers ``PromptConverter`` subclasses from ``pyrit.prompt_converter`` and
builds configured instances on demand. This is the canonical home for the
"build a converter from a type name + params" logic, so it can be reused by any
caller (e.g. an attack strategy or agent that builds converters on the fly).

Unlike ``ConverterRegistry`` (an object registry that stores pre-built
instances), this is a class registry: it stores classes and introspects /
instantiates them on demand.
"""

from __future__ import annotations

import inspect
import logging
import re
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Union, get_args, get_origin

from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.class_registries.base_class_registry import (
    BaseClassRegistry,
    ClassEntry,
)

if TYPE_CHECKING:
    from pyrit.prompt_converter import PromptConverter

logger = logging.getLogger(__name__)

# Scalar Python types whose string values can be coerced to the real type in
# ``create_instance`` / ``_coerce_params``.
_SIMPLE_TYPES: set[type] = {str, int, float, bool}


def get_union_non_none_args(annotation: Any) -> list[Any] | None:
    """
    Return the non-``None`` members of a union annotation, or None if not a union.

    Handles both ``typing.Union[X, None]`` and PEP 604 ``X | None``. This is a
    general type-introspection utility (not presentation), reused by coercion,
    LLM-target detection, and callers that need to render a type.

    Args:
        annotation (Any): The type annotation to inspect.

    Returns:
        list[Any] | None: The non-None union members, or None when the annotation
        is not a union.
    """
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        return [a for a in get_args(annotation) if a is not type(None)]
    return None


class ConverterParameterMetadata(NamedTuple):
    """
    A converter constructor parameter described for dynamic construction.

    Carries raw introspection data so callers can build converters on the fly.
    ``annotation`` is the parameter's raw type annotation; rendering it to a
    human-readable string is a presentation concern left to the caller.
    ``coercible_from_string`` is True when a string value can be coerced to the
    annotated type (the registry coerces these in ``create_instance``).
    ``requires_llm`` is True when the parameter expects a ``PromptTarget`` (i.e.
    the converter performs an LLM-based transformation).

    NamedTuple so consumers can read fields by name while the value stays
    immutable (safe to cache inside a frozen ``ConverterMetadata``).
    """

    name: str
    annotation: Any
    required: bool
    default_value: str | None
    choices: tuple[str, ...] | None
    description: str | None
    coercible_from_string: bool
    requires_llm: bool


@dataclass(frozen=True)
class ConverterMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered ``PromptConverter`` class.

    Use ``ConverterClassRegistry.get_class()`` to get the actual class or
    ``create_instance()`` to build a configured instance.
    """

    # Input data types the converter accepts (stringified PromptDataType values).
    supported_input_types: tuple[str, ...] = field(kw_only=True, default=())

    # Output data types the converter produces (stringified PromptDataType values).
    supported_output_types: tuple[str, ...] = field(kw_only=True, default=())

    # Simple constructor parameters suitable for dynamic form generation.
    parameters: tuple[ConverterParameterMetadata, ...] = field(kw_only=True, default=())

    # Whether the converter requires an LLM target.
    is_llm_based: bool = field(kw_only=True, default=False)


def _is_simple_type(annotation: Any) -> bool:
    """
    Return True if a string value can be coerced to the annotated type.

    Covers the scalar types in ``_SIMPLE_TYPES`` (str/int/float/bool),
    ``Literal`` annotations, and an ``Optional`` wrapping one of those.

    Returns:
        bool: True if the annotation is coercible from a string, False otherwise.
    """
    if annotation in _SIMPLE_TYPES:
        return True
    if get_origin(annotation) is Literal:
        return True
    non_none = get_union_non_none_args(annotation)
    if non_none is not None:
        return len(non_none) == 1 and _is_simple_type(non_none[0])
    return False


def _is_llm_target_annotation(annotation: Any) -> bool:
    """
    Return True if the annotation is a ``PromptTarget`` (or subclass).

    Handles unioned forms such as ``PromptTarget | None``. A converter parameter
    with such an annotation indicates the converter performs an LLM-based
    transformation.

    Returns:
        bool: True if the annotation expects a ``PromptTarget``, False otherwise.
    """
    if annotation is inspect.Parameter.empty:
        return False

    from pyrit.prompt_target import PromptTarget

    candidates = get_union_non_none_args(annotation)
    if candidates is None:
        candidates = [annotation]
    for candidate in candidates:
        try:
            if isinstance(candidate, type) and issubclass(candidate, PromptTarget):
                return True
        except TypeError:
            continue
    return False


def _parse_arg_descriptions(converter_class: type) -> dict[str, str]:
    """
    Parse parameter descriptions from a Google-style docstring Args section.

    Returns:
        dict[str, str]: Mapping of parameter names to their descriptions.
    """
    doc = (converter_class.__init__.__doc__ or converter_class.__doc__ or "").strip()
    match = re.search(r"Args:\s*\n(.*?)(?:\n\s*\n|\n\s*Returns:|\n\s*Raises:|\Z)", doc, re.DOTALL)
    if not match:
        return {}
    args_block = match.group(1)
    # Detect indentation of first parameter line
    indent_match = re.match(r"^(\s+)", args_block)
    indent = indent_match.group(1) if indent_match else r"\s+"
    pattern = rf"^{indent}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+?)(?=\n{indent}\w|\Z)"
    descriptions: dict[str, str] = {}
    for m in re.finditer(pattern, args_block, re.DOTALL | re.MULTILINE):
        descriptions[m.group(1)] = " ".join(m.group(2).split())
    return descriptions


def _extract_parameters(converter_class: type) -> tuple[ConverterParameterMetadata, ...]:
    """
    Extract constructor parameters from a converter class.

    Surfaces every settable constructor parameter (excluding ``self`` and
    var-args) so a caller has the full picture for dynamic construction. Each
    parameter records its raw ``annotation`` and a ``coercible_from_string`` flag
    indicating whether the registry can coerce a string value to its type.

    Returns:
        tuple[ConverterParameterMetadata, ...]: The constructor parameters.
    """
    try:
        sig = inspect.signature(converter_class.__init__)
    except (ValueError, TypeError):
        return ()

    arg_descriptions = _parse_arg_descriptions(converter_class)

    params: list[ConverterParameterMetadata] = []
    for name, p in sig.parameters.items():
        if name in ("self", "args", "kwargs"):
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        no_default = p.default is inspect.Parameter.empty
        is_sentinel = hasattr(p.default, "__class__") and "Sentinel" in type(p.default).__name__
        required = no_default or is_sentinel

        default_value: str | None = None
        if not required and p.default is not None:
            default_value = str(p.default)

        choices: tuple[str, ...] | None = None
        choice_annotation = p.annotation
        non_none_choice = get_union_non_none_args(choice_annotation)
        if non_none_choice is not None and len(non_none_choice) == 1:
            choice_annotation = non_none_choice[0]
        if get_origin(choice_annotation) is Literal:
            choices = tuple(str(a) for a in get_args(choice_annotation))

        params.append(
            ConverterParameterMetadata(
                name=name,
                annotation=p.annotation,
                required=required,
                default_value=default_value,
                choices=choices,
                description=arg_descriptions.get(name),
                coercible_from_string=_is_simple_type(p.annotation),
                requires_llm=_is_llm_target_annotation(p.annotation),
            )
        )

    return tuple(params)


def _coerce_params(*, converter_class: type, params: dict[str, Any]) -> dict[str, Any]:
    """
    Coerce parameter values to match the converter's ``__init__`` type annotations.

    Callers may send all values as strings (e.g. a form or an agent); this
    converts them to int, float, or bool as needed based on the constructor
    signature.

    Args:
        converter_class (type): The converter class whose ``__init__`` signature
            drives coercion.
        params (dict[str, Any]): The raw parameter values.

    Returns:
        dict[str, Any]: Params with values coerced to the expected types.

    Raises:
        ValueError: If the signature cannot be inspected or a value cannot be
            coerced to the annotated type.
    """
    try:
        sig = inspect.signature(converter_class.__init__)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to inspect __init__ signature for converter '{converter_class.__name__}': {e}") from e

    coerced = dict(params)
    for name, value in coerced.items():
        if name not in sig.parameters or not isinstance(value, str):
            continue
        annotation = sig.parameters[name].annotation
        if annotation is inspect.Parameter.empty:
            continue

        # Unwrap X | None (or Optional[X]) to X
        non_none = get_union_non_none_args(annotation)
        if non_none is not None and len(non_none) == 1:
            annotation = non_none[0]

        try:
            if annotation is int:
                coerced[name] = int(value)
            elif annotation is float:
                coerced[name] = float(value)
            elif annotation is bool:
                lowered = value.strip().lower()
                if lowered in ("true", "1", "yes"):
                    coerced[name] = True
                elif lowered in ("false", "0", "no"):
                    coerced[name] = False
                else:
                    raise ValueError(f"cannot interpret {value!r} as a boolean")
        except (ValueError, TypeError) as e:
            raise ValueError(f"Parameter '{name}' expects {annotation.__name__}, got {value!r}") from e

    return coerced


class ConverterClassRegistry(BaseClassRegistry["PromptConverter", ConverterMetadata]):
    """
    Registry for discovering and building ``PromptConverter`` instances.

    Discovers all concrete ``PromptConverter`` subclasses exported from
    ``pyrit.prompt_converter`` and registers them keyed by their exact class
    name (e.g. ``"Base64Converter"``). Provides parameter/catalog introspection
    via ``list_metadata()`` and on-demand construction via ``create_instance()``.

    Constructed converters are NOT stored anywhere by this registry; storing
    lifecycle-managed instances is the job of ``ConverterRegistry`` (the object
    registry) or the caller.
    """

    def _get_registry_name(self, cls: type) -> str:
        """
        Use the exact class name as the registry key.

        Converters are referenced by their class name (e.g. ``"Base64Converter"``)
        rather than the snake_case default used by other class registries.

        Returns:
            str: The class name.
        """
        return cls.__name__

    def _discover(self) -> None:
        """Discover all concrete ``PromptConverter`` subclasses from ``pyrit.prompt_converter``."""
        from pyrit import prompt_converter
        from pyrit.prompt_converter import PromptConverter

        for name in prompt_converter.__all__:
            cls = getattr(prompt_converter, name, None)
            if cls is None or not isinstance(cls, type):
                continue
            if not issubclass(cls, PromptConverter) or cls is PromptConverter:
                continue
            self._class_entries[name] = ClassEntry(registered_class=cls)
            logger.debug(f"Registered converter class: {name}")

    def _build_metadata(self, name: str, entry: ClassEntry[PromptConverter]) -> ConverterMetadata:
        """
        Build metadata for a ``PromptConverter`` class.

        Args:
            name (str): The registry name (exact class name) of the converter.
            entry (ClassEntry[PromptConverter]): The class entry being described.

        Returns:
            ConverterMetadata: Metadata describing the converter class.
        """
        converter_class = entry.registered_class

        # First paragraph of the docstring as a short description.
        raw_doc = (converter_class.__doc__ or "").strip()
        description = raw_doc.split("\n\n")[0].replace("\n", " ").strip()

        supported_input_types = tuple(str(dt) for dt in getattr(converter_class, "SUPPORTED_INPUT_TYPES", ()))
        supported_output_types = tuple(str(dt) for dt in getattr(converter_class, "SUPPORTED_OUTPUT_TYPES", ()))

        parameters = _extract_parameters(converter_class)

        return ConverterMetadata(
            class_name=converter_class.__name__,
            class_module=converter_class.__module__,
            class_description=description,
            registry_name=name,
            supported_input_types=supported_input_types,
            supported_output_types=supported_output_types,
            parameters=parameters,
            is_llm_based=any(p.requires_llm for p in parameters),
        )

    def get_converter_class(self, *, converter_type: str) -> type[PromptConverter]:
        """
        Resolve a converter class by its exact class name.

        Args:
            converter_type (str): The exact class name (e.g. ``"Base64Converter"``).

        Returns:
            type[PromptConverter]: The converter class.

        Raises:
            ValueError: If the converter type is not registered.
        """
        self._ensure_discovered()
        entry = self._class_entries.get(converter_type)
        if entry is None:
            raise ValueError(
                f"Converter type '{converter_type}' not found. Available types: {sorted(self._class_entries.keys())}"
            )
        return entry.registered_class

    def create_instance(self, name: str, **kwargs: object) -> PromptConverter:
        """
        Build a configured converter instance by class name.

        Overrides the base implementation to coerce string parameter values to
        their annotated types (int/float/bool), so a caller passing values as
        strings (e.g. an agent or a form) gets a correctly-typed converter. The
        returned instance is not registered anywhere; the caller decides whether
        and where to store it.

        Args:
            name (str): The exact converter class name (e.g. ``"Base64Converter"``).
            **kwargs (object): Constructor parameters. String values are coerced
                to their annotated types.

        Returns:
            PromptConverter: The constructed converter instance.

        Raises:
            ValueError: If the converter type is not registered or a parameter
                cannot be coerced.
        """
        self._ensure_discovered()
        entry = self._class_entries.get(name)
        if entry is None:
            raise ValueError(
                f"Converter type '{name}' not found. Available types: {sorted(self._class_entries.keys())}"
            )
        resolved = _coerce_params(converter_class=entry.registered_class, params=dict(kwargs))
        return entry.create_instance(**resolved)
