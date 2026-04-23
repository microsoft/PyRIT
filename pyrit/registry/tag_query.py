# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Composable tag-based query predicates.

``TagQuery`` is a frozen dataclass that expresses AND / OR predicates
over string tag sets. Leaf instances test directly against a tag set;
composite instances are built with the ``&`` (AND) and ``|`` (OR) operators.

Examples::

    # Classmethod shortcuts (preferred)
    q = TagQuery.all("core", "single_turn")
    q = TagQuery.any_of("single_turn", "multi_turn")
    q = TagQuery.exclude("deprecated")

    # Composition via operators
    q = TagQuery.all("A") & TagQuery.any_of("B", "C")     # A AND (B OR C)
    q = (q1 | q2) & q3                                     # arbitrary nesting

    # Constructor form (also accepts plain sets)
    q = TagQuery(include_all={"core", "single_turn"})

The class is **registry-agnostic** — it works with any collection whose
items expose a ``tags`` attribute (``list[str]`` or ``set[str]``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class Taggable(Protocol):
    """Any object that exposes a ``tags`` attribute."""

    @property
    def tags(self) -> list[str]:  # noqa: D102
        ...


_T = TypeVar("_T", bound=Taggable)

_VALID_OPS = frozenset({"", "and", "or"})
_OP_FUNC: dict[str, Callable[..., bool]] = {"and": all, "or": any}


@dataclass(frozen=True)
class TagQuery:
    """
    Boolean predicate over string tag sets.

    Leaf fields (``include_all``, ``include_any``, ``exclude``) are evaluated
    against a tag set directly.  Composite queries are produced by the ``&``
    and ``|`` operators and stored in ``_op`` / ``_children``.

    Prefer the classmethod shortcuts :meth:`all`, :meth:`any_of`, and
    :meth:`exclude` for single-field leaves.

    Args:
        include_all: Tags that must **all** be present (AND).
        include_any: Tags of which **at least one** must be present (OR).
        exclude_tags: Tags that must **not** be present.
    """

    include_all: frozenset[str] = frozenset()
    include_any: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()

    _op: str = field(default="", repr=False)
    _children: tuple[TagQuery, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        """
        Coerce set fields to frozenset and validate composite invariants.

        Raises:
            ValueError: If the operator or children are inconsistent.
        """
        # Accept plain sets for convenience; coerce to frozenset for immutability.
        for attr in ("include_all", "include_any", "exclude_tags"):
            val = getattr(self, attr)
            if not isinstance(val, frozenset):
                object.__setattr__(self, attr, frozenset(val))

        if self._op not in _VALID_OPS:
            raise ValueError(f"Invalid TagQuery op {self._op!r}; must be one of {sorted(_VALID_OPS)}")
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

    # ------------------------------------------------------------------
    # Classmethod constructors
    # ------------------------------------------------------------------

    @classmethod
    def all(cls, *tags: str) -> TagQuery:
        """
        Leaf query: every tag must be present.

        Returns:
            A TagQuery that matches when all given tags are present.
        """
        return cls(include_all=frozenset(tags))

    @classmethod
    def any_of(cls, *tags: str) -> TagQuery:
        """
        Leaf query: at least one tag must be present.

        Returns:
            A TagQuery that matches when at least one given tag is present.
        """
        return cls(include_any=frozenset(tags))

    @classmethod
    def none_of(cls, *tags: str) -> TagQuery:
        """
        Leaf query: none of the given tags may be present.

        Returns:
            A TagQuery that matches when none of the given tags are present.
        """
        return cls(exclude_tags=frozenset(tags))

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
        if self._op:
            return _OP_FUNC[self._op](c.matches(tags) for c in self._children)
        return self._matches_leaf(tags)

    def _matches_leaf(self, tags: set[str] | frozenset[str]) -> bool:
        if self.exclude_tags and self.exclude_tags & tags:
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
