# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Migration history must be immutable. This hook enforces that by preventing deletion or updates to migration scripts.

Checks staged changes (local pre-commit), the full branch diff against origin/main (CI PRs),
and the previous commit (CI merge-queue / push-to-main).
"""

import os
import subprocess
import sys

_VERSIONS_PATH = "pyrit/memory/alembic/versions/"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git_stdout(*args: str) -> str:
    return _git(*args).stdout.strip()


def _get_violations(diff_spec: list[str]) -> list[str]:
    """Return lines from ``git diff --name-status`` that are not pure additions."""
    output = _git_stdout("diff", "--name-status", *diff_spec, "--", _VERSIONS_PATH)
    return [line for line in output.splitlines() if line and not line.startswith("A")]


def _in_ci() -> bool:
    return os.environ.get("CI", "").lower() in {"1", "true"} or "GITHUB_ACTIONS" in os.environ


def _fail_ci(reason: str) -> bool:
    """Fail closed in CI when we can't perform the check, pass through locally."""
    if _in_ci():
        print(f"[ERROR] Cannot verify alembic revision immutability: {reason}")
        print("        Ensure the CI checkout has full history (fetch-depth: 0).")
        return True
    return False


def has_revision_violations() -> bool:
    # Local pre-commit: check staged changes
    violations = _get_violations(["--cached"])
    if violations:
        _report(violations)
        return True

    # CI (PR): diff branch against its merge-base with origin/main.
    # The three-dot syntax (A...B) resolves to ``git diff $(merge-base A B) B``
    # automatically, so we don't need a separate merge-base call.  When
    # origin/main is missing (shallow clone) git exits non-zero.
    pr_diff = _git("diff", "--name-status", "origin/main...HEAD", "--", _VERSIONS_PATH)
    if pr_diff.returncode == 0:
        violations = [line for line in pr_diff.stdout.strip().splitlines() if line and not line.startswith("A")]
        if violations:
            _report(violations)
            return True
    elif _fail_ci("origin/main is not available (shallow clone?)"):
        return True

    # CI (merge-queue / push-to-main): on main the branch *is* origin/main, so
    # the diff above is empty.  Compare HEAD against its first parent to catch
    # deletions or modifications introduced by the merge commit itself.
    head_parent = _git("rev-parse", "--verify", "HEAD~1")
    if head_parent.returncode == 0:
        violations = _get_violations(["HEAD~1..HEAD"])
        if violations:
            _report(violations)
            return True
    elif _fail_ci("HEAD~1 is not available (shallow clone?)"):
        return True

    return False


def _report(violations: list[str]) -> None:
    print("[ERROR] Migration scripts can only be added, not modified or deleted.")
    print("The following disallowed changes were detected:")
    for v in violations:
        print(f"  {v}")


if __name__ == "__main__":
    if has_revision_violations():
        sys.exit(1)
