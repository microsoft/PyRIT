# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path

import pytest

from build_scripts.validate_docs import find_orphaned_files, parse_toc_files, validate_toc_files


class TestParseTocFiles:
    def test_extracts_single_file(self) -> None:
        toc = [{"file": "intro"}]
        result = parse_toc_files(toc)
        assert "intro" in result

    def test_extracts_nested_children(self) -> None:
        toc = [{"file": "parent", "children": [{"file": "child"}]}]
        result = parse_toc_files(toc)
        assert "parent" in result
        assert "child" in result

    def test_ignores_entries_without_file(self) -> None:
        toc = [{"title": "No file here"}]
        result = parse_toc_files(toc)
        assert len(result) == 0

    def test_empty_toc(self) -> None:
        result = parse_toc_files([])
        assert result == set()

    def test_normalizes_backslashes(self) -> None:
        toc = [{"file": "setup\\install"}]
        result = parse_toc_files(toc)
        assert "setup/install" in result


class TestValidateTocFiles:
    def test_no_errors_when_files_exist(self, tmp_path: Path) -> None:
        (tmp_path / "intro.md").write_text("# Intro")
        errors = validate_toc_files({"intro.md"}, tmp_path)
        assert errors == []

    def test_error_when_file_missing(self, tmp_path: Path) -> None:
        errors = validate_toc_files({"missing.md"}, tmp_path)
        assert len(errors) == 1
        assert "missing.md" in errors[0]

    def test_skips_api_generated_files(self, tmp_path: Path) -> None:
        errors = validate_toc_files({"api/some_module"}, tmp_path)
        assert errors == []

    def test_multiple_missing_files(self, tmp_path: Path) -> None:
        errors = validate_toc_files({"a.md", "b.md"}, tmp_path)
        assert len(errors) == 2


class TestFindOrphanedFiles:
    def test_no_orphans_when_all_referenced(self, tmp_path: Path) -> None:
        (tmp_path / "intro.md").write_text("# Intro")
        orphaned = find_orphaned_files({"intro.md"}, tmp_path)
        assert orphaned == []

    def test_detects_orphaned_markdown(self, tmp_path: Path) -> None:
        (tmp_path / "orphan.md").write_text("# Orphan")
        orphaned = find_orphaned_files(set(), tmp_path)
        assert any("orphan.md" in o for o in orphaned)

    def test_skips_build_directory(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "_build"
        build_dir.mkdir()
        (build_dir / "generated.md").write_text("# Generated")
        orphaned = find_orphaned_files(set(), tmp_path)
        assert not any("_build" in o for o in orphaned)

    def test_skips_myst_yml(self, tmp_path: Path) -> None:
        (tmp_path / "myst.yml").write_text("project:")
        orphaned = find_orphaned_files(set(), tmp_path)
        assert not any("myst.yml" in o for o in orphaned)

    def test_skips_py_companion_files(self, tmp_path: Path) -> None:
        (tmp_path / "notebook.ipynb").write_text("{}")
        (tmp_path / "notebook.py").write_text("# companion")
        orphaned = find_orphaned_files(set(), tmp_path)
        assert not any("notebook.py" in o for o in orphaned)
