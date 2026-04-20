# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

import pyrit
from pyrit.identifiers import ComponentIdentifier, Identifiable, compute_eval_hash, config_hash
from pyrit.identifiers.evaluation_identifier import ChildEvalRule, _build_eval_dict

# Test constants mirroring Scorer's ClassVars — keeps tests decoupled from pyrit.score
_CHILD_EVAL_RULES: dict[str, ChildEvalRule] = {
    "prompt_target": ChildEvalRule(
        included_params=frozenset({"model_name", "temperature", "top_p"}),
    ),
}


class TestComponentIdentifierCreation:
    """Tests for ComponentIdentifier creation."""

    def test_creation_minimal(self):
        """Test creating a ComponentIdentifier with only required fields."""
        identifier = ComponentIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
        )
        assert identifier.class_name == "TestScorer"
        assert identifier.class_module == "pyrit.score.test_scorer"
        assert identifier.params == {}
        assert identifier.children == {}
        assert identifier.hash is not None
        assert len(identifier.hash) == 64

    def test_creation_with_params(self):
        """Test creating a ComponentIdentifier with params."""
        identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://api.openai.com", "model_name": "gpt-4o"},
        )
        assert identifier.params["endpoint"] == "https://api.openai.com"
        assert identifier.params["model_name"] == "gpt-4o"

    def test_creation_with_children(self):
        """Test creating a ComponentIdentifier with children."""
        child = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
        )
        identifier = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending",
            children={"objective_target": child},
        )
        assert "objective_target" in identifier.children
        child_result = identifier.children["objective_target"]
        assert isinstance(child_result, ComponentIdentifier)
        assert child_result.class_name == "OpenAIChatTarget"

    def test_creation_with_list_children(self):
        """Test creating a ComponentIdentifier with a list of children."""
        child1 = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.converters",
        )
        child2 = ComponentIdentifier(
            class_name="ROT13Converter",
            class_module="pyrit.converters",
        )
        identifier = ComponentIdentifier(
            class_name="TestAttack",
            class_module="pyrit.executor",
            children={"request_converters": [child1, child2]},
        )
        converters = identifier.children["request_converters"]
        assert isinstance(converters, list)
        assert len(converters) == 2
        assert converters[0].class_name == "Base64Converter"
        assert converters[1].class_name == "ROT13Converter"

    def test_pyrit_version_set(self):
        """Test that pyrit_version is set to current version."""
        identifier = ComponentIdentifier(
            class_name="Test",
            class_module="test",
        )
        assert identifier.pyrit_version == pyrit.__version__


class TestComponentIdentifierHash:
    """Tests for hash computation."""

    def test_hash_deterministic(self):
        """Test that identical configs produce the same hash."""
        id1 = ComponentIdentifier(
            class_name="TestClass",
            class_module="test.module",
            params={"key": "value"},
        )
        id2 = ComponentIdentifier(
            class_name="TestClass",
            class_module="test.module",
            params={"key": "value"},
        )
        assert id1.hash == id2.hash

    def test_hash_differs_for_different_class_name(self):
        """Test that different class names produce different hashes."""
        id1 = ComponentIdentifier(class_name="ClassA", class_module="mod")
        id2 = ComponentIdentifier(class_name="ClassB", class_module="mod")
        assert id1.hash != id2.hash

    def test_hash_differs_for_different_class_module(self):
        """Test that different class modules produce different hashes."""
        id1 = ComponentIdentifier(class_name="Class", class_module="mod.a")
        id2 = ComponentIdentifier(class_name="Class", class_module="mod.b")
        assert id1.hash != id2.hash

    def test_hash_differs_for_different_params(self):
        """Test that different params produce different hashes."""
        id1 = ComponentIdentifier(class_name="C", class_module="m", params={"key": "val1"})
        id2 = ComponentIdentifier(class_name="C", class_module="m", params={"key": "val2"})
        assert id1.hash != id2.hash

    def test_hash_excludes_none_params(self):
        """Test that None params are excluded from hash computation."""
        id1 = ComponentIdentifier(class_name="C", class_module="m", params={})
        id2 = ComponentIdentifier(class_name="C", class_module="m", params={"optional": None})
        assert id1.hash == id2.hash

    def test_hash_differs_for_different_children(self):
        """Test that different children produce different hashes."""
        child_a = ComponentIdentifier(class_name="ChildA", class_module="m")
        child_b = ComponentIdentifier(class_name="ChildB", class_module="m")
        id1 = ComponentIdentifier(class_name="Parent", class_module="m", children={"child": child_a})
        id2 = ComponentIdentifier(class_name="Parent", class_module="m", children={"child": child_b})
        assert id1.hash != id2.hash

    def test_hash_does_not_include_pyrit_version(self):
        """Test that pyrit_version does not affect the hash."""
        id1 = ComponentIdentifier(class_name="C", class_module="m")
        # Manually set a different pyrit_version (bypass frozen)
        id2 = ComponentIdentifier(class_name="C", class_module="m", pyrit_version="0.0.0")
        assert id1.hash == id2.hash

    def test_hash_length(self):
        """Test that hash is SHA256 (64 hex chars)."""
        identifier = ComponentIdentifier(class_name="C", class_module="m")
        assert len(identifier.hash) == 64


