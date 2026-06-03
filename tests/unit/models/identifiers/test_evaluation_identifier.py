# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for pyrit.models.identifiers.evaluation_identifier.

Covers the ``EvaluationIdentifier`` abstract base class, the ``_build_eval_dict``
helper, and the ``compute_eval_hash`` free function.
"""

from typing import ClassVar

import pytest

from pyrit.models.identifiers import ComponentIdentifier, compute_eval_hash
from pyrit.models.identifiers.evaluation_identifier import ChildEvalRule, EvaluationIdentifier, _build_eval_dict

# ---------------------------------------------------------------------------
# Concrete subclass for testing the ABC
# ---------------------------------------------------------------------------


class _StubEvaluationIdentifier(EvaluationIdentifier):
    """Minimal concrete subclass for testing the abstract base class."""

    CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
        "my_target": ChildEvalRule(included_params=frozenset({"model_name"})),
    }


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_CHILD_EVAL_RULES: dict[str, ChildEvalRule] = {
    "prompt_target": ChildEvalRule(
        included_params=frozenset({"model_name", "temperature", "top_p"}),
    ),
}


class TestBuildEvalDict:
    """Tests for _build_eval_dict filtering logic."""

    def test_target_child_params_filtered(self):
        """Test that target children only keep behavioral params."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "endpoint": "https://example.com"},
        )
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )

        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        # "endpoint" must not appear anywhere in the child sub-dict
        assert "endpoint" not in str(result)
        assert "children" in result

    def test_non_target_child_params_kept(self):
        """Test that non-target children keep all params (full recursive treatment)."""
        child = ComponentIdentifier(
            class_name="SubScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5, "extra": "value"},
        )
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"sub_scorer": child},
        )

        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert "children" in result

    def test_no_children_produces_flat_dict(self):
        """Test that an identifier with no children produces a dict without 'children' key."""
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"threshold": 0.5},
        )

        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert "children" not in result
        assert result[ComponentIdentifier.KEY_CLASS_NAME] == "Scorer"


