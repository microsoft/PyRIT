# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend initializer service and routes.
"""

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models import (
    ListRegisteredInitializersResponse,
    PaginationInfo,
)
from pyrit.backend.models.initializers import (
    ConfiguredInitializerSetting,
    RegisteredInitializer,
)
from pyrit.backend.services import InitializerService, get_initializer_service
from pyrit.models import Parameter
from pyrit.registry import InitializerMetadata
from pyrit.registry.file_document_storage import DocumentStorageUnavailableError, DocumentUnreadableError


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> Iterator[TestClient]:
    """Create a test client for the FastAPI app."""
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app, headers=compatibility_headers)
    finally:
        app.dependency_overrides.pop(require_admin, None)


@pytest.fixture
def client_with_custom_initializers_enabled(compatibility_headers: dict[str, str]):
    """Create a test client with allow_custom_initializers enabled."""
    app.state.allow_custom_initializers = True
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app, headers=compatibility_headers)
    finally:
        app.state.allow_custom_initializers = False
        app.dependency_overrides.pop(require_admin, None)


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
    supported_parameters: tuple[Parameter, ...] = (
        Parameter(name="tags", description="Comma-separated tag filter", default=["default"]),
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
            service._registry.get_all_registered_class_metadata.return_value = []

            result = await service.list_initializers_async()

            assert result.items == []
            assert result.pagination.has_more is False

    async def test_list_initializers_returns_initializers_from_registry(self) -> None:
        metadata = _make_initializer_metadata()

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

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
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=3)

            assert len(result.items) == 3
            assert result.pagination.has_more is True
            assert result.pagination.next_cursor == "init_2"

    async def test_list_initializers_paginates_with_cursor(self) -> None:
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(5)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=2, cursor="init_1")

            assert len(result.items) == 2
            assert result.items[0].initializer_name == "init_2"
            assert result.items[1].initializer_name == "init_3"
            assert result.pagination.has_more is True

    async def test_list_initializers_rejects_a_cursor_that_names_no_initializer(self) -> None:
        """A stale cursor must raise, not silently restart at the first page."""
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(5)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            with pytest.raises(ValueError, match="cursor is not valid for the initializer registry"):
                await service.list_initializers_async(limit=2, cursor="removed_initializer")

    async def test_list_initializers_last_page_has_more_false(self) -> None:
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(3)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            result = await service.list_initializers_async(limit=5)

            assert len(result.items) == 3
            assert result.pagination.has_more is False
            assert result.pagination.next_cursor is None

    async def test_list_initializers_with_no_env_vars(self) -> None:
        metadata = _make_initializer_metadata(required_env_vars=(), supported_parameters=())

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

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
            service._registry.get_all_registered_class_metadata.return_value = [metadata]

            result = await service.get_initializer_async(initializer_name="target")

            assert result is not None
            assert result.initializer_name == "target"

    async def test_get_initializer_returns_none_for_missing(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = []

            result = await service.get_initializer_async(initializer_name="nonexistent")

            assert result is None


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
            supported_parameters=[Parameter(name="tags", description="Tag filter", default=["default"])],
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
            assert item["supported_parameters"][0]["default"] == ["default"]

    def test_list_initializers_returns_400_after_the_cursor_initializer_is_removed(self, client: TestClient) -> None:
        """Removing the initializer a client's cursor names must not replay the first page."""
        metadata_list = [_make_initializer_metadata(registry_name=f"init_{i}", class_name=f"Init{i}") for i in range(3)]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata_list

            with patch("pyrit.backend.routes.initializers.get_initializer_service", return_value=service):
                first = client.get("/api/initializers?limit=1")
                cursor = first.json()["pagination"]["next_cursor"]
                assert cursor == "init_0"
                assert (
                    client.get(f"/api/initializers?limit=1&cursor={cursor}").json()["items"][0]["initializer_name"]
                    == "init_1"
                )

                service._registry.get_all_registered_class_metadata.return_value = metadata_list[1:]
                response = client.get(f"/api/initializers?limit=1&cursor={cursor}")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "The cursor is not valid for the initializer registry"

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

    def test_get_initializer_settings_returns_200(self, client: TestClient) -> None:
        app.state.configured_initializers = [
            ConfiguredInitializerSetting(
                initializer_name="target",
                parameters={"tags": ["default"]},
                order_index=0,
            )
        ]
        try:
            response = client.get("/api/initializers/settings")
            assert response.status_code == status.HTTP_200_OK
            assert response.json() == {
                "configured": [
                    {
                        "initializer_name": "target",
                        "parameters": {"tags": ["default"]},
                        "order_index": 0,
                    }
                ]
            }
        finally:
            del app.state.configured_initializers

    def test_initializer_settings_mutation_routes_are_removed(self, client: TestClient) -> None:
        assert client.post("/api/initializers/settings", json={}).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert client.put("/api/initializers/settings/item", json={}).status_code in {
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        }
        assert client.delete("/api/initializers/settings/item").status_code in {
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        }

    def test_apply_initializer_route_is_removed(self, client: TestClient) -> None:
        response = client.post("/api/initializers/target/apply", json={"parameters": {}})

        assert response.status_code in {
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        }


# ============================================================================
# Service Register/Unregister Tests
# ============================================================================


_SAMPLE_SCRIPT = """
from pyrit.setup.pyrit_initializer import PyRITInitializer

class MyCustomInitializer(PyRITInitializer):
    \"\"\"A custom test initializer.\"\"\"

    async def initialize_async(self) -> None:
        pass
"""