class TestComponentIdentifierProperties:
    """Tests for computed properties."""

    def test_short_hash(self):
        """Test short_hash returns first 8 chars."""
        identifier = ComponentIdentifier(class_name="Test", class_module="mod")
        assert identifier.short_hash == identifier.hash[:8]
        assert len(identifier.short_hash) == 8

    def test_unique_name(self):
        """Test unique_name format: class_name::short_hash."""
        identifier = ComponentIdentifier(class_name="TestTarget", class_module="mod")
        expected = f"TestTarget::{identifier.short_hash}"
        assert identifier.unique_name == expected


class TestComponentIdentifierToDict:
    """Tests for to_dict serialization."""

    def test_to_dict_basic(self):
        """Test basic to_dict output."""
        identifier = ComponentIdentifier(
            class_name="TestClass",
            class_module="test.module",
        )
        result = identifier.to_dict()
        assert result["class_name"] == "TestClass"
        assert result["class_module"] == "test.module"
        assert result["hash"] == identifier.hash
        assert result["pyrit_version"] == pyrit.__version__

    def test_to_dict_params_inlined(self):
        """Test that params are inlined at top level in to_dict."""
        identifier = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"endpoint": "https://api.example.com", "model_name": "gpt-4o"},
        )
        result = identifier.to_dict()
        assert result["endpoint"] == "https://api.example.com"
        assert result["model_name"] == "gpt-4o"
        # params themselves should NOT appear as a nested dict
        assert "params" not in result

    def test_to_dict_with_children(self):
        """Test that children are nested under 'children' key."""
        child = ComponentIdentifier(class_name="Child", class_module="mod.child")
        identifier = ComponentIdentifier(
            class_name="Parent",
            class_module="mod.parent",
            children={"target": child},
        )
        result = identifier.to_dict()
        assert "children" in result
        assert "target" in result["children"]
        assert result["children"]["target"]["class_name"] == "Child"

    def test_to_dict_with_list_children(self):
        """Test to_dict with list of children."""
        c1 = ComponentIdentifier(class_name="Conv1", class_module="m")
        c2 = ComponentIdentifier(class_name="Conv2", class_module="m")
        identifier = ComponentIdentifier(
            class_name="Attack",
            class_module="m",
            children={"converters": [c1, c2]},
        )
        result = identifier.to_dict()
        assert len(result["children"]["converters"]) == 2
        assert result["children"]["converters"][0]["class_name"] == "Conv1"

    def test_to_dict_no_children_key_when_empty(self):
        """Test that 'children' key is absent when there are no children."""
        identifier = ComponentIdentifier(class_name="C", class_module="m")
        result = identifier.to_dict()
        assert "children" not in result

    def test_to_dict_no_truncation_by_default(self):
        """Test that values are not truncated when max_value_length is not set."""
        long_value = "x" * 200
        identifier = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"system_prompt": long_value},
        )
        result = identifier.to_dict()
        assert result["system_prompt"] == long_value

    def test_to_dict_truncates_long_string_params(self):
        """Test that string params exceeding max_value_length are truncated."""
        long_value = "x" * 200
        identifier = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"system_prompt": long_value},
        )
        result = identifier.to_dict(max_value_length=100)
        assert result["system_prompt"] == "x" * 100 + "..."
        assert len(result["system_prompt"]) == 103

    def test_to_dict_does_not_truncate_short_string_params(self):
        """Test that string params within max_value_length are not truncated."""
        short_value = "short"
        identifier = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"system_prompt": short_value},
        )
        result = identifier.to_dict(max_value_length=100)
        assert result["system_prompt"] == short_value

    def test_to_dict_does_not_truncate_non_string_params(self):
        """Test that non-string params are not affected by max_value_length."""
        identifier = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"count": 999999, "flag": True},
        )
        result = identifier.to_dict(max_value_length=5)
        assert result["count"] == 999999
        assert result["flag"] is True

    def test_to_dict_does_not_truncate_structural_keys(self):
        """Test that class_name, class_module, hash, pyrit_version are never truncated."""
        long_module = "pyrit.module." + "sub." * 50
        identifier = ComponentIdentifier(
            class_name="VeryLongClassNameForTesting",
            class_module=long_module,
        )
        result = identifier.to_dict(max_value_length=10)
        assert result["class_name"] == "VeryLongClassNameForTesting"
        assert result["class_module"] == long_module
        assert result["hash"] == identifier.hash
        assert result["pyrit_version"] == identifier.pyrit_version

    def test_to_dict_truncation_propagates_to_children(self):
        """Test that max_value_length is propagated to children."""
        long_value = "y" * 200
        child = ComponentIdentifier(
            class_name="Child",
            class_module="mod.child",
            params={"endpoint": long_value},
        )
        parent = ComponentIdentifier(
            class_name="Parent",
            class_module="mod.parent",
            children={"target": child},
        )
        result = parent.to_dict(max_value_length=50)
        child_result = result["children"]["target"]
        assert child_result["endpoint"] == "y" * 50 + "..."

    def test_to_dict_truncation_propagates_to_list_children(self):
        """Test that max_value_length is propagated to list children."""
        long_value = "z" * 200
        c1 = ComponentIdentifier(class_name="Conv1", class_module="m", params={"data": long_value})
        c2 = ComponentIdentifier(class_name="Conv2", class_module="m", params={"data": "short"})
        parent = ComponentIdentifier(
            class_name="Attack",
            class_module="m",
            children={"converters": [c1, c2]},
        )
        result = parent.to_dict(max_value_length=80)
        assert result["children"]["converters"][0]["data"] == "z" * 80 + "..."
        assert result["children"]["converters"][1]["data"] == "short"