class TestComputeEvalHash:
    """Tests for the compute_eval_hash free function."""

    def test_deterministic(self):
        """Test that the same identifier + config produces the same hash."""
        identifier = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score")
        h1 = compute_eval_hash(identifier, child_eval_rules=_CHILD_EVAL_RULES)
        h2 = compute_eval_hash(identifier, child_eval_rules=_CHILD_EVAL_RULES)
        assert h1 == h2

    def test_empty_rules_returns_component_hash(self):
        """Test that empty child_eval_rules bypasses filtering and returns component hash."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "endpoint": "https://example.com"},
        )
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )

        result = compute_eval_hash(
            identifier,
            child_eval_rules={},
        )
        assert result == identifier.hash

    def test_returns_64_char_hex(self):
        """Test that the hash is a 64-char lowercase hex string (SHA-256)."""
        identifier = ComponentIdentifier(class_name="S", class_module="m")
        result = compute_eval_hash(identifier, child_eval_rules=_CHILD_EVAL_RULES)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestEvaluationIdentifier:
    """Tests for the EvaluationIdentifier abstract base class."""

    def test_identifier_property_returns_original(self):
        """Test that .identifier returns the ComponentIdentifier passed at construction."""
        cid = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score")
        identity = _StubEvaluationIdentifier(cid)
        assert identity.identifier is cid

    def test_eval_hash_is_string(self):
        """Test that .eval_hash is a valid hex string."""
        cid = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score")
        identity = _StubEvaluationIdentifier(cid)
        assert isinstance(identity.eval_hash, str)
        assert len(identity.eval_hash) == 64

    def test_eval_hash_matches_free_function(self):
        """Test that .eval_hash matches calling compute_eval_hash directly."""
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"threshold": 0.5},
        )
        identity = _StubEvaluationIdentifier(cid)

        expected = compute_eval_hash(
            cid,
            child_eval_rules=_StubEvaluationIdentifier.CHILD_EVAL_RULES,
        )
        assert identity.eval_hash == expected

    def test_eval_hash_differs_from_component_hash_when_target_filtered(self):
        """Test that eval hash differs from component hash when target children have operational params."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "endpoint": "https://example.com"},
        )
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"my_target": child},
        )
        identity = _StubEvaluationIdentifier(cid)

        # "endpoint" is operational, so eval hash should differ from full component hash
        assert identity.eval_hash != cid.hash

    def test_cannot_instantiate_abc_directly(self):
        """Test that EvaluationIdentifier cannot be instantiated without ClassVars."""
        with pytest.raises(AttributeError):
            EvaluationIdentifier(ComponentIdentifier(class_name="X", class_module="m"))  # type: ignore[abstract]

    def test_custom_classvars_produce_expected_hash(self):
        """Test that a concrete subclass with custom ClassVars produces the correct eval hash."""

        class CustomIdentity(EvaluationIdentifier):
            CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {
                "special_target": ChildEvalRule(
                    included_params=frozenset({"model_name", "temperature"}),
                ),
            }

        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "temperature": 0.7, "endpoint": "https://example.com"},
        )
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"special_target": child},
        )
        identity = CustomIdentity(cid)

        expected = compute_eval_hash(
            cid,
            child_eval_rules={
                "special_target": ChildEvalRule(
                    included_params=frozenset({"model_name", "temperature"}),
                ),
            },
        )
        assert identity.eval_hash == expected

    def test_uses_eval_hash_when_available(self):
        """Test that EvaluationIdentifier uses eval_hash instead of recomputing."""
        stored_hash = "stored_eval_hash_value_" + "0" * 42  # 64 chars
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"system_prompt": "truncated..."},
        ).with_eval_hash(stored_hash)

        identity = _StubEvaluationIdentifier(cid)
        assert identity.eval_hash == stored_hash

    def test_computes_eval_hash_when_not_set(self):
        """Test that eval_hash is computed normally when eval_hash is None."""
        cid = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"threshold": 0.5},
        )
        assert cid.eval_hash is None

        identity = _StubEvaluationIdentifier(cid)
        expected = compute_eval_hash(cid, child_eval_rules=_StubEvaluationIdentifier.CHILD_EVAL_RULES)
        assert identity.eval_hash == expected

    def test_truncation_roundtrip_preserves_eval_hash(self):
        """Regression test: eval_hash survives DB round-trip with param truncation.

        This is the core scenario for the bug fix. A scorer with a long system_prompt
        gets stored to the DB with truncation. The eval_hash computed from the untruncated
        identifier is included in to_dict(). After from_dict() reconstruction, the
        EvaluationIdentifier should use the stored eval_hash (not recompute from truncated params).
        """
        # Build a scorer identifier with a long system_prompt and a target child
        long_prompt = "Evaluate whether the response achieves the objective. " * 10
        target_child = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "gpt-4o", "endpoint": "https://api.openai.com", "temperature": 0.0},
        )
        scorer_id = ComponentIdentifier(
            class_name="SelfAskTrueFalseScorer",
            class_module="pyrit.score",
            params={"system_prompt_template": long_prompt},
            children={"prompt_target": target_child},
        )

        # Compute eval_hash from the untruncated identifier (the correct hash)
        correct_eval_hash = compute_eval_hash(scorer_id, child_eval_rules=_CHILD_EVAL_RULES)
        scorer_id = scorer_id.with_eval_hash(correct_eval_hash)

        # Simulate DB storage: serialize with truncation
        truncated_dict = scorer_id.to_dict(max_value_length=80)

        # Verify params are actually truncated
        assert truncated_dict["system_prompt_template"].endswith("...")

        # Reconstruct from truncated dict (simulates DB read)
        reconstructed = ComponentIdentifier.from_dict(truncated_dict)

        # The reconstructed identifier has truncated params, so recomputing would give wrong hash
        recomputed = compute_eval_hash(reconstructed, child_eval_rules=_CHILD_EVAL_RULES)
        assert recomputed != correct_eval_hash, "Truncated params should produce different eval_hash"

        # But EvaluationIdentifier uses the preserved eval_hash, giving the correct result
        identity = _StubEvaluationIdentifier(reconstructed)
        assert identity.eval_hash == correct_eval_hash

    def test_eval_hash_preserved_through_double_roundtrip(self):
        """Test that eval_hash is preserved when retrieved from DB and re-stored.

        Simulates: fresh save → DB retrieve → re-store → DB retrieve.
        The eval_hash computed at first save should survive all round-trips.
        """
        long_prompt = "Evaluate whether the response achieves the objective. " * 10
        scorer_id = ComponentIdentifier(
            class_name="SelfAskTrueFalseScorer",
            class_module="pyrit.score",
            params={"system_prompt_template": long_prompt},
        )

        # First save: compute eval_hash from untruncated identifier
        correct_eval_hash = compute_eval_hash(scorer_id, child_eval_rules=_CHILD_EVAL_RULES)
        scorer_id = scorer_id.with_eval_hash(correct_eval_hash)
        d1 = scorer_id.to_dict(max_value_length=80)

        # First retrieve
        r1 = ComponentIdentifier.from_dict(d1)
        assert _StubEvaluationIdentifier(r1).eval_hash == correct_eval_hash

        # Re-store: EvaluationIdentifier should use stored value, not recompute
        d2 = r1.to_dict(max_value_length=80)

        # Second retrieve
        r2 = ComponentIdentifier.from_dict(d2)
        assert _StubEvaluationIdentifier(r2).eval_hash == correct_eval_hash


