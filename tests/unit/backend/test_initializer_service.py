# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend initializer service and routes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.initializers import (
    ListRegisteredInitializersResponse,
)
from pyrit.backend.services.initializer_service import InitializerService, get_initializer_service
from pyrit.models.catalog.initializer import (
    InitializerParameterSummary,
    RegisteredInitializer,
)
from pyrit.registry import InitializerMetadata


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def client_with_custom_initializers_enabled():
    """Create a test client with allow_custom_initializers enabled."""
    app.state.allow_custom_initializers = True
    yield TestClient(app)
    app.state.allow_custom_initializers = False


@pytest.fixture(autouse=True)
def clear_service_cache():
    """Clear the initializer service singleton cache between tests."""
    get_initializer_service.cache_clear()
    yield
    get_initializer_service.cache_clear()


def _make_initializer_metadata(
    *,
    registry_name: str = "target",
    class_name: str = "TargetInitializer",
    description: str = "Registers targets",
    required_env_vars: tuple[str, ...] = ("AZURE_OPENAI_ENDPOINT",),
    supported_parameters: tuple[tuple[str, str, list[str] | None], ...] = (
        ("tags", "Comma-separated tag filter", ["default"]),
    ),
) -> InitializerMetadata:
    """Create an InitializerMetadata instance for testing."""
    return InitializerMetadata(
        registry_name=registry_name,
        class_name=class_name,
        class_module="pyrit.setup.initializers.target",
        class_description=description,
        required_env_vars=required_env_vars,
        supported_parameters=supported_parameters,
    )


# ============================================================================
# InitializerService Unit Tests
# ============================================================================


class TestInitializerServiceListInitializers:
    """Tests for InitializerService.list_initializers_async."""

    async def test_list_initializers_returns_empty_when_no_initializers(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = []

            result = await service.list_initializers_async()

            assert result.items == []
            assert result.pagination.has_more is False

    async def test_list_initializers_returns_initializers_from_registry(self) -> None:
        metadata = _make_initializer_metadata()

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.list_initializers_async()

            assert len(result.items) == 1
            item = result.items[0]
            assert item.initializer_name == "target"
            assert item.initializer_type == "TargetInitializer"
            assert item.description == "Registers targets"
            assert item.required_env_vars == ["AZURE_OPENAI_ENDPOINT"]
            assert len(item.supported_parameters) == 1
            assert item.supported_parameters[0].name == "tags"
            assert item.supported_parameters[0].description == "Comma-separated tag filter"
            assert item.supported_parameters[0].default == ["default"]

    async def test_list_initializers_paginates_with_limit(self) -> None:
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(5)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=3)

            assert len(result.items) == 3
            assert result.pagination.has_more is True
            assert result.pagination.next_cursor == "init_2"

    async def test_list_initializers_paginates_with_cursor(self) -> None:
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(5)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=2, cursor="init_1")

            assert len(result.items) == 2
            assert result.items[0].initializer_name == "init_2"
            assert result.items[1].initializer_name == "init_3"
            assert result.pagination.has_more is True

    async def test_list_initializers_last_page_has_more_false(self) -> None:
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(3)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=5)

            assert len(result.items) == 3
            assert result.pagination.has_more is False
            assert result.pagination.next_cursor is None

    async def test_list_initializers_with_no_env_vars(self) -> None:
        metadata = _make_initializer_metadata(required_env_vars=(), supported_parameters=())

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.list_initializers_async()

            assert result.items[0].required_env_vars == []
            assert result.items[0].supported_parameters == []