class TestComponentIdentifierFromDict:
    """Tests for from_dict deserialization."""

    def test_from_dict_basic(self):
        """Test basic from_dict reconstruction."""
        data = {
            "class_name": "TestClass",
            "class_module": "test.module",
            "hash": "a1b2c3d4e5f6" * 5 + "a1b2",  # 62 chars, pad to 64 below
        }
        # Pad to a valid 64-char hex string
        stored_hash = "a1b2c3d4e5f6" * 5 + "a1b2a1b2"
        data["hash"] = stored_hash
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.class_name == "TestClass"
        assert identifier.class_module == "test.module"
        # Stored hash is preserved as-is
        assert identifier.hash == stored_hash

    def test_from_dict_with_params(self):
        """Test from_dict with inlined params."""
        data = {
            "class_name": "Target",
            "class_module": "mod",
            "endpoint": "https://api.example.com",
            "model_name": "gpt-4o",
        }
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.params["endpoint"] == "https://api.example.com"
        assert identifier.params["model_name"] == "gpt-4o"

    def test_from_dict_with_children(self):
        """Test from_dict with nested children."""
        data = {
            "class_name": "Attack",
            "class_module": "mod",
            "children": {
                "target": {
                    "class_name": "OpenAIChatTarget",
                    "class_module": "pyrit.prompt_target",
                },
            },
        }
        identifier = ComponentIdentifier.from_dict(data)
        assert "target" in identifier.children
        child = identifier.children["target"]
        assert isinstance(child, ComponentIdentifier)
        assert child.class_name == "OpenAIChatTarget"

    def test_from_dict_with_list_children(self):
        """Test from_dict with list children."""
        data = {
            "class_name": "Attack",
            "class_module": "mod",
            "children": {
                "converters": [
                    {"class_name": "Conv1", "class_module": "m"},
                    {"class_name": "Conv2", "class_module": "m"},
                ],
            },
        }
        identifier = ComponentIdentifier.from_dict(data)
        converters = identifier.children["converters"]
        assert isinstance(converters, list)
        assert len(converters) == 2
        assert converters[0].class_name == "Conv1"

    def test_from_dict_handles_legacy_type_key(self):
        """Test that from_dict handles legacy '__type__' key."""
        data = {
            "__type__": "LegacyClass",
            "__module__": "legacy.module",
        }
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.class_name == "LegacyClass"
        assert identifier.class_module == "legacy.module"

    def test_from_dict_ignores_unknown_fields_as_params(self):
        """Test that unknown fields become params."""
        data = {
            "class_name": "Test",
            "class_module": "mod",
            "custom_field": "custom_value",
        }
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.params["custom_field"] == "custom_value"

    def test_from_dict_provides_defaults_for_missing_fields(self):
        """Test that from_dict defaults missing class_name/class_module."""
        data = {}
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.class_name == "Unknown"
        assert identifier.class_module == "unknown"

    def test_from_dict_does_not_mutate_input(self):
        """Test that from_dict does not mutate the input dictionary."""
        data = {
            "class_name": "Test",
            "class_module": "mod",
            "key": "value",
        }
        original = dict(data)
        ComponentIdentifier.from_dict(data)
        assert data == original

    def test_from_dict_preserves_stored_hash(self):
        """Test that from_dict preserves the stored hash rather than recomputing it.

        The stored hash was computed from untruncated data and is the correct identity.
        Recomputing from potentially truncated DB values would produce a wrong hash.
        """
        original = ComponentIdentifier(
            class_name="Target",
            class_module="mod",
            params={"system_prompt": "a" * 200},
        )
        original_hash = original.hash

        # Serialize with truncation (simulates DB storage with column limits)
        truncated_dict = original.to_dict(max_value_length=50)
        # The stored hash in truncated_dict is the original (correct) hash
        assert truncated_dict["hash"] == original_hash

        # Deserialize — from_dict should preserve the stored hash
        reconstructed = ComponentIdentifier.from_dict(truncated_dict)
        assert reconstructed.hash == original_hash

    def test_from_dict_preserves_stored_hash_with_children(self):
        """Test that from_dict preserves stored hash when children have truncated params."""
        child = ComponentIdentifier(
            class_name="Child",
            class_module="mod.child",
            params={"endpoint": "x" * 300},
        )
        parent = ComponentIdentifier(
            class_name="Parent",
            class_module="mod.parent",
            children={"target": child},
        )
        original_parent_hash = parent.hash
        original_child_hash = child.hash

        truncated_dict = parent.to_dict(max_value_length=50)
        reconstructed = ComponentIdentifier.from_dict(truncated_dict)

        # Both parent and child should preserve their stored hashes
        assert reconstructed.hash == original_parent_hash
        child_recon = reconstructed.children["target"]
        assert isinstance(child_recon, ComponentIdentifier)
        assert child_recon.hash == original_child_hash

    def test_from_dict_preserves_explicit_stored_hash(self):
        """Test that from_dict uses the stored hash value exactly as provided."""
        known_hash = "abc123def456" * 5 + "abcd"  # 64 chars
        data = {
            "class_name": "Test",
            "class_module": "mod",
            "hash": known_hash,
            "param": "value",
        }
        identifier = ComponentIdentifier.from_dict(data)
        assert identifier.hash == known_hash

    def test_from_dict_computes_hash_when_no_stored_hash(self):
        """Test that from_dict computes a hash when none is stored."""
        data = {
            "class_name": "Test",
            "class_module": "mod",
            "param": "value",
        }
        identifier = ComponentIdentifier.from_dict(data)
        # Should have a valid computed hash
        assert len(identifier.hash) == 64
        # And it should match a freshly constructed identifier
        fresh = ComponentIdentifier(class_name="Test", class_module="mod", params={"param": "value"})
        assert identifier.hash == fresh.hash


