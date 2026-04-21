# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Composable tag-based query predicates.

``TagQuery`` is a frozen dataclass that expresses AND / OR / NOT predicates
over string tag sets. Leaf instances test directly against a tag set;
composite instances are built with the ``&`` (AND), ``|`` (OR), and ``~``
(NOT) operators.

Examples::

    # Simple leaves
    q = TagQuery(include_all=frozenset({"core", "single_turn"}))   # A AND B
    q = TagQuery(include_any=frozenset({"single_turn", "multi_turn"}))  # A OR B
    q = TagQuery(exclude=frozenset({"deprecated"}))                # NOT deprecated

    # Composition via operators
    q = TagQuery(include_all=frozenset({"A"})) & TagQuery(include_any=frozenset({"B", "C"}))  # A AND (B OR C)
    q = (q1 | q2) & q3   # arbitrary nesting
    q = ~TagQuery(include_all=frozenset({"deprecated"}))  # invert

The class is **registry-agnostic** — it works with any collection whose
items expose a ``tags`` attribute (``list[str]`` or ``set[str]``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Taggable(Protocol):
    """Any object that exposes a ``tags`` attribute."""

    tags: list[str]


@dataclass(frozen=True)
class TagQuery:
    """
    Boolean predicate over string tag sets.

    Leaf fields (``include_all``, ``include_any``, ``exclude``) are evaluated
    against a tag set directly.  Composite queries are produced by the ``&``,
    ``|``, and ``~`` operators and stored in ``_op`` / ``_children``.

    Args:
        include_all: Tags that must **all** be present (AND).
        include_any: Tags of which **at least one** must be present (OR).
        exclude: Tags that must **not** be present (NOT).
    """

    include_all: frozenset[str] = frozenset()
    include_any: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()

    _op: str = field(default="", repr=False)
    _children: tuple[TagQuery, ...] = field(default=(), repr=False)

    # ------------------------------------------------------------------
    # Operators
    # ------------------------------------------------------------------

    def __and__(self, other: TagQuery) -> TagQuery:
        """Both sub-queries must match."""
        return TagQuery(_op="and", _children=(self, other))

    def __or__(self, other: TagQuery) -> TagQuery:
        """Either sub-query must match."""
        return TagQuery(_op="or", _children=(self, other))

    def __invert__(self) -> TagQuery:
        """Negate: matches when the inner query does **not** match."""
        return TagQuery(_op="not", _children=(self,))

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def matches(self, tags: set[str] | frozenset[str]) -> bool:
        """
        Return ``True`` if *tags* satisfies this query.

        Args:
            tags: The tag set to test.

        Returns:
            Whether the tag set matches.
        """
        if self._op == "and":
            return all(c.matches(tags) for c in self._children)
        if self._op == "or":
            return any(c.matches(tags) for c in self._children)
        if self._op == "not":
            return not self._children[0].matches(tags)
        return self._matches_leaf(tags)

    def _matches_leaf(self, tags: set[str] | frozenset[str]) -> bool:
        if self.exclude and self.exclude & tags:
            return False
        if self.include_all and not self.include_all <= tags:
            return False
        if self.include_any and not self.include_any & tags:
            return False
        return True

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def filter(self, items: list[Taggable]) -> list[Taggable]:
        """
        Return *items* whose tags satisfy this query.

        Args:
            items: Objects with a ``tags`` attribute.

        Returns:
            Filtered list preserving original order.
        """
        return [item for item in items if self.matches(set(item.tags))]
