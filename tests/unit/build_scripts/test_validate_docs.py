# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from build_scripts.validate_docs import find_orphaned_files, parse_toc_files, validate_toc_files


def test_parse_toc_files_extracts_single_file() -> None:
    toc = [{"file": "intro"}]
    result = parse_toc_files(toc)
    assert "intro" in result


def test_parse_toc_files_extracts_nested_children() -> None:
    toc = [{"file": "parent", "children": [{"file": "child"}]}]
    result = parse_toc_files(toc)
    assert "parent" in result
    assert "child" in result


def test_parse_toc_files_ignores_entries_without_file() -> None:
    toc = [{"title": "No file here"}]
    result = parse_toc_files(toc)
    assert len(result) == 0


def test_parse_toc_files_empty_toc() -> None:
    result = parse_toc_files([])
    assert result == set()


def test_parse_toc_files_normalizes_backslashes() -> None:
    toc = [{"file": "setup\\install"}]
    result = parse_toc_files(toc)
    assert "setup/install" in result


def test_validate_toc_files_no_errors_when_files_exist(tmp_path: Path) -> None:
    (tmp_path / "intro.md").write_text("# Intro")
    errors = validate_toc_files({"intro.md"}, tmp_path)
    assert errors == []


def test_validate_toc_files_error_when_file_missing(tmp_path: Path) -> None:
    errors = validate_toc_files({"missing.md"}, tmp_path)
    assert len(errors) == 1
    assert "missing.md" in errors[0]


def test_validate_toc_files_skips_api_generated_files(tmp_path: Path) -> None:
    # When doc/api/ does not exist (e.g. pre-commit before any docs build),
    # api/* TOC entries are skipped because they will be generated later.
    errors = validate_toc_files({"api/some_module"}, tmp_path)
    assert errors == []


def test_validate_toc_files_validates_api_entries_when_api_dir_exists(tmp_path: Path) -> None:
    # Once doc/api/ exists (post gen_api_md.py), api/* TOC entries are
    # validated like any other file so stale entries are caught.
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "pyrit_existing.md").write_text("# existing")

    errors = validate_toc_files({"api/pyrit_existing.md", "api/pyrit_missing.md"}, tmp_path)

    assert len(errors) == 1
    assert "pyrit_missing.md" in errors[0]


def test_validate_toc_files_multiple_missing_files(tmp_path: Path) -> None:
    errors = validate_toc_files({"a.md", "b.md"}, tmp_path)
    assert len(errors) == 2


def test_find_orphaned_files_no_orphans_when_all_referenced(tmp_path: Path) -> None:
    (tmp_path / "intro.md").write_text("# Intro")
    orphaned = find_orphaned_files({"intro.md"}, tmp_path)
    assert orphaned == []


def test_find_orphaned_files_detects_orphaned_markdown(tmp_path: Path) -> None:
    (tmp_path / "orphan.md").write_text("# Orphan")
    orphaned = find_orphaned_files(set(), tmp_path)
    assert any("orphan.md" in o for o in orphaned)


def test_find_orphaned_files_skips_build_directory(tmp_path: Path) -> None:
    build_dir = tmp_path / "_build"
    build_dir.mkdir()
    (build_dir / "generated.md").write_text("# Generated")
    orphaned = find_orphaned_files(set(), tmp_path)
    assert not any("_build" in o for o in orphaned)


def test_find_orphaned_files_skips_myst_yml(tmp_path: Path) -> None:
    (tmp_path / "myst.yml").write_text("project:")
    orphaned = find_orphaned_files(set(), tmp_path)
    assert not any("myst.yml" in o for o in orphaned)


def test_find_orphaned_files_skips_py_companion_files(tmp_path: Path) -> None:
    (tmp_path / "notebook.ipynb").write_text("{}")
    (tmp_path / "notebook.py").write_text("# companion")
    orphaned = find_orphaned_files(set(), tmp_path)
    assert not any("notebook.py" in o for o in orphaned)


def test_find_orphaned_files_detects_orphaned_api_pages_when_dir_exists(tmp_path: Path) -> None:
    # Post-build: doc/api/ exists. Any generated page that isn't in the TOC
    # is flagged so stale or unlisted modules surface immediately instead of
    # showing up later as a Read the Docs build failure.
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "pyrit_listed.md").write_text("# listed")
    (api_dir / "pyrit_orphan.md").write_text("# orphan")

    orphaned = find_orphaned_files({"api/pyrit_listed.md"}, tmp_path)

    assert any("pyrit_orphan.md" in o for o in orphaned)
    assert not any("pyrit_listed.md" in o for o in orphaned)