class TestComponentIdentifierRoundtrip:
    """Tests for to_dict -> from_dict roundtrip."""

    def test_roundtrip_basic(self):
        """Test basic roundtrip preserves identity."""
        original = ComponentIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score",
            params={"system_prompt": "Score 1-10"},
        )
        reconstructed = ComponentIdentifier.from_dict(original.to_dict())
        assert reconstructed.class_name == original.class_name
        assert reconstructed.class_module == original.class_module
        assert reconstructed.params == original.params
        assert reconstructed.hash == original.hash

    def test_roundtrip_with_children(self):
        """Test roundtrip with nested children."""
        child = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"endpoint": "https://api.example.com"},
        )
        original = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor",
            children={"objective_target": child},
        )
        reconstructed = ComponentIdentifier.from_dict(original.to_dict())
        assert reconstructed.hash == original.hash
        child_recon = reconstructed.children["objective_target"]
        assert isinstance(child_recon, ComponentIdentifier)
        assert child_recon.class_name == "OpenAIChatTarget"
        assert child_recon.params["endpoint"] == "https://api.example.com"

    def test_roundtrip_with_list_children(self):
        """Test roundtrip with list children."""
        c1 = ComponentIdentifier(class_name="Conv1", class_module="m")
        c2 = ComponentIdentifier(class_name="Conv2", class_module="m")
        original = ComponentIdentifier(
            class_name="Attack",
            class_module="m",
            children={"converters": [c1, c2]},
        )
        reconstructed = ComponentIdentifier.from_dict(original.to_dict())
        assert reconstructed.hash == original.hash
        recon_converters = reconstructed.children["converters"]
        assert isinstance(recon_converters, list)
        assert len(recon_converters) == 2

    def test_roundtrip_preserves_eval_hash(self):
        """Test that eval_hash is preserved through to_dict -> from_dict round-trip."""
        expected_eval_hash = "abc123" * 10 + "abcd"  # 64 chars
        original = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"system_prompt": "Score the response"},
        ).with_eval_hash(expected_eval_hash)
        d = original.to_dict()
        assert d["eval_hash"] == expected_eval_hash

        reconstructed = ComponentIdentifier.from_dict(d)
        assert reconstructed.eval_hash == expected_eval_hash

    def test_roundtrip_eval_hash_survives_truncation(self):
        """Regression test: eval_hash computed before truncation is preserved after round-trip.

        This is the core bug fix — long params get truncated in to_dict(), which would
        cause eval_hash recomputation to produce a wrong hash. By storing eval_hash in
        the dict, it survives truncation.
        """
        long_prompt = "You are a scorer that evaluates responses. " * 20  # >80 chars
        eval_hash_before_truncation = "correct_eval_hash_" + "0" * 46  # 64 chars
        original = ComponentIdentifier(
            class_name="SelfAskTrueFalseScorer",
            class_module="pyrit.score",
            params={"system_prompt_template": long_prompt},
        ).with_eval_hash(eval_hash_before_truncation)

        # Serialize with truncation (simulates DB storage)
        truncated_dict = original.to_dict(max_value_length=80)
        # Params are truncated
        assert truncated_dict["system_prompt_template"].endswith("...")
        # But eval_hash is preserved
        assert truncated_dict["eval_hash"] == eval_hash_before_truncation

        # Deserialize
        reconstructed = ComponentIdentifier.from_dict(truncated_dict)
        # eval_hash is available on the reconstructed identifier
        assert reconstructed.eval_hash == eval_hash_before_truncation
        # And it's NOT in params (from_dict pops it as a reserved key)
        assert "eval_hash" not in reconstructed.params

    def test_roundtrip_no_eval_hash_when_not_set(self):
        """Test that eval_hash is None when not set on the identifier."""
        original = ComponentIdentifier(
            class_name="Test",
            class_module="mod",
            params={"key": "value"},
        )
        d = original.to_dict()
        assert "eval_hash" not in d

        reconstructed = ComponentIdentifier.from_dict(d)
        assert reconstructed.eval_hash is None

    def test_to_dict_includes_eval_hash_from_prior_roundtrip(self):
        """Test that to_dict re-emits eval_hash from a prior round-trip."""
        eval_hash = "deadbeef" * 8  # 64 chars
        original = ComponentIdentifier(
            class_name="Test",
            class_module="mod",
        ).with_eval_hash(eval_hash)
        d1 = original.to_dict()
        reconstructed = ComponentIdentifier.from_dict(d1)

        # Re-serialize — eval_hash should be emitted
        d2 = reconstructed.to_dict()
        assert d2["eval_hash"] == eval_hash

    def test_double_roundtrip_preserves_eval_hash_and_identity_hash(self):
        """Test that both eval_hash and identity hash survive retrieve → re-store → retrieve."""
        long_prompt = "Score the response carefully. " * 20
        original = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            params={"system_prompt": long_prompt},
        )
        original_hash = original.hash
        eval_hash = "eval_" + "a1b2c3d4" * 7 + "a1b2c3"  # 64 chars
        original = original.with_eval_hash(eval_hash)

        # First round-trip: store with truncation
        d1 = original.to_dict(max_value_length=80)
        r1 = ComponentIdentifier.from_dict(d1)
        assert r1.hash == original_hash
        assert r1.eval_hash == eval_hash

        # Second round-trip: re-store (simulating retrieve → use → re-store)
        d2 = r1.to_dict(max_value_length=80)
        r2 = ComponentIdentifier.from_dict(d2)
        assert r2.hash == original_hash
        assert r2.eval_hash == eval_hash


