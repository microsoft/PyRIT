// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const sourcePath = path.resolve(__dirname, "..", "..", "..", "build_scripts", "version_picker_assets", "closest_page.js");
const source = fs.readFileSync(sourcePath, "utf8");

const prefixCases = [
  ["empty inputs", "", [], 0],
  ["empty page", "", ["a"], 0],
  ["empty target", "a/b/", [], 0],
  ["full match", "a/b/c/", ["a", "b", "c"], 3],
  ["partial match, two segments", "a/b/x/", ["a", "b", "c"], 2],
  ["partial match, one segment", "a/x/c/", ["a", "b", "c"], 1],
  ["strips trailing slashes", "a/b/c////", ["a", "b", "c"], 3],
  ["no match", "x/y/", ["a", "b"], 0],
];

const realisticPages = [
  "", "api/index/", "api/pyrit-analytics/", "api/pyrit-attacks/",
  "code/framework/", "code/scenarios/", "code/scenarios/configuring-scenarios/",
  "code/scenarios/scenarios/", "deployment/", "deployment/run-locally/",
];
const closestCases = [
  ["empty pages", [], "anything", null],
  ["non-array pages", {}, "x", null],
  ["exact match", ["", "code/scenarios/scenarios/", "api/index/"],
    "code/scenarios/scenarios/", "code/scenarios/scenarios/"],
  ["exact match without trailing slash", ["", "code/scenarios/scenarios/"],
    "code/scenarios/scenarios", "code/scenarios/scenarios/"],
  ["strips query and fragment", ["", "code/scenarios/scenarios/"],
    "code/scenarios/scenarios/?foo=bar#anchor", "code/scenarios/scenarios/"],
  ["strips query without trailing slash", ["", "code/scenarios/scenarios/"],
    "code/scenarios/scenarios?x=1", "code/scenarios/scenarios/"],
  ["empty relpath matches root", ["", "api/index/"], "", ""],
  ["ancestor match", ["", "code/scenarios/", "api/index/"],
    "code/scenarios/missing-page", "code/scenarios/"],
  ["skips to grandparent", ["", "code/", "api/index/"], "code/scenarios/missing-page", "code/"],
  ["prefers sibling over unrelated root",
    ["", "code/scenarios/configuring-scenarios/", "code/scenarios/scenarios/", "api/index/"],
    "code/scenarios/scenario-parameters/", "code/scenarios/configuring-scenarios/"],
  ["longest prefix sibling",
    ["", "code/scenarios/configuring-scenarios/", "code/scenarios/sub/page/", "code/other/"],
    "code/scenarios/sub/missing/", "code/scenarios/sub/page/"],
  ["alphabetical tiebreak",
    ["", "code/scenarios/zebra/", "code/scenarios/apple/", "code/scenarios/banana/"],
    "code/scenarios/missing/", "code/scenarios/apple/"],
  ["falls through to root", ["", "totally/unrelated/page/"], "code/scenarios/scenarios/", ""],
  ["only root is a page", [""], "any/path/", ""],
  ["realistic layout, deleted leaf", realisticPages, "code/scenarios/scenario-parameters/", "code/scenarios/"],
  ["realistic layout, deleted API page", realisticPages, "api/missing/", "api/index/"],
  ["realistic layout, removed section", realisticPages, "removed-section/page/", ""],
];

for (const [functionName, cases] of [
  ["commonSegmentPrefix", prefixCases],
  ["findClosestPage", closestCases],
]) {
  const script = new vm.Script(source + `\n${functionName}(...args);`, { filename: sourcePath });
  for (const [name, ...values] of cases) {
    const expected = values.pop();
    test(`${functionName}: ${name}`, () => {
      const actual = script.runInNewContext({ args: structuredClone(values) }, { timeout: 1000 });
      assert.equal(actual, expected);
    });
  }
}
