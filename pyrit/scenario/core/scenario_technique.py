# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base class for scenario attack techniques with group-based aggregation.

This module provides a generic base class for creating enum-based attack technique
hierarchies where techniques can be grouped by categories (e.g., complexity, encoding type)
and automatically expanded during scenario initialization.
"""

from __future__ import annotations

from enum import Enum, EnumMeta
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.common.deprecation import print_deprecation_message

if TYPE_CHECKING:
    from collections.abc import Sequence

# TypeVar for the enum subclass itself
T = TypeVar("T", bound="ScenarioTechnique")


class _DeprecatedEnumMeta(EnumMeta):
    """
    Custom Enum metaclass that supports deprecated member aliases.

    Subclasses of ScenarioTechnique can define deprecated member name mappings
    by setting ``__deprecated_members__`` on the class after definition.
    Each entry maps the old name to a ``(new_name, removed_in)`` tuple::

        MyTechnique.__deprecated_members__ = {"OLD_NAME": ("NewName", "0.15.0")}

    Accessing ``MyTechnique.OLD_NAME`` will emit a DeprecationWarning and return
    the same enum member as ``MyTechnique.NewName``.
    """

    def __getattr__(cls, name: str) -> Any:
        deprecated = cls.__dict__.get("__deprecated_members__")
        if deprecated and name in deprecated:
            new_name, removed_in = deprecated[name]
            print_deprecation_message(
                old_item=f"{cls.__name__}.{name}",
                new_item=f"{cls.__name__}.{new_name}",
                removed_in=removed_in,
            )
            return cls[new_name]
        raise AttributeError(name)


class ScenarioTechnique(Enum, metaclass=_DeprecatedEnumMeta):
    """
    Base class for attack techniques with tag-based categorization and aggregation.

    This class provides a pattern for defining attack techniques as enums where each
    technique has a set of tags for flexible categorization. It supports aggregate tags
    (like "easy", "moderate", "difficult" or "fast", "medium") that automatically expand
    to include all techniques with that tag.

    **Convention**: Technique enum members should map 1:1 to selectable **attack techniques**
    (e.g., ``PromptSending``, ``RolePlay``, ``TAP``) or to aggregates of techniques
    (e.g., ``DEFAULT``, ``SINGLE_TURN``).  Datasets control *what* content or objectives
    are tested; techniques control *how* attacks are executed.  Avoid encoding dataset or
    category selection into the technique enum — use ``DatasetConfiguration`` and the
    ``--dataset-names`` CLI flag for that axis.

    **Tags**: Flexible categorization system where techniques can have multiple tags
    (e.g., {"easy", "converter"}, {"difficult", "multi_turn"})

    Subclasses should define their enum members with (value, tags) tuples and
    override the get_aggregate_tags() classmethod to specify which tags
    represent aggregates that should expand.

    **Convention**: All subclasses should include `ALL = ("all", {"all"})` as the first
    aggregate member. The base class automatically handles expanding "all" to
    include all non-aggregate techniques.

    The normalization process automatically:
    1. Expands aggregate tags into their constituent techniques
    2. Excludes the aggregate tag enum members themselves from the final set
    3. Handles the special "all" tag by expanding to all non-aggregate techniques
    """

    _tags: set[str]

    def __new__(cls, value: str, tags: set[str] | None = None) -> ScenarioTechnique:
        """
        Create a new ScenarioTechnique with value and tags.

        Args:
            value: The technique value/name.
            tags: Optional set of tags for categorization.

        Returns:
            ScenarioTechnique: The new enum member.
        """
        obj = object.__new__(cls)
        obj._value_ = value
        obj._tags = tags or set()
        return obj

    @property
    def tags(self) -> set[str]:
        """
        The tags for this attack technique.

        Tags provide a flexible categorization system, allowing techniques
        to be classified along multiple dimensions (e.g., by complexity, type, or technique).

        Returns:
            set[str]: The tags (e.g., {"easy", "converter", "encoding"}).
        """
        return self._tags

    @classmethod
    def get_aggregate_tags(cls: type[T]) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Subclasses should override this method to specify which tags
        are aggregate markers (e.g., {"easy", "moderate", "difficult"} for complexity-based
        scenarios or {"fast", "medium"} for speed-based scenarios).

        The base class automatically includes "all" as an aggregate tag that expands
        to all non-aggregate techniques.

        Returns:
            set[str]: Set of tags that represent aggregates.
        """
        return {"all"}

    @classmethod
    def get_techniques_by_tag(cls: type[T], tag: str) -> set[T]:
        """
        Get all attack techniques that have a specific tag.

        This method returns concrete attack techniques (not aggregate markers)
        that include the specified tag.

        Args:
            tag (str): The tag to filter by (e.g., "easy", "converter", "multi_turn").

        Returns:
            set[T]: Set of techniques that include the specified tag, excluding
                    any aggregate markers.
        """
        aggregate_tags = cls.get_aggregate_tags()
        return {technique for technique in cls if tag in technique.tags and technique.value not in aggregate_tags}

    @classmethod
    def get_all_techniques(cls: type[T]) -> list[T]:
        """
        Get all non-aggregate techniques for this technique enum.

        This method returns all concrete attack techniques, excluding aggregate markers
        (like ALL, EASY, MODERATE, DIFFICULT) that are used for grouping.

        Returns:
            list[T]: List of all non-aggregate techniques.

        Example:
            >>> # Get all concrete techniques for a technique enum
            >>> all_techniques = FoundryTechnique.get_all_techniques()
            >>> # Returns: [Base64, ROT13, Leetspeak, ..., Crescendo]
            >>> # Excludes: ALL, EASY, MODERATE, DIFFICULT
        """
        aggregate_tags = cls.get_aggregate_tags()
        return [s for s in cls if s.value not in aggregate_tags]

    @classmethod
    def get_aggregate_techniques(cls: type[T]) -> list[T]:
        """
        Get all aggregate techniques for this technique enum.

        This method returns only the aggregate markers (like ALL, EASY, MODERATE, DIFFICULT)
        that are used to group concrete techniques by tags.

        Returns:
            list[T]: List of all aggregate techniques.

        Example:
            >>> # Get all aggregate techniques for a technique enum
            >>> aggregates = FoundryTechnique.get_aggregate_techniques()
            >>> # Returns: [ALL, EASY, MODERATE, DIFFICULT]
        """
        aggregate_tags = cls.get_aggregate_tags()
        return [s for s in cls if s.value in aggregate_tags]

    @classmethod
    def expand(cls: type[T], techniques: set[T]) -> list[T]:
        """
        Expand a set of techniques (including aggregates) into an ordered, deduplicated list.

        Aggregate markers (like EASY, ALL) are expanded into their constituent concrete techniques.
        The result is sorted by enum definition order for determinism.

        Args:
            techniques (set[T]): Set of techniques, which may include aggregate markers.

        Returns:
            list[T]: Ordered list of concrete techniques with aggregates expanded.
        """
        concrete: set[T] = set(techniques)
        aggregate_tags = cls.get_aggregate_tags()
        aggregates_to_expand = {
            tag for technique in techniques if technique.value in aggregate_tags for tag in technique.tags
        }
        for aggregate_tag in aggregates_to_expand:
            aggregate_marker = next((s for s in concrete if s.value == aggregate_tag), None)
            if aggregate_marker:
                concrete.remove(aggregate_marker)
            if aggregate_tag == "all":
                concrete.update(cls.get_all_techniques())
            else:
                concrete.update(cls.get_techniques_by_tag(aggregate_tag))
        return [s for s in cls if s in concrete]

    @classmethod
    def resolve(cls: type[T], techniques: Sequence[Any] | None, *, default: T) -> list[T]:
        """
        Resolve technique inputs into a concrete, ordered, deduplicated list.

        Handles None (returns expanded default), plain techniques, and aggregate techniques.
        Non-cls items (e.g., FoundryComposite) are silently skipped for
        backward compatibility.

        Args:
            techniques (Sequence[Any] | None): Techniques to resolve. If None or empty,
                expands the default.
            default (T): Default aggregate technique to use when techniques is None or empty.

        Returns:
            list[T]: Ordered, deduplicated list of concrete techniques.
        """
        if not techniques:
            return cls.expand({default})

        result: list[T] = []
        seen: set[T] = set()
        aggregate_tags = cls.get_aggregate_tags()
        for item in techniques:
            if not isinstance(item, cls):
                continue
            if item.value in aggregate_tags:
                for s in cls.expand({item}):  # type: ignore[ty:invalid-argument-type]
                    if s not in seen:
                        seen.add(s)
                        result.append(s)
            else:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
        return result
