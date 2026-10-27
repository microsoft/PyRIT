# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Run documentation JavaScript tests with one Node startup per suite.

The Node tests use fresh, time-limited VMs for behavior checks. The shared
correctness watchdog allows for slow CI startup, not slow JavaScript execution.
Local environments without Node skip these tests.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("suite", ["test_closest_page.cjs", "test_landing_video_players.cjs"])
def test_docs_javascript(suite: str) -> None:
    result = subprocess.run(
        ["node", "--test-reporter=tap", str(Path(__file__).with_name(suite))],
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = dict(re.findall(r"^# (tests|pass) (\d+)$", result.stdout, re.MULTILINE))
    assert int(summary.get("tests", "0")) > 0, result.stdout
    assert summary.get("pass") == summary["tests"], result.stdout
