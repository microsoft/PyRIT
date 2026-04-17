# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base class for scenario attack strategies with group-based aggregation.

This module provides a generic base class for creating enum-based attack strategy
hierarchies where strategies can be grouped by categories (e.g., complexity, encoding type)
and automatically expanded during scenario initialization.

It also provides ScenarioCompositeStrategy for representing composed attack strategies.
"""

from __future__ import annotations

from enum import Enum, EnumMeta
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.common.deprecation import print_deprecation_message

if TYPE_CHECKING:
    from collections.abc import Sequence

# TypeVar for the enum subclass itself
T = TypeVar("T", bound="ScenarioStrategy")


class _DeprecatedEnumMeta(EnumMeta):
    """
    Custom Enum metaclass that supports deprecated member aliases.

    Subclasses of ScenarioStrategy can define deprecated member name mappings
    by setting ``__deprecated_members__`` on the class after definition.
    Each entry maps the old name to a ``(new_name, removed_in)`` tuple::

        MyStrategy.__deprecated_members__ = {"OLD_NAME": ("NewName", "0.15.0")}

    Accessing ``MyStrategy.OLD_NAME`` will emit a DeprecationWarning and return
    the same enum member as ``MyStrategy.NewName``.
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


class ScenarioStrategy(Enum, metaclass=_DeprecatedEnumMeta):
    """
    Base class for attack strategies with tag-based categorization and aggregation.

    This class provides a pattern for defining attack strategies as enums where each
    strategy has a set of tags for flexible categorization. It supports aggregate tags
    (like "easy", "moderate", "difficult" or "fast", "medium") that automatically expand
    to include all strategies with that tag.

    **Tags**: Flexible categorization system where strategies can have multiple tags
    (e.g., {"easy", "converter"}, {"difficult", "multi_turn"})

    Subclasses should define their enum members with (value, tags) tuples and
    override the get_aggregate_tags() classmethod to specify which tags
    represent aggregates that should expand.

    **Convention**: All subclasses should include `ALL = ("all", {"all"})` as the first
    aggregate member. The base class automatically handles expanding "all" to
    include all non-aggregate strategies.

    The normalization process automatically:
    1. Expands aggregate tags into their constituent strategies
    2. Excludes the aggregate tag enum members themselves from the final set
    3. Handles the special "all" tag by expanding to all non-aggregate strategies
    """

    _tags: set[str]

    def __new__(cls, value: str, tags: set[str] | None = None) -> ScenarioStrategy:
        """
        Create a new ScenarioStrategy with value and tags.

        Args:
            value: The strategy value/name.
            tags: Optional set of tags for categorization.

        Returns:
            ScenarioStrategy: The new enum member.
        """
        obj = object.__new__(cls)
        obj._value_ = value
        obj._tags = tags or set()
        return obj

    @property
    def tags(self) -> set[str]:
        """
        Get the tags for this attack strategy.

        Tags provide a flexible categorization system, allowing strategies
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
        to all non-aggregate strategies.

        Returns:
            Set[str]: Set of tags that represent aggregates.
        """
        return {"all"}

    @classmethod
    def get_strategies_by_tag(cls: type[T], tag: str) -> set[T]:
        """
        Get all attack strategies that have a specific tag.

        This method returns concrete attack strategies (not aggregate markers)
        that include the specified tag.

        Args:
            tag (str): The tag to filter by (e.g., "easy", "converter", "multi_turn").

        Returns:
            Set[T]: Set of strategies that include the specified tag, excluding
                    any aggregate markers.
        """
        aggregate_tags = cls.get_aggregate_tags()
        return {strategy for strategy in cls if tag in strategy.tags and strategy.value not in aggregate_tags}

    @classmethod
    def get_all_strategies(cls: type[T]) -> list[T]:
        """
        Get all non-aggregate strategies for this strategy enum.

        This method returns all concrete attack strategies, excluding aggregate markers
        (like ALL, EASY, MODERATE, DIFFICULT) that are used for grouping.

        Returns:
            list[T]: List of all non-aggregate strategies.

        Example:
            >>> # Get all concrete strategies for a strategy enum
            >>> all_strategies = FoundryStrategy.get_all_strategies()
            >>> # Returns: [Base64, ROT13, Leetspeak, ..., Crescendo]
            >>> # Excludes: ALL, EASY, MODERATE, DIFFICULT
        """
        aggregate_tags = cls.get_aggregate_tags()
        return [s for s in cls if s.value not in aggregate_tags]

    @classmethod
    def get_aggregate_strategies(cls: type[T]) -> list[T]:
        """
        Get all aggregate strategies for this strategy enum.

        This method returns only the aggregate markers (like ALL, EASY, MODERATE, DIFFICULT)
        that are used to group concrete strategies by tags.

        Returns:
            list[T]: List of all aggregate strategies.

        Example:
            >>> # Get all aggregate strategies for a strategy enum
            >>> aggregates = FoundryStrategy.get_aggregate_strategies()
            >>> # Returns: [ALL, EASY, MODERATE, DIFFICULT]
        """
        aggregate_tags = cls.get_aggregate_tags()
        return [s for s in cls if s.value in aggregate_tags]

    @classmethod
    def normalize_strategies(cls: type[T], strategies: set[T]) -> set[T]:
        """
        Normalize a set of attack strategies by expanding aggregate tags.

        This method processes a set of strategies and expands any aggregate tags
        (like EASY, MODERATE, DIFFICULT or FAST, MEDIUM) into their constituent concrete strategies.
        The aggregate tag markers themselves are removed from the result.

        The special "all" tag is automatically supported and expands to all non-aggregate strategies.

        Args:
            strategies (Set[T]): The initial set of attack strategies, which may include
                                aggregate tags.

        Returns:
            Set[T]: The normalized set of concrete attack strategies with aggregate tags
                   expanded and removed.
        """
        print_deprecation_message(
            old_item="ScenarioStrategy.normalize_strategies",
            new_item="ScenarioStrategy.expand",
            removed_in="0.15.0",
        )
        return set(cls.expand(strategies))

    @classmethod
    def expand(cls: type[T], strategies: set[T]) -> list[T]:
        """
        Expand a set of strategies (including aggregates) into an ordered, deduplicated list.

        Aggregate markers (like EASY, ALL) are expanded into their constituent concrete strategies.
        The result is sorted by enum definition order for determinism.

        Args:
            strategies (set[T]): Set of strategies, which may include aggregate markers.

        Returns:
            list[T]: Ordered list of concrete strategies with aggregates expanded.
        """
        concrete: set[T] = set(strategies)
        aggregate_tags = cls.get_aggregate_tags()
        aggregates_to_expand = {
            tag for strategy in strategies if strategy.value in aggregate_tags for tag in strategy.tags
        }
        for aggregate_tag in aggregates_to_expand:
            aggregate_marker = next((s for s in concrete if s.value == aggregate_tag), None)
            if aggregate_marker:
                concrete.remove(aggregate_marker)
            if aggregate_tag == "all":
                concrete.update(cls.get_all_strategies())
            else:
                concrete.update(cls.get_strategies_by_tag(aggregate_tag))
        return [s for s in cls if s in concrete]

    @classmethod
    def resolve(cls: type[T], strategies: Sequence[Any] | None, *, default: T) -> list[T]:
        """
        Resolve strategy inputs into a concrete, ordered, deduplicated list.

        Handles None (returns expanded default), plain strategies, and aggregate strategies.
        Non-cls items (e.g., ScenarioCompositeStrategy) are silently skipped for
        backward compatibility.

        Args:
            strategies (Sequence[Any] | None): Strategies to resolve. If None or empty,
                expands the default.
            default (T): Default aggregate strategy to use when strategies is None or empty.

        Returns:
            list[T]: Ordered, deduplicated list of concrete strategies.
        """
        if not strategies:
            return cls.expand({default})

        result: list[T] = []
        seen: set[T] = set()
        aggregate_tags = cls.get_aggregate_tags()
        for item in strategies:
            if not isinstance(item, cls):
                continue
            if item.value in aggregate_tags:
                for s in cls.expand({item}):
                    if s not in seen:
                        seen.add(s)
                        result.append(s)
            else:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
        return result


