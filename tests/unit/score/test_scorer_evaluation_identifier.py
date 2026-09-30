# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for pyrit.score.scorer_evaluation.scorer_evaluation_identifier.

Covers ``ScorerEvaluationIdentifier`` ClassVar values and eval-hash delegation.
"""

from collections.abc import Iterable
from itertools import permutations

import pytest

from pyrit.models import ComponentIdentifier, Identifiable, Score, ScorerEvaluationIdentifier, compute_eval_hash
from pyrit.score import SubStringScorer, TrueFalseCompositeScorer, TrueFalseScoreAggregator
from pyrit.score.score_aggregator_result import ScoreAggregatorResult
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc


class TestScorerEvaluationIdentifierConstants:
    """Tests for the ClassVar constants on ScorerEvaluationIdentifier."""

    def test_child_eval_rules_keys(self):
        """Test that CHILD_EVAL_RULES contains the expected scorer target names.

        ``prompt_target`` carries the scorer's inner target projection. ``targets``
        is the global wrapper-passthrough rule derived from
        ``TargetIdentifier.targets`` (it only fires on nested multi-targets).
        """
        assert set(ScorerEvaluationIdentifier.CHILD_EVAL_RULES.keys()) == {"prompt_target", "targets", "sub_scorers"}

    def test_sub_scorers_require_explicit_order_independence(self) -> None:
        rule = ScorerEvaluationIdentifier.CHILD_EVAL_RULES["sub_scorers"]
        assert rule.unordered_when == "sub_scorers_order_independent"

    def test_prompt_target_rule(self):
        """Test that prompt_target has the expected included params and fallbacks."""
        rule = ScorerEvaluationIdentifier.CHILD_EVAL_RULES["prompt_target"]
        assert rule.included_params == frozenset({"underlying_model_name", "temperature", "top_p"})
        assert rule.param_fallbacks == {"underlying_model_name": "model_name"}


class TestScorerEvaluationIdentifierEvalHash:
    """Tests for ScorerEvaluationIdentifier eval hash computation."""

    def test_deterministic(self):
        """Test that the same identifier produces the same eval hash."""
        cid = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", params={"threshold": 0.5})
        h1 = ScorerEvaluationIdentifier(cid).eval_hash
        h2 = ScorerEvaluationIdentifier(cid).eval_hash
        assert h1 == h2

    def test_operational_params_ignored(self):
        """Test that operational target params don't affect the scorer eval hash."""
        child1 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4", "endpoint": "https://endpoint-a.com"},
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4", "endpoint": "https://endpoint-b.com"},
        )
        id1 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child1})
        id2 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child2})

        assert ScorerEvaluationIdentifier(id1).eval_hash == ScorerEvaluationIdentifier(id2).eval_hash

    def test_behavioral_params_affect_hash(self):
        """Test that behavioral target params do affect the scorer eval hash."""
        child1 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4", "temperature": 0.7},
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4", "temperature": 0.0},
        )
        id1 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child1})
        id2 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child2})

        assert ScorerEvaluationIdentifier(id1).eval_hash != ScorerEvaluationIdentifier(id2).eval_hash

    def test_eval_hash_matches_free_function(self):
        """Test that eval_hash matches calling compute_eval_hash with scorer constants."""
        cid = ComponentIdentifier(class_name="MyScorer", class_module="pyrit.score", params={"k": "v"})
        identity = ScorerEvaluationIdentifier(cid)

        expected = compute_eval_hash(
            cid,
            child_eval_rules=ScorerEvaluationIdentifier.CHILD_EVAL_RULES,
        )
        assert identity.eval_hash == expected


