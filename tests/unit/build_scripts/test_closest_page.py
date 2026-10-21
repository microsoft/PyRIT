# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Run the real closest-page algorithm in Node, with one startup for all cases.

The companion Node tests keep named cases and use a fresh, time-limited VM for
each call. The process deadline allows for slow CI startup, not algorithm speed.
Local environments without Node skip these tests.
"""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_closest_page_scenarios() -> None:
    result = subprocess.run(
        ["node", "--test-reporter=tap", str(Path(__file__).with_suffix(".cjs"))],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# tests 25" in result.stdout and "# pass 25" in result.stdout
