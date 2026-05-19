# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import MagicMock, patch

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
        assert args.config_file == Path("./custom_conf.yaml")

    def test_parse_args_accepts_host_port(self) -> None:
        args = pyrit_backend.parse_args(args=["--host", "0.0.0.0", "--port", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_parse_args_accepts_reload(self) -> None:
        args = pyrit_backend.parse_args(args=["--reload"])
        assert args.reload is True


class TestMain:
    """Tests for pyrit_backend.main."""

    @patch("uvicorn.run")
    def test_main_starts_uvicorn(self, mock_run: MagicMock) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 0
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == "pyrit.backend.main:app"

    @patch("uvicorn.run")
    def test_main_forwards_config_file_via_env(self, mock_run: MagicMock) -> None:
        import os

        with patch.dict(os.environ, {}, clear=False):
            pyrit_backend.main(args=["--config-file", "./custom.yaml"])
            assert os.environ.get("PYRIT_CONFIG_FILE") is not None
            assert "custom.yaml" in os.environ["PYRIT_CONFIG_FILE"]

    @patch("uvicorn.run")
    def test_main_passes_host_and_port(self, mock_run: MagicMock) -> None:
        pyrit_backend.main(args=["--host", "0.0.0.0", "--port", "9000"])
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["host"] == "0.0.0.0"
        assert call_kwargs["port"] == 9000

    def test_main_invalid_args(self) -> None:
        result = pyrit_backend.main(args=["--invalid-flag"])
        assert result == 2

    @patch("uvicorn.run", side_effect=KeyboardInterrupt())
    def test_main_keyboard_interrupt_returns_zero(self, mock_run: MagicMock, capsys) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 0
        captured = capsys.readouterr()
        assert "Backend stopped" in captured.out

    @patch("uvicorn.run", side_effect=RuntimeError("boom"))
    def test_main_unexpected_exception_returns_one(self, mock_run: MagicMock, capsys) -> None:
        result = pyrit_backend.main(args=[])
        assert result == 1
        captured = capsys.readouterr()
        assert "boom" in captured.out

    @patch("uvicorn.run")
    def test_main_forwards_log_level(self, mock_run: MagicMock) -> None:
        pyrit_backend.main(args=["--log-level", "DEBUG"])
        assert mock_run.call_args.kwargs["log_level"] == "debug"

    @patch("uvicorn.run")
    def test_main_forwards_reload_flag(self, mock_run: MagicMock) -> None:
        pyrit_backend.main(args=["--reload"])
        assert mock_run.call_args.kwargs["reload"] is True
