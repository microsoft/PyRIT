// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const playerPath = path.resolve(__dirname, "..", "..", "..", "doc", "assets", "videos", "player.js");
const source = fs.readFileSync(playerPath, "utf8");
const setup = `
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
const document = { querySelector: () => video };
const window = {
  frameElement: scenario === "standalone" ? null : frame,
  addEventListener(name, callback) { windowEvents[name] = callback; },
};
`;
const checks = `
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
`;
const script = new vm.Script(setup + source + checks, { filename: playerPath });

for (const scenario of ["play", "hidden_play", "hide", "visible", "standalone", "pagehide", "restore"]) {
  test(scenario, () => {
    // Include event callbacks in the execution limit; Node startup has a separate deadline.
    script.runInNewContext({ assert, scenario }, { timeout: 1000 });
  });
}
