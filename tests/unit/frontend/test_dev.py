# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the local frontend development launcher."""

import importlib.util
import io
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


def _load_dev_module() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "frontend" / "dev.py"
    spec = importlib.util.spec_from_file_location("pyrit_frontend_dev", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_backend_selects_local_authentication() -> None:
    module = _load_dev_module()
    process = MagicMock()
    with (
        patch.object(module.os, "chdir"),
        patch.object(module.subprocess, "run"),
        patch.object(module.subprocess, "Popen", return_value=process) as popen,
        patch.dict(os.environ, {}, clear=True),
    ):
        assert module.start_backend() is process

    command = popen.call_args.args[0]
    environment = popen.call_args.kwargs["env"]
    assert command[command.index("--host") + 1] == "localhost"
    assert environment["PYRIT_AUTH_MODE"] == "local"


def test_start_frontend_binds_vite_to_loopback() -> None:
    module = _load_dev_module()
    process = MagicMock()
    with (
        patch.object(module.os, "chdir"),
        patch.object(module.subprocess, "Popen", return_value=process) as popen,
        patch.dict(os.environ, {}, clear=True),
    ):
        assert module.start_frontend() is process

    environment = popen.call_args.kwargs["env"]
    assert environment["PYRIT_FRONTEND_HOST"] == "127.0.0.1"


def test_start_frontend_preserves_explicit_host_override() -> None:
    module = _load_dev_module()
    with (
        patch.object(module.os, "chdir"),
        patch.object(module.subprocess, "Popen", return_value=MagicMock()) as popen,
        patch.dict(os.environ, {"PYRIT_FRONTEND_HOST": "0.0.0.0"}, clear=True),
    ):
        module.start_frontend()

    assert popen.call_args.kwargs["env"]["PYRIT_FRONTEND_HOST"] == "0.0.0.0"


def test_forward_process_output_drains_child_pipe() -> None:
    module = _load_dev_module()
    process = MagicMock(stdout=io.BytesIO(b"vite ready\n"))
    output = MagicMock()
    output.buffer = MagicMock()
    with patch.object(module.sys, "stdout", output):
        thread = module._forward_process_output(process)
        thread.join(timeout=1)

    assert not thread.is_alive()
    output.buffer.write.assert_called_once_with(b"vite ready\n")


def test_vite_defaults_to_loopback() -> None:
    vite_config = (Path(__file__).resolve().parents[3] / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "process.env.PYRIT_FRONTEND_HOST ?? '127.0.0.1'" in vite_config
    assert "host: frontendHost" in vite_config
    assert "host: true" not in vite_config


def test_seeded_frontend_ci_selects_local_authentication() -> None:
    playwright_config = (Path(__file__).resolve().parents[3] / "frontend" / "playwright.config.ts").read_text(
        encoding="utf-8"
    )

    assert "--auth-mode local --host 127.0.0.1" in playwright_config
