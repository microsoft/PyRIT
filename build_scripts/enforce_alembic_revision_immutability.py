# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Migration history must be immutable. This hook enforces that by preventing deletion or updates to migration scripts.

Checks both staged changes (local pre-commit) and the full branch diff against origin/main (CI).
"""

import subprocess
import sys

_VERSIONS_PATH = "pyrit/memory/alembic/versions/"


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip()


def _has_non_add_changes(diff_spec: list[str]) -> bool:
    output = _git("diff", "--name-status", *diff_spec, "--", _VERSIONS_PATH)
    return any(line and not line.startswith("A") for line in output.splitlines())


def has_revision_violations() -> bool:
    # Local pre-commit: check staged changes
    if _has_non_add_changes(["--cached"]):
        return True

    # CI: check full branch diff against origin/main
    merge_base = _git("merge-base", "origin/main", "HEAD")
    return bool(merge_base and _has_non_add_changes([f"{merge_base}...HEAD"]))


if __name__ == "__main__":
    if has_revision_violations():
        print("[ERROR] Migration scripts can only be added, not modified or deleted.")
        sys.exit(1)