class TestComponentIdentifierNormalize:
    """Tests for normalize class method."""

    def test_normalize_returns_component_identifier_unchanged(self):
        """Test that normalize returns a ComponentIdentifier as-is."""
        original = ComponentIdentifier(class_name="Test", class_module="mod")
        result = ComponentIdentifier.normalize(original)
        assert result is original

    def test_normalize_converts_dict(self):
        """Test that normalize converts a dict to ComponentIdentifier."""
        data = {"class_name": "Test", "class_module": "mod", "endpoint": "https://api.example.com"}
        result = ComponentIdentifier.normalize(data)
        assert isinstance(result, ComponentIdentifier)
        assert result.class_name == "Test"
        assert result.params["endpoint"] == "https://api.example.com"

    def test_normalize_raises_for_invalid_type(self):
        """Test that normalize raises TypeError for non-dict/non-ComponentIdentifier."""
        with pytest.raises(TypeError, match="Expected ComponentIdentifier or dict"):
            ComponentIdentifier.normalize("invalid")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="Expected ComponentIdentifier or dict"):
            ComponentIdentifier.normalize(123)  # type: ignore[arg-type]


class TestComponentIdentifierFrozen:
    """Tests for frozen immutability."""

    def test_cannot_modify_class_name(self):
        """Test that class_name is immutable."""
        identifier = ComponentIdentifier(class_name="Test", class_module="mod")
        with pytest.raises(AttributeError):
            identifier.class_name = "Modified"  # type: ignore[misc]

    def test_cannot_modify_hash(self):
        """Test that hash is immutable."""
        identifier = ComponentIdentifier(class_name="Test", class_module="mod")
        with pytest.raises(AttributeError):
            identifier.hash = "new_hash"  # type: ignore[misc]

    def test_not_natively_hashable_due_to_dict_fields(self):
        """Test that frozen identifier with dict fields is not natively hashable."""
        identifier = ComponentIdentifier(class_name="Test", class_module="mod")
        with pytest.raises(TypeError):
            hash(identifier)


class TestComponentIdentifierOf:
    """Tests for the ComponentIdentifier.of() factory method."""

    def test_of_extracts_class_info(self):
        """Test that of() extracts class name and module from an object."""

        class MyScorer:
            pass

        obj = MyScorer()
        identifier = ComponentIdentifier.of(obj)
        assert identifier.class_name == "MyScorer"
        assert "test_component_identifier" in identifier.class_module

    def test_of_with_params(self):
        """Test that of() includes params."""

        class MyTarget:
            pass

        obj = MyTarget()
        identifier = ComponentIdentifier.of(obj, params={"endpoint": "https://api.example.com"})
        assert identifier.params["endpoint"] == "https://api.example.com"

    def test_of_filters_none_params(self):
        """Test that of() filters out None-valued params."""

        class MyTarget:
            pass

        obj = MyTarget()
        identifier = ComponentIdentifier.of(
            obj,
            params={"endpoint": "https://api.example.com", "model_name": None},
        )
        assert "endpoint" in identifier.params
        assert "model_name" not in identifier.params

    def test_of_with_children(self):
        """Test that of() includes children."""

        class MyAttack:
            pass

        child = ComponentIdentifier(class_name="Child", class_module="mod")
        obj = MyAttack()
        identifier = ComponentIdentifier.of(obj, children={"target": child})
        assert "target" in identifier.children


