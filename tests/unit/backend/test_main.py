# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the FastAPI application entry point (main.py).

Covers the lifespan manager and setup_frontend function.
"""

import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from pyrit.backend.main import SPAStaticFiles, app, lifespan, setup_frontend
from pyrit.setup.configuration_loader import ConfigurationLoader


class TestLifespan:
    """Tests for the application lifespan context manager."""

    async def test_lifespan_yields(self) -> None:
        """Test that lifespan delegates to ConfigurationLoader and yields."""
        fake_config = ConfigurationLoader()
        service = MagicMock(run_additional_initializers_async=AsyncMock())
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()) as init_mock,
            patch("pyrit.backend.main.get_initializer_service", return_value=service),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            init_mock.assert_awaited_once_with(raise_on_initializer_error=False)
            assert app.state.default_labels == {}
            assert app.state.max_concurrent_scenario_runs == fake_config.max_concurrent_scenario_runs
            assert app.state.allow_custom_initializers is False
            service.run_additional_initializers_async.assert_awaited_once_with()

    async def test_lifespan_warns_when_custom_initializers_allowed(self) -> None:
        """Test that lifespan logs a warning when allow_custom_initializers is enabled."""
        fake_config = ConfigurationLoader(allow_custom_initializers=True)
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch(
                "pyrit.backend.main.get_initializer_service",
                return_value=MagicMock(
                    run_additional_initializers_async=AsyncMock(),
                ),
            ),
            patch("pyrit.backend.main.setup_frontend"),
            patch.object(logging.getLogger("pyrit.backend.main"), "warning") as mock_warning,
        ):
            async with lifespan(app):
                pass

            mock_warning.assert_called_once()

    async def test_lifespan_populates_default_labels_from_operator_and_operation(self) -> None:
        """Test that operator and operation are exposed as default_labels."""
        fake_config = ConfigurationLoader(operator="alice", operation="op-42")
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch(
                "pyrit.backend.main.get_initializer_service",
                return_value=MagicMock(
                    run_additional_initializers_async=AsyncMock(),
                ),
            ),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            assert app.state.default_labels == {"operator": "alice", "operation": "op-42"}

    async def test_lifespan_loads_explicit_config_as_override(self) -> None:
        """Test that PYRIT_CONFIG_FILE overlays the default configuration."""
        fake_config = ConfigurationLoader()
        with (
            patch.dict(os.environ, {"PYRIT_CONFIG_FILE": "/tmp/foo.yaml"}, clear=False),
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config) as load_mock,
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch(
                "pyrit.backend.main.get_initializer_service",
                return_value=MagicMock(
                    run_additional_initializers_async=AsyncMock(),
                ),
            ),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            assert str(load_mock.call_args.kwargs["config_file"]).endswith("foo.yaml")

    async def test_lifespan_configures_custom_initializer_source_from_config(self) -> None:
        """Test that YAML config determines the custom script source."""
        fake_config = ConfigurationLoader(custom_initializers_source="C:/yaml/initializers")
        registry = MagicMock()
        service = MagicMock(run_additional_initializers_async=AsyncMock())
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch("pyrit.backend.main.InitializerRegistry.get_registry_singleton", return_value=registry),
            patch("pyrit.backend.main.get_initializer_service", return_value=service),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

        registry.configure_custom_scripts_source.assert_called_once_with("C:/yaml/initializers")
        registry.register_stored_initializers.assert_not_called()

    async def test_lifespan_registers_stored_initializers_when_enabled(self) -> None:
        """Test that enabled custom initializers are registered before baseline initialization."""
        fake_config = ConfigurationLoader(allow_custom_initializers=True)
        call_order: list[str] = []
        registry = MagicMock()
        registry.register_stored_initializers.side_effect = lambda: call_order.append("custom")
        service = MagicMock(run_additional_initializers_async=AsyncMock())

        async def initialize_async(*, raise_on_initializer_error: bool) -> None:
            assert raise_on_initializer_error is False
            call_order.append("baseline")

        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock(side_effect=initialize_async)),
            patch("pyrit.backend.main.InitializerRegistry.get_registry_singleton", return_value=registry),
            patch("pyrit.backend.main.get_initializer_service", return_value=service),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

        registry.register_stored_initializers.assert_called_once_with()
        assert call_order == ["custom", "baseline"]

    async def test_lifespan_downloads_blob_config_to_temporary_file(self) -> None:
        """Test that an Azure Blob config URI is materialized and removed after loading."""
        fake_config = ConfigurationLoader()
        config_content = b"operator: blob-user\n"
        loaded_path: Path | None = None

        def load_config(*, config_file: Path, env_akv_ref: list[str] | None = None) -> ConfigurationLoader:
            nonlocal loaded_path
            assert env_akv_ref is None
            loaded_path = config_file
            assert config_file.suffix == ".yaml"
            assert config_file.read_bytes() == config_content
            return fake_config

        with (
            patch.dict(
                os.environ,
                {"PYRIT_CONFIG_FILE": "https://account.blob.core.windows.net/config/config.yaml"},
                clear=False,
            ),
            patch(
                "pyrit.backend.services.configuration_file_service._download_blob_config_async",
                new=AsyncMock(return_value=config_content),
            ),
            patch.object(ConfigurationLoader, "load_with_overrides", side_effect=load_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch(
                "pyrit.backend.main.get_initializer_service",
                return_value=MagicMock(
                    run_additional_initializers_async=AsyncMock(),
                ),
            ),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

        assert loaded_path is not None
        assert not loaded_path.exists()


class TestSetupFrontend:
    """Tests for the setup_frontend function."""

    def test_dev_mode_does_not_mount_static(self) -> None:
        """Test that DEV_MODE skips static file serving."""
        with (
            patch("pyrit.backend.main.DEV_MODE", True),
            patch("builtins.print") as mock_print,
        ):
            setup_frontend()

            mock_print.assert_called_once()
            assert "DEVELOPMENT" in mock_print.call_args[0][0]

    def test_frontend_exists_mounts_static(self) -> None:
        """Test that setup_frontend mounts StaticFiles when frontend exists."""
        mock_frontend_path = MagicMock()
        mock_frontend_path.exists.return_value = True
        mock_frontend_path.__str__ = lambda self: "/tmp/fake_frontend"

        # Create the directory so StaticFiles doesn't raise
        os.makedirs("/tmp/fake_frontend", exist_ok=True)

        with (
            patch("pyrit.backend.main.DEV_MODE", False),
            patch("pyrit.backend.main.Path") as mock_path_cls,
            patch("builtins.print"),
        ):
            mock_path_instance = MagicMock()
            mock_path_instance.parent.__truediv__ = MagicMock(return_value=mock_frontend_path)
            mock_path_cls.return_value = mock_path_instance

            setup_frontend()

    def test_frontend_missing_warns_but_continues(self) -> None:
        """Test that setup_frontend warns but does not exit when frontend is missing."""
        mock_frontend_path = MagicMock()
        mock_frontend_path.exists.return_value = False
        mock_frontend_path.__str__ = lambda self: "/nonexistent/frontend"

        with (
            patch("pyrit.backend.main.DEV_MODE", False),
            patch("pyrit.backend.main.Path") as mock_path_cls,
            patch("builtins.print") as mock_print,
        ):
            mock_path_instance = MagicMock()
            mock_path_instance.parent.__truediv__ = MagicMock(return_value=mock_frontend_path)
            mock_path_cls.return_value = mock_path_instance

            setup_frontend()  # Should NOT raise

            # Verify warning was printed
            printed = " ".join(str(c) for c in mock_print.call_args_list)
            assert "warning" in printed.lower()


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    """Build a TestClient whose root is an SPAStaticFiles mount over a fake frontend build."""
    (tmp_path / "index.html").write_text("<!doctype html><title>spa-index</title>")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('real asset')")

    test_app = FastAPI()

    @test_app.get("/api/real")
    def _real() -> dict[str, bool]:
        return {"ok": True}

    test_app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True), name="frontend")
    return TestClient(test_app)


class TestSPAStaticFiles:
    """Tests for the SPA fallback that serves index.html on unmatched non-API paths."""

    def test_root_serves_index(self, spa_client: TestClient) -> None:
        """Test that the root path serves index.html."""
        resp = spa_client.get("/")
        assert resp.status_code == 200
        assert "spa-index" in resp.text

    def test_serves_real_asset(self, spa_client: TestClient) -> None:
        """Test that an existing static asset is served directly, not the fallback."""
        resp = spa_client.get("/assets/app.js")
        assert resp.status_code == 200
        assert "real asset" in resp.text

    def test_unknown_spa_path_serves_index(self, spa_client: TestClient) -> None:
        """Test that a deep client-side route falls back to index.html with a 200."""
        resp = spa_client.get("/attacks/ar-99")
        assert resp.status_code == 200
        assert "spa-index" in resp.text

    def test_nested_unknown_spa_path_serves_index(self, spa_client: TestClient) -> None:
        """Test that a multi-segment client-side route also falls back to index.html."""
        resp = spa_client.get("/attacks/ar-99/conversations/c-1")
        assert resp.status_code == 200
        assert "spa-index" in resp.text

    def test_unknown_api_path_still_404(self, spa_client: TestClient) -> None:
        """Test that an unknown /api path stays a real 404 instead of being masked by index.html."""
        resp = spa_client.get("/api/bogus")
        assert resp.status_code == 404
        assert "spa-index" not in resp.text

    def test_api_prefixed_client_route_serves_index(self, spa_client: TestClient) -> None:
        """Test that a client route merely starting with "api" (e.g. /apikeys) still falls back to index.html."""
        resp = spa_client.get("/apikeys")
        assert resp.status_code == 200
        assert "spa-index" in resp.text

    async def test_windows_backslash_api_path_still_404(self, tmp_path: Path) -> None:
        """Test that a backslash-normalized /api path (as Starlette produces on Windows) stays a real 404.

        On Windows ``StaticFiles`` hands ``get_response`` an ``os.sep``-joined path
        ("api\\bogus"), so the ``/api`` guard must normalize separators before matching.
        ``os.sep`` is patched so the Windows branch is exercised on any platform.
        """
        (tmp_path / "index.html").write_text("<!doctype html><title>spa-index</title>")
        spa = SPAStaticFiles(directory=str(tmp_path), html=True)
        scope = {"type": "http", "method": "GET"}

        with patch("pyrit.backend.main.os.sep", "\\"):
            with pytest.raises(StarletteHTTPException) as exc_info:
                await spa.get_response("api\\bogus", scope)

        assert exc_info.value.status_code == 404
