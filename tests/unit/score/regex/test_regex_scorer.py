# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import inspect
from collections.abc import Callable

import pytest

import pyrit.score as score_package
import pyrit.score.true_false.regex as regex_package
from pyrit.memory import SQLiteMemory
from pyrit.models import ComponentIdentifier, ScorerEvaluationIdentifier
from pyrit.score import (
    AnsiEscapeOutputScorer,
    AnthraxKeywordScorer,
    CredentialLeakScorer,
    EscapedAnsiOutputScorer,
    FentanylKeywordScorer,
    LDAPInjectionOutputScorer,
    MarkdownInjectionScorer,
    MethKeywordScorer,
    NerveAgentKeywordScorer,
    OpenRedirectOutputScorer,
    PathTraversalOutputScorer,
    RegexScorer,
    ShellCommandOutputScorer,
    SQLInjectionOutputScorer,
    SSRFOutputScorer,
    SSTIOutputScorer,
    XSSOutputScorer,
    XXEOutputScorer,
)
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseScoreAggregator

_TEST_PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "Credit Card": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
}

_CONFIGURABLE_SCORERS = (
    AnsiEscapeOutputScorer,
    CredentialLeakScorer,
    EscapedAnsiOutputScorer,
    LDAPInjectionOutputScorer,
    MarkdownInjectionScorer,
    OpenRedirectOutputScorer,
    PathTraversalOutputScorer,
    ShellCommandOutputScorer,
    SQLInjectionOutputScorer,
    SSRFOutputScorer,
    SSTIOutputScorer,
    XSSOutputScorer,
    XXEOutputScorer,
)

_FIXED_SCORERS = (
    AnthraxKeywordScorer,
    FentanylKeywordScorer,
    MethKeywordScorer,
    NerveAgentKeywordScorer,
)

_DEFAULT_SCORER_CASES = (
    *((scorer_class, ["security"]) for scorer_class in _CONFIGURABLE_SCORERS),
    (AnthraxKeywordScorer, ["anthrax"]),
    (FentanylKeywordScorer, ["fentanyl"]),
    (MethKeywordScorer, ["meth"]),
    (NerveAgentKeywordScorer, ["nerve_agent"]),
)
_DEFAULT_SCORERS = tuple(scorer_class for scorer_class, _categories in _DEFAULT_SCORER_CASES)


def _scorer_factory(scorer_class: type[RegexScorer]) -> Callable[..., RegexScorer]:
    return scorer_class


async def test_regex_scorer_detects_match(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="SSN is 123-45-6789"))[0]
    assert score.get_value() is True
    assert "SSN" in score.score_rationale


async def test_regex_scorer_no_match(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="Nothing sensitive here."))[0]
    assert score.get_value() is False
    assert score.score_rationale == ""


async def test_regex_scorer_multiple_matches(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS)
    score = (await scorer.score_text_async(text="SSN 123-45-6789 and card 4111-1111-1111-1111"))[0]
    assert score.get_value() is True
    assert "SSN" in score.score_rationale
    assert "Credit Card" in score.score_rationale


async def test_regex_scorer_categories_propagate(patch_central_database):
    scorer = RegexScorer(patterns=_TEST_PATTERNS, categories=["pii"])
    score = (await scorer.score_text_async(text="SSN is 123-45-6789"))[0]
    assert "pii" in score.score_category


def test_regex_scorer_rejects_empty_patterns():
    with pytest.raises(ValueError, match="non-empty"):
        RegexScorer(patterns={})


@pytest.mark.parametrize("scorer_class", _CONFIGURABLE_SCORERS)
def test_configurable_subclass_constructor_contract(scorer_class: type[RegexScorer]) -> None:
    parameters = inspect.signature(scorer_class).parameters

    assert list(parameters) == ["patterns", "score_aggregator"]
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values())
    assert parameters["patterns"].default is None
    assert parameters["score_aggregator"].default is TrueFalseScoreAggregator.OR


@pytest.mark.parametrize("scorer_class", _FIXED_SCORERS)
def test_fixed_subclass_constructor_contract(scorer_class: type[RegexScorer]) -> None:
    assert not inspect.signature(scorer_class).parameters


@pytest.mark.parametrize(("scorer_class", "expected_categories"), _DEFAULT_SCORER_CASES)
def test_subclass_declarative_defaults_preserve_patterns_and_categories(
    scorer_class: type[RegexScorer], expected_categories: list[str]
) -> None:
    scorer = _scorer_factory(scorer_class)()
    default_patterns = scorer_class._DEFAULT_PATTERNS

    assert default_patterns is not None
    assert list(scorer._patterns.items()) == list(default_patterns.items())
    assert scorer._patterns is not default_patterns
    assert scorer._score_categories == expected_categories
    assert isinstance(scorer, RegexScorer)


