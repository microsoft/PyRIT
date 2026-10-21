# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Regression tests for the landing page's embedded walkthroughs."""

import re
import shutil
import subprocess
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("scenario", ["play", "hidden_play", "hide", "visible", "standalone", "pagehide", "restore"])
def test_player_coordinates_playback(scenario: str) -> None:
    program = """
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const scenario = process.argv[2];
const events = {};
const windowEvents = {};
const video = {
  paused: false,
  pause() { this.paused = true; },
  addEventListener(name, callback) { events[name] = callback; },
};
const siblingVideo = { paused: false, pause() { this.paused = true; } };
const sibling = { contentDocument: { querySelector: () => siblingVideo } };
const unloaded = { contentDocument: null };
let visible = true;
const tabSet = { querySelectorAll: () => [frame, sibling, unloaded] };
const frame = {
  closest: () => tabSet,
  getClientRects: () => visible ? [{}] : [],
};
let observer;
class MutationObserver {
  constructor(callback) { this.callback = callback; observer = this; }
  observe(target, options) {
    assert.equal(target, tabSet);
    assert.deepEqual(Array.from(options.attributeFilter), ["class", "hidden", "style"]);
    assert.equal(options.subtree, true);
    this.disconnected = false;
  }
  disconnect() { this.disconnected = true; }
}
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), {
  document: { querySelector: () => video },
  window: {
    frameElement: scenario === "standalone" ? null : frame,
    addEventListener(name, callback) { windowEvents[name] = callback; },
  },
  MutationObserver,
});
assert.equal(video.paused, false);
assert.equal(siblingVideo.paused, false);
if (scenario === "standalone") {
  assert.equal(observer, undefined);
} else if (scenario === "pagehide") {
  windowEvents.pagehide();
  assert.equal(observer.disconnected, true);
} else if (scenario === "restore") {
  windowEvents.pagehide();
  windowEvents.pageshow();
  assert.equal(observer.disconnected, false);
  visible = false;
  observer.callback();
  assert.equal(video.paused, true);
} else if (scenario === "play" || scenario === "hidden_play") {
  visible = scenario === "play";
  events.play();
  assert.equal(video.paused, !visible);
  assert.equal(siblingVideo.paused, visible);
} else {
  visible = scenario === "visible";
  observer.callback();
  assert.equal(video.paused, !visible);
  assert.equal(siblingVideo.paused, false);
}
"""
    result = subprocess.run(
        ["node", "-e", program, str(DOC_ROOT / "assets" / "videos" / "player.js"), scenario],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
