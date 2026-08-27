# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend initializer service and routes.
"""

from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.initializers import (
    ApplyInitializerResponse,
    BaselineInitializerSetting,
    CustomInitializerListResponse,
    CustomInitializerResponse,
    InitializerSettingsResponse,
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
)
from pyrit.backend.services.initializer_service import InitializerService, get_initializer_service
from pyrit.backend.services.stale_while_revalidate_cache import StaleWhileRevalidateCache
from pyrit.models import AdditionalInitializer, Parameter
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
    """Tests for baseline + additional initializer settings behavior."""

    async def test_list_initializer_settings_returns_baseline_and_additional(self) -> None:
        metadata = [
            _make_initializer_metadata(registry_name="target", class_name="TargetInitializer"),
            _make_initializer_metadata(registry_name="widget", class_name="WidgetInitializer"),
            _make_initializer_metadata(registry_name="custom", class_name="CustomInitializer"),
        ]
        baseline_initializers = [
            BaselineInitializerSetting(
                initializer_name="target",
                parameters={"tags": ["baseline"]},
                order_index=0,
            ),
            BaselineInitializerSetting(
                initializer_name="widget",
                parameters={"mode": "baseline"},
                order_index=1,
            ),
        ]
        additional = [
            AdditionalInitializer(id="a1", initializer_name="custom", parameters={"tags": ["extra"]}, order_index=0),
            AdditionalInitializer(id="a2", initializer_name="target", order_index=1),
        ]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = additional

            result = await service.list_initializer_settings_async(baseline_initializers=baseline_initializers)

            assert [item.initializer_name for item in result.baseline] == ["target", "widget"]
            assert [item.order_index for item in result.baseline] == [0, 1]
            assert result.baseline[0].parameters == {"tags": ["baseline"]}

            assert [item.id for item in result.additional] == ["a1", "a2"]
            assert [item.initializer_name for item in result.additional] == ["custom", "target"]
            assert result.additional[0].parameters == {"tags": ["extra"]}

    async def test_list_initializer_settings_shows_all_configured_baseline_initializers(self) -> None:
        """The read-only baseline list reflects exactly what ``.pyrit_conf`` configured to run,
        preserving order, with no initializer types filtered out."""
        metadata = [
            _make_initializer_metadata(registry_name="target", class_name="TargetInitializer"),
            _make_initializer_metadata(registry_name="scorer", class_name="ScorerInitializer"),
            _make_initializer_metadata(registry_name="technique", class_name="TechniqueInitializer"),
            _make_initializer_metadata(registry_name="load_default_datasets", class_name="LoadDefaultDatasets"),
        ]
        baseline_initializers = [
            BaselineInitializerSetting(initializer_name="technique", order_index=0),
            BaselineInitializerSetting(initializer_name="target", order_index=1),
            BaselineInitializerSetting(initializer_name="scorer", order_index=2),
            BaselineInitializerSetting(initializer_name="load_default_datasets", order_index=3),
        ]

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = metadata
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = []

            result = await service.list_initializer_settings_async(baseline_initializers=baseline_initializers)

            assert [item.initializer_name for item in result.baseline] == [
                "technique",
                "target",
                "scorer",
                "load_default_datasets",
            ]
            assert [item.order_index for item in result.baseline] == [0, 1, 2, 3]

    async def test_list_initializer_settings_passes_through_unregistered_names(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.get_all_registered_class_metadata.return_value = []
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(id="a1", initializer_name="gone")
            ]

            result = await service.list_initializer_settings_async(baseline_initializers=[])

            assert result.additional[0].initializer_name == "gone"

    async def test_create_additional_initializer_validates_and_persists(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()

            result = await service.create_additional_initializer_async(
                initializer_name="target",
                parameters={"tags": ["saved"]},
                order_index=2,
            )

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params={"tags": ["saved"]},
            )
            service._memory.add_additional_initializer.assert_called_once()
            assert result.initializer_name == "target"
            assert result.parameters == {"tags": ["saved"]}
            assert result.order_index == 2
            assert result.id

    async def test_create_additional_initializer_appends_after_existing_when_order_index_missing(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(id="a1", initializer_name="target", order_index=0),
                AdditionalInitializer(id="a2", initializer_name="widget", order_index=3),
            ]

            result = await service.create_additional_initializer_async(
                initializer_name="target",
                parameters=None,
                order_index=None,
            )

            assert result.order_index == 4

    async def test_create_additional_initializer_starts_at_zero_when_none_exist(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = []

            result = await service.create_additional_initializer_async(
                initializer_name="target",
                parameters=None,
                order_index=None,
            )

            assert result.order_index == 0

    async def test_update_additional_initializer_preserves_existing_order_when_missing(self) -> None:
        existing = AdditionalInitializer(
            id="a1", initializer_name="target", parameters={"tags": ["old"]}, order_index=7
        )

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [existing]

            result = await service.update_additional_initializer_async(
                initializer_id="a1",
                parameters={"tags": ["new"]},
                order_index=None,
            )

            assert result.order_index == 7

    async def test_update_additional_initializer_preserves_id_and_name(self) -> None:
        existing = AdditionalInitializer(id="a1", initializer_name="target", parameters={"tags": ["old"]})

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [existing]

            result = await service.update_additional_initializer_async(
                initializer_id="a1",
                parameters={"tags": ["new"]},
                order_index=5,
            )

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params={"tags": ["new"]},
            )
            service._memory.add_additional_initializer.assert_called_once()
            assert result == AdditionalInitializer(
                id="a1",
                initializer_name="target",
                parameters={"tags": ["new"]},
                order_index=5,
            )

    async def test_update_additional_initializer_raises_key_error_when_missing(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = []

            with pytest.raises(KeyError):
                await service.update_additional_initializer_async(
                    initializer_id="missing", parameters=None, order_index=None
                )

    async def test_delete_additional_initializer_calls_memory(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._memory = MagicMock()
            service._registry = MagicMock()

            await service.delete_additional_initializer_async(initializer_id="a1")

            service._memory.delete_additional_initializer.assert_called_once_with(initializer_id="a1")

    async def test_apply_initializer_uses_explicit_parameters(self) -> None:
        initializer = MagicMock()
        initializer.validate = MagicMock()
        initializer.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.return_value = initializer
            service._memory = MagicMock()

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

    async def test_apply_initializer_with_no_parameters(self) -> None:
        initializer = MagicMock()
        initializer.validate = MagicMock()
        initializer.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.return_value = initializer
            service._memory = MagicMock()

            result = await service.apply_initializer_async(initializer_name="target")

            service._registry.create_and_configure.assert_called_once_with(
                "target",
                initializer_params=None,
            )
            assert result.applied_parameters is None

    async def test_apply_initializer_propagates_validation_errors(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.side_effect = ValueError("Unknown parameter")
            service._memory = MagicMock()

            with pytest.raises(ValueError, match="Unknown parameter"):
                await service.apply_initializer_async(initializer_name="target")

    async def test_run_additional_initializers_runs_each_in_order(self) -> None:
        first = MagicMock()
        first.validate = MagicMock()
        first.initialize_async = AsyncMock(return_value=None)
        second = MagicMock()
        second.validate = MagicMock()
        second.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.side_effect = [first, second]
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(id="a1", initializer_name="target", parameters={"tags": ["one"]}, order_index=0),
                AdditionalInitializer(id="a2", initializer_name="widget", order_index=1),
            ]

            await service.run_additional_initializers_async(allow_custom_initializers=True)

            assert service._registry.create_and_configure.call_args_list[0].args == ("target",)
            assert service._registry.create_and_configure.call_args_list[0].kwargs == {
                "initializer_params": {"tags": ["one"]}
            }
            assert service._registry.create_and_configure.call_args_list[1].args == ("widget",)
            first.initialize_async.assert_awaited_once()
            second.initialize_async.assert_awaited_once()

    async def test_run_additional_initializers_no_op_when_empty(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = []

            await service.run_additional_initializers_async(allow_custom_initializers=True)

            service._registry.create_and_configure.assert_not_called()

    async def test_run_additional_initializers_skips_custom_when_disabled(self) -> None:
        builtin = MagicMock()
        builtin.validate = MagicMock()
        builtin.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.is_builtin.side_effect = lambda name: name == "builtin"
            service._registry.create_and_configure.return_value = builtin
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(id="a1", initializer_name="builtin", order_index=0),
                AdditionalInitializer(id="a2", initializer_name="custom", order_index=1),
            ]

            await service.run_additional_initializers_async(allow_custom_initializers=False)

            service._registry.create_and_configure.assert_called_once_with("builtin", initializer_params=None)
            builtin.initialize_async.assert_awaited_once()

    async def test_run_additional_initializers_isolates_failures(self) -> None:
        failing = MagicMock()
        failing.supported_parameters = []
        failing.validate = MagicMock(side_effect=ValueError("missing required environment variable"))
        healthy = MagicMock()
        healthy.supported_parameters = []
        healthy.validate = MagicMock()
        healthy.initialize_async = AsyncMock(return_value=None)

        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.create_and_configure.side_effect = [failing, healthy]
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(id="bad", initializer_name="target", order_index=0),
                AdditionalInitializer(id="good", initializer_name="widget", order_index=1),
            ]

            await service.run_additional_initializers_async(allow_custom_initializers=True)

            failing.initialize_async.assert_not_called()
            healthy.initialize_async.assert_awaited_once()


# ============================================================================
# Route Tests
# ============================================================================


class TestInitializerServiceValueValidation:
    """Raw parameter values are coerced against declared types and rejected when invalid."""

    @staticmethod
    def _service_with_parameters(parameters: list[Parameter]) -> InitializerService:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = []
            configured = MagicMock()
            configured.supported_parameters = parameters
            service._registry.create_and_configure.return_value = configured
            return service

    async def test_create_rejects_value_that_violates_declared_type(self) -> None:
        service = self._service_with_parameters([Parameter(name="days", description="d", default=30, param_type=int)])

        with pytest.raises(ValueError, match="days"):
            await service.create_additional_initializer_async(
                initializer_name="refresh_datasets",
                parameters={"days": "abc"},
                order_index=None,
            )

        service._memory.add_additional_initializer.assert_not_called()

    async def test_create_accepts_value_that_matches_declared_type(self) -> None:
        service = self._service_with_parameters([Parameter(name="days", description="d", default=30, param_type=int)])

        result = await service.create_additional_initializer_async(
            initializer_name="refresh_datasets",
            parameters={"days": 7},
            order_index=0,
        )

        assert result.parameters == {"days": 7}
        service._memory.add_additional_initializer.assert_called_once()

    async def test_create_rejects_out_of_set_list_value(self) -> None:
        service = self._service_with_parameters(
            [Parameter(name="tags", description="d", default=["a"], param_type=list[Literal["a", "b"]])]
        )

        with pytest.raises(ValueError, match="tags"):
            await service.create_additional_initializer_async(
                initializer_name="target",
                parameters={"tags": ["bogus"]},
                order_index=None,
            )

    async def test_apply_rejects_value_that_violates_declared_type(self) -> None:
        service = self._service_with_parameters([Parameter(name="days", description="d", default=30, param_type=int)])

        with pytest.raises(ValueError, match="days"):
            await service.apply_initializer_async(
                initializer_name="refresh_datasets",
                parameters={"days": "abc"},
            )


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
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.list_initializer_settings_async = AsyncMock(
                return_value=InitializerSettingsResponse(baseline=[], additional=[])
            )
            mock_get_service.return_value = mock_service

            response = client.get("/api/initializers/settings")

            assert response.status_code == status.HTTP_200_OK
            body = response.json()
            assert body["baseline"] == []
            assert body["additional"] == []

    def test_post_additional_initializer_returns_created_row(self, client: TestClient) -> None:
        created = AdditionalInitializer(
            id="a1",
            initializer_name="target",
            parameters={"tags": ["saved"]},
            order_index=2,
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_additional_initializer_async = AsyncMock(return_value=created)
            mock_get_service.return_value = mock_service

            response = client.post(
                "/api/initializers/settings",
                json={"initializer_name": "target", "parameters": {"tags": ["saved"]}, "order_index": 2},
            )

            assert response.status_code == status.HTTP_201_CREATED
            body = response.json()
            assert body["id"] == "a1"
            assert body["initializer_name"] == "target"
            mock_service.create_additional_initializer_async.assert_called_once_with(
                initializer_name="target",
                parameters={"tags": ["saved"]},
                order_index=2,
            )

    def test_post_additional_initializer_returns_404_for_missing_initializer(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_additional_initializer_async = AsyncMock(side_effect=KeyError("missing"))
            mock_get_service.return_value = mock_service

            response = client.post("/api/initializers/settings", json={"initializer_name": "unknown"})

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_put_additional_initializer_returns_updated_row(self, client: TestClient) -> None:
        updated = AdditionalInitializer(
            id="a1",
            initializer_name="target",
            parameters={"tags": ["new"]},
            order_index=5,
        )

        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_additional_initializer_async = AsyncMock(return_value=updated)
            mock_get_service.return_value = mock_service

            response = client.put(
                "/api/initializers/settings/a1",
                json={"parameters": {"tags": ["new"]}, "order_index": 5},
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.json()["parameters"] == {"tags": ["new"]}
            mock_service.update_additional_initializer_async.assert_called_once_with(
                initializer_id="a1",
                parameters={"tags": ["new"]},
                order_index=5,
            )

    def test_put_additional_initializer_returns_404_when_missing(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.update_additional_initializer_async = AsyncMock(side_effect=KeyError("missing"))
            mock_get_service.return_value = mock_service

            response = client.put("/api/initializers/settings/missing", json={})

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_additional_initializer_returns_204(self, client: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.delete_additional_initializer_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client.delete("/api/initializers/settings/a1")

            assert response.status_code == status.HTTP_204_NO_CONTENT
            mock_service.delete_additional_initializer_async.assert_called_once_with(initializer_id="a1")

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


def _initialize_custom_cache(service: InitializerService) -> None:
    service._custom_cache = StaleWhileRevalidateCache[CustomInitializerListResponse](
        ttl_seconds=10.0,
        load_async=lambda _: service._load_custom_initializers_async(),
    )


class TestInitializerServiceCustomScripts:
    """Tests for custom initializer script storage operations."""

    async def test_save_custom_initializer_stores_script(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.get_custom_initializer_source.return_value = "C:/custom/my_custom.py"
            service._registry = mock_registry
            service._memory = MagicMock()
            _initialize_custom_cache(service)

            result = await service.save_custom_initializer_async(name="my_custom", script_content=_SAMPLE_SCRIPT)

            mock_registry.save_custom_initializer_script.assert_called_once_with(
                name="my_custom", script_content=_SAMPLE_SCRIPT
            )
            assert result.initializer_name == "my_custom"
            assert result.script_content == _SAMPLE_SCRIPT

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
            _initialize_custom_cache(service)
            service._registry.custom_scripts_source = "C:/custom"
            service._registry.list_custom_initializer_sources.return_value = {"my_custom": _SAMPLE_SCRIPT}
            service._registry.get_custom_initializer_source.return_value = "C:/custom/my_custom.py"

            result = await service.list_custom_initializers_async()

            assert result.source == "C:/custom"
            assert result.items == [
                CustomInitializerResponse(
                    initializer_name="my_custom",
                    script_content=_SAMPLE_SCRIPT,
                    source="C:/custom/my_custom.py",
                )
            ]

    async def test_list_custom_initializers_refreshes_expired_cache_in_background(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            _initialize_custom_cache(service)
            service._registry.custom_scripts_source = "C:/custom"
            service._registry.list_custom_initializer_sources.side_effect = [
                {"my_custom": "VALUE = 1\n"},
                {"my_custom": "VALUE = 2\n"},
            ]
            service._registry.get_custom_initializer_source.return_value = "C:/custom/my_custom.py"

            with patch(
                "pyrit.backend.services.stale_while_revalidate_cache.time.monotonic", return_value=100.0
            ) as monotonic_mock:
                first = await service.list_custom_initializers_async()
                monotonic_mock.return_value = 111.0
                stale = await service.list_custom_initializers_async()
                refresh_task = service._custom_cache.get_refresh_task("custom-initializers")
                assert refresh_task is not None
                await refresh_task
                refreshed = await service.list_custom_initializers_async()

            assert first.items[0].script_content == "VALUE = 1\n"
            assert stale.items[0].script_content == "VALUE = 1\n"
            assert refreshed.items[0].script_content == "VALUE = 2\n"
            assert service._registry.list_custom_initializer_sources.call_count == 2

    async def test_restore_custom_initializers_delegates_to_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._registry.custom_scripts_source = "C:/custom"
            service._registry.list_custom_initializer_sources.return_value = {"my_custom": _SAMPLE_SCRIPT}
            service._registry.get_custom_initializer_source.return_value = "C:/custom/my_custom.py"
            _initialize_custom_cache(service)

            await service.restore_custom_initializers_async()

            service._registry.restore_custom_initializers.assert_called_once_with()
            cached = await service.list_custom_initializers_async()
            assert cached.items[0].script_content == _SAMPLE_SCRIPT


class TestInitializerServiceDeleteCustomScript:
    """Tests for InitializerService.delete_custom_initializer_async."""

    async def test_delete_custom_initializer_calls_registry(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            service._registry = mock_registry
            service._memory = MagicMock()
            _initialize_custom_cache(service)

            await service.delete_custom_initializer_async(initializer_name="target")

            mock_registry.delete_custom_initializer_script.assert_called_once_with(name="target")

    async def test_delete_custom_initializer_propagates_key_error(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            mock_registry = MagicMock()
            mock_registry.delete_custom_initializer_script.side_effect = KeyError("not found")
            service._registry = mock_registry
            service._memory = MagicMock()

            with pytest.raises(KeyError):
                await service.delete_custom_initializer_async(initializer_name="nonexistent")

    async def test_unregister_initializer_rejects_additional_initializer_reference(self) -> None:
        with patch.object(InitializerService, "__init__", lambda self: None):
            service = InitializerService()
            service._registry = MagicMock()
            service._memory = MagicMock()
            service._memory.get_additional_initializers.return_value = [
                AdditionalInitializer(initializer_name="custom", order_index=0)
            ]

            with pytest.raises(ValueError, match="additional initializer settings reference it"):
                await service.delete_custom_initializer_async(initializer_name="custom")

            service._registry.delete_custom_initializer_script.assert_not_called()


# ============================================================================
# POST / DELETE Route Tests
# ============================================================================


class TestCustomInitializerRoutes:
    """Tests for stored custom initializer script routes."""

    def test_get_custom_initializers_returns_403_when_disabled(self, client: TestClient) -> None:
        app.state.allow_custom_initializers = False

        response = client.get("/api/initializers/custom")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_get_custom_initializers_returns_storage_source_and_items(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        custom = CustomInitializerResponse(
            initializer_name="my_custom",
            script_content=_SAMPLE_SCRIPT,
            source="C:/custom/my_custom.py",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            response_model = CustomInitializerListResponse(source="C:/custom", items=[custom])
            mock_service.list_custom_initializers_async = AsyncMock(return_value=response_model)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.get("/api/initializers/custom")

            assert response.status_code == status.HTTP_200_OK
            assert response.json() == response_model.model_dump()

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


class TestDeleteCustomInitializerRoute:
    """Tests for DELETE /api/initializers/custom/{name} route."""

    def test_delete_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        app.state.allow_custom_initializers = False
        response = client.delete("/api/initializers/custom/target")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_delete_returns_204_on_success(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.delete_custom_initializer_async = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/custom/target")

            assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_delete_returns_404_when_not_found(self, client_with_custom_initializers_enabled: TestClient) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.delete_custom_initializer_async = AsyncMock(side_effect=KeyError("not found"))
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/custom/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_returns_400_when_additional_initializer_references_name(
        self, client_with_custom_initializers_enabled: TestClient
    ) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.delete_custom_initializer_async = AsyncMock(
                side_effect=ValueError(
                    "Cannot remove initializer 'custom' while additional initializer settings reference it."
                )
            )
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.delete("/api/initializers/custom/custom")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "additional initializer settings reference it" in response.json()["detail"]


class TestUpdateCustomInitializerRoute:
    """Tests for PUT /api/initializers/custom/{name} route."""

    def test_put_returns_403_when_custom_initializers_disabled(self, client: TestClient) -> None:
        app.state.allow_custom_initializers = False

        response = client.put("/api/initializers/custom/custom", json={"script_content": _SAMPLE_SCRIPT})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_put_updates_custom_initializer(self, client_with_custom_initializers_enabled: TestClient) -> None:
        custom = CustomInitializerResponse(
            initializer_name="custom",
            script_content=_SAMPLE_SCRIPT,
            source="C:/custom/custom.py",
        )
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.save_custom_initializer_async = AsyncMock(return_value=custom)
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.put(
                "/api/initializers/custom/custom",
                json={"script_content": _SAMPLE_SCRIPT},
            )

        assert response.status_code == status.HTTP_200_OK
        mock_service.save_custom_initializer_async.assert_awaited_once_with(
            name="custom",
            script_content=_SAMPLE_SCRIPT,
        )

    def test_put_maps_invalid_source_error(
        self,
        client_with_custom_initializers_enabled: TestClient,
    ) -> None:
        with patch("pyrit.backend.routes.initializers.get_initializer_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.save_custom_initializer_async = AsyncMock(side_effect=ValueError("invalid source"))
            mock_get_service.return_value = mock_service

            response = client_with_custom_initializers_enabled.put(
                "/api/initializers/custom/custom",
                json={"script_content": _SAMPLE_SCRIPT},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