@pytest.mark.parametrize("scorer_class", _CONFIGURABLE_SCORERS)
def test_configurable_subclass_custom_patterns_override_defaults(scorer_class: type[RegexScorer]) -> None:
    custom_patterns = {"second": "two", "first": "one"}
    scorer = _scorer_factory(scorer_class)(
        patterns=custom_patterns,
        score_aggregator=TrueFalseScoreAggregator.AND,
    )

    assert list(scorer._patterns.items()) == list(custom_patterns.items())
    assert scorer._patterns is not custom_patterns
    assert scorer._score_aggregator is TrueFalseScoreAggregator.AND


@pytest.mark.parametrize("scorer_class", _DEFAULT_SCORERS)
def test_subclass_exports_and_identifier_serialization(scorer_class: type[RegexScorer]) -> None:
    scorer = _scorer_factory(scorer_class)()
    identifier = scorer.get_identifier()

    assert scorer_class.__name__ in score_package.__all__
    assert scorer_class.__name__ in regex_package.__all__
    assert getattr(score_package, scorer_class.__name__) is scorer_class
    assert getattr(regex_package, scorer_class.__name__) is scorer_class
    assert ComponentIdentifier.model_validate_json(identifier.model_dump_json()) == identifier


def _identity(scorer: RegexScorer) -> tuple[str, str]:
    identifier = scorer.get_identifier()
    return identifier.hash, ScorerEvaluationIdentifier(identifier).eval_hash


@pytest.mark.usefixtures("patch_central_database")
def test_regex_scorer_identity_follows_its_patterns() -> None:
    first = RegexScorer(patterns={"secret": "alpha"})
    other = RegexScorer(patterns={"secret": "beta"})
    reordered = RegexScorer(patterns={"b": "two", "a": "one"})
    same = RegexScorer(patterns={"a": "one", "b": "two"})

    assert first.get_identifier().params["pattern_count"] == other.get_identifier().params["pattern_count"]
    assert _identity(first)[0] != _identity(other)[0]
    assert _identity(first)[1] != _identity(other)[1]
    assert _identity(reordered) == _identity(same)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scorer_class", (RegexScorer, *_CONFIGURABLE_SCORERS))
@pytest.mark.parametrize(
    ("left_patterns", "right_patterns", "text"),
    [
        ({"a": "x\0b\0y", "c": "z"}, {"a": "x", "b": "y\0c\0z"}, "x"),
        ({"a": "x\0b"}, {"a\0x": "b"}, "b"),
    ],
)
async def test_nul_patterns_keep_distinct_persisted_identities_async(
    *,
    scorer_class: type[RegexScorer],
    left_patterns: dict[str, str],
    right_patterns: dict[str, str],
    text: str,
    sqlite_instance: SQLiteMemory,
) -> None:
    left = _scorer_factory(scorer_class)(patterns=left_patterns)
    right = _scorer_factory(scorer_class)(patterns=right_patterns)

    left_score = (await left.score_text_async(text))[0]
    right_score = (await right.score_text_async(text))[0]

    assert left_score.get_value() is False
    assert right_score.get_value() is True
    assert _identity(left)[0] != _identity(right)[0]
    assert _identity(left)[1] != _identity(right)[1]

    stored = await sqlite_instance.get_scores_async(score_ids=[str(left_score.id), str(right_score.id)])
    assert len(stored) == 2
    assert {score.scorer_class_identifier.hash for score in stored} == {
        left.get_identifier().hash,
        right.get_identifier().hash,
    }
    assert {score.scorer_class_identifier.eval_hash for score in stored} == {
        left.get_identifier().eval_hash,
        right.get_identifier().eval_hash,
    }


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scorer_class", _DEFAULT_SCORERS)
def test_default_patterns_keep_their_existing_identity(scorer_class: type[RegexScorer]) -> None:
    # Stored metrics and scores for default configurations must keep matching.
    assert "patterns_digest" not in _scorer_factory(scorer_class)().get_identifier().params


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scorer_class", _CONFIGURABLE_SCORERS)
def test_custom_patterns_get_their_own_identity(scorer_class: type[RegexScorer]) -> None:
    default = _scorer_factory(scorer_class)()
    custom = _scorer_factory(scorer_class)(patterns={"custom": "only-this"})
    other = _scorer_factory(scorer_class)(patterns={"custom": "something-else"})

    assert len({_identity(default), _identity(custom), _identity(other)}) == 3
    assert custom.get_identifier().params["patterns_digest"] == hashlib.sha256(b'{"custom":"only-this"}').hexdigest()
