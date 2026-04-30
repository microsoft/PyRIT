# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Import guard tests to prevent performance regressions.

These tests verify that importing key entry points does NOT pull in heavy
third-party modules. Each test spawns a fresh subprocess (since sys.modules
is global and sticky within a process) and checks which modules are loaded.

If a test fails, it means someone added a top-level import that pulls in an
expensive dependency. The fix is to defer that import to point-of-use (inside
the method that actually needs it).
"""

import subprocess
import sys

import pytest


def _check_forbidden_imports(*, import_statement: str, forbidden: list[str]) -> list[str]:
    """
    Run `import_statement` in a subprocess and return any forbidden modules that got loaded.
    """
    code = (
        "import sys\n"
        f"{import_statement}\n"
        "forbidden = " + repr(forbidden) + "\n"
        "loaded = [m for m in forbidden if any(k == m or k.startswith(m + '.') for k in sys.modules)]\n"
        "if loaded:\n"
        "    print(','.join(sorted(loaded)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Subprocess crashed: {result.stderr}")
    output = result.stdout.strip()
    return output.split(",") if output else []


# Heavy modules that should never be loaded during CLI arg parsing.
# This ensures `pyrit_scan --help` stays near-instant (~0.3s).
_CLI_FORBIDDEN = [
    "alembic",
    "av",
    "azure.storage.blob",
    "httpx",
    "numpy",
    "openai",
    "pandas",
    "pydantic",
    "scipy",
    "sqlalchemy",
    "torch",
    "transformers",
]

# Heavy modules that should not be loaded by `import pyrit` alone.
_IMPORT_PYRIT_FORBIDDEN = [
    "alembic",
    "av",
    "azure.storage.blob",
    "openai",
    "pandas",
    "scipy",
    "sqlalchemy",
    "torch",
    "transformers",
]

# Heavy modules that should not be loaded by importing the PromptTarget base class.
_PROMPT_TARGET_FORBIDDEN = [
    "av",
    "pandas",
    "scipy",
    "torch",
    "transformers",
]


class TestImportGuards:
    """Verify heavy modules are not eagerly loaded at key import points."""

    def test_cli_arg_parsing_does_not_load_heavy_modules(self):
        """
        Importing pyrit_scan's module-level symbols (for --help) must not
        pull in any heavy third-party dependencies.
        """
        loaded = _check_forbidden_imports(
            import_statement="from pyrit.cli.pyrit_scan import parse_args",
            forbidden=_CLI_FORBIDDEN,
        )
        assert not loaded, (
            f"CLI arg parsing loaded heavy modules: {loaded}. "
            f"Move these imports to point-of-use (inside a function/method)."
        )

    def test_import_pyrit_does_not_load_heavy_modules(self):
        """
        `import pyrit` must stay fast and not pull in database or ML libraries.
        """
        loaded = _check_forbidden_imports(
            import_statement="import pyrit",
            forbidden=_IMPORT_PYRIT_FORBIDDEN,
        )
        assert not loaded, (
            f"`import pyrit` loaded heavy modules: {loaded}. "
            f"Check pyrit/__init__.py and ensure heavy submodules are not eagerly imported."
        )

    def test_prompt_target_base_does_not_load_ml_modules(self):
        """
        Importing PromptTarget must not pull in ML frameworks like transformers or av.
        These are only needed by specific subclasses (HuggingFaceChatTarget, etc.).
        """
        loaded = _check_forbidden_imports(
            import_statement="from pyrit.prompt_target import PromptTarget",
            forbidden=_PROMPT_TARGET_FORBIDDEN,
        )
        assert not loaded, (
            f"PromptTarget base class loaded ML modules: {loaded}. "
            f"Ensure heavy subclass imports use __getattr__ lazy loading in __init__.py."
        )
