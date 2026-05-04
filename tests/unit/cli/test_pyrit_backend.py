# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pyrit.cli import pyrit_backend


class TestParseArgs:
    """Tests for pyrit_backend.parse_args."""

    def test_parse_args_defaults(self) -> None:
        """Should parse backend defaults correctly."""
        args = pyrit_backend.parse_args(args=[])

        assert args.host == "localhost"
        assert args.port == 8000
        assert args.config_file is None

    def test_parse_args_accepts_config_file(self) -> None:
        """Should parse --config-file argument."""
        args = pyrit_backend.parse_args(args=["--config-file", "./custom_conf.yaml"])

        assert args.config_file == Path("./custom_conf.yaml")


class TestInitializeAndRun:
    """Tests for pyrit_backend.initialize_and_run_async."""

    async def test_initialize_and_run_passes_config_file_to_frontend_core(self) -> None:
        """Should forward parsed config file path to FrontendCore."""
        parsed_args = pyrit_backend.parse_args(args=["--config-file", "./custom_conf.yaml"])

        with (
            patch("pyrit.cli.pyrit_backend.frontend_core.FrontendCore") as mock_core_class,
            patch("uvicorn.Config") as mock_uvicorn_config,
            patch("uvicorn.Server") as mock_uvicorn_server,
        ):
            mock_core = MagicMock()
            mock_core.initialize_async = AsyncMock()
            mock_core._initializer_configs = None
            mock_core_class.return_value = mock_core

            mock_server = MagicMock()
            mock_server.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server

            result = await pyrit_backend.initialize_and_run_async(parsed_args=parsed_args)

            assert result == 0
            mock_core_class.assert_called_once()
            assert mock_core_class.call_args.kwargs["config_file"] == Path("./custom_conf.yaml")
            mock_core.initialize_async.assert_awaited_once()
            mock_uvicorn_config.assert_called_once()
            mock_uvicorn_server.assert_called_once()
            mock_server.serve.assert_awaited_once()