@pytest.mark.usefixtures("patch_central_database")
class TestCompositeEvaluationOrder:
    @pytest.mark.parametrize(
        "aggregator",
        [TrueFalseScoreAggregator.OR, TrueFalseScoreAggregator.AND, TrueFalseScoreAggregator.MAJORITY],
    )
    def test_permutations_preserve_eval_identity_and_content_order(self, aggregator: TrueFalseAggregatorFunc) -> None:
        children = [SubStringScorer(substring=value) for value in ("a", "b", "c")]
        identifiers = [
            TrueFalseCompositeScorer(aggregator=aggregator, scorers=list(order)).get_identifier()
            for order in permutations(children)
        ]

        assert len({ScorerEvaluationIdentifier(identifier).eval_hash for identifier in identifiers}) == 1
        assert len({identifier.hash for identifier in identifiers}) == 6
        for order, identifier in zip(permutations(children), identifiers, strict=True):
            assert [child.hash for child in identifier.get_child_list("sub_scorers")] == [
                child.get_identifier().hash for child in order
            ]
            restored = ComponentIdentifier.model_validate_json(identifier.model_dump_json())
            assert restored.hash == identifier.hash
            assert ScorerEvaluationIdentifier(restored).eval_hash == identifier.eval_hash

    def test_nested_permutations_preserve_eval_identity(self) -> None:
        a, b, c = [SubStringScorer(substring=value) for value in ("a", "b", "c")]
        first = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.OR,
            scorers=[TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[a, b]), c],
        )
        second = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.OR,
            scorers=[c, TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[b, a])],
        )
        assert first.get_identifier().eval_hash == second.get_identifier().eval_hash
        assert first.get_identifier().hash != second.get_identifier().hash

    def test_configuration_aggregator_and_multiplicity_remain_distinct(self) -> None:
        a, b, c = [SubStringScorer(substring=value) for value in ("a", "b", "c")]
        configurations = [
            (TrueFalseScoreAggregator.OR, [a, b]),
            (TrueFalseScoreAggregator.OR, [a, c]),
            (TrueFalseScoreAggregator.AND, [a, b]),
            (TrueFalseScoreAggregator.MAJORITY, [a, b]),
            (TrueFalseScoreAggregator.MAJORITY, [a, a, b]),
            (TrueFalseScoreAggregator.MAJORITY, [a, b, b]),
        ]
        hashes = {
            TrueFalseCompositeScorer(aggregator=aggregator, scorers=scorers).get_identifier().eval_hash
            for aggregator, scorers in configurations
        }
        assert len(hashes) == len(configurations)

    def test_custom_aggregator_with_builtin_name_remains_ordered(self) -> None:
        def first_score(scores: Iterable[Score]) -> ScoreAggregatorResult:
            return TrueFalseScoreAggregator.OR([next(iter(scores))])

        first_score.__name__ = TrueFalseScoreAggregator.OR.__name__
        a, b = [SubStringScorer(substring=value) for value in ("a", "b")]
        first = TrueFalseCompositeScorer(aggregator=first_score, scorers=[a, b]).get_identifier()
        second = TrueFalseCompositeScorer(aggregator=first_score, scorers=[b, a]).get_identifier()
        builtin = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[a, b]).get_identifier()

        assert "sub_scorers_order_independent" not in first.params
        assert first.eval_hash != second.eval_hash
        assert first.eval_hash != builtin.eval_hash
        outer_first = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[TrueFalseCompositeScorer(aggregator=first_score, scorers=[a, b]), a],
        )
        outer_second = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.AND,
            scorers=[a, TrueFalseCompositeScorer(aggregator=first_score, scorers=[b, a])],
        )
        assert outer_first.get_identifier().eval_hash != outer_second.get_identifier().eval_hash

    def test_legacy_identifiers_are_not_inferred_from_aggregator_names(self) -> None:
        a, b = [SubStringScorer(substring=value) for value in ("a", "b")]
        current = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.OR, scorers=[a, b]).get_identifier()
        legacy_data = current.model_dump()
        del legacy_data["sub_scorers_order_independent"]
        legacy = ComponentIdentifier.model_validate(legacy_data)
        reversed_legacy = ComponentIdentifier(
            class_name=legacy.class_name,
            class_module=legacy.class_module,
            params=legacy.params,
            children={"sub_scorers": list(reversed(legacy.get_child_list("sub_scorers")))},
        )

        assert ScorerEvaluationIdentifier(legacy).eval_hash != ScorerEvaluationIdentifier(reversed_legacy).eval_hash
        assert ScorerEvaluationIdentifier(legacy).eval_hash != current.eval_hash
        assert legacy.hash != current.hash


