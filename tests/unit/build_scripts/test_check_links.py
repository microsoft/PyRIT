# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path

import pytest

from build_scripts.check_links import extract_urls, resolve_relative_url, strip_fragment


class TestStripFragment:
    def test_removes_fragment(self) -> None:
        assert strip_fragment("https://example.com/page#section") == "https://example.com/page"

    def test_no_fragment_unchanged(self) -> None:
        assert strip_fragment("https://example.com/page") == "https://example.com/page"

    def test_empty_fragment(self) -> None:
        assert strip_fragment("https://example.com/page#") == "https://example.com/page"

    def test_preserves_query_string(self) -> None:
        result = strip_fragment("https://example.com/page?q=1#section")
        assert "q=1" in result
        assert "section" not in result


class TestResolveRelativeUrl:
    def test_http_url_unchanged(self) -> None:
        url = "https://example.com"
        assert resolve_relative_url("/some/file.md", url) == url

    def test_mailto_unchanged(self) -> None:
        url = "mailto:test@example.com"
        assert resolve_relative_url("/some/file.md", url) == url

    def test_relative_url_resolved(self, tmp_path: Path) -> None:
        base = str(tmp_path / "docs" / "file.md")
        target = str(tmp_path / "docs" / "other.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text("# Other")
        result = resolve_relative_url(base, "other.md")
        assert "other" in result

    def test_relative_url_with_md_extension(self, tmp_path: Path) -> None:
        base = str(tmp_path / "docs" / "file.md")
        target = tmp_path / "docs" / "other.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Other")
        result = resolve_relative_url(base, "other")
        assert result.endswith(".md")


class TestExtractUrls:
    def test_extracts_markdown_links(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("[Click here](https://example.com)")
        urls = extract_urls(str(f))
        assert "https://example.com" in urls

    def test_extracts_href_links(self, tmp_path: Path) -> None:
        f = tmp_path / "test.html"
        f.write_text('<a href="https://example.com">link</a>')
        urls = extract_urls(str(f))
        assert "https://example.com" in urls

    def test_extracts_src_links(self, tmp_path: Path) -> None:
        f = tmp_path / "test.html"
        f.write_text('<img src="https://example.com/image.png">')
        urls = extract_urls(str(f))
        assert "https://example.com/image.png" in urls

    def test_empty_file_returns_no_urls(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("")
        urls = extract_urls(str(f))
        assert urls == []

    def test_strips_fragments_from_extracted_urls(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("[link](https://example.com/page#section)")
        urls = extract_urls(str(f))
        assert "https://example.com/page" in urls
        assert not any("#section" in u for u in urls)
