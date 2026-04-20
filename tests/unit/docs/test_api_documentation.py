# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests to ensure API documentation completeness.

These tests verify that all public classes and functions exported by PyRIT modules
are documented in doc/api.rst to prevent accidentally missing items in the documentation.
"""

import importlib
import re
from pathlib import Path
from typing import Set

import pytest


def get_api_rst_path() -> Path:
    """Get the path to the api.rst file."""
    # Start from this test file and navigate to doc/api.rst
    # tests/unit/docs -> tests/unit -> tests -> workspace_root
    test_dir = Path(__file__).parent
    workspace_root = test_dir.parent.parent.parent
    return workspace_root / "doc" / "api.rst"


def get_documented_items_from_rst() -> dict[str, Set[str]]:
    """
    Parse api.rst and extract all documented items by module.

    Returns:
        Dictionary mapping module paths to sets of documented class/function names
    """
    api_rst = get_api_rst_path()
    content = api_rst.read_text()

    documented: dict[str, Set[str]] = {}
    current_module = None

    # Find module definitions like :py:mod:`pyrit.prompt_converter`
    module_pattern = r":py:mod:`([^`]+)`"
    # Find documented items in autosummary sections
    item_pattern = r"^\s+(\w+)$"

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if this is a module definition
        module_match = re.match(module_pattern, line)
        if module_match:
            current_module = module_match.group(1)
            documented[current_module] = set()

            # Look ahead for the autosummary section
            j = i + 1
            in_autosummary = False
            while j < len(lines) and j < i + 200:  # Look ahead up to 200 lines for large sections
                if ".. autosummary::" in lines[j]:
                    in_autosummary = True
                elif in_autosummary:
                    item_match = re.match(item_pattern, lines[j])
                    if item_match:
                        documented[current_module].add(item_match.group(1))
                    elif lines[j].strip() and not lines[j].strip().startswith(":"):
                        # End of autosummary section - but keep going if it's just continuing the list
                        if not lines[j].strip().startswith("..") and not lines[j].startswith("    "):
                            break
                j += 1

        i += 1

    return documented


def get_module_exports(module_path: str) -> Set[str]:
    """
    Get all exported items from a module's __all__ list.

    Args:
        module_path: Full module path like 'pyrit.prompt_converter'

    Returns:
        Set of exported class/function names, or empty set if module doesn't exist or has no __all__
    """
    try:
        module = importlib.import_module(module_path)
        if hasattr(module, "__all__"):
            return set(module.__all__)
        return set()
    except (ImportError, AttributeError):
        return set()


# Define modules to check and their exclusions
MODULES_TO_CHECK = {
    "pyrit.prompt_converter": {
        "exclude": {"AllWordsSelectionStrategy"},  # Internal helper, not documented
    },
    "pyrit.executor.promptgen.fuzzer": {
        "exclude": {"_PromptNode"},  # Exclude private/internal classes
    },
    # Additional modules can be added here as their documentation is completed
    "pyrit.score": {
        "exclude": set(),
    },
    "pyrit.prompt_target": {
        "exclude": set(),
    },
    "pyrit.memory": {
        "exclude": set(),
    },
    "pyrit.models": {
        "exclude": {"AttackResultT", "Seed", "StrategyResultT"},
    },
    "pyrit.executor.attack": {
        "exclude": set(),
    },
    "pyrit.identifiers": {
        "exclude": {"LegacyIdentifiable"},
    },
}


@pytest.mark.parametrize("module_path,config", MODULES_TO_CHECK.items())
def test_all_exports_documented_in_api_rst(module_path: str, config: dict):
    """
    Test that all items in a module's __all__ are documented in api.rst.

    This test helps prevent accidentally forgetting to add new classes/functions
    to the API documentation.
    """
    # Get what's actually exported by the module
    exported = get_module_exports(module_path)

    if not exported:
        pytest.skip(f"Module {module_path} has no __all__ or couldn't be imported")

    # Get what's documented in api.rst
    documented_by_module = get_documented_items_from_rst()
    documented = documented_by_module.get(module_path, set())

    # Apply exclusions
    excluded = config.get("exclude", set())
    exported_to_check = exported - excluded

    # Find missing items
    missing = exported_to_check - documented

    # Create a helpful error message
    if missing:
        missing_list = sorted(missing)
        msg = f"\n\nModule '{module_path}' has {len(missing)} undocumented exports:\n"
        msg += "\n".join(f"  - {item}" for item in missing_list)
        msg += f"\n\nPlease add these to doc/api.rst in the :py:mod:`{module_path}` section."
        pytest.fail(msg)


def test_api_rst_file_exists():
    """Verify that the api.rst file exists."""
    api_rst = get_api_rst_path()
    assert api_rst.exists(), f"api.rst not found at {api_rst}"


def test_documented_modules_have_all_list():
    """
    Test that all modules we're checking actually have an __all__ list.

    This ensures we're not silently skipping modules that should be tested.
    """
    missing_all = []

    for module_path in MODULES_TO_CHECK.keys():
        try:
            module = importlib.import_module(module_path)
            if not hasattr(module, "__all__"):
                missing_all.append(module_path)
        except ImportError:
            missing_all.append(f"{module_path} (import failed)")

    if missing_all:
        msg = "\n\nThe following modules are configured for testing but don't have __all__:\n"
        msg += "\n".join(f"  - {m}" for m in missing_all)
        msg += "\n\nEither add __all__ to these modules or remove them from MODULES_TO_CHECK."
        pytest.fail(msg)
