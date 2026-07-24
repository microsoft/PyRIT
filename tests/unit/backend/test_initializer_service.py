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
    ApplyInitializerResponse,
    ListEffectiveInitializerSettingsResponse,
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
)
from pyrit.backend.services.initializer_service import InitializerService, get_initializer_service
from pyrit.models import InitializerSetting, Parameter
from pyrit.registry import InitializerMetadata
from pyrit.setup.configuration_loader import InitializerConfig


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


class TestInitializerServiceSettings:
    """Tests for merged initializer settings behavior."""

    async def test_list_effective_initializer_settings_merges_baseline_and_overrides(self) -> None:
        metadata = [
            _make_initializer_metadata(registry_name="target", class_name="TargetInitializer"),
            _make_initializer_metadata(registry_name="widget", class_name="WidgetInitializer"),
            _make_initializer_metadata(registry_name="custom", class_name="CustomInitializer"),
        ]
        baseline_initializers = [
            InitializerConfig(name="target", args={"tags": ["baseline"]}),
            InitializerConfig(name="widget", args={"mode": "baseline"}),
        ]
        saved_overrides = [
            InitializerSetting(initializer_name="target", enabled=False, order_index=3),
            InitializerSetting(
                initializer_name="custom",
                enabled=True,
                parameters={"tags": ["override"]},
                order_index=0,
            ),
        ]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = saved_overrides

            result = await service.list_effective_initializer_settings_async(
                baseline_initializers=baseline_initializers
            )

            assert [item.initializer_name for item in result.items] == ["custom", "widget", "target"]
            assert [item.source for item in result.items] == ["override", "baseline", "baseline+override"]
            assert result.items[0].parameters == {"tags": ["override"]}
            assert result.items[1].parameters == {"mode": "baseline"}
            assert result.items[2].enabled is False
            assert result.items[2].saved_order_index == 3

    async def test_list_effective_initializer_settings_hides_scanner_only_initializers(self) -> None:
        """Scorer/technique/dataset/scenario-metadata initializers have no GUI-visible
        effect (no scenario-run, dataset, or live-scoring UI exists here), so the
        GUI-facing effective settings list excludes them even when they are configured
        in the baseline or have a saved override."""
        metadata = [
            _make_initializer_metadata(registry_name="target", class_name="TargetInitializer"),
            _make_initializer_metadata(registry_name="scorer", class_name="ScorerInitializer"),
            _make_initializer_metadata(registry_name="technique", class_name="TechniqueInitializer"),
            _make_initializer_metadata(registry_name="load_default_datasets", class_name="LoadDefaultDatasets"),
            _make_initializer_metadata(
                registry_name="preload_scenario_metadata", class_name="PreloadScenarioMetadata"
            ),
        ]
        baseline_initializers = [
            InitializerConfig(name="technique"),
            InitializerConfig(name="target"),
            InitializerConfig(name="scorer"),
            InitializerConfig(name="load_default_datasets"),
        ]
        saved_overrides = [
            InitializerSetting(initializer_name="scorer", parameters={"mode": "strict"}),
            InitializerSetting(initializer_name="preload_scenario_metadata", enabled=False),
        ]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = saved_overrides

            result = await service.list_effective_initializer_settings_async(
                baseline_initializers=baseline_initializers
            )

            assert [item.initializer_name for item in result.items] == ["target"]

    async def test_list_effective_initializer_settings_handles_disabled_override_without_order(self) -> None:
        baseline_initializers = [InitializerConfig(name="target", args={"tags": ["baseline"]})]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = [
                _make_initializer_metadata(registry_name="target")
            ]
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = [
                InitializerSetting(initializer_name="target", enabled=False)
            ]

            result = await service.list_effective_initializer_settings_async(
                baseline_initializers=baseline_initializers
            )

            assert result.items[0].enabled is False
            assert result.items[0].order_index == 0
            assert result.items[0].source == "baseline+override"

    async def test_save_initializer_setting_validates_and_persists(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()

            result = await service.save_initializer_setting_async(
                initializer_name="target",
                enabled=False,
                parameters={"tags": ["saved"]},
                order_index=2,
            )

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params={"tags": ["saved"]},
            )
            service._memory.add_initializer_setting.assert_called_once()
            assert result == InitializerSetting(
                initializer_name="target",
                enabled=False,
                parameters={"tags": ["saved"]},
                order_index=2,
            )

    async def test_delete_initializer_setting_calls_memory(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._memory = MagicMock()
            service._registry = MagicMock()

            await service.delete_initializer_setting_async(initializer_name="target")

            service._memory.delete_initializer_setting.assert_called_once_with(initializer_name="target")

    async def test_apply_initializer_uses_explicit_parameters(self) -> None:
        initializer = MagicMock()
        initializer.validate = MagicMock()
        initializer.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.return_value = initializer
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = [
                InitializerSetting(initializer_name="target", parameters={"tags": ["saved"]})
            ]

            result = await service.apply_initializer_async(
                initializer_name="target",
                parameters={"tags": ["explicit"]},
            )

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params={"tags": ["explicit"]},
            )
            initializer.validate.assert_called_once()
            initializer.initialize_async.assert_awaited_once()
            assert result == ApplyInitializerResponse(
                initializer_name="target",
                status="applied",
                applied_parameters={"tags": ["explicit"]},
            )

    async def test_apply_initializer_uses_saved_parameters_when_body_is_empty(self) -> None:
        initializer = MagicMock()
        initializer.validate = MagicMock()
        initializer.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.return_value = initializer
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = [
                InitializerSetting(initializer_name="target", parameters={"tags": ["saved"]})
            ]

            result = await service.apply_initializer_async(initializer_name="target")

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params={"tags": ["saved"]},
            )
            assert result.applied_parameters == {"tags": ["saved"]}

    async def test_apply_initializer_propagates_validation_errors(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.side_effect = ValueError("Unknown parameter")
            service._memory = MagicMock()
            service._memory.get_initializer_settings.return_value = []

            with pytest.raises(ValueError, match="Unknown parameter"):
                await service.apply_initializer_async(initializer_name="target")


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
        with (
            patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service,
            patch(
                "pyrit.backend.routes.initializers.asyncio.to_thread",
                new=AsyncMock(return_value=[]),
            ),
        ):
            mock_service = MagicMock()
            mock_service.list_effective_initializer_settings_async = AsyncMock(
                return_value=ListEffectiveInitializerSettingsResponse(items=[])
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers/settings")

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["items"] == []

    def test_put_initializer_settings_returns_saved_row(self, client: TestClient) -> None:
        saved_setting = InitializerSetting(
            initializer_name="target",
            enabled=False,
            parameters={"tags": ["saved"]},
            order_index=2,
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.save_initializer_setting_async = AsyncMock(return_value=saved_setting)
            mock_get_service.return_value = mock_service

            response = client.put(
                "/api/initializers/target/settings",
                json={"enabled": False, "parameters": {"tags": ["saved"]}, "order_index": 2},
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["initializer_name"] == "target"
            mock_service.save_initializer_setting_async.assert_called_once_with(
                initializer_name="target",
                enabled=False,
                parameters={"tags": ["saved"]},
                order_index=2,
            )

    def test_put_initializer_settings_returns_404_for_missing_initializer(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.save_initializer_setting_async = AsyncMock(side_effect=KeyError("missing"))
            mock_get_service.return_value = mock_service

            response = client.put("/api/initializers/unknown/settings", json={"enabled": True})

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_initializer_settings_returns_204(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.delete_initializer_setting_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.delete("/api/initializers/target/settings")

            assert response.status_code == status.HTTP_204_NO_CONTENT
            mock_service.delete_initializer_setting_async.assert_called_once_with(initializer_name="target")

    def test_post_apply_initializer_returns_200(self, client: TestClient) -> None:
        apply_result = ApplyInitializerResponse(
            initializer_name="target",
            status="applied",
            applied_parameters={"tags": ["saved"]},
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.apply_initializer_async = AsyncMock(return_value=apply_result)
            mock_get_service.return_value = mock_service

            response = client.post("/api/initializers/target/apply", json={"parameters": {"tags": ["saved"]}})

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["status"] == "applied"
            mock_service.apply_initializer_async.assert_called_once_with(
                initializer_name="target",
                parameters={"tags": ["saved"]},
            )

    def test_post_apply_initializer_returns_400_for_invalid_parameters(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.apply_initializer_async = AsyncMock(side_effect=ValueError("bad params"))
            mock_get_service.return_value = mock_service

            response = client.post("/api/initializers/target/apply", json={"parameters": {"bad": True}})

            assert response.status_code == status.HTTP_400_BAD_REQUEST


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


class TestInitializerServiceRegister:
    """Tests for InitializerService.register_initializer_async."""

    async def test_register_initializer_calls_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.register_from_content.return_value = "my_custom"
            mock_registry.get_all_registered_class_metadata.return_value = [
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
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

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
