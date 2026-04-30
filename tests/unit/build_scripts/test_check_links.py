# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from build_scripts.check_links import extract_urls, resolve_relative_url, strip_fragment


def test_strip_fragment_removes_fragment() -> None:
    assert strip_fragment("https://example.com/page#section") == "https://example.com/page"


def test_strip_fragment_no_fragment_unchanged() -> None:
    assert strip_fragment("https://example.com/page") == "https://example.com/page"


def test_strip_fragment_empty_fragment() -> None:
    assert strip_fragment("https://example.com/page#") == "https://example.com/page"


def test_strip_fragment_preserves_query_string() -> None:
    result = strip_fragment("https://example.com/page?q=1#section")
    assert "q=1" in result
    assert "section" not in result


def test_resolve_relative_url_http_url_unchanged() -> None:
    url = "https://example.com"
    assert resolve_relative_url("/some/file.md", url) == url


def test_resolve_relative_url_mailto_unchanged() -> None:
    url = "mailto:test@example.com"
    assert resolve_relative_url("/some/file.md", url) == url


def test_resolve_relative_url_resolved(tmp_path: Path) -> None:
    base = str(tmp_path / "docs" / "file.md")
    target = tmp_path / "docs" / "other.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Other")
    result = resolve_relative_url(base, "other.md")
    assert result == str(target)


def test_resolve_relative_url_with_md_extension(tmp_path: Path) -> None:
    base = str(tmp_path / "docs" / "file.md")
    target = tmp_path / "docs" / "other.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Other")
    result = resolve_relative_url(base, "other")
    assert result.endswith(".md")


def test_extract_urls_extracts_markdown_links(tmp_path: Path) -> None:
    f = tmp_path / "test.md"
    f.write_text("[Click here](https://example.com)")
    urls = extract_urls(str(f))
    assert "https://example.com" in urls


def test_extract_urls_extracts_href_links(tmp_path: Path) -> None:
    f = tmp_path / "test.html"
    f.write_text('<a href="https://example.com">link</a>')
    urls = extract_urls(str(f))
    assert "https://example.com" in urls


def test_extract_urls_extracts_src_links(tmp_path: Path) -> None:
    f = tmp_path / "test.html"
    f.write_text('<img src="https://example.com/image.png">')
    urls = extract_urls(str(f))
    assert "https://example.com/image.png" in urls


def test_extract_urls_empty_file_returns_no_urls(tmp_path: Path) -> None:
    f = tmp_path / "empty.md"
    f.write_text("")
    urls = extract_urls(str(f))
    assert urls == []


def test_extract_urls_strips_fragments(tmp_path: Path) -> None:
    f = tmp_path / "test.md"
    f.write_text("[link](https://example.com/page#section)")
    urls = extract_urls(str(f))
    assert "https://example.com/page" in urls
    assert not any("#section" in u for u in urls)