@pytest.mark.usefixtures("patch_central_database")
class TestScorerGetEvalHash:
    """Tests for ScorerEvaluationIdentifier eval_hash computation."""

    def test_eval_hash_uses_scorer_identity(self):
        """Test that ScorerEvaluationIdentifier computes eval_hash from identifier."""

        class FakeScorer(Identifiable):
            def _build_identifier(self) -> ComponentIdentifier:
                child = ComponentIdentifier(
                    class_name="Target",
                    class_module="pyrit.target",
                    params={"underlying_model_name": "gpt-4", "endpoint": "https://example.com"},
                )
                return ComponentIdentifier.of(self, children={"prompt_target": child})

        scorer = FakeScorer()
        identifier = scorer.get_identifier()
        eval_hash = ScorerEvaluationIdentifier(identifier).eval_hash

        expected = compute_eval_hash(
            identifier,
            child_eval_rules=ScorerEvaluationIdentifier.CHILD_EVAL_RULES,
        )
        assert eval_hash == expected

    def test_eval_hash_filters_operational_params(self):
        """Test that eval_hash filters operational params from target children."""

        class ScorerLike(Identifiable):
            def __init__(self, *, endpoint: str):
                self._endpoint = endpoint

            def _build_identifier(self) -> ComponentIdentifier:
                child = ComponentIdentifier(
                    class_name="Target",
                    class_module="pyrit.target",
                    params={"underlying_model_name": "gpt-4", "endpoint": self._endpoint},
                )
                return ComponentIdentifier.of(self, children={"prompt_target": child})

        scorer_a = ScorerLike(endpoint="https://endpoint-a.com")
        scorer_b = ScorerLike(endpoint="https://endpoint-b.com")

        hash_a = ScorerEvaluationIdentifier(scorer_a.get_identifier()).eval_hash
        hash_b = ScorerEvaluationIdentifier(scorer_b.get_identifier()).eval_hash

        # Different endpoints should produce same eval hash (operational param filtered)
        assert hash_a == hash_b
        # But different component hashes (endpoint is in full identity)
        assert scorer_a.get_identifier().hash != scorer_b.get_identifier().hash

    def test_eval_hash_no_target_children_equals_component_hash(self):
        """Test that eval hash equals component hash when there are no target children."""

        class SimpleScorer(Identifiable):
            def _build_identifier(self) -> ComponentIdentifier:
                return ComponentIdentifier.of(self, params={"key": "value"})

        scorer = SimpleScorer()
        identifier = scorer.get_identifier()
        eval_hash = ScorerEvaluationIdentifier(identifier).eval_hash

        # No children named "prompt_target" or "converter_target", so no filtering occurs
        assert eval_hash == identifier.hash


class TestScorerEvalHashFallback:
    """Tests for underlying_model_name → model_name fallback behavior."""

    def test_underlying_model_name_used_when_present(self):
        """Test that underlying_model_name is used for eval hash when set."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "model_name": "my-deployment", "temperature": 0.7},
        )
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )
        identity = ScorerEvaluationIdentifier(cid)

        # Different deployment name, same underlying model → same eval hash
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "model_name": "other-deployment", "temperature": 0.7},
        )
        cid2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child2},
        )
        identity2 = ScorerEvaluationIdentifier(cid2)
        assert identity.eval_hash == identity2.eval_hash

    def test_falls_back_to_model_name_when_underlying_empty(self):
        """Test that model_name is used as fallback when underlying_model_name is empty."""
        child_with_underlying = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "model_name": "my-deployment", "temperature": 0.7},
        )
        child_without_underlying = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "", "model_name": "gpt-4o", "temperature": 0.7},
        )
        cid1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_underlying},
        )
        cid2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_without_underlying},
        )

        # Both should produce the same eval hash since the effective model is "gpt-4o"
        assert ScorerEvaluationIdentifier(cid1).eval_hash == ScorerEvaluationIdentifier(cid2).eval_hash

    def test_falls_back_to_model_name_when_underlying_missing(self):
        """Test that model_name is used as fallback when underlying_model_name is absent."""
        child_with_underlying = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        child_with_model_name_only = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4o", "temperature": 0.7},
        )
        cid1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_underlying},
        )
        cid2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_model_name_only},
        )

        assert ScorerEvaluationIdentifier(cid1).eval_hash == ScorerEvaluationIdentifier(cid2).eval_hash

    def test_different_underlying_models_produce_different_hash(self):
        """Test that different underlying model names produce different eval hashes."""
        child1 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o-mini", "temperature": 0.7},
        )
        cid1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child1},
        )
        cid2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child2},
        )

        assert ScorerEvaluationIdentifier(cid1).eval_hash != ScorerEvaluationIdentifier(cid2).eval_hash
