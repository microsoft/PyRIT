# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend common models.
"""

from pyrit.backend.models.common import (
    FieldError,
    PaginationInfo,
    ProblemDetail,
    filter_sensitive_fields,
)


class TestPaginationInfo:
    """Tests for PaginationInfo model."""

    def test_pagination_info_creation(self) -> None:
        """Test creating a PaginationInfo object."""
        info = PaginationInfo(limit=50, has_more=True, next_cursor="abc123")

        assert info.limit == 50
        assert info.has_more is True
        assert info.next_cursor == "abc123"
        assert info.prev_cursor is None

    def test_pagination_info_full(self) -> None:
        """Test creating a PaginationInfo with all fields."""
        info = PaginationInfo(
            limit=100,
            has_more=False,
            next_cursor="next",
            prev_cursor="prev",
        )

        assert info.limit == 100
        assert info.has_more is False
        assert info.next_cursor == "next"
        assert info.prev_cursor == "prev"


class TestFieldError:
    """Tests for FieldError model."""

    def test_field_error_minimal(self) -> None:
        """Test creating a FieldError with minimal fields."""
        error = FieldError(field="name", message="Required field")

        assert error.field == "name"
        assert error.message == "Required field"
        assert error.code is None
        assert error.value is None

    def test_field_error_full(self) -> None:
        """Test creating a FieldError with all fields."""
        error = FieldError(
            field="pieces[0].data_type",
            message="Invalid value",
            code="type_error",
            value="invalid",
        )

        assert error.field == "pieces[0].data_type"
        assert error.message == "Invalid value"
        assert error.code == "type_error"
        assert error.value == "invalid"


class TestProblemDetail:
    """Tests for ProblemDetail model."""

    def test_problem_detail_minimal(self) -> None:
        """Test creating a ProblemDetail with minimal fields."""
        problem = ProblemDetail(
            type="/errors/test",
            title="Test Error",
            status=400,
            detail="A test error occurred",
        )

        assert problem.type == "/errors/test"
        assert problem.title == "Test Error"
        assert problem.status == 400
        assert problem.detail == "A test error occurred"
        assert problem.instance is None
        assert problem.errors is None

    def test_problem_detail_with_errors(self) -> None:
        """Test creating a ProblemDetail with field errors."""
        errors = [
            FieldError(field="name", message="Required"),
            FieldError(field="age", message="Must be positive"),
        ]
        problem = ProblemDetail(
            type="/errors/validation",
            title="Validation Error",
            status=422,
            detail="Request validation failed",
            instance="/api/v1/test",
            errors=errors,
        )

        assert len(problem.errors) == 2
        assert problem.instance == "/api/v1/test"


class TestFilterSensitiveFields:
    """Tests for filter_sensitive_fields function."""

    def test_filter_removes_api_key(self) -> None:
        """Test that API keys are filtered out."""
        data = {
            "name": "test",
            "api_key": "secret123",
            "endpoint": "https://api.test.com",
        }

        result = filter_sensitive_fields(data)

        assert "name" in result
        assert "endpoint" in result
        assert "api_key" not in result

    def test_filter_removes_password(self) -> None:
        """Test that passwords are filtered out."""
        data = {
            "username": "user",
            "password": "secret",
        }

        result = filter_sensitive_fields(data)

        assert "username" in result
        assert "password" not in result

    def test_filter_removes_token(self) -> None:
        """Test that tokens are filtered out."""
        data = {
            "access_token": "abc123",
            "refresh_token": "xyz789",
            "data": "public",
        }

        result = filter_sensitive_fields(data)

        assert "data" in result
        assert "access_token" not in result
        assert "refresh_token" not in result

    def test_filter_handles_nested_dicts(self) -> None:
        """Test that nested dictionaries are recursively filtered."""
        data = {
            "config": {
                "api_key": "secret",
                "endpoint": "https://test.com",
            },
            "name": "test",
        }

        result = filter_sensitive_fields(data)

        assert result["name"] == "test"
        assert "api_key" not in result["config"]
        assert result["config"]["endpoint"] == "https://test.com"

    def test_filter_handles_lists(self) -> None:
        """Test that lists with dicts are filtered."""
        data = {
            "items": [
                {"api_key": "secret1", "id": 1},
                {"api_key": "secret2", "id": 2},
            ],
        }

        result = filter_sensitive_fields(data)

        assert len(result["items"]) == 2
        assert "api_key" not in result["items"][0]
        assert result["items"][0]["id"] == 1

    def test_filter_non_dict_returns_as_is(self) -> None:
        """Test that non-dict input is returned as-is."""
        result = filter_sensitive_fields("not a dict")  # type: ignore[arg-type]
        assert result == "not a dict"

    def test_filter_preserves_allowed_fields(self) -> None:
        """Test that allowed fields are preserved."""
        data = {
            "model_name": "gpt-4",
            "temperature": 0.7,
            "deployment_name": "my-deployment",
            "api_key": "secret",
        }

        result = filter_sensitive_fields(data)

        assert result["model_name"] == "gpt-4"
        assert result["temperature"] == 0.7
        assert result["deployment_name"] == "my-deployment"
        assert "api_key" not in result

    def test_filter_removes_secret_fields(self) -> None:
        """Test that secret-related fields are filtered out."""
        data = {
            "client_secret": "secret123",
            "secret_key": "key456",
            "model_name": "gpt-4",
        }

        result = filter_sensitive_fields(data)

        assert "client_secret" not in result
        assert "secret_key" not in result
        assert result["model_name"] == "gpt-4"

    def test_filter_removes_credential_fields(self) -> None:
        """Test that credential-related fields are filtered out."""
        data = {
            "credentials": "cred123",
            "user_credential": "cred456",
            "endpoint": "https://api.test.com",
        }

        result = filter_sensitive_fields(data)

        assert "credentials" not in result
        assert "user_credential" not in result
        assert result["endpoint"] == "https://api.test.com"

    def test_filter_removes_auth_fields(self) -> None:
        """Test that auth-related fields are filtered out."""
        data = {
            "auth_header": "Bearer token",
            "authorization": "secret",
            "username": "user",
        }

        result = filter_sensitive_fields(data)

        assert "auth_header" not in result
        assert "authorization" not in result
        assert result["username"] == "user"

    def test_filter_empty_dict(self) -> None:
        """Test filtering an empty dictionary."""
        result = filter_sensitive_fields({})

        assert result == {}

    def test_filter_deeply_nested_dicts(self) -> None:
        """Test filtering deeply nested dictionaries."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "api_key": "secret",
                        "data": "public",
                    }
                }
            }
        }

        result = filter_sensitive_fields(data)

        assert result["level1"]["level2"]["level3"]["data"] == "public"
        assert "api_key" not in result["level1"]["level2"]["level3"]

    def test_filter_list_with_non_dict_items(self) -> None:
        """Test filtering lists containing non-dict items."""
        data = {
            "items": ["string1", 123, True, None],
            "api_key": "secret",
        }

        result = filter_sensitive_fields(data)

        assert result["items"] == ["string1", 123, True, None]
        assert "api_key" not in result

    def test_filter_mixed_list(self) -> None:
        """Test filtering lists with mixed dict and non-dict items."""
        data = {
            "items": [
                {"api_key": "secret", "id": 1},
                "string",
                {"password": "pass", "name": "test"},
            ],
        }

        result = filter_sensitive_fields(data)

        assert len(result["items"]) == 3
        assert result["items"][0] == {"id": 1}
        assert result["items"][1] == "string"
        assert result["items"][2] == {"name": "test"}

    def test_filter_case_insensitive(self) -> None:
        """Test that filtering is case-insensitive."""
        data = {
            "API_KEY": "secret",
            "Api_Key": "secret2",
            "apikey": "secret3",
            "name": "test",
        }

        result = filter_sensitive_fields(data)

        # All variations should be filtered
        assert "API_KEY" not in result
        assert "Api_Key" not in result
        # Note: "apikey" contains "key" so should be filtered
        assert "apikey" not in result
        assert result["name"] == "test"