class TestParamFallbacks:
    """Tests for ChildEvalRule.param_fallbacks in _build_eval_dict."""

    _RULES_WITH_FALLBACK: dict[str, ChildEvalRule] = {
        "prompt_target": ChildEvalRule(
            included_params=frozenset({"underlying_model_name", "temperature"}),
            param_fallbacks={"underlying_model_name": "model_name"},
        ),
    }

    def test_primary_param_used_when_present(self):
        """Test that the primary param value is used when it is non-empty."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "model_name": "deploy-1", "temperature": 0.7},
        )
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )

        result = _build_eval_dict(identifier, child_eval_rules=self._RULES_WITH_FALLBACK)
        # The child hash should be based on underlying_model_name="gpt-4o", not model_name
        assert "children" in result

    def test_fallback_used_when_primary_empty(self):
        """Test that fallback param used when primary is empty string."""
        child_with_underlying = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "model_name": "deploy-1", "temperature": 0.7},
        )
        child_with_fallback = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "", "model_name": "gpt-4o", "temperature": 0.7},
        )
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_underlying},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_fallback},
        )

        result1 = _build_eval_dict(id1, child_eval_rules=self._RULES_WITH_FALLBACK)
        result2 = _build_eval_dict(id2, child_eval_rules=self._RULES_WITH_FALLBACK)

        assert result1["children"]["prompt_target"] == result2["children"]["prompt_target"]

    def test_fallback_used_when_primary_missing(self):
        """Test that fallback param used when primary key is absent."""
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
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_underlying},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with_model_name_only},
        )

        result1 = _build_eval_dict(id1, child_eval_rules=self._RULES_WITH_FALLBACK)
        result2 = _build_eval_dict(id2, child_eval_rules=self._RULES_WITH_FALLBACK)

        assert result1["children"]["prompt_target"] == result2["children"]["prompt_target"]

    def test_no_fallback_when_no_rules(self):
        """Test that param_fallbacks=None means no fallback applied."""
        rules_without_fallback: dict[str, ChildEvalRule] = {
            "prompt_target": ChildEvalRule(
                included_params=frozenset({"underlying_model_name", "temperature"}),
            ),
        }
        child_with = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        child_without = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={"model_name": "gpt-4o", "temperature": 0.7},
        )
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_with},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child_without},
        )

        result1 = _build_eval_dict(id1, child_eval_rules=rules_without_fallback)
        result2 = _build_eval_dict(id2, child_eval_rules=rules_without_fallback)

        # Without fallback, these should produce different hashes
        assert result1["children"]["prompt_target"] != result2["children"]["prompt_target"]


def test_compute_eval_hash_raises_when_hash_none_and_no_rules():
    identifier = ComponentIdentifier.__new__(ComponentIdentifier)
    object.__setattr__(identifier, "hash", None)
    object.__setattr__(identifier, "class_name", "Test")
    object.__setattr__(identifier, "class_module", "test.module")
    with pytest.raises(RuntimeError, match="hash should be set by __post_init__"):
        compute_eval_hash(identifier, child_eval_rules={})


# ---------------------------------------------------------------------------
# inner_child_name tests
# ---------------------------------------------------------------------------


class TestInnerChildName:
    """Tests for the inner_child_name feature in ChildEvalRule."""

    def test_unwrap_substitutes_first_inner_child(self):
        """When the child has a sub-child matching inner_child_name, the unwrapped eval hash
        matches a direct (non-wrapped) target with the same behavioral params."""
        inner_target_east = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7, "endpoint": "https://east.example.com"},
        )
        inner_target_west = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7, "endpoint": "https://west.example.com"},
        )
        wrapper = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [1, 1]},
            children={"targets": [inner_target_east, inner_target_west]},
        )
        scorer_wrapped = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": wrapper},
        )
        scorer_direct = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": inner_target_east},
        )

        rules = {
            "prompt_target": ChildEvalRule(
                included_params=frozenset({"underlying_model_name", "temperature"}),
                inner_child_name="targets",
            ),
        }

        result_wrapped = _build_eval_dict(scorer_wrapped, child_eval_rules=rules)
        result_direct = _build_eval_dict(scorer_direct, child_eval_rules=rules)

        # Unwrapped hash should match the direct target (same behavioral params)
        assert result_wrapped["children"]["prompt_target"] == result_direct["children"]["prompt_target"]

    def test_unwrap_no_op_when_child_has_no_matching_subchild(self):
        """When the child doesn't have the named sub-child, use the child as-is."""
        regular_target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        scorer = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": regular_target},
        )

        rules = {
            "prompt_target": ChildEvalRule(
                included_params=frozenset({"underlying_model_name", "temperature"}),
                inner_child_name="targets",  # OpenAIChatTarget has no "targets" child
            ),
        }

        result = _build_eval_dict(scorer, child_eval_rules=rules)
        # Should still work — uses OpenAIChatTarget directly
        assert "children" in result

        # Compare with rules without inner_child_name — should be identical
        rules_no_inner = {
            "prompt_target": ChildEvalRule(
                included_params=frozenset({"underlying_model_name", "temperature"}),
            ),
        }
        result_no_inner = _build_eval_dict(scorer, child_eval_rules=rules_no_inner)
        assert result == result_no_inner

    def test_scorer_eval_hash_matches_with_and_without_round_robin(self):
        """ScorerEvaluationIdentifier produces the same eval_hash whether
        the scorer uses a direct target or a RoundRobinTarget wrapping it."""
        from pyrit.models.identifiers.evaluation_identifier import ScorerEvaluationIdentifier

        inner_target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "endpoint": "https://east.example.com",
                "model_name": "gpt4o-east",
            },
        )
        inner_target_west = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "endpoint": "https://west.example.com",
                "model_name": "gpt4o-west",
            },
        )

        wrapper = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [1, 1]},
            children={"targets": [inner_target, inner_target_west]},
        )

        scorer_direct = ComponentIdentifier(
            class_name="SelfAskScaleScorer",
            class_module="pyrit.score.self_ask_scale_scorer",
            params={"scorer_type": "float_scale"},
            children={"prompt_target": inner_target},
        )
        scorer_rr = ComponentIdentifier(
            class_name="SelfAskScaleScorer",
            class_module="pyrit.score.self_ask_scale_scorer",
            params={"scorer_type": "float_scale"},
            children={"prompt_target": wrapper},
        )

        eval_direct = ScorerEvaluationIdentifier(scorer_direct).eval_hash
        eval_rr = ScorerEvaluationIdentifier(scorer_rr).eval_hash

        assert eval_direct == eval_rr


