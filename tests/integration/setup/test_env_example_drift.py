# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import re
import subprocess
from unittest import mock

from dotenv import dotenv_values

_ENV_EXAMPLE_PATH_ENV = "PYRIT_ENV_EXAMPLE_PATH"
_REPOSITORY_ROOT_ENV = "PYRIT_REPOSITORY_ROOT"
_ENVIRONMENT_NAME_PATTERN = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*(?![A-Za-z0-9_])")
_DOTENV_ASSIGNMENT_NAME_PATTERN = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=", re.MULTILINE)
_DOTENV_COMPLETE_REFERENCE_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_BLANK_LINE_BETWEEN_COMMENTS_PATTERN = re.compile(r"^#[^\r\n]*\r?\n[ \t]*\r?\n(?=#)", re.MULTILINE)


def _get_repository_root() -> pathlib.Path:
    configured_root = os.getenv(_REPOSITORY_ROOT_ENV)
    if configured_root:
        root = pathlib.Path(configured_root)
        if root.is_dir():
            return root
        raise AssertionError(f"{_REPOSITORY_ROOT_ENV} does not identify a directory: {root}")

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError("Could not locate the repository root with git.")
    return pathlib.Path(result.stdout.strip())


def _get_env_example_path(*, repository_root: pathlib.Path) -> pathlib.Path:
    configured_path = os.getenv(_ENV_EXAMPLE_PATH_ENV)
    path = pathlib.Path(configured_path) if configured_path else repository_root / ".env_example"
    if not path.is_file():
        raise AssertionError(f"Could not locate .env_example at {path}.")
    return path


def _grep_repository_for_environment_names(*, environment_names: set[str], repository_root: pathlib.Path) -> set[str]:
    grep_pattern = "(" + "|".join(sorted(environment_names)) + ")"
    result = subprocess.run(
        ["git", "grep", "-I", "-h", "-E", grep_pattern, "--", ".", ":(exclude).env_example"],
        capture_output=True,
        check=False,
        cwd=repository_root,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise AssertionError(f"Could not search tracked repository files with git: {result.stderr.strip()}")
    return environment_names & set(_ENVIRONMENT_NAME_PATTERN.findall(result.stdout))


def _find_referenced_environment_names(
    *,
    environment_names: set[str],
    repository_root: pathlib.Path,
    env_example_path: pathlib.Path,
) -> set[str]:
    example_contents = env_example_path.read_text(encoding="utf-8")
    example_without_assignment_names = _DOTENV_ASSIGNMENT_NAME_PATTERN.sub("=", example_contents)
    referenced_names = environment_names & set(_ENVIRONMENT_NAME_PATTERN.findall(example_without_assignment_names))
    referenced_names.update(
        _grep_repository_for_environment_names(
            environment_names=environment_names,
            repository_root=repository_root,
        )
    )
    return referenced_names


def test_env_example_names_are_referenced_in_repository() -> None:
    """Catch example entries with no weak textual reference in tracked repository files."""
    repository_root = _get_repository_root()
    env_example_path = _get_env_example_path(repository_root=repository_root)
    environment_names = set(dotenv_values(dotenv_path=env_example_path, interpolate=False))
    assert environment_names, ".env_example contains no dotenv assignments."

    referenced_names = _find_referenced_environment_names(
        environment_names=environment_names,
        repository_root=repository_root,
        env_example_path=env_example_path,
    )
    unreferenced_names = environment_names - referenced_names
    assert not unreferenced_names, ".env_example contains names with no tracked repository reference: " + ", ".join(
        sorted(unreferenced_names)
    )


def test_env_example_url_values_are_not_wrapped_in_angle_brackets() -> None:
    """Ensure URL placeholder styling does not become part of parsed dotenv values."""
    repository_root = _get_repository_root()
    env_example_path = _get_env_example_path(repository_root=repository_root)
    values = dotenv_values(dotenv_path=env_example_path, interpolate=False)

    wrapped_names = {name for name, value in values.items() if value and ("<" in value or ">" in value)}
    assert not wrapped_names, ".env_example contains values wrapped in angle brackets: " + ", ".join(
        sorted(wrapped_names)
    )


def test_env_example_comment_blocks_do_not_contain_blank_lines() -> None:
    """Keep consecutive comment lines together so the example remains compact."""
    repository_root = _get_repository_root()
    env_example_path = _get_env_example_path(repository_root=repository_root)
    contents = env_example_path.read_text(encoding="utf-8")

    assert not _BLANK_LINE_BETWEEN_COMMENTS_PATTERN.search(contents), (
        ".env_example contains a blank line between consecutive comment lines."
    )


def test_env_example_aliases_resolve_in_assignment_order() -> None:
    """Ensure complete-value aliases resolve to their sources without ambient environment values."""
    repository_root = _get_repository_root()
    env_example_path = _get_env_example_path(repository_root=repository_root)
    raw_values = dotenv_values(dotenv_path=env_example_path, interpolate=False)
    aliases = {
        name: match.group(1)
        for name, value in raw_values.items()
        if value and (match := _DOTENV_COMPLETE_REFERENCE_PATTERN.fullmatch(value))
    }
    assert aliases, ".env_example contains no complete-value aliases."

    with mock.patch.dict(os.environ, {}, clear=True):
        resolved_values = dotenv_values(dotenv_path=env_example_path, interpolate=True)

    unresolved_names = {name for name in aliases if not resolved_values.get(name)}
    assert not unresolved_names, ".env_example contains aliases that resolve to empty values: " + ", ".join(
        sorted(unresolved_names)
    )

    mismatched_names = {
        name for name, source_name in aliases.items() if resolved_values[name] != resolved_values.get(source_name)
    }
    assert not mismatched_names, ".env_example contains aliases that differ from their sources: " + ", ".join(
        sorted(mismatched_names)
    )