class TestComponentIdentifierStrRepr:
    """Tests for __str__ and __repr__."""

    def test_str_format(self):
        """Test __str__ returns ClassName::short_hash."""
        identifier = ComponentIdentifier(class_name="TestScorer", class_module="mod")
        result = str(identifier)
        assert result == f"TestScorer::{identifier.short_hash}"

    def test_repr_includes_details(self):
        """Test __repr__ includes class, params, and hash."""
        identifier = ComponentIdentifier(
            class_name="TestTarget",
            class_module="mod",
            params={"endpoint": "https://api.example.com"},
        )
        result = repr(identifier)
        assert "ComponentIdentifier" in result
        assert "TestTarget" in result
        assert "endpoint" in result
        assert identifier.short_hash in result


class TestConfigHash:
    """Tests for the config_hash utility function."""

    def test_deterministic(self):
        """Test that config_hash is deterministic."""
        d = {"key": "value", "num": 42}
        assert config_hash(d) == config_hash(d)

    def test_differs_for_different_dicts(self):
        """Test that different dicts produce different hashes."""
        assert config_hash({"a": 1}) != config_hash({"a": 2})

    def test_key_order_independent(self):
        """Test that key order does not affect hash (sorted keys)."""
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert config_hash(d1) == config_hash(d2)


class TestIdentifiable:
    """Tests for the Identifiable abstract base class."""

    def test_identifiable_requires_build_identifier(self):
        """Test that Identifiable requires _build_identifier implementation."""
        with pytest.raises(TypeError):
            Identifiable()  # type: ignore[abstract]

    def test_identifiable_get_identifier_caches(self):
        """Test that get_identifier caches the result."""

        class MyComponent(Identifiable):
            def __init__(self):
                self.build_count = 0

            def _build_identifier(self) -> ComponentIdentifier:
                self.build_count += 1
                return ComponentIdentifier(class_name="MyComponent", class_module="test")

        component = MyComponent()
        id1 = component.get_identifier()
        id2 = component.get_identifier()
        assert id1 is id2
        assert component.build_count == 1

    def test_identifiable_returns_component_identifier(self):
        """Test that get_identifier returns a ComponentIdentifier."""

        class MyComponent(Identifiable):
            def _build_identifier(self) -> ComponentIdentifier:
                return ComponentIdentifier.of(self, params={"key": "val"})

        component = MyComponent()
        identifier = component.get_identifier()
        assert isinstance(identifier, ComponentIdentifier)
        assert identifier.class_name == "MyComponent"
        assert identifier.params["key"] == "val"


