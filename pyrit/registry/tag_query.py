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
from typing import Protocol, TypeVar, runtime_checkable


@runtime_checkable
class Taggable(Protocol):
    """Any object that exposes a ``tags`` attribute."""

    @property
    def tags(self) -> list[str]:  # noqa: D102
        ...


_T = TypeVar("_T", bound=Taggable)

_VALID_OPS = frozenset({"", "and", "or", "not"})


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

    def __post_init__(self) -> None:
        """
        Validate composite TagQuery invariants.

        Raises:
            ValueError: If the operator or children are inconsistent.
        """
        if self._op not in _VALID_OPS:
            raise ValueError(f"Invalid TagQuery op {self._op!r}; must be one of {sorted(_VALID_OPS)}")
        if self._op == "not" and len(self._children) != 1:
            raise ValueError("'not' TagQuery must have exactly 1 child")
        if self._op in ("and", "or") and len(self._children) < 2:
            raise ValueError(f"'{self._op}' TagQuery must have at least 2 children")
        if self._op == "" and self._children:
            raise ValueError("Leaf TagQuery must not have children")

    # ------------------------------------------------------------------
    # Operators
    # ------------------------------------------------------------------

    def __and__(self, other: TagQuery) -> TagQuery:
        """
        Both sub-queries must match.

        Returns:
            TagQuery: A composite AND query.
        """
        return TagQuery(_op="and", _children=(self, other))

    def __or__(self, other: TagQuery) -> TagQuery:
        """
        Either sub-query must match.

        Returns:
            TagQuery: A composite OR query.
        """
        return TagQuery(_op="or", _children=(self, other))

    def __invert__(self) -> TagQuery:
        """
        Negate: matches when the inner query does **not** match.

        Returns:
            TagQuery: A composite NOT query.
        """
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
        return not (self.include_any and not self.include_any & tags)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def filter(self, items: list[_T]) -> list[_T]:
        """
        Return *items* whose tags satisfy this query.

        Args:
            items: Objects with a ``tags`` attribute.

        Returns:
            Filtered list preserving original order.
        """
        return [item for item in items if self.matches(set(item.tags))]
