# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the harm_definition module.
"""

from pathlib import Path

import pytest
import yaml

from pyrit.common.path import HARM_DEFINITION_PATH
from pyrit.models.harm_definition import (
    HarmDefinition,
    ScaleDescription,
    get_all_harm_definitions,
)


class TestScaleDescription:
    """Tests for the ScaleDescription dataclass."""

    def test_scale_description_creation(self) -> None:
        """Test creating a ScaleDescription with valid values."""
        scale = ScaleDescription(score_value="1", description="No harm present.")
        assert scale.score_value == "1"
        assert scale.description == "No harm present."

    def test_scale_description_with_numeric_score_value(self) -> None:
        """Test that score_value is stored as string."""
        scale = ScaleDescription(score_value="5", description="Maximum harm.")
        assert scale.score_value == "5"
        assert isinstance(scale.score_value, str)


class TestHarmDefinition:
    """Tests for the HarmDefinition dataclass."""

    def test_harm_definition_creation(self) -> None:
        """Test creating a HarmDefinition with valid values."""
        scales = [
            ScaleDescription(score_value="1", description="No harm."),
            ScaleDescription(score_value="5", description="Maximum harm."),
        ]
        harm_def = HarmDefinition(
            version="1.0",
            category="violence",
            scale_descriptions=scales,
            source_path="/path/to/file.yaml",
        )
        assert harm_def.version == "1.0"
        assert harm_def.category == "violence"
        assert len(harm_def.scale_descriptions) == 2
        assert harm_def.source_path == "/path/to/file.yaml"

    def test_harm_definition_default_values(self) -> None:
        """Test HarmDefinition with default values."""
        harm_def = HarmDefinition(version="1.0", category="test")
        assert harm_def.scale_descriptions == []
        assert harm_def.source_path is None

    def test_get_scale_description_found(self) -> None:
        """Test get_scale_description returns correct description when found."""
        scales = [
            ScaleDescription(score_value="1", description="No harm."),
            ScaleDescription(score_value="3", description="Moderate harm."),
            ScaleDescription(score_value="5", description="Maximum harm."),
        ]
        harm_def = HarmDefinition(version="1.0", category="test", scale_descriptions=scales)

        assert harm_def.get_scale_description("1") == "No harm."
        assert harm_def.get_scale_description("3") == "Moderate harm."
        assert harm_def.get_scale_description("5") == "Maximum harm."

    def test_get_scale_description_not_found(self) -> None:
        """Test get_scale_description returns None when not found."""
        scales = [ScaleDescription(score_value="1", description="No harm.")]
        harm_def = HarmDefinition(version="1.0", category="test", scale_descriptions=scales)

        assert harm_def.get_scale_description("99") is None
        assert harm_def.get_scale_description("") is None


class TestHarmDefinitionValidateCategory:
    """Tests for the HarmDefinition.validate_category static method."""

    def test_validate_category_valid_lowercase(self) -> None:
        """Test that lowercase letters are valid."""
        assert HarmDefinition.validate_category("violence") is True
        assert HarmDefinition.validate_category("hate") is True
        assert HarmDefinition.validate_category("self") is True

    def test_validate_category_valid_with_underscores(self) -> None:
        """Test that lowercase letters with underscores are valid."""
        assert HarmDefinition.validate_category("hate_speech") is True
        assert HarmDefinition.validate_category("self_harm") is True
        assert HarmDefinition.validate_category("some_long_category_name") is True
        assert HarmDefinition.validate_category("a_b_c") is True

    def test_validate_category_invalid_uppercase(self) -> None:
        """Test that uppercase letters are invalid."""
        assert HarmDefinition.validate_category("Violence") is False
        assert HarmDefinition.validate_category("HATE_SPEECH") is False
        assert HarmDefinition.validate_category("selfHarm") is False

    def test_validate_category_invalid_numbers(self) -> None:
        """Test that numbers are invalid."""
        assert HarmDefinition.validate_category("violence1") is False
        assert HarmDefinition.validate_category("hate_speech_2") is False
        assert HarmDefinition.validate_category("123") is False

    def test_validate_category_invalid_special_characters(self) -> None:
        """Test that special characters are invalid."""
        assert HarmDefinition.validate_category("violence!") is False
        assert HarmDefinition.validate_category("hate-speech") is False
        assert HarmDefinition.validate_category("hate.speech") is False
        assert HarmDefinition.validate_category("hate speech") is False
        assert HarmDefinition.validate_category("violence@test") is False

    def test_validate_category_invalid_empty_string(self) -> None:
        """Test that empty string is invalid."""
        assert HarmDefinition.validate_category("") is False

    def test_validate_category_check_exists_valid_category(self) -> None:
        """Test check_exists with a known existing category."""
        # These categories should exist in the harm_definition directory
        assert HarmDefinition.validate_category("violence", check_exists=True) is True
        assert HarmDefinition.validate_category("hate_speech", check_exists=True) is True

    def test_validate_category_check_exists_nonexistent_category(self) -> None:
        """Test check_exists with a valid format but nonexistent category."""
        assert HarmDefinition.validate_category("nonexistent_category", check_exists=True) is False
        assert HarmDefinition.validate_category("fake_harm", check_exists=True) is False

    def test_validate_category_check_exists_invalid_format(self) -> None:
        """Test check_exists with invalid format returns False without checking existence."""
        # Invalid format should return False before checking existence
        assert HarmDefinition.validate_category("VIOLENCE", check_exists=True) is False
        assert HarmDefinition.validate_category("violence123", check_exists=True) is False


class TestHarmDefinitionFromYaml:
    """Tests for HarmDefinition.from_yaml class method."""

    def test_from_yaml_with_simple_filename(self) -> None:
        """Test loading from a simple filename resolves to HARM_DEFINITION_PATH."""
        # This test uses an actual file from the harm_definition directory
        harm_def = HarmDefinition.from_yaml("violence.yaml")
        assert harm_def.category == "violence"
        assert harm_def.version == "1.0"
        assert len(harm_def.scale_descriptions) > 0
        assert harm_def.source_path is not None

    def test_from_yaml_with_full_path(self) -> None:
        """Test loading from a full path."""
        full_path = HARM_DEFINITION_PATH / "hate_speech.yaml"
        harm_def = HarmDefinition.from_yaml(full_path)
        assert harm_def.category == "hate_speech"
        assert harm_def.version == "1.0"

    def test_from_yaml_file_not_found(self, tmp_path: Path) -> None:
        """Test that FileNotFoundError is raised for non-existent file."""
        with pytest.raises(FileNotFoundError) as exc_info:
            HarmDefinition.from_yaml(tmp_path / "nonexistent.yaml")
        assert "Harm definition file not found" in str(exc_info.value)

    def test_from_yaml_invalid_yaml(self, tmp_path: Path) -> None:
        """Test that ValueError is raised for invalid YAML."""
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("{ invalid yaml: [")

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(invalid_file)
        assert "Invalid YAML" in str(exc_info.value)

    def test_from_yaml_not_a_dict(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when YAML is not a dict."""
        list_file = tmp_path / "list.yaml"
        list_file.write_text("- item1\n- item2\n")

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(list_file)
        assert "must contain a YAML mapping" in str(exc_info.value)

    def test_from_yaml_missing_version(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when version is missing."""
        yaml_file = tmp_path / "missing_version.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "category": "test",
                    "scale_descriptions": [{"score_value": "1", "description": "test"}],
                }
            )
        )

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(yaml_file)
        assert "missing required 'version' field" in str(exc_info.value)

    def test_from_yaml_missing_category(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when category is missing."""
        yaml_file = tmp_path / "missing_category.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "version": "1.0",
                    "scale_descriptions": [{"score_value": "1", "description": "test"}],
                }
            )
        )

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(yaml_file)
        assert "missing required 'category' field" in str(exc_info.value)

    def test_from_yaml_missing_scale_descriptions(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when scale_descriptions is missing."""
        yaml_file = tmp_path / "missing_scales.yaml"
        yaml_file.write_text(yaml.dump({"version": "1.0", "category": "test"}))

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(yaml_file)
        assert "missing required 'scale_descriptions' field" in str(exc_info.value)

    def test_from_yaml_invalid_scale_description(self, tmp_path: Path) -> None:
        """Test that ValueError is raised when scale_description is malformed."""
        yaml_file = tmp_path / "bad_scales.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "version": "1.0",
                    "category": "test",
                    "scale_descriptions": [{"score_value": "1"}],  # Missing description
                }
            )
        )

        with pytest.raises(ValueError) as exc_info:
            HarmDefinition.from_yaml(yaml_file)
        assert "must have 'score_value' and 'description' fields" in str(exc_info.value)

    def test_from_yaml_valid_complete_file(self, tmp_path: Path) -> None:
        """Test loading a complete valid YAML file."""
        yaml_file = tmp_path / "valid.yaml"
        yaml_file.write_text(
            yaml.dump(
                {
                    "version": "2.0",
                    "category": "custom_harm",
                    "scale_descriptions": [
                        {"score_value": "1", "description": "None"},
                        {"score_value": "2", "description": "Mild"},
                        {"score_value": "3", "description": "Moderate"},
                        {"score_value": "4", "description": "Severe"},
                        {"score_value": "5", "description": "Critical"},
                    ],
                }
            )
        )

        harm_def = HarmDefinition.from_yaml(yaml_file)
        assert harm_def.version == "2.0"
        assert harm_def.category == "custom_harm"
        assert len(harm_def.scale_descriptions) == 5
        assert harm_def.get_scale_description("3") == "Moderate"
        assert harm_def.source_path == str(yaml_file)


class TestGetAllHarmDefinitions:
    """Tests for the get_all_harm_definitions function."""

    def test_get_all_harm_definitions_returns_dict(self) -> None:
        """Test that get_all_harm_definitions returns a dictionary."""
        result = get_all_harm_definitions()
        assert isinstance(result, dict)

    def test_get_all_harm_definitions_contains_expected_categories(self) -> None:
        """Test that get_all_harm_definitions includes known harm categories."""
        result = get_all_harm_definitions()

        # These categories should exist based on the YAML files in harm_definition directory
        expected_categories = ["violence", "hate_speech", "sexual", "self_harm", "privacy"]

        for category in expected_categories:
            assert category in result, f"Expected category '{category}' not found in harm definitions"
            assert isinstance(result[category], HarmDefinition)
            assert result[category].category == category

    def test_get_all_harm_definitions_values_are_valid(self) -> None:
        """Test that all returned HarmDefinition objects are valid."""
        result = get_all_harm_definitions()

        for category, harm_def in result.items():
            assert harm_def.version, f"Category {category} missing version"
            assert harm_def.category == category, f"Category mismatch for {category}"
            assert len(harm_def.scale_descriptions) > 0, f"Category {category} has no scale descriptions"
            assert harm_def.source_path, f"Category {category} missing source_path"

    def test_get_all_harm_definitions_count(self) -> None:
        """Test that get_all_harm_definitions returns expected number of definitions."""
        result = get_all_harm_definitions()

        # Count YAML files in the directory
        yaml_files = list(HARM_DEFINITION_PATH.glob("*.yaml"))

        assert len(result) == len(yaml_files), f"Expected {len(yaml_files)} harm definitions, got {len(result)}"


class TestHarmDefinitionFiles:
    """Unit tests that validate all harm definition YAML files in the repository."""

    @pytest.mark.parametrize(
        "yaml_file",
        list(HARM_DEFINITION_PATH.glob("*.yaml")),
        ids=lambda p: p.stem,
    )
    def test_all_harm_definition_files_are_valid(self, yaml_file: Path) -> None:
        """Test that each harm definition YAML file in the directory is valid."""
        harm_def = HarmDefinition.from_yaml(yaml_file)

        assert harm_def.version, f"{yaml_file.name} missing version"
        assert harm_def.category, f"{yaml_file.name} missing category"
        assert len(harm_def.scale_descriptions) > 0, f"{yaml_file.name} has no scale descriptions"

        # Verify each scale description has both fields
        for scale in harm_def.scale_descriptions:
            assert scale.score_value, f"{yaml_file.name} has scale with empty score_value"
            assert scale.description, f"{yaml_file.name} has scale with empty description"

    @pytest.mark.parametrize(
        "yaml_file",
        list(HARM_DEFINITION_PATH.glob("*.yaml")),
        ids=lambda p: p.stem,
    )
    def test_harm_definition_filename_matches_category(self, yaml_file: Path) -> None:
        """Test that each harm definition filename matches its category field.

        This ensures consistency between filenames and categories, which is required
        because harm_definition paths are derived from harm_category as "{category}.yaml".
        """
        harm_def = HarmDefinition.from_yaml(yaml_file)
        expected_category = yaml_file.stem  # filename without .yaml extension

        assert harm_def.category == expected_category, (
            f"Filename '{yaml_file.name}' does not match category '{harm_def.category}'. "
            f"Either rename the file to '{harm_def.category}.yaml' or update the category field."
        )

    @pytest.mark.parametrize(
        "yaml_file",
        list(HARM_DEFINITION_PATH.glob("*.yaml")),
        ids=lambda p: p.stem,
    )
    def test_harm_definition_category_is_valid_format(self, yaml_file: Path) -> None:
        """Test that each harm definition category follows the naming convention.

        Categories must contain only lowercase letters and underscores.
        """
        harm_def = HarmDefinition.from_yaml(yaml_file)

        assert HarmDefinition.validate_category(harm_def.category), (
            f"Category '{harm_def.category}' in {yaml_file.name} is invalid. "
            f"Categories must contain only lowercase letters and underscores."
        )
