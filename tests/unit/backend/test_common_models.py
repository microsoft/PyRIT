# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend common models.
"""

import json
import subprocess
import sys
import uuid

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.json_schema import JsonSchemaMode
from pydantic.main import IncEx

from pyrit.backend.main import app
from pyrit.backend.models import (
    AddMessageRequest,
    ConverterPreviewRequest,
    CreateAttackRequest,
    CreateConverterRequest,
    CreateScorerRequest,
    CreateTargetRequest,
    FieldError,
    MessagePieceRequest,
    PaginationInfo,
    ProblemDetail,
    RegisterInitializerRequest,
    UpdateAttackRequest,
    filter_sensitive_fields,
)
from pyrit.backend.models.attacks import SaveConversationRequest
from pyrit.backend.models.common import (
    MAX_FILE_CONTENT_LENGTH,
    MAX_IDENTIFIER_LENGTH,
    MAX_ITEMS,
    MAX_LABEL_KEY_LENGTH,
    MAX_LABEL_VALUE_LENGTH,
    MAX_TEXT_LENGTH,
    LabelFilterStr,
)
from pyrit.backend.models.configuration import (
    ReinitializeRequest,
    UpdateConfigurationFileRequest,
    UpdateEnvironmentFileRequest,
)
from pyrit.backend.models.message_sends import MessageSendRequest
from pyrit.backend.models.scores import ManualScoreRequest
from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.models.component_spec import TargetBinding
from unit.async_utils import TEST_WATCHDOG_SECONDS


def test_create_component_models_import_without_field_shadow_warnings() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error:Field name:UserWarning",
            "-c",
            "from pyrit.backend.models import CreateConverterRequest, CreateScorerRequest, CreateTargetRequest",
        ],
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest, CreateTargetRequest])
@pytest.mark.parametrize("register", [None, True, False])
def test_create_component_registration_alias(
    *,
    request_model: type[CreateConverterRequest | CreateScorerRequest | CreateTargetRequest],
    register: bool | None,
) -> None:
    payload: dict[str, str | bool] = {"name": "created", "type": "Example"}
    if register is not None:
        payload["register"] = register
    expected = register is not False
    request = request_model.model_validate(payload)
    python_request = request_model(
        name="created", type="Example", **({} if register is None else {"register": register})
    )

    assert request.register_instance is expected
    assert request.register is expected
    assert python_request == request
    assert request_model.model_validate_json(json.dumps(payload)) == request
    for serialized in (request.model_dump(), request.model_dump(by_alias=True), json.loads(request.model_dump_json())):
        assert serialized["register"] is expected
        assert "register_instance" not in serialized
        assert request_model.model_validate(serialized) == request
    assert request.model_dump(by_alias=False)["register_instance"] is expected
    assert ("register_instance" in request.model_fields_set) is (register is not None)
    assert "register" not in request_model.model_fields


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest, CreateTargetRequest])
def test_create_component_registration_python_access(
    request_model: type[CreateConverterRequest | CreateScorerRequest | CreateTargetRequest],
) -> None:
    request = request_model(name="created", type="Example", register_instance=False)
    assert request.register is False
    request.register = True
    assert request.register_instance is True
    assert request.model_dump()["register"] is True
    request.register_instance = False
    assert request.register is False
    assert request.model_dump()["register"] is False
    copied = request.model_copy(update={"register_instance": True})
    assert copied.register is True
    assert request.register is False


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest, CreateTargetRequest])
@pytest.mark.parametrize("register", [True, False])
@pytest.mark.parametrize("deep", [True, False])
def test_create_component_registration_copy_public_key(
    *,
    request_model: type[CreateConverterRequest | CreateScorerRequest | CreateTargetRequest],
    register: bool,
    deep: bool,
) -> None:
    request = request_model(name="created", type="Example", register=not register, params={"value": 1})
    updates = {"register": register}
    copied = request.model_copy(update=updates, deep=deep)
    assert copied.register is register
    assert copied.register_instance is register
    assert copied.model_dump()["register"] is register
    assert request.register is not register
    assert updates == {"register": register}
    assert "register_instance" in copied.model_fields_set
    assert "register" not in copied.model_fields_set
    assert (copied.params is request.params) is not deep
    with pytest.raises(ValueError, match="updates must match"):
        request.model_copy(update={"register": register, "register_instance": not register})


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest, CreateTargetRequest])
@pytest.mark.parametrize("fields", [{"register"}, {"register": True}])
def test_create_component_registration_public_field_filters(
    *,
    request_model: type[CreateConverterRequest | CreateScorerRequest | CreateTargetRequest],
    fields: IncEx,
) -> None:
    request = request_model(name="created", type="Example", register=False, params={"first": 1, "second": 2})
    assert request.model_dump(include=fields) == {"register": False}
    assert json.loads(request.model_dump_json(include=fields)) == {"register": False}
    assert "register" not in request.model_dump(exclude=fields)
    assert "register" not in json.loads(request.model_dump_json(exclude=fields))
    assert request.model_dump(include=fields, by_alias=False) == {"register_instance": False}
    assert request.model_dump(include={"register_instance"}) == {"register": False}
    assert request.model_dump(include={"register": True, "params": {"first"}}) == {
        "register": False,
        "params": {"first": 1},
    }
    assert fields in ({"register"}, {"register": True})


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest, CreateTargetRequest])
@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_create_component_registration_schema(
    *,
    request_model: type[CreateConverterRequest | CreateScorerRequest | CreateTargetRequest],
    mode: JsonSchemaMode,
) -> None:
    schema = request_model.model_json_schema(mode=mode)
    assert schema["properties"]["register"] == {
        "default": True,
        "description": "Register the object; false returns only its descriptor",
        "title": "Register",
        "type": "boolean",
    }
    assert "register_instance" not in schema["properties"]
    assert "register" not in schema["required"]
    assert request_model.model_fields["register_instance"].alias == "register"


@pytest.mark.parametrize("request_model", [CreateConverterRequest, CreateScorerRequest])
@pytest.mark.parametrize("register", [None, True, False])
def test_create_component_registration_requires_name(
    *,
    request_model: type[CreateConverterRequest | CreateScorerRequest],
    register: bool | None,
) -> None:
    payload: dict[str, str | bool] = {"type": "Example"}
    if register is not None:
        payload["register"] = register
    if register is False:
        assert request_model.model_validate(payload).name is None
    else:
        with pytest.raises(ValidationError, match="name is required when register=true"):
            request_model.model_validate(payload)


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_registry_name": "t" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"source_conversation_id": "c" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"name": "n" * (MAX_TEXT_LENGTH + 1)},
        {"labels": {f"key{i}": "value" for i in range(MAX_ITEMS + 1)}},
        {"labels": {"k" * (MAX_LABEL_KEY_LENGTH + 1): "value"}},
        {"labels": {"key": "v" * (MAX_LABEL_VALUE_LENGTH + 1)}},
        {"cutoff_index": -1},
    ],
)
def test_create_attack_request_rejects_values_over_limits(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CreateAttackRequest.model_validate({"target_registry_name": "target", **overrides})


def test_create_attack_request_accepts_values_at_limits() -> None:
    request = CreateAttackRequest(
        target_registry_name="t" * MAX_IDENTIFIER_LENGTH,
        name="n" * MAX_TEXT_LENGTH,
        labels={f"{i:0{MAX_LABEL_KEY_LENGTH}d}": "v" * MAX_LABEL_VALUE_LENGTH for i in range(MAX_ITEMS)},
        cutoff_index=0,
    )

    assert len(request.labels or {}) == MAX_ITEMS


def test_update_attack_request_rejects_oversized_objective() -> None:
    with pytest.raises(ValidationError):
        UpdateAttackRequest(objective="o" * (MAX_TEXT_LENGTH + 1))


def test_update_attack_request_expected_objective_is_not_length_limited() -> None:
    request = UpdateAttackRequest(objective="o", expected_objective="o" * (MAX_TEXT_LENGTH + 1))

    assert len(request.expected_objective or "") == MAX_TEXT_LENGTH + 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_conversation_id": "c" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"converter_ids": ["converter"] * (MAX_ITEMS + 1)},
        {"request_converter_configurations": [{"converter_ids": ["converter"]}] * (MAX_ITEMS + 1)},
        {"pieces": [{"original_value": "text", "mime_type": "m" * (MAX_IDENTIFIER_LENGTH + 1)}]},
        {"start_token": "s" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"end_token": "e" * (MAX_IDENTIFIER_LENGTH + 1)},
    ],
)
def test_add_message_request_rejects_values_over_limits(overrides: dict[str, object]) -> None:
    payload = {"pieces": [{"original_value": "text"}], "target_conversation_id": "conversation", **overrides}

    with pytest.raises(ValidationError):
        AddMessageRequest.model_validate(payload)


def test_message_piece_content_is_not_length_limited() -> None:
    piece = MessagePieceRequest(original_value="x" * (MAX_TEXT_LENGTH + 1))

    assert len(piece.original_value) == MAX_TEXT_LENGTH + 1


_SOURCE = {"source_name": "source", "source_hash": "hash"}
_BINDING = {"source_name": "target", "source_hash": "hash", "temperature": 0.5, "effective_hash": "effective"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_name": "s" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"source_hash": "h" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"effective_hash": "e" * (MAX_IDENTIFIER_LENGTH + 1)},
        {"params": {"p" * (MAX_IDENTIFIER_LENGTH + 1): 1}},
        {"params": {f"p{i}": 1 for i in range(MAX_ITEMS + 1)}},
    ],
)
def test_source_specs_reject_values_over_limits(overrides: dict[str, object]) -> None:
    source = {**_SOURCE, **overrides}

    with pytest.raises(ValidationError):
        CreateTargetRequest.model_validate({"type": "T", "register": False, "source": source})
    with pytest.raises(ValidationError):
        CreateConverterRequest.model_validate({"type": "T", "register": False, "source": source})
    with pytest.raises(ValidationError):
        ConverterPreviewRequest.model_validate(
            {"original_value": "x", "converter_ids": ["c"], "converter_specs": [source]}
        )


def test_source_specs_accept_values_at_limits() -> None:
    source = {
        "source_name": "s" * MAX_IDENTIFIER_LENGTH,
        "source_hash": "h" * MAX_IDENTIFIER_LENGTH,
        "effective_hash": "e" * MAX_IDENTIFIER_LENGTH,
        "params": {f"{i:0{MAX_IDENTIFIER_LENGTH}d}": i for i in range(MAX_ITEMS)},
    }

    request = CreateConverterRequest.model_validate({"type": "T", "register": False, "source": source})

    assert request.source is not None
    assert len(request.source.params) == MAX_ITEMS


@pytest.mark.parametrize("field", ["source_name", "source_hash", "effective_hash"])
def test_target_bindings_reject_values_over_limits(field: str) -> None:
    binding = {**_BINDING, field: "x" * (MAX_IDENTIFIER_LENGTH + 1)}
    message = {"pieces": [{"original_value": "text"}], "target_conversation_id": "conversation"}

    with pytest.raises(ValidationError, match="limited to 256 characters"):
        CreateAttackRequest.model_validate({"target_registry_name": "target", "target_binding": binding})
    with pytest.raises(ValidationError, match="limited to 256 characters"):
        SaveConversationRequest.model_validate(
            {"save_id": str(uuid.uuid4()), "destination": "new_attack", "target_binding": binding}
        )
    with pytest.raises(ValidationError, match="limited to 256 characters"):
        AddMessageRequest.model_validate({**message, "target_binding": binding})
    with pytest.raises(ValidationError, match="limited to 256 characters"):
        MessageSendRequest.model_validate({**message, "submission_id": "send", "target_binding": binding})


def test_target_binding_input_stays_a_saved_binding() -> None:
    saved = TargetBinding(**{**_BINDING, "source_name": "s" * MAX_IDENTIFIER_LENGTH})

    request = CreateAttackRequest.model_validate(
        {"target_registry_name": "target", "target_binding": saved.model_dump(mode="json")}
    )

    assert type(request.target_binding) is TargetBinding
    assert request.target_binding == saved


def test_saved_target_binding_over_the_limit_still_loads() -> None:
    name = "s" * (MAX_IDENTIFIER_LENGTH + 1)

    binding = TargetBinding.from_metadata({"target_binding": {**_BINDING, "source_name": name}})

    assert binding is not None
    assert binding.source_name == name


def test_openapi_shows_the_target_binding_limit_on_requests_only() -> None:
    schemas = app.openapi()["components"]["schemas"]
    limited = ("source_name", "source_hash", "effective_hash")

    for request_model in ("CreateAttackRequest", "AddMessageRequest", "SaveConversationRequest", "MessageSendRequest"):
        options = schemas[request_model]["properties"]["target_binding"]["anyOf"]
        binding = next(option for option in options if option.get("type") != "null")
        assert {field: binding["properties"][field].get("maxLength") for field in limited} == dict.fromkeys(
            limited, MAX_IDENTIFIER_LENGTH
        )
    saved_bindings = [
        schema for name, schema in schemas.items() if name in {"TargetBinding-Input", "TargetBinding-Output"}
    ]
    assert saved_bindings
    assert all("maxLength" not in field for schema in saved_bindings for field in schema["properties"].values())


@pytest.mark.parametrize(
    ("model", "payload", "field"),
    [
        (UpdateConfigurationFileRequest, {"content": "x" * (MAX_FILE_CONTENT_LENGTH + 1), "version": "v"}, "content"),
        (UpdateEnvironmentFileRequest, {"content": "x" * (MAX_FILE_CONTENT_LENGTH + 1), "version": "v"}, "content"),
        (ReinitializeRequest, {"version": "v" * (MAX_IDENTIFIER_LENGTH + 1)}, "version"),
        (
            RegisterInitializerRequest,
            {"name": "custom", "script_content": "x" * (MAX_FILE_CONTENT_LENGTH + 1)},
            "script_content",
        ),
        (
            CreateConverterRequest,
            {"name": "c", "type": "T", "params": {f"p{i}": 1 for i in range(MAX_ITEMS + 1)}},
            "params",
        ),
        (ConverterPreviewRequest, {"original_value": "x", "converter_ids": ["c"] * (MAX_ITEMS + 1)}, "converter_ids"),
        (
            ConverterPreviewRequest,
            {"original_value": "x", "converter_ids": [], "start_token": "s" * (MAX_IDENTIFIER_LENGTH + 1)},
            "start_token",
        ),
        (
            ConverterPreviewRequest,
            {"original_value": "x", "converter_ids": [], "end_token": "e" * (MAX_IDENTIFIER_LENGTH + 1)},
            "end_token",
        ),
        (
            CreateTechniqueRequest,
            {"name": "t", "type": "PromptSendingAttack", "description": "d" * (MAX_TEXT_LENGTH + 1)},
            "description",
        ),
        (
            CreateTechniqueRequest,
            {"name": "t", "type": "RedTeamingAttack", "adversarial_chat": "a" * (MAX_IDENTIFIER_LENGTH + 1)},
            "adversarial_chat",
        ),
        (
            CreateTechniqueRequest,
            {"name": "t", "type": "PromptSendingAttack", "request_converters": ["c" * (MAX_IDENTIFIER_LENGTH + 1)]},
            "request_converters",
        ),
        (
            CreateTechniqueRequest,
            {"name": "t", "type": "PromptSendingAttack", "response_converters": ["c" * (MAX_IDENTIFIER_LENGTH + 1)]},
            "response_converters",
        ),
        (
            CreateTechniqueRequest,
            {"name": "t", "type": "PromptSendingAttack", "params": {"p" * (MAX_IDENTIFIER_LENGTH + 1): 1}},
            "params",
        ),
        (CreateTargetRequest, {"type": "t" * (MAX_IDENTIFIER_LENGTH + 1)}, "type"),
        (
            SaveConversationRequest,
            {
                "save_id": str(uuid.uuid4()),
                "destination": "new_attack",
                "target_registry_name": "t" * (MAX_IDENTIFIER_LENGTH + 1),
            },
            "target_registry_name",
        ),
        (
            ManualScoreRequest,
            {
                "attack_result_id": str(uuid.uuid4()),
                "message_id": str(uuid.uuid4()),
                "value": True,
                "rationale": "r" * (MAX_TEXT_LENGTH + 1),
            },
            "rationale",
        ),
    ],
)
def test_request_models_reject_values_over_limits(
    model: type[BaseModel], payload: dict[str, object], field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        model.model_validate(payload)

    assert [item["loc"][0] for item in error.value.errors()] == [field]


def test_create_technique_request_accepts_values_at_limits() -> None:
    request = CreateTechniqueRequest(
        name="t",
        type="RedTeamingAttack",
        description="d" * MAX_TEXT_LENGTH,
        adversarial_chat="a" * MAX_IDENTIFIER_LENGTH,
        request_converters=["c" * MAX_IDENTIFIER_LENGTH],
        params={"p" * MAX_IDENTIFIER_LENGTH: 1},
        adversarial_system_prompt="s" * (MAX_TEXT_LENGTH + 1),
    )

    assert len(request.adversarial_system_prompt or "") == MAX_TEXT_LENGTH + 1


def test_label_filter_limits_ignore_surrounding_whitespace() -> None:
    padded = f"  {'k' * MAX_LABEL_KEY_LENGTH}  :  {'v' * MAX_LABEL_VALUE_LENGTH}  "

    assert TypeAdapter(LabelFilterStr).validate_python(padded) == padded
    with pytest.raises(ValidationError, match="limited"):
        TypeAdapter(LabelFilterStr).validate_python(f"{'k' * (MAX_LABEL_KEY_LENGTH + 1)}:v")
