# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the torch-free GCG dataset adapter export."""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from unit.async_utils import TEST_WATCHDOG_SECONDS


def test_create_gcg_data_config_async_is_exported_without_torch() -> None:
    code = dedent(
        """
        import importlib.abc
        import sys

        attempted_torch_imports = []

        class BlockTorch(importlib.abc.MetaPathFinder):
            def find_spec(
                self, fullname: str, path: object | None = None, target: object | None = None
            ) -> None:
                if fullname == "torch" or fullname.startswith("torch."):
                    attempted_torch_imports.append(fullname)
                    raise ModuleNotFoundError("Torch import blocked for this test", name=fullname)

        assert not any(name == "torch" or name.startswith("torch.") for name in sys.modules)
        sys.meta_path.insert(0, BlockTorch())

        import pyrit.executor.promptgen.gcg as gcg

        assert "create_gcg_data_config_async" in gcg.__all__
        assert "create_gcg_data_config_async" in dir(gcg)

        from pyrit.executor.promptgen.gcg import create_gcg_data_config_async
        from pyrit.executor.promptgen.gcg.data import create_gcg_data_config_async as helper

        assert create_gcg_data_config_async is helper
        assert gcg.create_gcg_data_config_async is helper
        assert not attempted_torch_imports, attempted_torch_imports
        assert not any(name == "torch" or name.startswith("torch.") for name in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[5],
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
