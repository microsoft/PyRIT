# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for pyrit.score.scorer_evaluation.scorer_evaluation_identifier.

Covers ``ScorerEvaluationIdentifier`` ClassVar values and eval-hash delegation.
"""

import pytest

from pyrit.identifiers import ComponentIdentifier, Identifiable, compute_eval_hash
from pyrit.identifiers.evaluation_identifier import ScorerEvaluationIdentifier


class TestScorerEvaluationIdentifierConstants:
    """Tests for the ClassVar constants on ScorerEvaluationIdentifier."""

    def test_child_eval_rules_keys(self):
        """Test that CHILD_EVAL_RULES contains the expected scorer target names."""
        assert set(ScorerEvaluationIdentifier.CHILD_EVAL_RULES.keys()) == {"prompt_target"}

    def test_prompt_target_rule(self):
        """Test that prompt_target has the expected included params."""
        rule = ScorerEvaluationIdentifier.CHILD_EVAL_RULES["prompt_target"]
        assert rule.included_params == frozenset({"model_name", "temperature", "top_p"})


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
            params={"model_name": "gpt-4", "endpoint": "https://endpoint-a.com"},
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "endpoint": "https://endpoint-b.com"},
        )
        id1 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child1})
        id2 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child2})

        assert ScorerEvaluationIdentifier(id1).eval_hash == ScorerEvaluationIdentifier(id2).eval_hash

    def test_behavioral_params_affect_hash(self):
        """Test that behavioral target params do affect the scorer eval hash."""
        child1 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "temperature": 0.7},
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "temperature": 0.0},
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
class TestScorerGetEvalHash:
    """Tests for ScorerEvaluationIdentifier eval_hash computation."""

    def test_eval_hash_uses_scorer_identity(self):
        """Test that ScorerEvaluationIdentifier computes eval_hash from identifier."""

        class FakeScorer(Identifiable):
            def _build_identifier(self) -> ComponentIdentifier:
                child = ComponentIdentifier(
                    class_name="Target",
                    class_module="pyrit.target",
                    params={"model_name": "gpt-4", "endpoint": "https://example.com"},
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
                    params={"model_name": "gpt-4", "endpoint": self._endpoint},
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
