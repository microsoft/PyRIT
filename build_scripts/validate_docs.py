# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Fast validation script for Jupyter Book 2 documentation.

Validates that all file references in doc/myst.yml exist and detects
orphaned documentation files. Designed to run quickly in pre-commit
instead of a full `jupyter-book build`.

Exit codes:
    0: All validations passed
    1: Validation errors found
"""

import sys
from pathlib import Path

import yaml


def parse_toc_files(toc_entries: list, files: set | None = None) -> set[str]:
    """Recursively extract all file references from myst.yml toc."""
    if files is None:
        files = set()
    for entry in toc_entries:
        if isinstance(entry, dict):
            if "file" in entry:
                files.add(entry["file"].replace("\\", "/"))
            if "children" in entry:
                parse_toc_files(entry["children"], files)
    return files


def validate_toc_files(toc_files: set[str], doc_root: Path) -> list[str]:
    """Check that all files referenced in the TOC exist."""
    # Directories with auto-generated content (gitignored, created during build)
    generated_dirs = {"api/", "api\\"}

    errors = []
    for file_ref in toc_files:
        # Skip files in auto-generated directories
        if any(file_ref.startswith(d) for d in generated_dirs):
            continue
        file_path = doc_root / file_ref
        if not file_path.exists():
            errors.append(f"File referenced in myst.yml TOC not found: '{file_ref}'")
    return errors


def find_orphaned_files(toc_files: set[str], doc_root: Path) -> list[str]:
    """Find documentation files not referenced in the TOC."""
    skip_dirs = {
        "_build",
        "_api",
        "api",
        "css",
        ".ipynb_checkpoints",
        "__pycache__",
        "playwright_demo",
        "generate_docs",
    }
    skip_files = {
        "myst.yml",
        "roakey.png",
        "banner.png",
        ".gitignore",
        "references.bib",
        "requirements.txt",
    }

    # Normalize TOC references (strip extensions for comparison)
    toc_stems = set()
    for f in toc_files:
        p = Path(f)
        toc_stems.add(p.with_suffix("").as_posix())
        toc_stems.add(p.as_posix())

    orphaned = []
    for file_path in doc_root.rglob("*"):
        if file_path.is_dir():
            continue
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        if file_path.name in skip_files:
            continue
        if file_path.suffix not in [".md", ".ipynb", ".py", ".rst"]:
            continue
        # .py companion files for .ipynb are not orphaned
        if file_path.suffix == ".py":
            notebook_version = file_path.with_suffix(".ipynb")
            if notebook_version.exists():
                continue

        rel = file_path.relative_to(doc_root)
        rel_posix = rel.as_posix()
        rel_stem = rel.with_suffix("").as_posix()

        if rel_posix not in toc_stems and rel_stem not in toc_stems:
            orphaned.append(str(rel))

    return orphaned


def main() -> int:
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    doc_root = repo_root / "doc"
    myst_yml = doc_root / "myst.yml"

    if not myst_yml.exists():
        print(f"ERROR: myst.yml not found at {myst_yml}", file=sys.stderr)
        return 1

    with open(myst_yml, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    all_errors = []

    # Validate TOC file references
    toc = config.get("project", {}).get("toc", [])
    print("Validating myst.yml TOC file references...")
    toc_files = parse_toc_files(toc)
    toc_errors = validate_toc_files(toc_files, doc_root)
    if toc_errors:
        all_errors.extend(f"[myst.yml] {err}" for err in toc_errors)
    else:
        print(f"[OK] Validated {len(toc_files)} file references in myst.yml")

    # Check for orphaned files
    print("Checking for orphaned documentation files...")
    orphaned = find_orphaned_files(toc_files, doc_root)
    if orphaned:
        all_errors.extend(f"[orphaned] File exists but not in myst.yml: {f}" for f in orphaned)
    else:
        print("[OK] No orphaned documentation files found")

    if all_errors:
        print("\n" + "=" * 80, file=sys.stderr)
        print("VALIDATION ERRORS FOUND:", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        for error in all_errors:
            print(f"  • {error}", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        return 1

    print("\n[OK] All documentation validations passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
