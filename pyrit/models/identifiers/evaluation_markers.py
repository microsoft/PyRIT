# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Field-level evaluation markers for strongly-typed identifiers.

These markers are attached to identifier fields via ``typing.Annotated``
metadata and declare — on the identifier itself — what feeds the *eval hash*
(behavioral equivalence), as opposed to the full identity hash. The
``evaluation_identifier`` module derives the eval engine's per-child rules from
these markers, so the typed identifier classes are the single source of truth.

Usage::

    class TargetIdentifier(ComponentIdentifier):
        endpoint: Annotated[str | None, Evaluate.Exclude()] = None
        underlying_model_name: Annotated[str | None, Evaluate.Include(fallback="model_name")] = None
        temperature: Annotated[float | None, Evaluate.Include()] = None
        targets: Annotated[list["TargetIdentifier"], Evaluate.Unwrap()] = Field(default_factory=list)

Semantics:

* On a **scalar (param) field**:
  * ``Evaluate.Include()`` — keep this param in the eval hash (the default for an
    unmarked field). ``fallback`` names another param whose value is substituted
    when this one is missing or empty.
  * ``Evaluate.Exclude()`` — drop this param from the eval hash.
* On a **child (identifier) field**:
  * ``Evaluate.Include()`` — include the child, projecting its subtree with the
    child type's own markers (the default for an unmarked field). ``only_params``
    overrides that projection for this slot, restricting the child subtree to the
    named params (propagating downward).
  * ``Evaluate.Exclude()`` — drop the child entirely from the eval hash.
  * ``Evaluate.Unwrap()`` — mark a wrapper passthrough slot. When an identifier of
    the owning type is projected as a behavioral child, the eval hash "looks
    through" this slot and substitutes its first element (e.g. ``RoundRobinTarget``
    → its inner target). Affects the eval hash only; the identity hash keeps the
    wrapper distinct.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalMarker:
    """Base class for all ``Evaluate.*`` field markers."""


@dataclass(frozen=True)
class Include(EvalMarker):
    """
    Include a field in the eval hash.

    Args:
        fallback (str | None): For a param field, the name of another param whose
            value is used when this param is missing or an empty string. ``None``
            means no fallback.
        only_params (frozenset[str] | None): For a child field, restrict the
            child's subtree to these param names (overriding the child type's own
            projection and propagating downward). ``None`` means use the child
            type's own projection.
    """

    fallback: str | None = None
    only_params: frozenset[str] | None = None


@dataclass(frozen=True)
class Exclude(EvalMarker):
    """Drop a field (param or child) from the eval hash."""


@dataclass(frozen=True)
class Unwrap(EvalMarker):
    """
    Mark a wrapper passthrough child slot.

    Declared on the list-valued child field that holds inner components of a
    wrapper (e.g. ``TargetIdentifier.targets``). When an identifier of the owning
    type is projected as a behavioral child — or as an eval root — the eval hash
    substitutes the first element of this slot in place of the wrapper.
    """


class Evaluate:
    """Namespace for the field-level evaluation markers (see module docstring)."""

    Include = Include
    Exclude = Exclude
    Unwrap = Unwrap
