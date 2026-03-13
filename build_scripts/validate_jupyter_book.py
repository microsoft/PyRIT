# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Fast validation script for Jupyter Book documentation.

This script performs comprehensive validation of:
1. doc/api.rst - Validates that all module references exist and autosummary members are defined
2. doc/_toc.yml - Validates that all file references exist and detects orphaned doc files

Designed to replace the slow `jb build` command for local pre-commit validation while
maintaining thoroughness. The full `jb build` still runs in CI/pipeline.

Exit codes:
    0: All validations passed
    1: Validation errors found
"""

import os
import re
import sys
from pathlib import Path

import yaml


def parse_api_rst(api_rst_path: Path) -> list[tuple[str, list[str]]]:
    """
    Parse api.rst file to extract module names and their autosummary members.

    Returns:
        List of tuples: (module_name, [member_names])
    """
    with open(api_rst_path, encoding="utf-8") as f:
        content = f.read()

    modules = []
    # Pattern to match autosummary sections
    autosummary_pattern = re.compile(
        r"\.\. autosummary::\s+:nosignatures:\s+:toctree: _autosummary/\s+((?:\s+\w+\s*\n)+)", re.MULTILINE
    )

    # Split content by module sections using :py:mod:`module.name`
    sections = re.split(r":py:mod:`(pyrit\.[^`]+)`", content)

    for i in range(1, len(sections), 2):
        if i + 1 < len(sections):
            module_name = sections[i]
            section_content = sections[i + 1]

            # Extract autosummary members from this section
            members = []
            autosummary_match = autosummary_pattern.search(section_content)
            if autosummary_match:
                members_text = autosummary_match.group(1)
                members = [m.strip() for m in members_text.strip().split("\n") if m.strip()]

            modules.append((module_name, members))

    return modules


def validate_api_rst_modules(modules: list[tuple[str, list[str]]], repo_root: Path) -> list[str]:
    """
    Validate that modules exist and autosummary members are defined.

    Returns:
        List of error messages (empty if all validations passed)
    """
    errors = []

    for module_name, members in modules:
        # Check if module file exists
        # Convert module name to path: pyrit.analytics -> pyrit/analytics/__init__.py
        module_path = module_name.replace(".", os.sep)
        possible_paths = [
            repo_root / f"{module_path}.py",
            repo_root / module_path / "__init__.py",
        ]

        # For pyrit.scenario.* modules, also check in pyrit.scenario.scenarios.*
        # These are virtual modules registered via sys.modules aliasing
        if module_name.startswith("pyrit.scenario.") and module_name != "pyrit.scenario.scenarios":
            # e.g., pyrit.scenario.airt -> pyrit.scenario.scenarios.airt
            scenarios_path = module_name.replace("pyrit.scenario.", "pyrit.scenario.scenarios.", 1)
            scenarios_module_path = scenarios_path.replace(".", os.sep)
            possible_paths.extend(
                [
                    repo_root / f"{scenarios_module_path}.py",
                    repo_root / scenarios_module_path / "__init__.py",
                ]
            )

        module_exists = any(p.exists() for p in possible_paths)

        if not module_exists:
            errors.append(f"Module file not found for '{module_name}': checked {[str(p) for p in possible_paths]}")
            continue

        # Validate members by checking the source file directly (works even without dependencies)
        if members:
            # Find the actual module file
            module_file = None
            for path in possible_paths:
                if path.exists():
                    module_file = path
                    break

            if module_file:
                # Read the source file and check for member definitions
                try:
                    with open(module_file, encoding="utf-8") as f:
                        source_content = f.read()

                    for member in members:
                        # Check for various definition patterns:
                        # - def member(...
                        # - class member(...
                        # - member = ...
                        # Also check __all__ if present
                        patterns = [
                            rf"^def {re.escape(member)}\s*\(",
                            rf"^class {re.escape(member)}\s*[\(:]",
                            rf"^{re.escape(member)}\s*=",
                            rf"^\s+{re.escape(member)}\s*=",  # indented assignments
                            rf'"{re.escape(member)}"',  # in __all__ or strings
                            rf"'{re.escape(member)}'",
                        ]

                        found = any(re.search(pattern, source_content, re.MULTILINE) for pattern in patterns)

                        if not found:
                            errors.append(
                                f"Member '{member}' not found in module '{module_name}' (searched {module_file})"
                            )

                except Exception as e:
                    errors.append(f"Error reading source file for '{module_name}': {e}")

    return errors


def parse_toc_yml(toc_path: Path) -> set[str]:
    """
    Parse _toc.yml file to extract all file references.

    Returns:
        Set of file paths (relative to doc/ directory, without extensions)
    """
    with open(toc_path, encoding="utf-8") as f:
        toc_data = yaml.safe_load(f)

    files = set()

    def extract_files(node):
        if isinstance(node, dict):
            if "file" in node:
                files.add(node["file"])
            if "root" in node:
                files.add(node["root"])
            for value in node.values():
                extract_files(value)
        elif isinstance(node, list):
            for item in node:
                extract_files(item)

    extract_files(toc_data)
    return files


def validate_toc_yml_files(toc_files: set[str], doc_root: Path) -> list[str]:
    """
    Validate that all files referenced in _toc.yml exist.

    Returns:
        List of error messages (empty if all validations passed)
    """
    errors = []

    for file_ref in toc_files:
        # Check if file reference already includes an extension
        if file_ref.endswith((".rst", ".md", ".ipynb", ".py")):
            if not (doc_root / file_ref).exists():
                errors.append(f"File referenced in _toc.yml not found: '{file_ref}'")
            continue

        # Files in _toc.yml are usually listed without extensions
        # Check for .md, .ipynb, or .py files
        possible_extensions = [".md", ".ipynb", ".py"]
        file_exists = any((doc_root / f"{file_ref}{ext}").exists() for ext in possible_extensions)

        if not file_exists:
            errors.append(
                f"File referenced in _toc.yml not found: '{file_ref}' (checked extensions: {possible_extensions})"
            )

    return errors


def find_orphaned_doc_files(toc_files: set[str], doc_root: Path) -> list[str]:
    """
    Find documentation files that exist but are not referenced in _toc.yml.

    Returns:
        List of orphaned file paths
    """
    # Directories to skip
    skip_dirs = {
        "_build",
        "_autosummary",
        "_static",
        "_templates",
        "generate_docs",
        ".ipynb_checkpoints",
        "__pycache__",
        "playwright_demo",
    }

    # Files to skip (these are special/configuration files)
    skip_files = {"_config.yml", "conf.py", "references.bib", "roakey.png", ".gitignore", "requirements.txt"}

    # Normalize toc_files to handle both with and without extensions
    normalized_toc_files = set()
    for file_ref in toc_files:
        # Add the reference as-is
        normalized_toc_files.add(file_ref.replace(os.sep, "/"))
        # Also add without extension if it has one
        if file_ref.endswith((".rst", ".md", ".ipynb", ".py")):
            normalized_toc_files.add(Path(file_ref).with_suffix("").as_posix())

    orphaned = []

    for file_path in doc_root.rglob("*"):
        # Skip directories
        if file_path.is_dir():
            continue

        # Skip if in excluded directories
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue

        # Skip if in excluded files
        if file_path.name in skip_files:
            continue

        # Only check documentation files
        if file_path.suffix not in [".md", ".ipynb", ".py", ".rst"]:
            continue

        # Get relative path without extension
        rel_path = file_path.relative_to(doc_root)
        rel_path_no_ext = str(rel_path.with_suffix("")).replace(os.sep, "/")
        rel_path_with_ext = str(rel_path).replace(os.sep, "/")

        # Check if this file is referenced in _toc.yml (with or without extension)
        if rel_path_no_ext in normalized_toc_files or rel_path_with_ext in normalized_toc_files:
            continue

        # Check if companion .py file for .ipynb (notebooks often have both)
        if file_path.suffix == ".py":
            notebook_version = file_path.with_suffix(".ipynb")
            if notebook_version.exists():
                # .py file is companion to .ipynb, not orphaned
                continue

        orphaned.append(str(rel_path))

    return orphaned


def main():
    # Determine repository root (parent of build_scripts)
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    doc_root = repo_root / "doc"
    api_rst = doc_root / "api.rst"
    toc_yml = doc_root / "_toc.yml"

    # Add repo root to sys.path so we can import pyrit modules
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # Ensure required files exist
    if not api_rst.exists():
        print(f"ERROR: api.rst not found at {api_rst}", file=sys.stderr)
        return 1

    if not toc_yml.exists():
        print(f"ERROR: _toc.yml not found at {toc_yml}", file=sys.stderr)
        return 1

    all_errors = []

    # Validate api.rst
    print("Validating api.rst module references...")
    modules = parse_api_rst(api_rst)
    api_errors = validate_api_rst_modules(modules, repo_root)
    if api_errors:
        all_errors.extend([f"[api.rst] {err}" for err in api_errors])
    else:
        print(f"[OK] Validated {len(modules)} modules in api.rst")

    # Validate _toc.yml
    print("Validating _toc.yml file references...")
    toc_files = parse_toc_yml(toc_yml)
    toc_errors = validate_toc_yml_files(toc_files, doc_root)
    if toc_errors:
        all_errors.extend([f"[_toc.yml] {err}" for err in toc_errors])
    else:
        print(f"[OK] Validated {len(toc_files)} file references in _toc.yml")

    # Check for orphaned files
    print("Checking for orphaned documentation files...")
    orphaned = find_orphaned_doc_files(toc_files, doc_root)
    if orphaned:
        all_errors.extend([f"[orphaned] File exists but not in _toc.yml: {f}" for f in orphaned])
    else:
        print("[OK] No orphaned documentation files found")

    # Report results
    if all_errors:
        print("\n" + "=" * 80, file=sys.stderr)
        print("VALIDATION ERRORS FOUND:", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        for error in all_errors:
            print(f"  • {error}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        return 1
    print("\n[OK] All Jupyter Book validations passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