class TestBuildEvalDict:
    """Tests for the _build_eval_dict function."""

    def test_basic_identifier_without_params_or_children(self):
        """Test _build_eval_dict with a simple identifier with no params or children."""
        identifier = ComponentIdentifier(
            class_name="SimpleScorer",
            class_module="pyrit.score",
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert result["class_name"] == "SimpleScorer"
        assert result["class_module"] == "pyrit.score"
        assert "children" not in result

    def test_includes_all_non_none_params(self):
        """Test that all non-None params are included in the eval dict."""
        identifier = ComponentIdentifier(
            class_name="ParamScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5, "template": "prompt_text", "mode": "strict"},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert result["threshold"] == 0.5
        assert result["template"] == "prompt_text"
        assert result["mode"] == "strict"

    def test_included_params_filters_params(self):
        """Test that _included_params restricts which params are included."""
        identifier = ComponentIdentifier(
            class_name="FilteredScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5, "template": "prompt_text", "mode": "strict"},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
            _included_params=frozenset({"threshold", "mode"}),
        )

        assert result["threshold"] == 0.5
        assert result["mode"] == "strict"
        assert "template" not in result

    def test_none_params_are_excluded(self):
        """Test that None-valued params are excluded from the eval dict."""
        identifier = ComponentIdentifier(
            class_name="NoneScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5, "optional_field": None},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert result["threshold"] == 0.5
        assert "optional_field" not in result

    def test_target_children_hashed_with_behavioral_params_only(self):
        """Test that target children are projected to behavioral params only."""
        child = ComponentIdentifier(
            class_name="ChildTarget",
            class_module="pyrit.target",
            params={
                "model_name": "gpt-4",
                "temperature": 0.7,
                "top_p": 0.9,
                "max_requests_per_minute": 100,
                "endpoint": "https://example.com",
            },
        )
        identifier = ComponentIdentifier(
            class_name="ParentScorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert "children" in result
        assert isinstance(result["children"]["prompt_target"], str)

    def test_target_children_same_behavioral_different_operational_produce_same_hash(self):
        """Test that target children differing only in operational params produce the same child hash."""
        child1 = ComponentIdentifier(
            class_name="ChildTarget",
            class_module="pyrit.target",
            params={
                "model_name": "gpt-4",
                "temperature": 0.7,
                "endpoint": "https://endpoint-a.com",
                "max_requests_per_minute": 50,
            },
        )
        child2 = ComponentIdentifier(
            class_name="ChildTarget",
            class_module="pyrit.target",
            params={
                "model_name": "gpt-4",
                "temperature": 0.7,
                "endpoint": "https://endpoint-b.com",
                "max_requests_per_minute": 200,
            },
        )
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child1},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child2},
        )
        result1 = _build_eval_dict(id1, child_eval_rules=_CHILD_EVAL_RULES)
        result2 = _build_eval_dict(id2, child_eval_rules=_CHILD_EVAL_RULES)

        assert result1["children"]["prompt_target"] == result2["children"]["prompt_target"]

    def test_target_children_different_behavioral_produce_different_hash(self):
        """Test that target children differing in behavioral params produce different child hashes."""
        child1 = ComponentIdentifier(
            class_name="ChildTarget",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "temperature": 0.7},
        )
        child2 = ComponentIdentifier(
            class_name="ChildTarget",
            class_module="pyrit.target",
            params={"model_name": "gpt-3.5-turbo", "temperature": 0.7},
        )
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child1},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child2},
        )
        result1 = _build_eval_dict(id1, child_eval_rules=_CHILD_EVAL_RULES)
        result2 = _build_eval_dict(id2, child_eval_rules=_CHILD_EVAL_RULES)

        assert result1["children"]["prompt_target"] != result2["children"]["prompt_target"]

    def test_multiple_children_as_list(self):
        """Test that list-valued children produce a list of hashes."""
        child_a = ComponentIdentifier(
            class_name="ChildA",
            class_module="pyrit.target",
            params={"model_name": "gpt-4"},
        )
        child_b = ComponentIdentifier(
            class_name="ChildB",
            class_module="pyrit.target",
            params={"model_name": "gpt-3.5-turbo"},
        )
        identifier = ComponentIdentifier(
            class_name="MultiChildScorer",
            class_module="pyrit.score",
            children={"targets": [child_a, child_b]},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert "children" in result
        assert isinstance(result["children"]["targets"], list)
        assert len(result["children"]["targets"]) == 2

    def test_single_child_unwrapped(self):
        """Test that a single child is a scalar hash, not a list."""
        child = ComponentIdentifier(
            class_name="OnlyChild",
            class_module="pyrit.target",
            params={"model_name": "gpt-4"},
        )
        identifier = ComponentIdentifier(
            class_name="SingleChildScorer",
            class_module="pyrit.score",
            children={"target": child},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert isinstance(result["children"]["target"], str)

    def test_no_children_key_when_empty(self):
        """Test that 'children' key is absent when there are no children."""
        identifier = ComponentIdentifier(
            class_name="NoChildScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5},
        )
        result = _build_eval_dict(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert "children" not in result

    def test_non_target_children_with_different_params_produce_different_hash(self):
        """Test that non-target children differing in any param (including operational) produce different hashes."""
        child1 = ComponentIdentifier(
            class_name="SubScorer",
            class_module="pyrit.score",
            params={"system_prompt_template": "prompt_a", "endpoint": "https://a.com"},
        )
        child2 = ComponentIdentifier(
            class_name="SubScorer",
            class_module="pyrit.score",
            params={"system_prompt_template": "prompt_a", "endpoint": "https://b.com"},
        )
        id1 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"sub_scorer": child1},
        )
        id2 = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"sub_scorer": child2},
        )
        result1 = _build_eval_dict(id1, child_eval_rules=_CHILD_EVAL_RULES)
        result2 = _build_eval_dict(id2, child_eval_rules=_CHILD_EVAL_RULES)

        assert result1["children"]["sub_scorer"] != result2["children"]["sub_scorer"]

    def test_target_vs_non_target_children_handled_differently(self):
        """Test that target children filter params while non-target children keep all params."""
        child = ComponentIdentifier(
            class_name="SomeComponent",
            class_module="pyrit.target",
            params={"model_name": "gpt-4", "endpoint": "https://example.com"},
        )

        id_as_target = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )
        id_as_non_target = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"sub_scorer": child},
        )

        result_target = _build_eval_dict(id_as_target, child_eval_rules=_CHILD_EVAL_RULES)
        result_non_target = _build_eval_dict(id_as_non_target, child_eval_rules=_CHILD_EVAL_RULES)

        assert result_target["children"]["prompt_target"] != result_non_target["children"]["sub_scorer"]


