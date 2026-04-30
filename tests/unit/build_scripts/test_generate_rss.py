# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from build_scripts.generate_rss import extract_date_from_filename, parse_blog_markdown


def test_extract_date_from_filename_standard_date() -> None:
    assert extract_date_from_filename("2024_12_3.md") == "2024-12-03"


def test_extract_date_from_filename_double_digit_day_and_month() -> None:
    assert extract_date_from_filename("2023_11_25.md") == "2023-11-25"


def test_extract_date_from_filename_single_digit_month() -> None:
    assert extract_date_from_filename("2024_1_15.md") == "2024-01-15"


def test_extract_date_from_filename_returns_empty_for_invalid() -> None:
    assert extract_date_from_filename("no_date_here.md") == ""


def test_extract_date_from_filename_returns_empty_for_non_numeric() -> None:
    assert extract_date_from_filename("intro.md") == ""


def test_parse_blog_markdown_extracts_title(tmp_path: Path) -> None:
    f = tmp_path / "2024_01_01.md"
    f.write_text("# My Blog Title\n\nSome description here.")
    title, _ = parse_blog_markdown(f)
    assert title == "My Blog Title"


def test_parse_blog_markdown_extracts_description(tmp_path: Path) -> None:
    f = tmp_path / "2024_01_01.md"
    f.write_text("# Title\n\nThis is the description paragraph.")
    _, desc = parse_blog_markdown(f)
    assert "This is the description paragraph." in desc


def test_parse_blog_markdown_skips_small_tag(tmp_path: Path) -> None:
    f = tmp_path / "2024_01_01.md"
    f.write_text("# Title\n\n<small>date info</small>\n\nReal description here.")
    _, desc = parse_blog_markdown(f)
    assert "small" not in desc
    assert "Real description here." in desc


def test_parse_blog_markdown_empty_title_when_no_heading(tmp_path: Path) -> None:
    f = tmp_path / "2024_01_01.md"
    f.write_text("No heading here.\n\nJust paragraphs.")
    title, _ = parse_blog_markdown(f)
    assert title == ""


def test_parse_blog_markdown_multiline_description_joined(tmp_path: Path) -> None:
    f = tmp_path / "2024_01_01.md"
    f.write_text("# Title\n\nLine one.\nLine two.")
    _, desc = parse_blog_markdown(f)
    assert "Line one." in desc
    assert "Line two." in desc