class TestComputeInnerAttackEvalHash:
    """``compute_inner_attack_eval_hash`` should match what the executor stamps."""

    def _attack_with_identifier(self, identifier: ComponentIdentifier):
        from unittest.mock import MagicMock

        attack = MagicMock()
        attack.get_identifier.return_value = identifier
        return attack

    def test_matches_manual_two_step_composition(self):
        """Helper equals the executor recipe (build_atomic_attack_identifier + AtomicAttackEvaluationIdentifier)."""
        from pyrit.models.identifiers import (
            AtomicAttackEvaluationIdentifier,
            build_atomic_attack_identifier,
            compute_inner_attack_eval_hash,
        )

        inner_id = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending",
        )
        attack = self._attack_with_identifier(inner_id)

        expected = AtomicAttackEvaluationIdentifier(
            build_atomic_attack_identifier(attack_identifier=inner_id),
        ).eval_hash
        assert compute_inner_attack_eval_hash(attack=attack) == expected

    def test_differs_when_attack_class_differs(self):
        from pyrit.models.identifiers import compute_inner_attack_eval_hash

        a = self._attack_with_identifier(
            ComponentIdentifier(class_name="A", class_module="m"),
        )
        b = self._attack_with_identifier(
            ComponentIdentifier(class_name="B", class_module="m"),
        )
        assert compute_inner_attack_eval_hash(attack=a) != compute_inner_attack_eval_hash(attack=b)

    def test_stable_across_calls_for_same_attack(self):
        from pyrit.models.identifiers import compute_inner_attack_eval_hash

        attack = self._attack_with_identifier(
            ComponentIdentifier(class_name="Same", class_module="m"),
        )
        assert compute_inner_attack_eval_hash(attack=attack) == compute_inner_attack_eval_hash(attack=attack)

    def test_matches_persisted_row_eval_hash(self):
        """Whatever the helper returns, persisting an attack result with the same
        identifier must yield an entry with the same eval_hash."""
        from pyrit.memory.memory_models import AttackResultEntry
        from pyrit.models import AttackResult
        from pyrit.models.identifiers import build_atomic_attack_identifier, compute_inner_attack_eval_hash

        inner_id = ComponentIdentifier(
            class_name="MyAttack",
            class_module="pyrit.attacks",
        )
        attack = self._attack_with_identifier(inner_id)
        predicted = compute_inner_attack_eval_hash(attack=attack)

        result = AttackResult(
            conversation_id="conv_1",
            objective="o",
            atomic_attack_identifier=build_atomic_attack_identifier(attack_identifier=inner_id),
        )
        entry = AttackResultEntry(entry=result)
        assert entry.atomic_attack_identifier["eval_hash"] == predicted


