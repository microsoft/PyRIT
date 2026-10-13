// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.
(function () {
  "use strict";

  const video = document.querySelector("video");
  const frame = window.frameElement;
  const tabSet = frame?.closest(".myst-tab-set");
  if (!tabSet) return;

  video.addEventListener("play", () => {
    if (frame.getClientRects().length === 0) {
      video.pause();
      return;
    }
    for (const sibling of tabSet.querySelectorAll(".landing-demo-video iframe")) {
      if (sibling !== frame) {
        sibling.contentDocument?.querySelector("video")?.pause();
      }
    }
  });

  const observer = new MutationObserver(() => {
    if (frame.getClientRects().length === 0) video.pause();
  });
  function observeTabSet() {
    observer.observe(tabSet, {
      attributes: true,
      attributeFilter: ["class", "hidden", "style"],
      subtree: true,
    });
  }
  observeTabSet();
  window.addEventListener("pagehide", () => observer.disconnect());
  window.addEventListener("pageshow", observeTabSet);
})();
