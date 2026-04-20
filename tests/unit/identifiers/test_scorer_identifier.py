# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib

import pytest

from pyrit.identifiers import ScorerIdentifier


class TestScorerIdentifierBasic:
    """Test basic ScorerIdentifier functionality."""

    def test_scorer_identifier_creation_minimal(self):
        """Test creating a ScorerIdentifier with only required fields."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        )

        assert identifier.class_name == "TestScorer"
        assert identifier.class_module == "pyrit.score.test_scorer"
        assert identifier.system_prompt_template is None
        assert identifier.user_prompt_template is None
        assert identifier.sub_identifier is None
        assert identifier.target_info is None
        assert identifier.score_aggregator is None
        assert identifier.scorer_specific_params is None
        assert identifier.hash is not None
        assert len(identifier.hash) == 64  # SHA256 hex digest length

    def test_scorer_identifier_name_auto_computed(self):
        """Test that unique_name is auto-computed from class_name and hash."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        )

        # unique_name format: {full_snake_case}::{hash[:8]}
        assert identifier.unique_name.startswith("test_scorer::")
        assert len(identifier.unique_name.split("::")[1]) == 8
        assert identifier.unique_name == f"test_scorer::{identifier.hash[:8]}"

    def test_scorer_identifier_snake_class_name(self):
        """Test that snake_class_name converts to snake_case."""
        identifier = ScorerIdentifier(
            class_name="SelfAskRefusalScorer",
            class_module="pyrit.score.self_ask_refusal_scorer",
            class_description="A refusal scorer",
            identifier_type="instance",
        )

        # snake_class_name is the full snake_case class name
        assert identifier.snake_class_name == "self_ask_refusal_scorer"
        # unique_name uses the same snake case with hash
        assert identifier.unique_name.startswith("self_ask_refusal_scorer::")

    def test_scorer_identifier_creation_all_fields(self):
        """Test creating a ScorerIdentifier with all fields."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template="System prompt",
            user_prompt_template="User prompt",
            sub_identifier=[{"class_name": "SubScorer"}],
            target_info={"model_name": "gpt-4", "temperature": 0.7},
            score_aggregator="mean",
            scorer_specific_params={"param1": "value1"},
        )

        assert identifier.system_prompt_template == "System prompt"
        assert identifier.user_prompt_template == "User prompt"
        assert len(identifier.sub_identifier) == 1
        assert identifier.target_info["model_name"] == "gpt-4"
        assert identifier.score_aggregator == "mean"
        assert identifier.scorer_specific_params["param1"] == "value1"


class TestScorerIdentifierHash:
    """Test hash computation for ScorerIdentifier."""

    def test_hash_deterministic(self):
        """Test that hash is the same for identical configurations."""
        identifier1 = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template="Test prompt",
        )
        identifier2 = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template="Test prompt",
        )

        assert identifier1.hash == identifier2.hash
        assert len(identifier1.hash) == 64  # SHA256 hex digest length

    def test_hash_different_for_different_configs(self):
        """Test that different configurations produce different hashes."""
        base_args = {
            "class_module": "pyrit.score.test_scorer",
            "class_description": "A test scorer",
            "identifier_type": "instance",
        }

        identifier1 = ScorerIdentifier(class_name="TestScorer", **base_args)
        identifier2 = ScorerIdentifier(class_name="TestScorer", system_prompt_template="prompt", **base_args)
        identifier3 = ScorerIdentifier(class_name="OtherScorer", **base_args)

        assert identifier1.hash != identifier2.hash
        assert identifier1.hash != identifier3.hash
        assert identifier2.hash != identifier3.hash

    def test_hash_uses_full_prompt_not_truncated(self):
        """Test that hash is computed from full prompt values, not truncated."""
        long_prompt = "A" * 200

        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template=long_prompt,
        )

        # If hash was computed from truncated value, these would have the same hash
        identifier2 = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template="B" * 200,
        )

        assert identifier.hash != identifier2.hash


class TestScorerIdentifierToDict:
    """Test to_dict method for ScorerIdentifier."""

    def test_to_dict_basic(self):
        """Test basic to_dict output."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        )

        result = identifier.to_dict()

        assert result["class_name"] == "TestScorer"
        assert result["class_module"] == "pyrit.score.test_scorer"
        assert result["hash"] == identifier.hash
        assert result["unique_name"] == identifier.unique_name
        # class_description and identifier_type should be excluded
        assert "class_description" not in result
        assert "identifier_type" not in result
        # None values should be excluded
        assert "system_prompt_template" not in result

    def test_to_dict_short_prompt_preserved(self):
        """Test that short prompts (<= 100 chars) are preserved as-is."""
        short_prompt = "A" * 100  # Exactly 100 characters
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template=short_prompt,
            user_prompt_template=short_prompt,
        )

        result = identifier.to_dict()

        assert result["system_prompt_template"] == short_prompt
        assert result["user_prompt_template"] == short_prompt

    def test_to_dict_long_prompt_hashed(self):
        """Test that long prompts (> 100 chars) include hash suffix in to_dict."""
        long_prompt = "A" * 101  # Just over 100 characters
        expected_hash = hashlib.sha256(long_prompt.encode()).hexdigest()[:16]

        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template=long_prompt,
            user_prompt_template=long_prompt,
        )

        result = identifier.to_dict()

        # Long prompts are truncated to 100 chars with hash suffix appended
        assert result["system_prompt_template"].endswith(f"[sha256:{expected_hash}]")
        assert result["user_prompt_template"].endswith(f"[sha256:{expected_hash}]")
        assert len(result["system_prompt_template"]) <= 100 + len(f"... [sha256:{expected_hash}]")

    def test_to_dict_hash_matches_original(self):
        """Test that the hash in to_dict matches the object's hash."""
        long_prompt = "B" * 200

        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template=long_prompt,
        )

        result = identifier.to_dict()

        # Hash in to_dict should be the original hash (computed from full prompt)
        assert result["hash"] == identifier.hash


