# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base class for scenario attack strategies with group-based aggregation.

This module provides a generic base class for creating enum-based attack strategy
hierarchies where strategies can be grouped by categories (e.g., complexity, encoding type)
and automatically expanded during scenario initialization.

It also provides ScenarioCompositeStrategy for representing composed attack strategies.
"""

from enum import Enum
from typing import List, Sequence, Set, TypeVar

# TypeVar for the enum subclass itself
T = TypeVar("T", bound="ScenarioStrategy")


class ScenarioStrategy(Enum):
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

    def __new__(cls, value: str, tags: set[str] | None = None) -> "ScenarioStrategy":
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
    def get_aggregate_tags(cls: type[T]) -> Set[str]:
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
    def get_strategies_by_tag(cls: type[T], tag: str) -> Set[T]:
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
    def normalize_strategies(cls: type[T], strategies: Set[T]) -> Set[T]:
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
        normalized_strategies = set(strategies)

        # Find aggregate tags in the input and expand them
        aggregate_tags = cls.get_aggregate_tags()
        aggregates_to_expand = {
            tag for strategy in strategies if strategy.value in aggregate_tags for tag in strategy.tags
        }

        for aggregate_tag in aggregates_to_expand:
            # Remove the aggregate marker itself
            aggregate_marker = next((s for s in normalized_strategies if s.value == aggregate_tag), None)
            if aggregate_marker:
                normalized_strategies.remove(aggregate_marker)

            # Special handling for "all" tag - expand to all non-aggregate strategies
            if aggregate_tag == "all":
                normalized_strategies.update(cls.get_all_strategies())
            else:
                # Add all strategies with that tag
                normalized_strategies.update(cls.get_strategies_by_tag(aggregate_tag))

        return normalized_strategies

    @classmethod
    def prepare_scenario_strategies(
        cls: type[T],
        strategies: Sequence[T | "ScenarioCompositeStrategy"] | None = None,
        *,
        default_aggregate: T | None = None,
    ) -> List["ScenarioCompositeStrategy"]:
        """
        Prepare and normalize scenario strategies for use in a scenario.

        This helper method simplifies scenario initialization by:
        1. Handling None input with sensible defaults
        2. Auto-wrapping bare ScenarioStrategy instances into ScenarioCompositeStrategy
        3. Expanding aggregate tags (like EASY, ALL) into concrete strategies
        4. Validating compositions according to the strategy's rules

        This eliminates boilerplate code in scenario __init__ methods.

        Args:
            strategies (Sequence[T | ScenarioCompositeStrategy] | None): The strategies to prepare.
                Can be a mix of bare strategy enums and composite strategies.
                If None, uses default_aggregate to determine defaults.
            default_aggregate (T | None): The aggregate strategy to use when strategies is None.
                Common values: MyStrategy.ALL, MyStrategy.EASY. If None when strategies is None,
                raises ValueError.

        Returns:
            List[ScenarioCompositeStrategy]: Normalized list of composite strategies ready for use.

        Raises:
            ValueError: If strategies is None and default_aggregate is None, or if compositions
                       are invalid according to validate_composition().
        """
        # Handle None input with default aggregate
        if strategies is None:
            if default_aggregate is None:
                raise ValueError(
                    f"Either strategies or default_aggregate must be provided. "
                    f"Common defaults: {cls.__name__}.ALL, {cls.__name__}.EASY"
                )

            # Expand the default aggregate into concrete strategies
            expanded = cls.normalize_strategies({default_aggregate})
            # Wrap each in a ScenarioCompositeStrategy
            composite_strategies = [ScenarioCompositeStrategy(strategies=[strategy]) for strategy in expanded]
        else:
            # Process the provided strategies
            composite_strategies = []
            for item in strategies:
                if isinstance(item, ScenarioCompositeStrategy):
                    # Already a composite, use as-is
                    composite_strategies.append(item)
                elif isinstance(item, cls):
                    # Bare strategy enum - wrap it in a composite
                    composite_strategies.append(ScenarioCompositeStrategy(strategies=[item]))
                else:
                    # Not our strategy type - skip or could raise error
                    # For now, skip to allow flexibility
                    pass

        if not composite_strategies:
            raise ValueError(
                f"No valid {cls.__name__} strategies provided. "
                f"Provide at least one {cls.__name__} enum or ScenarioCompositeStrategy."
            )

        # Normalize compositions (expands aggregates, validates compositions)
        normalized = ScenarioCompositeStrategy.normalize_compositions(composite_strategies, strategy_type=cls)

        return normalized

    @classmethod
    def supports_composition(cls: type[T]) -> bool:
        """
        Indicate whether this strategy type supports composition.

        By default, strategies do NOT support composition (only single strategies allowed).
        Subclasses that support composition (e.g., FoundryStrategy) should override this
        to return True and implement validate_composition() to enforce their specific rules.

        Returns:
            bool: True if composition is supported, False otherwise.
        """
        return False

    @classmethod
    def validate_composition(cls: type[T], strategies: Sequence[T]) -> None:
        """
        Validate whether the given strategies can be composed together.

        The base implementation checks supports_composition() and raises an error if
        composition is not supported and multiple strategies are provided.

        Subclasses that support composition should override this method to define their
        specific composition rules (e.g., "no more than one attack strategy").

        Args:
            strategies (Sequence[T]): The strategies to validate for composition.

        Raises:
            ValueError: If the composition is invalid according to the subclass's rules.
                        The error message should clearly explain what rule was violated.

        Examples:
            # EncodingStrategy doesn't support composition (uses default)
            >>> EncodingStrategy.validate_composition([EncodingStrategy.Base64, EncodingStrategy.ROT13])
            ValueError: EncodingStrategy does not support composition. Each strategy must be used individually.

            # FoundryStrategy allows composition but with rules
            >>> FoundryStrategy.validate_composition([FoundryStrategy.Crescendo, FoundryStrategy.MultiTurn])
            ValueError: Cannot compose multiple attack strategies: ['crescendo', 'multi_turn']
        """
        if not strategies:
            raise ValueError("Cannot validate empty strategy list")

        # Filter to only instances of this strategy type
        typed_strategies = [s for s in strategies if isinstance(s, cls)]

        # Default rule: if composition is not supported, only single strategies allowed
        if not cls.supports_composition() and len(typed_strategies) > 1:
            raise ValueError(
                f"{cls.__name__} does not support composition. "
                f"Each strategy must be used individually. "
                f"Received: {[s.value for s in typed_strategies]}"
            )


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

        self._strategies = list(strategies)
        self._name = self.get_composite_name(self._strategies)

    @property
    def name(self) -> str:
        """Get the name of the composite strategy."""
        return self._name

    @property
    def strategies(self) -> List[ScenarioStrategy]:
        """Get the list of strategies in this composition."""
        return self._strategies

    @property
    def is_single_strategy(self) -> bool:
        """Check if this composition contains only a single strategy."""
        return len(self._strategies) == 1

    @staticmethod
    def extract_single_strategy_values(
        composites: Sequence["ScenarioCompositeStrategy"], *, strategy_type: type[T]
    ) -> Set[str]:
        """
        Extract strategy values from single-strategy composites.

        This is a helper method for scenarios that don't support composition and need
        to filter or map strategies by their values. It flattens the composites into
        a simple set of strategy values.

        This method enforces that all composites contain only a single strategy. If any
        composite contains multiple strategies, a ValueError is raised.

        Args:
            composites (Sequence[ScenarioCompositeStrategy]): List of composite strategies.
                Each composite must contain only a single strategy.
            strategy_type (type[T]): The strategy enum type to filter by.

        Returns:
            Set[str]: Set of strategy values (e.g., {"base64", "rot13", "morse_code"}).

        Raises:
            ValueError: If any composite contains multiple strategies.
        """
        # Check that all composites are single-strategy
        multi_strategy_composites = [comp for comp in composites if not comp.is_single_strategy]
        if multi_strategy_composites:
            composite_names = [comp.name for comp in multi_strategy_composites]
            raise ValueError(
                f"extract_single_strategy_values() requires all composites to contain a single strategy. "
                f"Found composites with multiple strategies: {composite_names}"
            )

        return {
            strategy.value
            for composite in composites
            for strategy in composite.strategies
            if isinstance(strategy, strategy_type)
        }

    @staticmethod
    def get_composite_name(strategies: Sequence[ScenarioStrategy]) -> str:
        """
        Generate a descriptive name for a composition of strategies.

        For single strategies, returns the strategy's value.
        For multiple strategies, generates a name like "ComposedStrategy(base64, rot13)".

        Args:
            strategies (Sequence[ScenarioStrategy]): The strategies to generate a name for.

        Returns:
            str: The generated composite name.

        Raises:
            ValueError: If strategies is empty.

        Example:
            >>> # Single strategy
            >>> name = ScenarioCompositeStrategy.get_composite_name([FoundryStrategy.Base64])
            >>> # Returns: "base64"
            >>>
            >>> # Multiple strategies
            >>> name = ScenarioCompositeStrategy.get_composite_name([
            ...     FoundryStrategy.Base64,
            ...     FoundryStrategy.Atbash
            ... ])
            >>> # Returns: "ComposedStrategy(base64, atbash)"
        """
        if not strategies:
            raise ValueError("Cannot generate name for empty strategy list")

        if len(strategies) == 1:
            return str(strategies[0].value)

        strategy_names = ", ".join(s.value for s in strategies)
        return f"ComposedStrategy({strategy_names})"

    @staticmethod
    def normalize_compositions(
        compositions: List["ScenarioCompositeStrategy"], *, strategy_type: type[T]
    ) -> List["ScenarioCompositeStrategy"]:
        """
        Normalize strategy compositions by expanding aggregates while preserving concrete compositions.

        Aggregate strategies are expanded into their constituent individual strategies.
        Each aggregate expansion creates separate single-strategy compositions.
        Concrete strategy compositions are preserved together as single compositions.

        This method also validates compositions according to the strategy's rules via validate_composition().

        Args:
            compositions (List[ScenarioCompositeStrategy]): List of composite strategies to normalize.
            strategy_type (type[T]): The strategy enum type to use for normalization and validation.

        Returns:
            List[ScenarioCompositeStrategy]: Normalized list of composite strategies with aggregates expanded.

        Raises:
            ValueError: If compositions is empty, contains empty compositions,
                mixes aggregates with concrete strategies in the same composition,
                has multiple aggregates in one composition, or violates validate_composition() rules.

        Example::

            # Aggregate expands to individual strategies
            [ScenarioCompositeStrategy(strategies=[EASY])]
            -> [ScenarioCompositeStrategy(strategies=[Base64]),
                ScenarioCompositeStrategy(strategies=[ROT13]), ...]

            # Concrete composition preserved
            [ScenarioCompositeStrategy(strategies=[Base64, Atbash])]
            -> [ScenarioCompositeStrategy(strategies=[Base64, Atbash])]

            # Error: Cannot mix aggregate with concrete in same composition
            [ScenarioCompositeStrategy(strategies=[EASY, Base64])] -> ValueError
        """
        if not compositions:
            raise ValueError("Compositions list cannot be empty")

        aggregate_tags = strategy_type.get_aggregate_tags()
        normalized_compositions: List[ScenarioCompositeStrategy] = []

        for composite in compositions:
            if not composite.strategies:
                raise ValueError("Empty compositions are not allowed")

            # Filter to only strategies of the specified type
            typed_strategies = [s for s in composite.strategies if isinstance(s, strategy_type)]
            if not typed_strategies:
                # No strategies of this type - skip
                continue

            # Check if composition contains any aggregates
            aggregates_in_composition = [s for s in typed_strategies if s.value in aggregate_tags]
            concretes_in_composition = [s for s in typed_strategies if s.value not in aggregate_tags]

            # Error if mixing aggregates with concrete strategies
            if aggregates_in_composition and concretes_in_composition:
                raise ValueError(
                    f"Cannot mix aggregate strategies {[s.value for s in aggregates_in_composition]} "
                    f"with concrete strategies {[s.value for s in concretes_in_composition]} "
                    f"in the same composition. Aggregates must be in their own composition to be expanded."
                )

            # Error if multiple aggregates in same composition
            if len(aggregates_in_composition) > 1:
                raise ValueError(
                    f"Cannot compose multiple aggregate strategies together: "
                    f"{[s.value for s in aggregates_in_composition]}. "
                    f"Each aggregate must be in its own composition."
                )

            # If composition has an aggregate, expand it into individual strategies
            if aggregates_in_composition:
                aggregate = aggregates_in_composition[0]
                expanded = strategy_type.normalize_strategies({aggregate})
                # Each expanded strategy becomes its own composition
                for strategy in expanded:
                    normalized_compositions.append(ScenarioCompositeStrategy(strategies=[strategy]))
            else:
                # Concrete composition - validate and preserve as-is
                strategy_type.validate_composition(typed_strategies)
                # Keep the composite (name is auto-generated from strategies)
                normalized_compositions.append(composite)

        if not normalized_compositions:
            raise ValueError("No valid strategy compositions after normalization")

        return normalized_compositions

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
