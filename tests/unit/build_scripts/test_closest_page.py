# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Behavioral tests for the closest_page.js algorithm.

closest_page.js is the single source of truth for the ``findClosestPage`` and
``commonSegmentPrefix`` functions, concat'd at build time into both:

  * picker.js (via inject_version_picker.py) -- cross-version sibling matching
    in the floating version picker dropdown.
  * 404.html (via compose_docs_dist.py.render_not_found_html) -- auto-redirect
    to the closest sibling within the requesting version.

These tests run the actual JS file in ``node`` so we catch behavioral
regressions in the real code (not a Python re-implementation). If ``node``
is not on PATH, the tests skip rather than fall back to a port.

Skipping (instead of failing) is intentional: CI runners always have node
(GitHub Actions ships it, and PyRIT's frontend/ uses it), so a missing-node
skip in CI would be visible in the test summary. Local devs without node
installed still get the rest of the test suite to run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLOSEST_PAGE_JS = REPO_ROOT / "build_scripts" / "version_picker_assets" / "closest_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run_find_closest_page(pages: list[str], rel_path: str) -> str | None:
    """Load closest_page.js in node and call findClosestPage(pages, rel_path)."""
    js_source = CLOSEST_PAGE_JS.read_text(encoding="utf-8")
    program = (
        js_source
        + "\nconst _pages = "
        + json.dumps(pages)
        + ";\nconst _target = "
        + json.dumps(rel_path)
        + ";\nconst _result = findClosestPage(_pages, _target);"
        + "\nprocess.stdout.write(JSON.stringify(_result === undefined ? null : _result));\n"
    )
    proc = subprocess.run(
        ["node", "-e", program],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _run_common_segment_prefix(page_path: str, target_segs: list[str]) -> int:
    """Load closest_page.js in node and call commonSegmentPrefix(page_path, target_segs)."""
    js_source = CLOSEST_PAGE_JS.read_text(encoding="utf-8")
    program = (
        js_source
        + "\nconst _result = commonSegmentPrefix("
        + json.dumps(page_path)
        + ", "
        + json.dumps(target_segs)
        + ");"
        + "\nprocess.stdout.write(JSON.stringify(_result));\n"
    )
    proc = subprocess.run(
        ["node", "-e", program],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


# --- commonSegmentPrefix ---------------------------------------------------


def test_common_prefix_empty_inputs():
    assert _run_common_segment_prefix("", []) == 0
    assert _run_common_segment_prefix("", ["a"]) == 0
    assert _run_common_segment_prefix("a/b/", []) == 0


def test_common_prefix_full_match():
    assert _run_common_segment_prefix("a/b/c/", ["a", "b", "c"]) == 3


def test_common_prefix_partial_match():
    assert _run_common_segment_prefix("a/b/x/", ["a", "b", "c"]) == 2
    assert _run_common_segment_prefix("a/x/c/", ["a", "b", "c"]) == 1


def test_common_prefix_strips_trailing_slashes():
    assert _run_common_segment_prefix("a/b/c////", ["a", "b", "c"]) == 3


def test_common_prefix_no_match():
    assert _run_common_segment_prefix("x/y/", ["a", "b"]) == 0


# --- findClosestPage: bad inputs ------------------------------------------


def test_find_closest_page_returns_null_for_empty_pages():
    assert _run_find_closest_page([], "anything") is None


def test_find_closest_page_returns_null_for_non_array_pages():
    # Simulate a malformed manifest by passing a non-array via raw JSON.
    js_source = CLOSEST_PAGE_JS.read_text(encoding="utf-8")
    program = (
        js_source + '\nconst _result = findClosestPage({}, "x");' + "\nprocess.stdout.write(JSON.stringify(_result));\n"
    )
    proc = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout) is None


# --- findClosestPage: exact match -----------------------------------------


def test_find_closest_page_exact_match():
    pages = ["", "code/scenarios/scenarios/", "api/index/"]
    assert _run_find_closest_page(pages, "code/scenarios/scenarios/") == "code/scenarios/scenarios/"


def test_find_closest_page_exact_match_without_trailing_slash():
    pages = ["", "code/scenarios/scenarios/"]
    assert _run_find_closest_page(pages, "code/scenarios/scenarios") == "code/scenarios/scenarios/"


def test_find_closest_page_strips_query_and_fragment():
    pages = ["", "code/scenarios/scenarios/"]
    assert _run_find_closest_page(pages, "code/scenarios/scenarios/?foo=bar#anchor") == "code/scenarios/scenarios/"
    assert _run_find_closest_page(pages, "code/scenarios/scenarios?x=1") == "code/scenarios/scenarios/"


def test_find_closest_page_empty_relpath_matches_root():
    pages = ["", "api/index/"]
    assert _run_find_closest_page(pages, "") == ""


# --- findClosestPage: ancestor fallback -----------------------------------


def test_find_closest_page_ancestor_match():
    pages = ["", "code/scenarios/", "api/index/"]
    # missing leaf, but the section index exists -> return the section
    assert _run_find_closest_page(pages, "code/scenarios/missing-page") == "code/scenarios/"


def test_find_closest_page_skips_to_grandparent():
    pages = ["", "code/", "api/index/"]
    # missing intermediate dir, but grandparent index exists
    assert _run_find_closest_page(pages, "code/scenarios/missing-page") == "code/"


# --- findClosestPage: sibling fallback ------------------------------------


def test_find_closest_page_prefers_sibling_over_unrelated_root():
    pages = [
        "",
        "code/scenarios/configuring-scenarios/",
        "code/scenarios/scenarios/",
        "api/index/",
    ]
    # missing exact, no ancestor index -> best sibling within code/scenarios.
    # Two candidates, equal prefix length (2 segments), tiebreak alphabetical:
    # the string "configuring-scenarios" is lexicographically smaller than "scenarios".
    result = _run_find_closest_page(pages, "code/scenarios/scenario-parameters/")
    assert result == "code/scenarios/configuring-scenarios/"


def test_find_closest_page_picks_longest_prefix_sibling():
    pages = [
        "",
        "code/scenarios/configuring-scenarios/",
        "code/scenarios/sub/page/",
        "code/other/",
    ]
    # target shares 3 segments with the sub/page (code/scenarios/sub) and only
    # 2 with configuring-scenarios (code/scenarios). Longer prefix wins.
    result = _run_find_closest_page(pages, "code/scenarios/sub/missing/")
    assert result == "code/scenarios/sub/page/"


def test_find_closest_page_alphabetical_tiebreak_when_prefix_equal():
    pages = [
        "",
        "code/scenarios/zebra/",
        "code/scenarios/apple/",
        "code/scenarios/banana/",
    ]
    # All three share the same 2-segment prefix; alphabetical winner is "apple"
    result = _run_find_closest_page(pages, "code/scenarios/missing/")
    assert result == "code/scenarios/apple/"


def test_find_closest_page_falls_through_to_root():
    pages = ["", "totally/unrelated/page/"]
    # nothing under any ancestor of the target; only root remains
    assert _run_find_closest_page(pages, "code/scenarios/scenarios/") == ""


def test_find_closest_page_returns_root_when_only_root_is_a_page():
    pages = [""]
    # the only page is root; the target has no real fallback under any ancestor
    assert _run_find_closest_page(pages, "any/path/") == ""


# --- findClosestPage: realistic-ish scenarios -----------------------------


def test_find_closest_page_realistic_pyrit_layout():
    """Modeled after a real PyRIT pages.json: deep structure with index pages."""
    pages = [
        "",
        "api/index/",
        "api/pyrit-analytics/",
        "api/pyrit-attacks/",
        "code/framework/",
        "code/scenarios/",
        "code/scenarios/configuring-scenarios/",
        "code/scenarios/scenarios/",
        "deployment/",
        "deployment/run-locally/",
    ]
    # User was on a page that no longer exists -> drop to scenarios index
    assert _run_find_closest_page(pages, "code/scenarios/scenario-parameters/") == "code/scenarios/"

    # User was on a deleted top-level page -> nearest sibling at /api alphabetically
    assert _run_find_closest_page(pages, "api/missing/") == "api/index/"

    # User was on a totally removed section -> root
    assert _run_find_closest_page(pages, "removed-section/page/") == ""
