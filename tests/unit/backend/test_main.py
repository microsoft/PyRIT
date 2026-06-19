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
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()) as init_mock,
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            init_mock.assert_awaited_once()
            assert app.state.default_labels == {}
            assert app.state.max_concurrent_scenario_runs == fake_config.max_concurrent_scenario_runs
            assert app.state.allow_custom_initializers is False

    async def test_lifespan_warns_when_custom_initializers_allowed(self) -> None:
        """Test that lifespan logs a warning when allow_custom_initializers is enabled."""
        fake_config = ConfigurationLoader(allow_custom_initializers=True)
        with (
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config),
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
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
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            assert app.state.default_labels == {"operator": "alice", "operation": "op-42"}

    async def test_lifespan_reads_config_file_env_var(self) -> None:
        """Test that PYRIT_CONFIG_FILE is forwarded to ConfigurationLoader.load_with_overrides."""
        fake_config = ConfigurationLoader()
        with (
            patch.dict(os.environ, {"PYRIT_CONFIG_FILE": "/tmp/foo.yaml"}, clear=False),
            patch.object(ConfigurationLoader, "load_with_overrides", return_value=fake_config) as load_mock,
            patch.object(ConfigurationLoader, "initialize_pyrit_async", new=AsyncMock()),
            patch("pyrit.backend.main.setup_frontend"),
        ):
            async with lifespan(app):
                pass

            call_kwargs = load_mock.call_args.kwargs
            assert str(call_kwargs["config_file"]).endswith("foo.yaml")


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
