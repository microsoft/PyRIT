# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter registry for PyRIT.

A single registry for ``PromptConverter`` that both:

- **builds** converters from a type name plus arguments — discovering converter
  classes, introspecting their constructor parameters, and constructing instances
  via the shared resolver (so LLM converters can be built by passing a
  ``converter_target`` registry name), and
- **holds** pre-configured converter instances registered via initializers or the
  backend.

It is a ``BuildableRegistry``: the registry's own surface (``get_class``,
``get_class_names``, ``list_class_metadata``, ``create_instance``) is the buildable
class catalog. Pre-configured instances live under the ``instances`` property
(``register``, ``get``, ``get_all_instances``, ``get_names``), a
``DefaultInstanceRegistry``.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, get_args, get_origin

from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.buildable_registry import BuildableRegistry
from pyrit.registry.class_registries.base_class_registry import ClassEntry
from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.resolution import get_union_non_none_args, is_coercible_from_string

if TYPE_CHECKING:
    from pyrit.prompt_converter import PromptConverter

logger = logging.getLogger(__name__)


def _prompt_converter_type() -> type[PromptConverter]:
    """
    Return the ``PromptConverter`` base class, importing it lazily.

    Used as the ``instance_type`` for the registry's ``instances`` container so
    a non-converter cannot be registered, without importing the converter
    package at module load (which would defeat lazy discovery).

    Returns:
        type[PromptConverter]: The ``PromptConverter`` base class.
    """
    from pyrit.prompt_converter import PromptConverter

    return PromptConverter


class _ConverterParameterMetadata(NamedTuple):
    """
    A converter constructor parameter described for dynamic construction.

    .. note::
        Transitional / internal. This bespoke shape is replaced by the unified
        ``pyrit.common.parameter.Parameter`` contract on ``ClassRegistryEntry`` in
        Phase 3 of the registry refactor and will be deleted then. Read the values
        if you must, but do not build new APIs around this type or import it as a
        stable public symbol.

    Carries raw introspection data so callers can build converters on the fly.
    ``annotation`` is the parameter's raw type annotation; rendering it to a
    human-readable string is a presentation concern left to the caller.
    ``coercible_from_string`` is True when a string value can be coerced to the
    annotated type. ``requires_llm`` is True when the parameter expects a
    ``PromptTarget`` (i.e. the converter performs an LLM-based transformation).

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

    Use ``ConverterRegistry.get_class()`` to get the actual class or
    ``create_instance()`` to build a configured instance.
    """

    # Input data types the converter accepts (stringified PromptDataType values).
    supported_input_types: tuple[str, ...] = field(kw_only=True, default=())

    # Output data types the converter produces (stringified PromptDataType values).
    supported_output_types: tuple[str, ...] = field(kw_only=True, default=())

    # Simple constructor parameters suitable for dynamic form generation.
    # Transitional element type — replaced by ``Parameter`` in Phase 3.
    parameters: tuple[_ConverterParameterMetadata, ...] = field(kw_only=True, default=())

    # Whether the converter requires an LLM target.
    is_llm_based: bool = field(kw_only=True, default=False)


def _requires_llm_target(annotation: Any) -> bool:
    """
    Return True if the annotation expects a ``PromptTarget`` (or subclass).

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


def _extract_parameters(converter_class: type) -> tuple[_ConverterParameterMetadata, ...]:
    """
    Extract constructor parameters from a converter class.

    Surfaces every settable constructor parameter (excluding ``self`` and
    var-args) so a caller has the full picture for dynamic construction. Each
    parameter records its raw ``annotation`` and a ``coercible_from_string`` flag
    indicating whether a string value can be coerced to its type.

    Returns:
        tuple[_ConverterParameterMetadata, ...]: The constructor parameters.
    """
    try:
        sig = inspect.signature(converter_class.__init__)
    except (ValueError, TypeError):
        return ()

    arg_descriptions = _parse_arg_descriptions(converter_class)

    params: list[_ConverterParameterMetadata] = []
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
            _ConverterParameterMetadata(
                name=name,
                annotation=p.annotation,
                required=required,
                default_value=default_value,
                choices=choices,
                description=arg_descriptions.get(name),
                coercible_from_string=is_coercible_from_string(p.annotation),
                requires_llm=_requires_llm_target(p.annotation),
            )
        )

    return tuple(params)


class ConverterRegistry(BuildableRegistry["PromptConverter", ConverterMetadata]):
    """
    Registry that discovers, builds, and holds ``PromptConverter`` instances.

    Discovers all concrete ``PromptConverter`` subclasses exported from
    ``pyrit.prompt_converter`` (keyed by their exact class name, e.g.
    ``"Base64Converter"``) for the buildable catalog. Pre-configured instances
    registered via initializers or the backend are held under the ``instances``
    property.

    Building a converter resolves its arguments through the shared resolver, so
    LLM converters can be constructed by passing a ``converter_target`` that names
    a target in the ``TargetRegistry``.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately.
        """
        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[PromptConverter] = DefaultInstanceRegistry(
            instance_type=_prompt_converter_type
        )

    def _get_registry_name(self, cls: type) -> str:
        """
        Use the exact class name as the catalog key.

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
        Build catalog metadata for a ``PromptConverter`` class.

        Args:
            name (str): The catalog name (exact class name) of the converter.
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
