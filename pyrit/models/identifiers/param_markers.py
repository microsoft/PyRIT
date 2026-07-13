# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Field-level *build* markers for strongly-typed identifiers.

These markers are attached to identifier fields via ``typing.Annotated``
metadata and declare — on the identifier itself — how each field maps to the
component's constructor. They are the build-time counterpart to the
``Evaluate.*`` markers (see ``evaluation_markers``): where ``Evaluate.*``
governs the eval hash, ``Param.*`` governs how the registry derives the
``Parameter`` list and resolves constructor arguments.

Usage::

    class ConverterIdentifier(ComponentIdentifier):
        supported_input_types: Annotated[
            list[PromptDataType] | None, Evaluate.Include(), Param.ClassAttr()
        ] = None
        converter_target: Annotated[
            TargetIdentifier | None, Evaluate.Include(), Param.Include()
        ] = None

Semantics (a field is, by default, an included constructor parameter named after
the field):

* ``Param.Exclude()`` — the field is part of identity/eval but is **not** a
  constructor input (e.g. a composite child slot with no 1:1 constructor arg).
* ``Param.ClassAttr(attr_name=...)`` — like ``Exclude`` (not a constructor input),
  but additionally declares that the field's value, when describing the *class*,
  is sourced from a class attribute. ``attr_name`` names that attribute; when
  omitted it defaults to the field name upper-cased (e.g. the
  ``supported_input_types`` field reads ``SUPPORTED_INPUT_TYPES``). A registry can
  read these off the class without constructing an instance.
* ``Param.Include(alias=...)`` — the field **is** a constructor parameter. Whether
  it is a coerced value or a registry **reference** is inferred from the field's
  type: a child-identifier type (e.g. ``TargetIdentifier``) is resolved by name
  from that kind's registry, while any other type is coerced from its raw value.
  ``alias`` names the constructor arg when it differs from the identifier field
  name.

An unmarked field behaves like ``Param.Include()`` with no alias.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParamMarker:
    """Base class for all ``Param.*`` field markers."""


@dataclass(frozen=True)
class ExcludeMarker(ParamMarker):
    """Mark an identity/eval field that is **not** a constructor parameter."""


@dataclass(frozen=True)
class ClassAttrMarker(ParamMarker):
    """
    Mark an identity/eval field whose value is sourced from a class attribute.

    Like ``ExcludeMarker``, the field is **not** a constructor parameter. In
    addition, it declares that the value (when describing the *class*, with no
    configured instance) can be read off a class attribute, so a registry can
    populate it without constructing an instance.

    Args:
        attr_name (str | None): The class attribute name to read. ``None`` means
            use the field name upper-cased (e.g. ``supported_input_types`` →
            ``SUPPORTED_INPUT_TYPES``).
    """

    attr_name: str | None = None


@dataclass(frozen=True)
class IncludeMarker(ParamMarker):
    """
    Mark an identity/eval field that **is** a constructor parameter.

    Whether the parameter is a plain coerced value or a registry **reference** is
    inferred from the field's type: a child-identifier type (e.g.
    ``TargetIdentifier``) is resolved by name from that kind's registry, while any
    other type is coerced from its raw value.

    Args:
        alias (str | None): The constructor arg name, when it differs from the
            identifier field name. ``None`` means use the field name.
    """

    alias: str | None = None


class Param:
    """Namespace for the field-level build markers (see module docstring)."""

    Exclude = ExcludeMarker
    ClassAttr = ClassAttrMarker
    Include = IncludeMarker