class TestInitializerServiceCustomRegistration:
    """Tests for runtime custom initializer registration."""

    async def test_register_initializer_still_updates_runtime_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [
                _make_initializer_metadata(registry_name="my_custom", class_name="MyCustomInitializer")
            ]

            result = await service.register_initializer_async(name="my_custom", script_content=_SAMPLE_SCRIPT)

            service._registry.register_from_content.assert_called_once_with(
                name="my_custom",
                script_content=_SAMPLE_SCRIPT,
            )
            assert result.initializer_name == "my_custom"

    async def test_list_custom_initializers_returns_stored_sources(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.list_stored_initializer_sources.return_value = (
                "C:/custom",
                [("my_custom", _SAMPLE_SCRIPT, "C:/custom/my_custom.py")],
            )

            result = await service.list_custom_initializers_async()

            assert result.source == "C:/custom"
            assert result.items[0].initializer_name == "my_custom"
            assert result.items[0].source == "C:/custom/my_custom.py"

    async def test_unregister_initializer_removes_runtime_registration(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()

            await service.unregister_initializer_async(initializer_name="my_custom")

            service._registry.unregister_and_cleanup.assert_called_once_with("my_custom")


# ============================================================================
# POST / DELETE Route Tests
# ============================================================================


class TestCustomInitializerRoutes:
    """Tests for runtime custom initializer routes."""

    def test_post_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        response = client.post(
            "/api/initializers",
            json={"name": "custom", "script_content": _SAMPLE_SCRIPT},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        response = client.delete("/api/initializers/custom")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize(
        ("method", "path", "json_body"),
        [
            ("GET", "/api/initializers/custom", None),
            ("POST", "/api/initializers", {"name": "custom", "script_content": _SAMPLE_SCRIPT}),
            ("DELETE", "/api/initializers/custom", None),
        ],
    )
    def test_custom_initializer_routes_require_admin(
        self,
        client_with_custom_initializers_enabled: TestClient,
        method: str,
        path: str,
        json_body: dict[str, str] | None,
    ) -> None:
        """Test that custom script operations apply the administrator dependency."""

        def reject_non_admin() -> None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access is required")

        app.dependency_overrides[require_admin] = reject_non_admin
        try:
            response = client_with_custom_initializers_enabled.request(method, path, json=json_body)
        finally:
            app.dependency_overrides.pop(require_admin)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_custom_initializers_uses_read_only_route(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_custom_initializers_async = AsyncMock(return_value={"source": "C:/custom", "items": []})
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.get("/api/initializers/custom")

        assert response.status_code == status.HTTP_200_OK
        mock_service.list_custom_initializers_async.assert_awaited_once_with()

    @pytest.mark.parametrize("operation", ["list", "register", "delete"])
    def test_custom_initializer_routes_return_503_for_storage_failure(
        self,
        client_with_custom_initializers_enabled: TestClient,
        operation: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test that the log names the storage location and the response doesn't."""
        error = DocumentStorageUnavailableError(
            label="Custom initializer", source="/srv/initializers", reason="Permission denied"
        )
        with (
            patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service,
            caplog.at_level(logging.WARNING, logger="pyrit.backend.middleware.error_handlers"),
        ):
            mock_service = MagicMock()
            mock_service.list_custom_initializers_async = AsyncMock(side_effect=error)
            mock_service.register_initializer_async = AsyncMock(side_effect=error)
            mock_service.unregister_initializer_async = AsyncMock(side_effect=error)
            mock_get_service.return_value = mock_service
            if operation == "list":
                response = client_with_custom_initializers_enabled.get("/api/initializers/custom")
            elif operation == "register":
                response = client_with_custom_initializers_enabled.post(
                    "/api/initializers",
                    json={"name": "custom", "script_content": _SAMPLE_SCRIPT},
                )
            else:
                response = client_with_custom_initializers_enabled.delete("/api/initializers/custom")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "Custom initializer storage is unavailable"
        assert "/srv/initializers" not in response.text
        assert "Custom initializer storage at /srv/initializers is unavailable: Permission denied" in caplog.text

    def test_deleting_a_script_that_cant_be_read_returns_503_with_the_reason(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        error = DocumentUnreadableError(
            label="Custom initializer",
            source="/srv/initializers",
            name="custom",
            file_name="custom.py",
            reason="Not a regular file",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.unregister_initializer_async = AsyncMock(side_effect=error)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/custom")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["detail"] == "Custom initializer 'custom' can't be read: Not a regular file"

    def test_post_root_still_registers_runtime_initializer(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        summary = RegisteredInitializer(
            initializer_name="runtime_custom",
            initializer_type="MyCustomInitializer",
            description="Custom init",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(return_value=summary)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.post(
                "/api/initializers",
                json={"name": "runtime_custom", "script_content": _SAMPLE_SCRIPT},
            )

        assert response.status_code == status.HTTP_201_CREATED
        mock_service.register_initializer_async.assert_awaited_once_with(
            name="runtime_custom",
            script_content=_SAMPLE_SCRIPT,
        )

    def test_post_rejects_script_without_initializer_subclass(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.register_initializer_async = AsyncMock(
                side_effect=ValueError(
                    "Uploaded script for 'not_an_initializer' does not contain a concrete PyRITInitializer subclass."
                )
            )
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.post(
                "/api/initializers",
                json={"name": "not_an_initializer", "script_content": "VALUE = 1\n"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "does not contain a concrete PyRITInitializer subclass" in response.json()["detail"]
