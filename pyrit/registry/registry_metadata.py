# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared metadata base type for PyRIT registries.

``RegistryMetadata`` is the minimal base every registry metadata dataclass
extends, carrying the common fields used for display, lookup, and filtering.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.models.parameter import Parameter


@dataclass(frozen=True)
class RegistryMetadata:
    """
    Minimal base for class-level registry metadata.

    Provides the common fields every registry metadata type needs for display,
    lookup, and filtering in class registries.

    Attributes:
        class_name (str): Python class name (e.g., "ContentHarmsScenario").
        class_module (str): Full module path (e.g., "pyrit.scenario.scenarios.content_harms").
        class_description (str): Human-readable description, typically from the class docstring.
        registry_name (str): The suffix-stripped snake_case key used in the registry
            (e.g., "content_harms" for ContentHarmsScenario).
        parameters (tuple[Parameter, ...]): The derived build contract for the class.
            Buildable registries (e.g. converters) populate this from the constructor
            signature; scenarios/initializers use their own ``supported_parameters``
            today and will migrate to this unified shape.
        class_attributes (Mapping[str, Any]): Values sourced from class attributes
            (declared on the identifier via ``Param.ClassAttr``), letting the entry
            describe class-level facts — e.g. a converter's supported input/output
            types — without constructing an instance. Empty for entries with none.
    """

    class_name: str
    class_module: str
    class_description: str = ""
    registry_name: str = ""
    parameters: tuple[Parameter, ...] = field(kw_only=True, default=())
    class_attributes: Mapping[str, Any] = field(kw_only=True, default_factory=dict)

    @staticmethod
    def description_from_docstring(cls: type, *, fallback: str = "") -> str:
        """
        Extract a normalized description from a class docstring.

        Collapses all whitespace into single spaces. Returns fallback if
        no docstring is present or the docstring is empty after cleaning.

        Returns:
            str: The cleaned docstring or the fallback value.
        """
        doc = cls.__doc__ or ""
        cleaned = " ".join(doc.split())
        return cleaned or fallback

    @staticmethod
    def summary_from_docstring(cls: type) -> str:
        """
        Extract a short summary from the first paragraph of a class docstring.

        Uses the class's own docstring only (never an inherited one), normalizes
        indentation, and collapses the first paragraph's whitespace onto one line.
        Empty when the class has no docstring. This is the catalog-display
        counterpart to ``description_from_docstring`` (which collapses the whole
        docstring); buildable registries populate ``class_description`` from this
        first-paragraph form.

        Returns:
            str: The first-paragraph summary, or "" when there is no docstring.
        """
        raw = cls.__doc__
        if not raw:
            return ""
        first_paragraph = inspect.cleandoc(raw).split("\n\n", 1)[0]
        return " ".join(first_paragraph.split())