class TestScorerIdentifierFromDict:
    """Test from_dict method for ScorerIdentifier."""

    def test_from_dict_basic(self):
        """Test creating ScorerIdentifier from a basic dict."""
        data = {
            "class_name": "TestScorer",
            "class_module": "pyrit.score.test_scorer",
            "class_description": "A test scorer",
            "identifier_type": "instance",
        }

        identifier = ScorerIdentifier.from_dict(data)

        assert identifier.class_name == "TestScorer"
        # unique_name is auto-computed
        assert identifier.unique_name.startswith("test_scorer::")

    def test_from_dict_handles_legacy_type_key(self):
        """Test that from_dict handles legacy '__type__' key."""
        data = {
            "__type__": "TestScorer",  # Legacy key
            "class_module": "pyrit.score.test_scorer",
            "class_description": "A test scorer",
            "identifier_type": "instance",
        }

        identifier = ScorerIdentifier.from_dict(data)

        assert identifier.class_name == "TestScorer"

    def test_from_dict_handles_deprecated_type_key(self):
        """Test that from_dict handles deprecated 'type' key with warning."""
        data = {
            "type": "TestScorer",  # Deprecated key
            "class_module": "pyrit.score.test_scorer",
            "class_description": "A test scorer",
            "identifier_type": "instance",
        }

        with pytest.warns(DeprecationWarning, match="'type' key in Identifier dict is deprecated"):
            identifier = ScorerIdentifier.from_dict(data)

        assert identifier.class_name == "TestScorer"

    def test_from_dict_ignores_unknown_fields(self):
        """Test that from_dict ignores fields not in the dataclass."""
        data = {
            "class_name": "TestScorer",
            "class_module": "pyrit.score.test_scorer",
            "class_description": "A test scorer",
            "identifier_type": "instance",
            "unknown_field": "should be ignored",
            "hash": "abc123stored_hash_preserved",
            "unique_name": "stored_name_ignored_because_recomputed",
        }

        identifier = ScorerIdentifier.from_dict(data)

        assert identifier.class_name == "TestScorer"
        # hash is preserved from dict (not recomputed) to handle truncated fields
        assert identifier.hash == "abc123stored_hash_preserved"
        # unique_name is recomputed from hash
        assert identifier.unique_name == f"test_scorer::{identifier.hash[:8]}"

    def test_from_dict_roundtrip(self):
        """Test that to_dict -> from_dict roundtrip works."""
        original = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
            system_prompt_template="Short prompt",
            target_info={"model": "gpt-4"},
        )

        storage_dict = original.to_dict()
        # Add back the excluded fields for reconstruction
        storage_dict["class_description"] = "A test scorer"
        storage_dict["identifier_type"] = "instance"

        reconstructed = ScorerIdentifier.from_dict(storage_dict)

        assert reconstructed.class_name == original.class_name
        assert reconstructed.system_prompt_template == original.system_prompt_template
        assert reconstructed.target_info == original.target_info
        # Hash should match since config is the same
        assert reconstructed.hash == original.hash


class TestScorerIdentifierFrozen:
    """Test that ScorerIdentifier is immutable (frozen)."""

    def test_cannot_modify_fields(self):
        """Test that attempting to modify fields raises an error."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        )

        with pytest.raises(AttributeError):
            identifier.class_name = "ModifiedScorer"

        with pytest.raises(AttributeError):
            identifier.system_prompt_template = "Modified"

    def test_can_use_as_dict_key(self):
        """Test that frozen identifier can be used as dict key (hashable)."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        )

        # Should be hashable and usable as dict key
        d = {identifier: "value"}
        assert d[identifier] == "value"
