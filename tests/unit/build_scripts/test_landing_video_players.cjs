// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const playerPath = path.resolve(__dirname, "..", "..", "..", "doc", "assets", "videos", "player.js");
const script = new vm.Script(fs.readFileSync(playerPath, "utf8"), { filename: playerPath });

function createPlayer({ standalone = false } = {}) {
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
  const tabSet = { querySelectorAll: () => [frame, sibling, unloaded] };
  const frame = {
    visible: true,
    closest: () => tabSet,
    getClientRects() { return this.visible ? [{}] : []; },
  };
  const context = vm.createContext({
    events,
    windowEvents,
    document: { querySelector: () => video },
    window: {
      frameElement: standalone ? null : frame,
      addEventListener(name, callback) { windowEvents[name] = callback; },
    },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; context.observer = this; }
      observe(target, options) {
        assert.equal(target, tabSet);
        assert.deepEqual(Array.from(options.attributeFilter), ["class", "hidden", "style"]);
        assert.equal(options.subtree, true);
        this.disconnected = false;
      }
      disconnect() { this.disconnected = true; }
    },
  });
  script.runInContext(context, { timeout: 1000 });
  return {
    video, siblingVideo, frame,
    get observer() { return context.observer; },
    // Event callbacks need the same execution limit as initialization.
    run(code) { vm.runInContext(code, context, { timeout: 1000 }); },
  };
}

test("initialization does not pause videos", () => {
  const player = createPlayer();
  assert.equal(player.video.paused, false);
  assert.equal(player.siblingVideo.paused, false);
});

for (const visible of [true, false]) {
  const state = visible ? "visible" : "hidden";
  test(`play in a ${state} frame pauses only the appropriate video`, () => {
    const player = createPlayer();
    player.frame.visible = visible;
    player.run("events.play()");
    assert.equal(player.video.paused, !visible);
    assert.equal(player.siblingVideo.paused, visible);
  });

  test(`tab mutation in a ${state} frame affects only its own video`, () => {
    const player = createPlayer();
    player.frame.visible = visible;
    player.run("observer.callback()");
    assert.equal(player.video.paused, !visible);
    assert.equal(player.siblingVideo.paused, false);
  });
}

test("standalone player does not observe tabs", () => {
  const player = createPlayer({ standalone: true });
  assert.equal(player.observer, undefined);
  assert.equal(player.video.paused, false);
  assert.equal(player.siblingVideo.paused, false);
});

test("pagehide disconnects the observer", () => {
  const player = createPlayer();
  player.run("windowEvents.pagehide()");
  assert.equal(player.observer.disconnected, true);
});

test("pageshow restores observation after pagehide", () => {
  const player = createPlayer();
  player.run("windowEvents.pagehide(); windowEvents.pageshow()");
  assert.equal(player.observer.disconnected, false);
  player.frame.visible = false;
  player.run("observer.callback()");
  assert.equal(player.video.paused, true);
});