# ---------------------------------------------------------------------------
# OWN_RULE / leaf-entity eval-hash tests
# ---------------------------------------------------------------------------


class TestOwnRule:
    """Tests for compute_eval_hash(own_rule=...) — leaf-entity filtering."""

    def test_own_rule_filters_root_params(self):
        """own_rule.included_params is applied to the root entity's params."""
        target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "endpoint": "https://east.example.com",
            },
        )
        rule = ChildEvalRule(
            included_params=frozenset({"underlying_model_name", "temperature", "top_p"}),
        )

        eval_hash = compute_eval_hash(target, child_eval_rules={}, own_rule=rule)

        # Same target body without endpoint should produce the same hash.
        target_no_endpoint = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
            },
        )
        eval_hash_no_endpoint = compute_eval_hash(target_no_endpoint, child_eval_rules={}, own_rule=rule)
        assert eval_hash == eval_hash_no_endpoint

    def test_own_rule_applies_param_fallbacks_at_root(self):
        """When the primary param is missing at the root, the fallback is substituted."""
        target_primary = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        target_fallback = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "gpt-4o", "temperature": 0.7},
        )
        rule = ChildEvalRule(
            included_params=frozenset({"underlying_model_name", "temperature"}),
            param_fallbacks={"underlying_model_name": "model_name"},
        )

        hash_primary = compute_eval_hash(target_primary, child_eval_rules={}, own_rule=rule)
        hash_fallback = compute_eval_hash(target_fallback, child_eval_rules={}, own_rule=rule)
        assert hash_primary == hash_fallback

    def test_own_rule_raises_on_exclude(self):
        """own_rule.exclude has no meaning at the root."""
        rule = ChildEvalRule(exclude=True)
        target = ComponentIdentifier(class_name="T", class_module="m")
        with pytest.raises(ValueError, match="exclude"):
            compute_eval_hash(target, child_eval_rules={}, own_rule=rule)

    def test_own_rule_raises_on_included_item_values(self):
        """own_rule.included_item_values is only meaningful for list children."""
        rule = ChildEvalRule(included_item_values={"is_general_technique": True})
        target = ComponentIdentifier(class_name="T", class_module="m")
        with pytest.raises(ValueError, match="included_item_values"):
            compute_eval_hash(target, child_eval_rules={}, own_rule=rule)

    def test_own_rule_raises_on_inner_child_name(self):
        """own_rule.inner_child_name is only meaningful for child rules."""
        rule = ChildEvalRule(inner_child_name="targets")
        target = ComponentIdentifier(class_name="T", class_module="m")
        with pytest.raises(ValueError, match="inner_child_name"):
            compute_eval_hash(target, child_eval_rules={}, own_rule=rule)

    def test_short_circuit_only_when_both_empty(self):
        """With own_rule set, the short-circuit MUST NOT return identifier.hash."""
        target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "endpoint": "https://east.example.com"},
        )
        rule = ChildEvalRule(included_params=frozenset({"underlying_model_name"}))
        eval_hash = compute_eval_hash(target, child_eval_rules={}, own_rule=rule)
        # The full identifier hash includes the endpoint; eval_hash must not.
        assert eval_hash != target.hash


