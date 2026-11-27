# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed results of checking a scoring expectation against a scorer."""

from __future__ import annotations


class ExpectationMismatchError(ValueError):
    """
    A well-formed expectation asks for something this scorer cannot judge.

    Subclasses ``ValueError`` so existing callers keep their behavior. Selection uses the
    type to try the next candidate, instead of parsing exception text.
    """

    def __init__(self, message: str, *, scorer_name: str, condition_names: tuple[str, ...] = ()) -> None:
        """
        Record which scorer rejected which condition types.

        Args:
            message (str): The human-readable reason.
            scorer_name (str): The class name of the scorer in the tree that rejected the input.
            condition_names (tuple[str, ...]): The condition type names involved, if any.
        """
        super().__init__(message)
        self.scorer_name = scorer_name
        self.condition_names = condition_names


class UnsupportedConditionError(ExpectationMismatchError):
    """The expectation carries a condition type the scorer tree does not evaluate."""


class MissingConditionError(ExpectationMismatchError):
    """The scorer requires a condition or criterion the expectation does not supply."""


class AmbiguousConditionError(ValueError):
    """
    The expectation is malformed: one leaf would receive several conditions of its type.

    This is an input error rather than a candidate mismatch, so selection raises it
    instead of trying another scorer.
    """