class TestInitializerServiceGetInitializer:
    """Tests for InitializerService.get_initializer_async."""

    async def test_get_initializer_returns_matching_initializer(self) -> None:
        metadata = _make_initializer_metadata(registry_name="target")

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = [metadata]

            result = await service.get_initializer_async(initializer_name="target")

            assert result is not None
            assert result.initializer_name == "target"

    async def test_get_initializer_returns_none_for_missing(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_metadata.return_value = []

            result = await service.get_initializer_async(initializer_name="nonexistent")

            assert result is None


# ============================================================================
# Route Tests
# ============================================================================


class TestInitializerRoutes:
    """Tests for initializer API routes."""

    def test_list_initializers_returns_200(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_initializers_async = AsyncMock(
                return_value=ListRegisteredInitializersResponse(
                    items=[],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["items"] == []
            assert data["pagination"]["has_more"] is False

    def test_list_initializers_with_items(self, client: TestClient) -> None:
        summary = RegisteredInitializer(
            initializer_name="target",
            initializer_type="TargetInitializer",
            description="Registers targets",
            required_env_vars=["AZURE_OPENAI_ENDPOINT"],
            supported_parameters=[
                InitializerParameterSummary(name="tags", description="Tag filter", default=["default"])
            ],
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_initializers_async = AsyncMock(
                return_value=ListRegisteredInitializersResponse(
                    items=[summary],
                    pagination=PaginationInfo(limit=50, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["initializer_name"] == "target"
            assert item["initializer_type"] == "TargetInitializer"
            assert item["required_env_vars"] == ["AZURE_OPENAI_ENDPOINT"]
            assert item["supported_parameters"][0]["name"] == "tags"

    def test_list_initializers_passes_pagination_params(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_initializers_async = AsyncMock(
                return_value=ListRegisteredInitializersResponse(
                    items=[],
                    pagination=PaginationInfo(limit=10, has_more=False, next_cursor=None, prev_cursor=None),
                )
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers?limit=10&cursor=target")

            assert response.status_code == status.HTTP_200_OK
            mock_service.list_initializers_async.assert_called_once_with(limit=10, cursor="target")

    def test_get_initializer_returns_200(self, client: TestClient) -> None:
        summary = RegisteredInitializer(
            initializer_name="target",
            initializer_type="TargetInitializer",
            description="Registers targets",
            required_env_vars=["AZURE_OPENAI_ENDPOINT"],
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_initializer_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers/target")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["initializer_name"] == "target"

    def test_get_initializer_returns_404_when_not_found(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_initializer_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND


# ============================================================================
# Service Register/Unregister Tests
# ============================================================================


_SAMPLE_SCRIPT = """
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

class MyCustomInitializer(PyRITInitializer):
    \"\"\"A custom test initializer.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""


class TestInitializerServiceRegister:
    """Tests for InitializerService.register_initializer_async."""

    async def test_register_initializer_calls_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.register_from_content.return_value = "my_custom"
            mock_registry.list_metadata.return_value = [
                _make_initializer_metadata(registry_name="my_custom", class_name="MyCustomInitializer")
            ]
            service._registry = mock_registry

            result = await service.register_initializer_async(name="my_custom", script_content=_SAMPLE_SCRIPT)

            mock_registry.register_from_content.assert_called_once_with(name="my_custom", script_content=_SAMPLE_SCRIPT)
            assert result.initializer_name == "my_custom"

    async def test_register_initializer_propagates_value_error(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.register_from_content.side_effect = ValueError("no classes found")
            service._registry = mock_registry

            with pytest.raises(ValueError):
                await service.register_initializer_async(name="bad", script_content="x = 1")


class TestInitializerServiceUnregister:
    """Tests for InitializerService.unregister_initializer_async."""

    async def test_unregister_initializer_calls_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            service._registry = mock_registry

            await service.unregister_initializer_async(initializer_name="target")

            mock_registry.unregister_and_cleanup.assert_called_once_with("target")

    async def test_unregister_initializer_propagates_key_error(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.unregister_and_cleanup.side_effect = KeyError("not found")
            service._registry = mock_registry

            with pytest.raises(KeyError):
                await service.unregister_initializer_async(initializer_name="nonexistent")

    async def test_unregister_initializer_propagates_value_error_for_builtin(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.unregister_and_cleanup.side_effect = ValueError("Cannot remove built-in")
            service._registry = mock_registry

            with pytest.raises(ValueError, match="Cannot remove built-in"):
                await service.unregister_initializer_async(initializer_name="simple")


# ============================================================================
# POST / DELETE Route Tests
# ============================================================================


class TestRegisterInitializerRoute:
    """Tests for POST /api/initializers route."""

    def test_post_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        app.state.allow_custom_initializers = False
        response = client.post("/api/initializers", json={"name": "test", "script_content": _SAMPLE_SCRIPT})
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.parametrize("bad_name", ["../traversal", "UPPER", "has space", "1digit", ""])
    def test_post_returns_422_for_invalid_name(
        self, client_with_custom_initializers_enabled: TestClient, bad_name: str
    ) -> None:
        response = client_with_custom_initializers_enabled.post(
            "/api/initializers", json={"name": bad_name, "script_content": _SAMPLE_SCRIPT}
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_post_returns_201_with_registered_initializer(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        summary = RegisteredInitializer(
            initializer_name="my_custom",
            initializer_type="MyCustomInitializer",
            description="Custom init",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.post(
                "/api/initializers", json={"name": "my_custom", "script_content": _SAMPLE_SCRIPT}
            )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["initializer_name"] == "my_custom"

    def test_post_returns_400_for_invalid_script(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(side_effect=ValueError("no classes"))
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.post(
                "/api/initializers", json={"name": "bad", "script_content": "x = 1"}
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_forwards_name_and_content(self, client_with_custom_initializers_enabled: TestClient) -> None:
        summary = RegisteredInitializer(
            initializer_name="my_init",
            initializer_type="MyInit",
            description="desc",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            client_with_custom_initializers_enabled.post(
                "/api/initializers", json={"name": "my_init", "script_content": _SAMPLE_SCRIPT}
            )

            call_kwargs = mock_service.register_initializer_async.call_args.kwargs
            assert call_kwargs["name"] == "my_init"
            assert call_kwargs["script_content"] == _SAMPLE_SCRIPT

    def test_post_returns_409_for_duplicate_name(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(
                side_effect=ValueError("Initializer 'dup' is already registered.")
            )
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.post(
                "/api/initializers", json={"name": "dup", "script_content": _SAMPLE_SCRIPT}
            )

            assert response.status_code == status.HTTP_409_CONFLICT


class TestUnregisterInitializerRoute:
    """Tests for DELETE /api/initializers/{name} route."""

    def test_delete_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        app.state.allow_custom_initializers = False
        response = client.delete("/api/initializers/target")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_returns_204_on_success(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.unregister_initializer_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/target")

            assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_delete_returns_404_when_not_found(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.unregister_initializer_async = AsyncMock(side_effect=KeyError("not found"))
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_returns_400_for_builtin_initializer(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.unregister_initializer_async = AsyncMock(
                side_effect=ValueError("Cannot remove built-in initializer 'simple'.")
            )
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/simple")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "built-in" in response.json()["detail"].lower()
