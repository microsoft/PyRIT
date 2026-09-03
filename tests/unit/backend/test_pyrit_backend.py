# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend import pyrit_backend


class TestParseArgs:
    """Tests for pyrit_backend.parse_args."""

    def test_parse_args_defaults(self) -> None:
        args = pyrit_backend.parse_args(args=[])
        assert args.host == "localhost"
        assert args.port == 8000
        assert args.config_file is None
        assert args.reload is False

    def test_parse_args_accepts_config_file(self) -> None:
        args = pyrit_backend.parse_args(args=["--config-file", "./custom_conf.yaml"])
        assert args.config_file == "./custom_conf.yaml"

    def test_parse_args_preserves_blob_config_uri(self) -> None:
        blob_uri = "https://behnamousatairtsa.blob.core.windows.net/copyrit/.pyrit_conf"

        args = pyrit_backend.parse_args(args=["--config-file", blob_uri])

        assert args.config_file == blob_uri

    def test_parse_args_accepts_host_port(self) -> None:
        args = pyrit_backend.parse_args(args=["--host", "0.0.0.0", "--port", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_parse_args_accepts_reload(self) -> None:
        args = pyrit_backend.parse_args(args=["--reload"])
        assert args.reload is True


class TestMain:
    """Tests for pyrit_backend.main."""

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_starts_uvicorn(self, mock_run_server: MagicMock) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 0
        mock_run_server.assert_called_once()

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_forwards_config_file_via_env(self, mock_run_server: MagicMock) -> None:
        import os

        with patch.dict(os.environ, {}, clear=False):
            pyrit_backend.main(args=["--config-file", "./custom.yaml"])
            assert os.environ.get("PYRIT_CONFIG_FILE") is not None
            assert "custom.yaml" in os.environ["PYRIT_CONFIG_FILE"]

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_forwards_blob_config_uri_via_env(self, mock_run_server: MagicMock) -> None:
        import os

        blob_uri = "https://behnamousatairtsa.blob.core.windows.net/copyrit/.pyrit_conf"
        with patch.dict(os.environ, {}, clear=False):
            pyrit_backend.main(args=["--config-file", blob_uri])

            assert os.environ["PYRIT_CONFIG_FILE"] == blob_uri

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_passes_host_and_port(self, mock_run_server: MagicMock) -> None:
        pyrit_backend.main(args=["--host", "0.0.0.0", "--port", "9000"])
        call_kwargs = mock_run_server.call_args.kwargs
        assert call_kwargs["host"] == "0.0.0.0"
        assert call_kwargs["port"] == 9000

    def test_main_invalid_args(self) -> None:
        result = pyrit_backend.main(args=["--invalid-flag"])
        assert result == 2

    @patch("pyrit.backend.pyrit_backend._run_server", side_effect=KeyboardInterrupt())
    def test_main_keyboard_interrupt_returns_zero(self, mock_run_server: MagicMock, capsys) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 0
        captured = capsys.readouterr()
        assert "Backend stopped" in captured.out

    @patch("pyrit.backend.pyrit_backend._run_server", side_effect=RuntimeError("boom"))
    def test_main_unexpected_exception_returns_one(self, mock_run_server: MagicMock, capsys) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 1
        captured = capsys.readouterr()
        assert "boom" in captured.out

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_forwards_log_level(self, mock_run_server: MagicMock) -> None:
        pyrit_backend.main(args=["--log-level", "DEBUG"])
        assert mock_run_server.call_args.kwargs["log_level"] == "debug"

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_forwards_reload_flag(self, mock_run_server: MagicMock) -> None:
        pyrit_backend.main(args=["--reload"])
        assert mock_run_server.call_args.kwargs["reload"] is True

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_warns_when_binding_non_loopback(self, mock_run_server: MagicMock, capsys) -> None:
        pyrit_backend.main(args=["--host", "0.0.0.0", "--port", "9000"])
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "0.0.0.0" in captured.err
        assert "9000" in captured.err

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_no_warning_for_localhost(self, mock_run_server: MagicMock, capsys) -> None:
        pyrit_backend.main(args=["--host", "localhost"])
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    @patch("pyrit.backend.pyrit_backend._run_server")
    def test_main_no_warning_for_127_0_0_1(self, mock_run_server: MagicMock, capsys) -> None:
        pyrit_backend.main(args=["--host", "127.0.0.1"])
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err


class TestRunServer:
    """Tests for uvicorn startup."""

    @patch("uvicorn.run")
    def test_starts_uvicorn_without_reload(self, mock_run: MagicMock) -> None:
        pyrit_backend._run_server(host="localhost", port=8000, log_level="warning", reload=False)

        mock_run.assert_called_once_with(
            "pyrit.backend.main:app",
            host="localhost",
            port=8000,
            log_level="warning",
            reload=False,
        )

    @patch("uvicorn.run")
    def test_reload_uses_uvicorn_reloader(self, mock_run: MagicMock) -> None:
        pyrit_backend._run_server(host="localhost", port=8000, log_level="warning", reload=True)

        mock_run.assert_called_once_with(
            "pyrit.backend.main:app",
            host="localhost",
            port=8000,
            log_level="warning",
            reload=True,
        )


class TestParseArgsDoesNotAcceptLegacyFlags:
    """
    Regression: the thin backend only takes --host/--port/--config-file/--log-level/--reload.
    Legacy --database and --initializers must be rejected so callers (docker/start.sh,
    frontend/dev.py) cannot silently regress to passing them.
    """

    def test_database_flag_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            pyrit_backend.parse_args(args=["--database", "SQLite"])
        assert exc_info.value.code != 0

    def test_initializers_flag_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            pyrit_backend.parse_args(args=["--initializers", "target"])
        assert exc_info.value.code != 0

    def test_initialization_scripts_flag_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            pyrit_backend.parse_args(args=["--initialization-scripts", "./x.py"])
        assert exc_info.value.code != 0
