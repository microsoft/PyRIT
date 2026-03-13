# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for pyrit.identifiers.evaluation_identifier.

Covers the ``EvaluationIdentifier`` abstract base class, the ``_build_eval_dict``
helper, and the ``compute_eval_hash`` free function.
"""

from typing import ClassVar

import pytest

from pyrit.identifiers import ComponentIdentifier, compute_eval_hash
from pyrit.identifiers.evaluation_identifier import ChildEvalRule, EvaluationIdentifier, _build_eval_dict

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
