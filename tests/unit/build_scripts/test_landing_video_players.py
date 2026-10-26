# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Regression tests for the landing page's embedded walkthroughs."""

import re
from pathlib import Path

import pytest
import yaml

DOC_ROOT = Path(__file__).resolve().parents[3] / "doc"


@pytest.mark.parametrize("name", ["scanner", "copyrit"])
def test_player_requires_manual_playback(name: str) -> None:
    html = (DOC_ROOT / "assets" / "videos" / f"{name}-player.html").read_text(encoding="utf-8")
    video = re.search(r"<video\b([^>]*)>", html)
    assert video is not None
    attributes = dict(re.findall(r'([\w-]+)(?:="([^"]*)")?', video.group(1)))
    assert "controls" in attributes
    assert "playsinline" in attributes
    assert attributes["preload"] == "none"
    assert not {"autoplay", "loop", "muted"} & attributes.keys()
    assert attributes["src"] == f"{name}-walkthrough.mp4"
    assert attributes["poster"] == f"../{name}-demo.png"
    assert "<!-- pyrit-no-version-picker -->" in html


def test_player_assets_are_published() -> None:
    config = yaml.safe_load((DOC_ROOT / "myst.yml").read_text(encoding="utf-8"))
    assert {"assets/videos", "scanner-demo.png", "copyrit-demo.png"} <= set(config["project"]["static_files"])
    for name in ["scanner", "copyrit"]:
        assert (DOC_ROOT / "assets" / "videos" / f"{name}-walkthrough.mp4").is_file()
        assert (DOC_ROOT / f"{name}-demo.png").is_file()
        assert f"{{iframe}} videos/{name}-player.html" in (DOC_ROOT / "index.md").read_text(encoding="utf-8")
    for asset in ["player.js", "player.css"]:
        assert (DOC_ROOT / "assets" / "videos" / asset).is_file()
