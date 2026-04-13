# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.common.utils import (
    combine_dict,
    get_kwarg_param,
    get_random_indices,
    verify_and_resolve_path,
    warn_if_set,
)


def test_combine_non_empty_dict():
    dict1 = {"a": "b"}
    dict2 = {"c": "d"}
    assert combine_dict(dict1, dict2) == {"a": "b", "c": "d"}


def test_combine_empty_dict():
    dict1 = {}
    dict2 = {}
    assert combine_dict(dict1, dict2) == {}


def test_combine_first_empty_dict():
    dict1 = {"a": "b"}
    dict2 = {}
    assert combine_dict(dict1, dict2) == {"a": "b"}


def test_combine_second_empty_dict():
    dict1 = {}
    dict2 = {"c": "d"}
    assert combine_dict(dict1, dict2) == {"c": "d"}


def test_combine_dict_same_keys():
    dict1 = {"c": "b"}
    dict2 = {"c": "d"}
    assert combine_dict(dict1, dict2) == {"c": "d"}


def test_get_random_indices():
    with patch("random.sample", return_value=[2, 4, 6]):
        result = get_random_indices(start=0, size=10, proportion=0.3)
        assert result == [2, 4, 6]

    assert get_random_indices(start=5, size=10, proportion=0) == []
    assert sorted(get_random_indices(start=27, size=10, proportion=1)) == list(range(27, 37))

    with pytest.raises(ValueError):
        get_random_indices(start=-1, size=10, proportion=0.5)
    with pytest.raises(ValueError):
        get_random_indices(start=0, size=0, proportion=0.5)
    with pytest.raises(ValueError):
        get_random_indices(start=0, size=10, proportion=-1)
    with pytest.raises(ValueError):
        get_random_indices(start=0, size=10, proportion=1.01)


# Tests for warn_if_set function
class TestWarnIfSet:
    """Test class for warn_if_set function."""

    def test_warn_if_set_with_set_field(self):
        """Test that warning is logged when field is set."""
        # Create a mock config object
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = "some_value"

        # Create a mock logger
        mock_logger = MagicMock()

        # Call the function
        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        # Assert warning was called
        mock_logger.warning.assert_called_once_with(
            "test_field was provided in TestConfig but is not used. This parameter will be ignored."
        )

    def test_warn_if_set_with_none_field(self):
        """Test that no warning is logged when field is None."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = None

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        # Assert no warning was called
        mock_logger.warning.assert_not_called()

    def test_warn_if_set_with_empty_list(self):
        """Test that no warning is logged when field is an empty list."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = []

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        mock_logger.warning.assert_not_called()

    def test_warn_if_set_with_non_empty_list(self):
        """Test that warning is logged when field is a non-empty list."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = ["item1", "item2"]

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        mock_logger.warning.assert_called_once_with(
            "test_field was provided in TestConfig but is not used. This parameter will be ignored."
        )

    def test_warn_if_set_with_empty_string(self):
        """Test that no warning is logged when field is an empty string."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = ""

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        mock_logger.warning.assert_not_called()

    def test_warn_if_set_with_non_empty_string(self):
        """Test that warning is logged when field is a non-empty string."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = "non_empty"

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        mock_logger.warning.assert_called_once()

    def test_warn_if_set_with_nonexistent_field(self):
        """Test that warning is logged when field doesn't exist on config."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        # Remove the nonexistent_field attribute
        del config.nonexistent_field

        mock_logger = MagicMock()

        # Mock hasattr to return False for nonexistent field
        with patch("pyrit.common.utils.hasattr", return_value=False):
            warn_if_set(config=config, unused_fields=["nonexistent_field"], log=mock_logger)

        mock_logger.warning.assert_called_once_with(
            "Field 'nonexistent_field' does not exist in TestConfig. Skipping unused parameter check."
        )

    def test_warn_if_set_with_multiple_fields(self):
        """Test warn_if_set with multiple fields."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.field1 = "value1"
        config.field2 = None
        config.field3 = ["item"]

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["field1", "field2", "field3"], log=mock_logger)

        # Should be called twice (field1 and field3 are set)
        assert mock_logger.warning.call_count == 2

    def test_warn_if_set_with_integer_field(self):
        """Test that warning is logged for non-zero integer."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = 42

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        mock_logger.warning.assert_called_once()

    def test_warn_if_set_with_zero_integer(self):
        """Test that warning is logged for zero integer (it's a set value, not empty)."""
        config = MagicMock()
        config.__class__.__name__ = "TestConfig"
        config.test_field = 0

        mock_logger = MagicMock()

        warn_if_set(config=config, unused_fields=["test_field"], log=mock_logger)

        # Zero integer is considered "set" since it's not None and doesn't have __len__
        mock_logger.warning.assert_called_once_with(
            "test_field was provided in TestConfig but is not used. This parameter will be ignored."
        )


