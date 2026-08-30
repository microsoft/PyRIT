# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from typing import cast

from pyrit.models import ComponentIdentifier, MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer


class RegexScorer(MessageTrueFalseScorer):
    """
    A scorer that evaluates text against a set of named regex patterns.

    Returns True if any pattern matches. Subclass and provide a default pattern
    set to create domain-specific scorers (e.g., credential detection, PII).
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    _DEFAULT_PATTERNS: dict[str, str] | None = None
    _DEFAULT_CATEGORIES: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        patterns: dict[str, str],
        categories: list[str] | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the RegexScorer.

        Args:
            patterns (dict[str, str]): A mapping of pattern names to regex strings.
            categories (list[str] | None): Optional score categories. Defaults to None.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If patterns is empty.
        """
        if not patterns:
            raise ValueError("patterns must be a non-empty dict")

        self._patterns = dict(patterns)
        self._compiled: dict[str, re.Pattern[str]] = {
            name: re.compile(pattern) for name, pattern in self._patterns.items()
        }
        self._score_categories = categories or []

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, score_aggregator=score_aggregator)

    def _initialize_with_defaults(
        self,
        *,
        patterns: dict[str, str] | None,
        score_aggregator: TrueFalseAggregatorFunc,
    ) -> None:
        default_patterns = self._DEFAULT_PATTERNS
        if default_patterns is None:
            raise TypeError(f"{type(self).__name__} must define _DEFAULT_PATTERNS")

        RegexScorer.__init__(
            self,
            patterns=patterns if patterns is not None else default_patterns,
            categories=list(self._DEFAULT_CATEGORIES),
            score_aggregator=score_aggregator,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "pattern_count": len(self._patterns),
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Check text against all patterns. Returns True if any pattern matches.

        Args:
            message_piece (MessagePiece): The message piece to evaluate.
            objective (str | None): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score with True if any pattern matched.
        """
        text = message_piece.converted_value
        matched: list[str] = [name for name, pattern in self._compiled.items() if pattern.search(text)]

        detected = bool(matched)
        rationale = f"Matched: {', '.join(matched)}" if detected else ""

        return [
            Score(
                score_value=str(detected).lower(),
                score_value_description="True if any pattern matched, else False.",
                score_metadata=None,
                score_type="true_false",
                score_category=self._score_categories,
                score_rationale=rationale,
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                objective=objective,
            )
        ]


class _RegexScorerDefaultsMixin:
    def _initialize_regex_scorer(
        self,
        *,
        patterns: dict[str, str] | None,
        score_aggregator: TrueFalseAggregatorFunc,
    ) -> None:
        RegexScorer._initialize_with_defaults(
            cast("RegexScorer", self),
            patterns=patterns,
            score_aggregator=score_aggregator,
        )


class _ConfigurableRegexScorerMixin(_RegexScorerDefaultsMixin):
    def __init__(
        self,
        *,
        patterns: dict[str, str] | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize a regex scorer with declarative defaults.

        Args:
            patterns (dict[str, str] | None): A mapping of pattern names to regex strings.
                Uses the subclass defaults when omitted. Pass a custom dict to override entirely.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._initialize_regex_scorer(patterns=patterns, score_aggregator=score_aggregator)


class _FixedRegexScorerMixin(_RegexScorerDefaultsMixin):
    def __init__(self) -> None:
        """Initialize a regex scorer with fixed declarative defaults."""
        self._initialize_regex_scorer(patterns=None, score_aggregator=TrueFalseScoreAggregator.OR)