class TestPaginationInfoEdgeCases:
    """Edge case tests for PaginationInfo."""

    def test_pagination_with_zero_limit(self) -> None:
        """Test creating pagination with zero limit."""
        # This tests the model creation, validation should happen at API level
        info = PaginationInfo(limit=0, has_more=False)

        assert info.limit == 0

    def test_pagination_with_large_limit(self) -> None:
        """Test creating pagination with large limit."""
        info = PaginationInfo(limit=10000, has_more=True)

        assert info.limit == 10000

    def test_pagination_with_empty_cursors(self) -> None:
        """Test pagination with empty string cursors."""
        info = PaginationInfo(
            limit=50,
            has_more=False,
            next_cursor="",
            prev_cursor="",
        )

        assert info.next_cursor == ""
        assert info.prev_cursor == ""


class TestProblemDetailEdgeCases:
    """Edge case tests for ProblemDetail."""

    def test_problem_detail_with_empty_errors_list(self) -> None:
        """Test ProblemDetail with empty errors list."""
        problem = ProblemDetail(
            type="/errors/test",
            title="Test",
            status=400,
            detail="Test error",
            errors=[],
        )

        assert problem.errors == []

    def test_problem_detail_serialization(self) -> None:
        """Test ProblemDetail JSON serialization."""
        problem = ProblemDetail(
            type="/errors/test",
            title="Test",
            status=400,
            detail="Test error",
        )

        data = problem.model_dump(exclude_none=True)

        assert "instance" not in data  # None should be excluded
        assert data["type"] == "/errors/test"