# Tests for get_kwarg_param function
class TestGetKwargParam:
    """Test class for get_kwarg_param function."""

    def test_get_kwarg_param_required_present_valid(self):
        """Test extracting a required parameter that is present and valid."""
        kwargs = {"param1": "test_value"}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)
        assert result == "test_value"
        # Parameter should be removed from kwargs
        assert "param1" not in kwargs

    def test_get_kwarg_param_required_missing(self):
        """Test that ValueError is raised when required parameter is missing."""
        kwargs = {}
        with pytest.raises(ValueError, match="Missing required parameter: param1"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)

    def test_get_kwarg_param_required_empty_string(self):
        """Test that ValueError is raised when required parameter is empty string."""
        kwargs = {"param1": ""}
        with pytest.raises(ValueError, match="Parameter 'param1' must be provided and non-empty"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)

    def test_get_kwarg_param_required_none(self):
        """Test that ValueError is raised when required parameter is None."""
        kwargs = {"param1": None}
        with pytest.raises(ValueError, match="Parameter 'param1' must be provided and non-empty"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)

    def test_get_kwarg_param_wrong_type(self):
        """Test that TypeError is raised when parameter is wrong type."""
        kwargs = {"param1": 123}
        with pytest.raises(TypeError, match="Parameter 'param1' must be of type str, got int"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)

    def test_get_kwarg_param_optional_missing_no_default(self):
        """Test optional parameter missing without default returns None."""
        kwargs = {}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=False)
        assert result is None

    def test_get_kwarg_param_optional_missing_with_default(self):
        """Test optional parameter missing with default returns default."""
        kwargs = {}
        result = get_kwarg_param(
            kwargs=kwargs, param_name="param1", expected_type=str, required=False, default_value="default"
        )
        assert result == "default"

    def test_get_kwarg_param_optional_empty_with_default(self):
        """Test optional parameter that is empty returns default."""
        kwargs = {"param1": ""}
        result = get_kwarg_param(
            kwargs=kwargs, param_name="param1", expected_type=str, required=False, default_value="default"
        )
        assert result == "default"

    def test_get_kwarg_param_optional_none_with_default(self):
        """Test optional parameter that is None returns default."""
        kwargs = {"param1": None}
        result = get_kwarg_param(
            kwargs=kwargs, param_name="param1", expected_type=str, required=False, default_value="default"
        )
        assert result == "default"

    def test_get_kwarg_param_optional_present_valid(self):
        """Test optional parameter that is present and valid."""
        kwargs = {"param1": "test_value"}
        result = get_kwarg_param(
            kwargs=kwargs, param_name="param1", expected_type=str, required=False, default_value="default"
        )
        assert result == "test_value"

    def test_get_kwarg_param_with_list_type(self):
        """Test get_kwarg_param with list type."""
        kwargs = {"param1": ["item1", "item2"]}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=list, required=True)
        assert result == ["item1", "item2"]

    def test_get_kwarg_param_with_int_type(self):
        """Test get_kwarg_param with integer type."""
        kwargs = {"param1": 42}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=int, required=True)
        assert result == 42

    def test_get_kwarg_param_with_empty_list_required(self):
        """Test that empty list is treated as falsy for required parameter."""
        kwargs = {"param1": []}
        with pytest.raises(ValueError, match="Parameter 'param1' must be provided and non-empty"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=list, required=True)

    def test_get_kwarg_param_with_empty_list_optional(self):
        """Test that empty list returns default for optional parameter."""
        kwargs = {"param1": []}
        result = get_kwarg_param(
            kwargs=kwargs, param_name="param1", expected_type=list, required=False, default_value=["default"]
        )
        assert result == ["default"]

    def test_get_kwarg_param_with_false_boolean(self):
        """Test that False boolean value is treated as falsy."""
        kwargs = {"param1": False}
        with pytest.raises(ValueError, match="Parameter 'param1' must be provided and non-empty"):
            get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=bool, required=True)

    def test_get_kwarg_param_with_true_boolean(self):
        """Test that True boolean value is valid."""
        kwargs = {"param1": True}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=bool, required=True)
        assert result is True

    def test_get_kwarg_param_kwargs_modification(self):
        """Test that parameter is removed from kwargs after extraction."""
        kwargs = {"param1": "value1", "param2": "value2"}
        result = get_kwarg_param(kwargs=kwargs, param_name="param1", expected_type=str, required=True)
        assert result == "value1"
        assert "param1" not in kwargs
        assert "param2" in kwargs  # Other params should remain


class TestVerifyAndResolvePath:
    """Test class for verify_and_resolve_path function."""

    def test_verify_and_resolve_path_rejects_nonexistent(self) -> None:
        """Test that the function correctly refuses to verify a non-existent path."""
        mock_path: str = "this/does/not/exist.yaml"
        with pytest.raises(FileNotFoundError, match="Path not found"):
            verify_and_resolve_path(mock_path)

    def test_verify_and_resolve_path_confirms_existing(self) -> None:
        """Test that the function verifies paths that currently exist under the scorer configs."""
        full_paths: list[str] = []
        for root, _dirs, files in os.walk(SCORER_SEED_PROMPT_PATH):
            full_paths.extend([os.path.join(root, f) for f in files if f.endswith(".yaml")])
        resolved_paths = [Path(p).resolve() for p in full_paths]
        attempted_paths = [verify_and_resolve_path(p) for p in full_paths]
        assert attempted_paths == resolved_paths

    def test_verify_and_resolve_path_with_path_object(self) -> None:
        """Test that the function works with Path objects."""
        # Use SCORER_SEED_PROMPT_PATH which we know exists
        result = verify_and_resolve_path(SCORER_SEED_PROMPT_PATH)
        assert result == SCORER_SEED_PROMPT_PATH.resolve()

    def test_verify_and_resolve_path_invalid_type(self) -> None:
        """Test that the function raises ValueError for invalid types."""
        with pytest.raises(ValueError, match="Path must be a string or Path object"):
            verify_and_resolve_path(123)  # type: ignore[arg-type]