class TestEvaluationIdentifierOwnRule:
    """Tests for the EvaluationIdentifier.OWN_RULE ClassVar."""

    def test_own_rule_defaults_to_none(self):
        """Subclasses that do not declare OWN_RULE inherit None."""
        assert _StubEvaluationIdentifier.OWN_RULE is None

    def test_subclass_with_own_rule_filters_root(self):
        """A subclass that sets OWN_RULE filters root params at eval time."""

        class TargetIdentity(EvaluationIdentifier):
            CHILD_EVAL_RULES: ClassVar[dict[str, ChildEvalRule]] = {}
            OWN_RULE: ClassVar = ChildEvalRule(
                included_params=frozenset({"underlying_model_name"}),
            )

        target = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "endpoint": "https://east.example.com"},
        )
        identity = TargetIdentity(target)
        # Eval hash should not equal the raw identifier hash (endpoint must be stripped).
        assert identity.eval_hash != target.hash


# ---------------------------------------------------------------------------
# ObjectiveTargetEvaluationIdentifier tests
# ---------------------------------------------------------------------------


class TestObjectiveTargetEvaluationIdentifier:
    """Tests for the ObjectiveTargetEvaluationIdentifier concrete subclass."""

    def test_different_endpoints_same_eval_hash(self):
        """Same model name + temperature + top_p on different endpoints → same eval hash."""
        from pyrit.models.identifiers.evaluation_identifier import ObjectiveTargetEvaluationIdentifier

        target_east = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "endpoint": "https://east.example.com",
                "model_name": "gpt4o-east",
            },
        )
        target_west = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={
                "underlying_model_name": "gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "endpoint": "https://west.example.com",
                "model_name": "gpt4o-west",
            },
        )

        eval_east = ObjectiveTargetEvaluationIdentifier(target_east).eval_hash
        eval_west = ObjectiveTargetEvaluationIdentifier(target_west).eval_hash
        assert eval_east == eval_west

    def test_different_temperature_different_eval_hash(self):
        """Behavioral params (temperature) DO contribute to the eval hash."""
        from pyrit.models.identifiers.evaluation_identifier import ObjectiveTargetEvaluationIdentifier

        target_cold = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.0},
        )
        target_hot = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 1.0},
        )

        eval_cold = ObjectiveTargetEvaluationIdentifier(target_cold).eval_hash
        eval_hot = ObjectiveTargetEvaluationIdentifier(target_hot).eval_hash
        assert eval_cold != eval_hot

    def test_model_name_fallback_to_model_name(self):
        """When underlying_model_name is missing, model_name is used as fallback."""
        from pyrit.models.identifiers.evaluation_identifier import ObjectiveTargetEvaluationIdentifier

        target_underlying = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o", "temperature": 0.7},
        )
        target_only_model_name = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "gpt-4o", "temperature": 0.7},
        )

        eval_a = ObjectiveTargetEvaluationIdentifier(target_underlying).eval_hash
        eval_b = ObjectiveTargetEvaluationIdentifier(target_only_model_name).eval_hash
        assert eval_a == eval_b

    def test_stored_eval_hash_takes_precedence(self):
        """A pre-stamped eval_hash is honored (DB round-trip safety)."""
        from pyrit.models.identifiers.evaluation_identifier import ObjectiveTargetEvaluationIdentifier

        stored = "objective_target_stored_hash" + "0" * 36
        cid = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"underlying_model_name": "gpt-4o"},
        ).with_eval_hash(stored)

        assert ObjectiveTargetEvaluationIdentifier(cid).eval_hash == stored
