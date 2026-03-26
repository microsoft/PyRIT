# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration test for pyrit_shell startup performance.

The shell uses a background thread to import heavy modules (frontend_core,
pyrit.scenario, etc.) so the banner animation can start quickly.  If the
module-level imports regress, the animation will be blocked and the shell
will appear to hang on startup.
"""

import subprocess
import sys

# Maximum acceptable time (seconds) for `from pyrit.cli import pyrit_shell`
# to complete.  The banner animation starts immediately after this import,
# so keeping it under 5 s is critical for perceived startup speed.
_MAX_IMPORT_SECONDS = 6


def test_pyrit_shell_module_imports_within_budget() -> None:
    """Importing pyrit.cli.pyrit_shell must complete in under 6 seconds.

    This guards against accidentally adding heavy top-level imports
    (e.g. ``from pyrit.cli import frontend_core``) that would block
    the main thread and delay the banner animation.
    """
    script = (
        "import time; "
        "t = time.perf_counter(); "
        "from pyrit.cli import pyrit_shell; "  # noqa: F401
        "elapsed = time.perf_counter() - t; "
        "print(f'{elapsed:.2f}'); "
        f"raise SystemExit(0 if elapsed < {_MAX_IMPORT_SECONDS} else 1)"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    elapsed_str = result.stdout.strip()
    assert result.returncode == 0, (
        f"pyrit_shell module import took {elapsed_str or '?'}s, "
        f"exceeding the {_MAX_IMPORT_SECONDS}s budget. "
        f"Check for heavy top-level imports in pyrit_shell.py.\n"
        f"returncode={result.returncode}\nstderr: {result.stderr.strip()}"
    )
