# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prove with ty that an ``assert_never`` dispatch handles every declared member."""

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from unit.async_utils import TEST_WATCHDOG_SECONDS

_REPO_ROOT = Path(__file__).resolve().parents[2]


def assert_dispatch_exhaustiveness(
    *, tmp_path: Path, probe_source: str, extended: bool, assertions: Sequence[str], new_member: str
) -> None:
    """
    Type-check a probe module and verify that only the expected ``assert_never`` calls fail.

    Args:
        tmp_path (Path): Directory for the generated probe module.
        probe_source (str): Source of the probe module.
        extended (bool): Whether the probe declares a member the dispatch does not handle.
        assertions (Sequence[str]): The ``assert_never(...)`` calls expected to fail when extended,
            once per expected failure.
        new_member (str): Text ty reports for the unhandled member.
    """
    probe = tmp_path / "dispatch_probe.py"
    probe.write_text(probe_source, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ty",
            "check",
            "--project",
            str(_REPO_ROOT),
            "--python-version",
            "3.11",
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=TEST_WATCHDOG_SECONDS,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == (1 if extended else 0), output
    if extended:
        assert output.count("error[") == len(assertions), output
        assert output.count("error[type-assertion-failure]") == len(assertions), output
        for assertion in set(assertions):
            assert output.count(assertion) >= assertions.count(assertion), output
        assert new_member in output, output