class ScenarioCompositeStrategy:
    """
    Represents a composition of one or more attack strategies.

    This class encapsulates a collection of ScenarioStrategy instances along with
    an auto-generated descriptive name, making it easy to represent both single strategies
    and composed multi-strategy attacks.

    The name is automatically derived from the strategies:
    - Single strategy: Uses the strategy's value (e.g., "base64")
    - Multiple strategies: Generates "ComposedStrategy(base64, rot13)"

    Example:
        >>> # Single strategy composition
        >>> single = ScenarioCompositeStrategy(strategies=[FoundryStrategy.Base64])
        >>> print(single.name)  # "base64"
        >>>
        >>> # Multi-strategy composition
        >>> composed = ScenarioCompositeStrategy(strategies=[
        ...     FoundryStrategy.Base64,
        ...     FoundryStrategy.ROT13
        ... ])
        >>> print(composed.name)  # "ComposedStrategy(base64, rot13)"
    """

    def __init__(self, *, strategies: Sequence[ScenarioStrategy]) -> None:
        """
        Initialize a ScenarioCompositeStrategy.

        The name is automatically generated based on the strategies.

        Args:
            strategies (Sequence[ScenarioStrategy]): The sequence of strategies in this composition.
                Must contain at least one strategy.

        Raises:
            ValueError: If strategies list is empty.

        Example:
            >>> # Single strategy
            >>> composite = ScenarioCompositeStrategy(strategies=[FoundryStrategy.Base64])
            >>> print(composite.name)  # "base64"
            >>>
            >>> # Multiple strategies
            >>> composite = ScenarioCompositeStrategy(strategies=[
            ...     FoundryStrategy.Base64,
            ...     FoundryStrategy.Atbash
            ... ])
            >>> print(composite.name)  # "ComposedStrategy(base64, atbash)"
        """
        if not strategies:
            raise ValueError("strategies list cannot be empty")

        print_deprecation_message(
            old_item="ScenarioCompositeStrategy",
            new_item="FoundryComposite (from pyrit.scenario.scenarios.foundry)",
            # Extended to 0.18.0 to give external callers (e.g. Foundry) time to migrate.
            removed_in="0.18.0",
        )

        self._strategies = list(strategies)
        if len(self._strategies) == 1:
            self._name = str(self._strategies[0].value)
        else:
            strategy_names = ", ".join(s.value for s in self._strategies)
            self._name = f"ComposedStrategy({strategy_names})"

    @property
    def name(self) -> str:
        """Get the name of the composite strategy."""
        return self._name

    @property
    def strategies(self) -> list[ScenarioStrategy]:
        """Get the list of strategies in this composition."""
        return self._strategies

    @property
    def is_single_strategy(self) -> bool:
        """Check if this composition contains only a single strategy."""
        return len(self._strategies) == 1

    def __repr__(self) -> str:
        """
        Get string representation of the composite strategy.

        Returns:
            str: Representation as string.
        """
        return f"ScenarioCompositeStrategy(name='{self._name}', strategies={self._strategies})"

    def __str__(self) -> str:
        """
        Get human-readable string representation.

        Returns:
            str: Name as string literal.
        """
        return self._name
