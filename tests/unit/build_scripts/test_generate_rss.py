# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _load_generate_rss_functions() -> tuple[Callable[[str], str], Callable[[Path], tuple[str, str]]]:
    """Load generate_rss helpers without executing the script body."""
    script_path = Path(__file__).resolve().parents[3] / "build_scripts" / "generate_rss.py"
    source = script_path.read_text(encoding="utf-8")
    parsed_module = ast.parse(source, filename=str(script_path))
    target_functions = {"extract_date_from_filename", "parse_blog_markdown"}
    selected_nodes: list[ast.stmt] = []
    for node in parsed_module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            selected_nodes.append(node)
            continue
        if isinstance(node, ast.FunctionDef) and node.name in target_functions:
            selected_nodes.append(node)
    safe_module = ast.Module(body=selected_nodes, type_ignores=[])
    namespace: dict[str, Any] = {}
    exec(compile(safe_module, filename=str(script_path), mode="exec"), namespace)
    return namespace["extract_date_from_filename"], namespace["parse_blog_markdown"]


extract_date_from_filename, parse_blog_markdown = _load_generate_rss_functions()


class TestExtractDateFromFilename:
    def test_standard_date(self) -> None:
        assert extract_date_from_filename("2024_12_3.md") == "2024-12-03"

    def test_double_digit_day_and_month(self) -> None:
        assert extract_date_from_filename("2023_11_25.md") == "2023-11-25"

    def test_single_digit_month(self) -> None:
        assert extract_date_from_filename("2024_1_15.md") == "2024-01-15"

    def test_returns_empty_for_invalid_filename(self) -> None:
        assert extract_date_from_filename("no_date_here.md") == ""

    def test_returns_empty_for_non_numeric(self) -> None:
        assert extract_date_from_filename("intro.md") == ""


class TestParseBlogMarkdown:
    def test_extracts_title(self, tmp_path: Path) -> None:
        f = tmp_path / "2024_01_01.md"
        f.write_text("# My Blog Title\n\nSome description here.")
        title, _ = parse_blog_markdown(f)
        assert title == "My Blog Title"

    def test_extracts_description(self, tmp_path: Path) -> None:
        f = tmp_path / "2024_01_01.md"
        f.write_text("# Title\n\nThis is the description paragraph.")
        _, desc = parse_blog_markdown(f)
        assert "This is the description paragraph." in desc

    def test_skips_small_tag_in_description(self, tmp_path: Path) -> None:
        f = tmp_path / "2024_01_01.md"
        f.write_text("# Title\n\n<small>date info</small>\n\nReal description here.")
        _, desc = parse_blog_markdown(f)
        assert "small" not in desc
        assert "Real description here." in desc

    def test_empty_title_when_no_heading(self, tmp_path: Path) -> None:
        f = tmp_path / "2024_01_01.md"
        f.write_text("No heading here.\n\nJust paragraphs.")
        title, _ = parse_blog_markdown(f)
        assert title == ""

    def test_multiline_description_joined(self, tmp_path: Path) -> None:
        f = tmp_path / "2024_01_01.md"
        f.write_text("# Title\n\nLine one.\nLine two.")
        _, desc = parse_blog_markdown(f)
        assert "Line one." in desc
        assert "Line two." in desc
