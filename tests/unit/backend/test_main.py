# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the FastAPI application entry point (main.py).

Covers the lifespan manager and setup_frontend function.
"""

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

from pyrit.backend.main import app, lifespan, setup_frontend
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
