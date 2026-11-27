# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Canary for the ``assert_never`` exhaustiveness guards (#4506).

The guards are ``TYPE_CHECKING``-only, so the repository-wide ``ty`` run is what proves each
dispatch handles every declared member. That proof is vacuous if ``assert_never`` ever stops
narrowing under the repository's ``ty`` version and configuration (an argument inferred as
``Any`` passes silently). This canary type-checks one synthetic dispatch and asserts that an
unhandled member is still reported, so a toolchain regression fails loudly here instead of
letting every guard in the repository go dead at once.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from unit.async_utils import TEST_WATCHDOG_SECONDS

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE_TEMPLATE = """\
from enum import Enum
from typing import TYPE_CHECKING, assert_never


class Canary(Enum):
    HANDLED = "handled"
{extra_member}

def dispatch(value: Canary) -> str:
    match value:
        case Canary.HANDLED:
            return "handled"
        case _:
            if TYPE_CHECKING:
                assert_never(value)
            return "fallback"
"""


@pytest.mark.parametrize("extend_enum", [False, True], ids=["handled", "unhandled"])
def test_assert_never_guards_still_narrow(tmp_path: Path, *, extend_enum: bool) -> None:
    extra_member = '    UNHANDLED = "unhandled"\n' if extend_enum else ""
    probe = tmp_path / "dispatch_probe.py"
    probe.write_text(_PROBE_TEMPLATE.format(extra_member=extra_member), encoding="utf-8")
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
    if extend_enum:
        assert result.returncode == 1, output
        assert output.count("error[") == 1, output
        assert "error[type-assertion-failure]" in output, output
        assert "Canary.UNHANDLED" in output, output
    else:
        assert result.returncode == 0, output
