# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deliberate schema migration tool for production databases.

This script is the ONLY sanctioned way to apply Alembic migrations to a production
database. It is intended to be run during the release process (see
doc/contributing/10_release_process.md) or by a CD pipeline — never by normal
application startup.

It constructs an AzureSQLMemory with skip_schema_migration=True (bypassing the
runtime guard), then explicitly calls _run_schema_migration to upgrade to head.
The environment checks ensure this only runs from a release branch.

Safety rails:
- Validates the environment (release branch, clean working tree, no .dev version).
- Interactive confirmation when running in a terminal.
- Exits non-zero on any failure.

Usage:
    python build_scripts/migrate_prod_memory_schema.py

The script reads the production connection string from
AZURE_SQL_DB_CONNECTION_STRING_PROD (loaded from ~/.pyrit/.env).
"""

import subprocess
import sys

import dotenv

from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

# Load .env files from ~/.pyrit/ (same files that initialize_pyrit_async loads)
# Use override=False so explicitly-set env vars take precedence over .env values
for _env_file in [CONFIGURATION_DIRECTORY_PATH / ".env", CONFIGURATION_DIRECTORY_PATH / ".env.local"]:
    if _env_file.exists():
        dotenv.load_dotenv(_env_file, override=False, interpolate=True)

# ANSI color codes
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _print_error(message: str) -> None:
    """Print an error message in red to stderr."""
    print(f"{_RED}ERROR: {message}{_RESET}", file=sys.stderr)


def _print_success(message: str) -> None:
    """Print a success message in green."""
    print(f"{_GREEN}{message}{_RESET}")


def _get_db_revision(engine) -> str | None:
    """Read the current Alembic revision from the database, or None if not versioned."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    from pyrit.memory.migration import PYRIT_MEMORY_ALEMBIC_VERSION_TABLE

    inspector = sa_inspect(engine)
    if PYRIT_MEMORY_ALEMBIC_VERSION_TABLE not in inspector.get_table_names():
        return None
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT version_num FROM {PYRIT_MEMORY_ALEMBIC_VERSION_TABLE}"))
        row = result.fetchone()
        return row[0] if row else None


def _check_release_environment() -> list[str]:
    """
    Validate that the script is running in a proper release environment.

    Returns a list of warning/error messages. Empty list means all checks pass.
    """
    issues: list[str] = []

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if not branch.startswith("releases/"):
            issues.append(
                f"Not on a release branch (current: '{branch}'). "
                "Production migrations should run from 'releases/vX.Y.Z'."
            )
    except (subprocess.CalledProcessError, FileNotFoundError):
        issues.append("Could not determine current Git branch.")

    try:
        dirty_files = subprocess.check_output(
            ["git", "status", "--porcelain", "--", "pyrit/memory/"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if dirty_files:
            issues.append(
                "Uncommitted changes detected in pyrit/memory/:\n"
                f"  {dirty_files}\n"
                "  Commit or stash changes before migrating production."
            )
    except (subprocess.CalledProcessError, FileNotFoundError):
        issues.append("Could not check Git working tree status.")

    try:
        from pyrit import __version__

        if ".dev" in __version__:
            issues.append(
                f"PyRIT version is '{__version__}' (development). "
                "Production migrations should use a release version (no .dev suffix)."
            )
    except ImportError:
        issues.append("Could not determine PyRIT version.")

    return issues


def main() -> int:
    """Entry point for production schema migration."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Apply Alembic schema migrations to the production database.",
    )
    parser.add_argument(
        "--skip-environment-check",
        action="store_true",
        help="Skip release environment checks (branch, clean tree, version). Use only in CI with caution.",
    )
    args = parser.parse_args()

    # Safety rail: Verify release environment
    if not args.skip_environment_check:
        issues = _check_release_environment()
        if issues:
            _print_error("Release environment checks failed:")
            for issue in issues:
                _print_error(f"  - {issue}")
            _print_error("Fix the above issues or pass --skip-environment-check (CI only).")
            return 1
    else:
        print(f"{_YELLOW}WARNING: Skipping release environment checks.{_RESET}")

    # Interactive confirmation
    if sys.stdin.isatty():
        print("About to migrate production database schema to head.")
        response = input("Type 'yes' to proceed: ")
        if response.strip().lower() != "yes":
            print("Aborted.")
            return 1

    # Construct AzureSQLMemory with skip_schema_migration=True to bypass the runtime guard,
    # then explicitly run migration.
    import logging
    import os

    from pyrit.memory import AzureSQLMemory

    prod_conn = os.environ.get(AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING_PROD)
    if not prod_conn:
        _print_error(f"Environment variable '{AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING_PROD}' is not set.")
        return 1

    try:
        # Suppress the constructor's pre-migration schema-mismatch warning: it's expected
        # here (we're about to migrate) and would be misleading noise.
        azure_logger = logging.getLogger("pyrit.memory.azure_sql_memory")
        previous_level = azure_logger.level
        azure_logger.setLevel(logging.ERROR)
        try:
            memory = AzureSQLMemory(
                connection_string=prod_conn,
                skip_schema_migration=True,
            )
        finally:
            azure_logger.setLevel(previous_level)

        before = _get_db_revision(memory.engine)
        print(f"Database revision before migration: {before or '(none — fresh database)'}")
        print("Running schema migration...")
        memory._run_schema_migration()
        after = _get_db_revision(memory.engine)
        print(f"Database revision after migration:  {after}")
        if before == after:
            print("No revision change (already up to date).")
        _print_success("Production schema migration completed and verified successfully.")
        return 0
    except Exception as e:
        _print_error(f"Migration failed: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