class TestComputeEvalHash:
    """Tests for the compute_eval_hash free function."""

    def test_deterministic_for_same_identifier(self):
        """Test that compute_eval_hash returns the same hash for the same identifier."""
        identifier = ComponentIdentifier(
            class_name="StableScorer",
            class_module="pyrit.score",
            params={"threshold": 0.5},
        )
        hash1 = compute_eval_hash(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )
        hash2 = compute_eval_hash(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert hash1 == hash2

    def test_returns_hex_string(self):
        """Test that compute_eval_hash returns a valid hex string."""
        identifier = ComponentIdentifier(
            class_name="HexScorer",
            class_module="pyrit.score",
        )
        result = compute_eval_hash(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )

        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_class_names_produce_different_hashes(self):
        """Test that different class names produce different eval hashes."""
        id1 = ComponentIdentifier(class_name="ScorerA", class_module="pyrit.score")
        id2 = ComponentIdentifier(class_name="ScorerB", class_module="pyrit.score")

        assert compute_eval_hash(id1, child_eval_rules=_CHILD_EVAL_RULES) != compute_eval_hash(
            id2, child_eval_rules=_CHILD_EVAL_RULES
        )

    def test_different_params_produce_different_hashes(self):
        """Test that different params produce different eval hashes."""
        id1 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", params={"threshold": 0.5})
        id2 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", params={"threshold": 0.8})

        assert compute_eval_hash(id1, child_eval_rules=_CHILD_EVAL_RULES) != compute_eval_hash(
            id2, child_eval_rules=_CHILD_EVAL_RULES
        )

    def test_eval_hash_differs_from_component_hash(self):
        """Test that eval hash differs from component hash when target children have operational params."""
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

        eval_hash = compute_eval_hash(
            identifier,
            child_eval_rules=_CHILD_EVAL_RULES,
        )
        assert eval_hash != identifier.hash

    def test_operational_child_params_ignored(self):
        """Test that operational params on target children don't affect eval hash."""
        child1 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={
                "model_name": "gpt-4",
                "temperature": 0.7,
                "endpoint": "https://endpoint-a.com",
                "max_requests_per_minute": 50,
            },
        )
        child2 = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            params={
                "model_name": "gpt-4",
                "temperature": 0.7,
                "endpoint": "https://endpoint-b.com",
                "max_requests_per_minute": 200,
            },
        )
        id1 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child1})
        id2 = ComponentIdentifier(class_name="Scorer", class_module="pyrit.score", children={"prompt_target": child2})

        assert compute_eval_hash(id1, child_eval_rules=_CHILD_EVAL_RULES) == compute_eval_hash(
            id2, child_eval_rules=_CHILD_EVAL_RULES
        )

    def test_included_child_params_affect_eval_hash(self):
        """Test that included params on target children do affect eval hash."""
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

        assert compute_eval_hash(id1, child_eval_rules=_CHILD_EVAL_RULES) != compute_eval_hash(
            id2, child_eval_rules=_CHILD_EVAL_RULES
        )

    def test_scorer_own_params_all_included(self):
        """Test that all of the scorer's own params (not just behavioral) are included."""
        id1 = ComponentIdentifier(
            class_name="Scorer", class_module="pyrit.score", params={"system_prompt_template": "template_a"}
        )
        id2 = ComponentIdentifier(
            class_name="Scorer", class_module="pyrit.score", params={"system_prompt_template": "template_b"}
        )

        assert compute_eval_hash(id1, child_eval_rules=_CHILD_EVAL_RULES) != compute_eval_hash(
            id2, child_eval_rules=_CHILD_EVAL_RULES
        )

    def test_empty_rules_returns_component_hash(self):
        """Test that empty child_eval_rules means no filtering — returns component hash."""
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


class TestCollectChildEvalHashes:
    """Tests for ComponentIdentifier._collect_child_eval_hashes."""

    def test_no_children_returns_empty(self):
        """Test that an identifier with no children returns empty set."""
        identifier = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
        )
        assert identifier._collect_child_eval_hashes() == set()

    def test_single_child_with_eval_hash(self):
        """Test collecting eval_hash from a single child."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            eval_hash="abc123",
        )
        parent = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )
        assert parent._collect_child_eval_hashes() == {"abc123"}

    def test_child_without_eval_hash_excluded(self):
        """Test that children without eval_hash are not included."""
        child = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
        )
        parent = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            children={"prompt_target": child},
        )
        assert parent._collect_child_eval_hashes() == set()

    def test_list_children_with_eval_hashes(self):
        """Test collecting eval_hashes from a list of children."""
        child1 = ComponentIdentifier(
            class_name="Scorer1",
            class_module="pyrit.score",
            eval_hash="hash1",
        )
        child2 = ComponentIdentifier(
            class_name="Scorer2",
            class_module="pyrit.score",
            eval_hash="hash2",
        )
        parent = ComponentIdentifier(
            class_name="Composite",
            class_module="pyrit.score",
            children={"sub_scorers": [child1, child2]},
        )
        assert parent._collect_child_eval_hashes() == {"hash1", "hash2"}

    def test_nested_children_collected_recursively(self):
        """Test that eval_hashes are collected from deeply nested children."""
        grandchild = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
            eval_hash="deep_hash",
        )
        child = ComponentIdentifier(
            class_name="InnerScorer",
            class_module="pyrit.score",
            eval_hash="child_hash",
            children={"prompt_target": grandchild},
        )
        parent = ComponentIdentifier(
            class_name="OuterScorer",
            class_module="pyrit.score",
            children={"sub_scorers": [child]},
        )
        assert parent._collect_child_eval_hashes() == {"child_hash", "deep_hash"}

    def test_mixed_children_with_and_without_eval_hash(self):
        """Test a mix of children where only some have eval_hash."""
        child_with = ComponentIdentifier(
            class_name="Scorer",
            class_module="pyrit.score",
            eval_hash="has_hash",
        )
        child_without = ComponentIdentifier(
            class_name="Target",
            class_module="pyrit.target",
        )
        parent = ComponentIdentifier(
            class_name="Composite",
            class_module="pyrit.score",
            children={"sub_scorers": [child_with, child_without]},
        )
        assert parent._collect_child_eval_hashes() == {"has_hash"}


def test_short_hash_raises_when_hash_none():
    obj = ComponentIdentifier.__new__(ComponentIdentifier)
    object.__setattr__(obj, "hash", None)
    object.__setattr__(obj, "class_name", "Test")
    object.__setattr__(obj, "class_module", "test.module")
    with pytest.raises(RuntimeError, match="hash should be set by __post_init__"):
        _ = obj.short_hash
