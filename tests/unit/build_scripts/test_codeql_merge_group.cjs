// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { mkdtemp, writeFile, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { gunzipSync } = require("node:zlib");
const { pathToFileURL } = require("node:url");
const publish = require(path.resolve(__dirname, "..", "..", "..", "build_scripts", "codeql_merge_group.cjs"));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const script = process.env.CODEQL_TEST_SCRIPT;

const SHA = "a".repeat(40);
const REF = `refs/heads/gh-readonly-queue/main/pr-42-${SHA}`;
const ID = "sarif-upload-id";
const CATEGORY = "/language:python";
const context = {
  repo: { owner: "owner", repo: "repository" }, eventName: "merge_group", ref: REF, sha: SHA, runId: 123,
  payload: { action: "checks_requested", merge_group: { base_ref: "refs/heads/main", head_ref: REF, head_sha: SHA } }
};
const routes = {
  ref: "GET /repos/{owner}/{repo}/git/matching-refs/{ref}",
  run: "GET /repos/{owner}/{repo}/actions/runs/{run_id}",
  jobs: "GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt_number}/jobs",
  cancel: "POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel",
  upload: "POST /repos/{owner}/{repo}/code-scanning/sarifs",
  processing: "GET /repos/{owner}/{repo}/code-scanning/sarifs/{sarif_id}"
};
const sarif = {
  version: "2.1.0", runs: [{ tool: { driver: { name: "CodeQL" } }, automationDetails: { id: `${CATEGORY}/` },
    results: [{ partialFingerprints: { primaryLocationLineHash: "existing-fingerprint" } }] }]
};
const refs = (sha = SHA) => [{ ref: REF, object: { type: "commit", sha } }];
const run = (changes = {}) => ({
  id: 123, run_attempt: 1, event: "merge_group", head_sha: SHA, head_branch: REF.slice(11),
  path: ".github/workflows/codeql.yml", status: "in_progress", conclusion: null, ...changes
});
const jobs = () => ({
  total_count: 3, jobs: Array.from({ length: 3 }, () => ({ conclusion: null, steps: [{ conclusion: "success" }] }))
});
const apiError = (status, message = "Request failed", errors) =>
  Object.assign(new Error(`${message} - https://docs.github.com/rest`), { status, response: { data: { message, errors } } });
const missing = () => apiError(404, `ref '${REF}' not found in this repository`);
const cancelled = new Error("runner cancelled");
const live = [[routes.ref, refs()], [routes.upload, { id: ID }, 202], [routes.processing, { processing_status: "complete" }],
  [routes.ref, refs()]];
const obsolete = () => [[routes.ref, []], [routes.run, run()], [routes.jobs, jobs()]];
const scenarios = [];
function scenario(name, replies, error, changes = {}) {
  scenarios.push({ name, replies, error, ...changes });
}

scenario("live matching candidate preserves prepared SARIF", live);
for (const category of ["/language:actions", "/language:javascript-typescript"]) {
  scenario(`live ${category} preserves its category`, live, undefined, {
    document: { ...sarif, runs: [{ ...sarif.runs[0], automationDetails: { id: `${category}/` } }] },
    options: { category }
  });
}
const otherSha = "b".repeat(40);
scenario("queue name suffix is not the tested head SHA", [
  [routes.ref, refs(otherSha)], ...live.slice(1, 3), [routes.ref, refs(otherSha)]
], undefined, {
  context: { sha: otherSha, payload: { ...context.payload,
    merge_group: { ...context.payload.merge_group, head_sha: otherSha } } }
});
for (const current of [[], refs("b".repeat(40)), [{ ref: REF + "-suffix" }]]) {
  scenario(`obsolete ref ${JSON.stringify(current)}`, [[routes.ref, current], ...obsolete().slice(1),
    [routes.cancel, {}, 202]], cancelled);
}
for (const status of [400, 404]) {
  for (const current of [[], refs("b".repeat(40))]) {
    scenario(`upload race ${status} ${JSON.stringify(current)}`, [[routes.ref, refs()],
      [routes.upload, apiError(status, `ref '${REF}' not found in this repository`)], [routes.ref, current],
      ...obsolete().slice(1), [routes.cancel, {}, 202]], cancelled);
  }
}
scenario("missing-ref rejection while candidate remains live fails", [[routes.ref, refs()], [routes.upload, missing()],
  [routes.ref, refs()]], /not found in this repository/);

const stages = {
  precheck: [[], routes.ref],
  upload: [[[routes.ref, refs()]], routes.upload],
  processing: [live.slice(0, 2), routes.processing],
  recheck: [[[routes.ref, refs()], [routes.upload, missing()]], routes.ref],
  run: [obsolete().slice(0, 1), routes.run],
  jobs: [obsolete().slice(0, 2), routes.jobs],
  cancel: [obsolete(), routes.cancel]
};
for (const [stage, [prefix, route]] of Object.entries(stages)) {
  for (const status of [401, 403, 404, 429, 500, 502, 503]) {
    scenario(`${stage} HTTP ${status} remains a failure`, [...prefix, [route, apiError(status)]], /Request failed/);
  }
  scenario(`${stage} network failure remains a failure`, [...prefix, [route, new Error("network failed")]], /network failed/);
}
for (const error of [apiError(400, "Invalid SARIF"), apiError(404, "Not Found"),
  apiError(422, `ref '${REF}' not found in this repository`),
  apiError("404", `ref '${REF}' not found in this repository`),
  apiError(400, `ref '${REF}' not found in this repository`, ["Invalid SARIF"])]) {
  scenario(`genuine upload rejection ${error.message}`, [[routes.ref, refs()], [routes.upload, error]], error);
}
for (const status of ["pending", "complete", "failed"]) {
  scenario(`expiry after accepted upload ${status}`, [...live.slice(0, 2),
    [routes.processing, { processing_status: status, errors: [`ref '${REF}' not found in this repository`] }],
    ...obsolete(), [routes.cancel, {}, 202]], cancelled);
}
for (const errors of [["Invalid SARIF"], [`ref '${REF}' not found in this repository`, "Invalid SARIF"]]) {
  scenario(`processing rejection ${errors}`, [...live.slice(0, 2),
    [routes.processing, { processing_status: "failed", errors }]], /SARIF processing failed/);
}
scenario("missing-ref processing rejection with live ref fails", [...live.slice(0, 2),
  [routes.processing, { processing_status: "failed", errors: [`ref '${REF}' not found in this repository`] }],
  [routes.ref, refs()]], /SARIF processing failed/);
scenario("bounded processing timeout", [...live.slice(0, 2),
  ...Array.from({ length: 5 }, () => [[routes.processing, { processing_status: "pending" }], [routes.ref, refs()]]).flat()],
  /five status checks/, { delays: [5000, 10000, 20000, 40000, 80000] });
scenario("accepted cancellation cannot return success", [...obsolete(), [routes.cancel, {}, 202]],
  /runner did not stop within 60 seconds/, { signal: false, delays: [60000] });
scenario("already cancelled workflow", [[routes.ref, []], [routes.run, run({ status: "completed", conclusion: "cancelled" })]],
  /already cancelled/);
scenario("cancellation conflict verifies existing cancellation", [...obsolete(), [routes.cancel, apiError(409)],
  [routes.run, run({ status: "completed", conclusion: "cancelled" })]], /already cancelled/);
scenario("unexplained cancellation conflict stays failure", [...obsolete(), [routes.cancel, apiError(409)],
  [routes.run, run()]], /Request failed/);
for (const change of [{ id: 999 }, { run_attempt: 2 }, { event: "push" }, { head_sha: "b".repeat(40) },
  { head_branch: "main" }, { path: ".github/workflows/other.yml" },
  { status: "completed", conclusion: "failure" }, { status: "completed", conclusion: "success" }]) {
  scenario(`cannot cancel unrelated or completed run ${JSON.stringify(change)}`, [[routes.ref, []], [routes.run, run(change)]],
    /Refusing to cancel/);
}
for (const conclusion of ["failure", "timed_out", "startup_failure", "action_required"]) {
  for (const location of ["job", "step"]) {
    const result = jobs();
    if (location === "job") result.jobs[0].conclusion = conclusion;
    else result.jobs[0].steps[0].conclusion = conclusion;
    scenario(`${location} ${conclusion} cannot be hidden by retirement`,
      [[routes.ref, []], [routes.run, run()], [routes.jobs, result]], /refusing to replace failure/);
  }
}
for (const result of [{ total_count: 101, jobs: jobs().jobs }, { total_count: 0, jobs: [] },
  { total_count: 1, jobs: [{ steps: {} }] }, { total_count: 1, jobs: [{ steps: [null] }] }]) {
  scenario(`invalid job listing ${JSON.stringify(result)}`, [[routes.ref, []], [routes.run, run()], [routes.jobs, result]],
    /Cannot verify|Invalid/);
}
for (const outcome of ["failure", "cancelled", "skipped", ""]) {
  scenario(`analysis outcome ${outcome} cannot publish or cancel`, [], /successful CodeQL analysis/,
    { options: { analysisOutcome: outcome } });
}
for (const change of [{ eventName: "push" }, { ref: "refs/heads/main" }, { sha: "b".repeat(40) }, { runId: 0 },
  { eventName: "pull_request" }, { eventName: "schedule" }, { eventName: "workflow_dispatch" },
  { payload: { action: "destroyed" } },
  { ref: "refs/heads/gh-readonly-queue/main/", payload: { ...context.payload,
    merge_group: { ...context.payload.merge_group, head_ref: "refs/heads/gh-readonly-queue/main/" } } }]) {
  scenario(`invalid context ${JSON.stringify(change)}`, [], /merge-group context/, { context: change });
}
scenario("invalid attempt cannot cancel another run", [], /merge-group context/, { options: { runAttempt: 0 } });
for (const result of [null, {}, [{ ref: REF, object: null }], [...refs(), ...refs()],
  [{ ref: REF, object: { type: "tag", sha: SHA } }], [{ ref: REF, object: { type: "commit", sha: "short" } }]]) {
  scenario(`invalid ref response ${JSON.stringify(result)}`, [[routes.ref, result]], /Invalid|multiple|valid commit/);
}
for (const document of [[], { version: "2.1.0", runs: [] }, { version: "2.1.0", runs: [{ tool: null }] },
  { ...sarif, runs: [{ ...sarif.runs[0], automationDetails: { id: "/language:actions/" } }] }]) {
  scenario(`invalid prepared SARIF ${JSON.stringify(document)}`, [], /Invalid|Expected prepared/, { document });
}
scenario("malformed JSON fails before API calls", [], /JSON/, { raw: "not JSON" });
scenario("unknown processing state fails", [...live.slice(0, 2), [routes.processing, { processing_status: "other" }]],
  /Invalid SARIF processing status/);
for (const result of [{}, { id: "" }, { id: 123 }]) {
  scenario(`upload ID must be valid ${JSON.stringify(result)}`, [[routes.ref, refs()], [routes.upload, result, 202]],
    /without a valid ID/);
}
scenario("unexpected successful HTTP status fails", [[routes.ref, refs(), 201]], /Unexpected HTTP/);
scenario("runner cancellation does not continue upload", [[routes.ref, refs()], [routes.upload, cancelled]], cancelled);

for (const item of scenarios) {
  test(item.name, async (t) => {
    const directory = await mkdtemp(path.join(tmpdir(), "pyrit-codeql-"));
    t.after(() => rm(directory, { recursive: true, force: true }));
    const file = path.join(directory, "upload.sarif");
    const prepared = item.raw ?? JSON.stringify(item.document ?? sarif);
    await writeFile(file, prepared);
    const replies = [...item.replies];
    const calls = [];
    const delays = [];
    const notices = [];
    const github = { request: async (route, options) => {
      calls.push({ route, options });
      assert.ok(replies.length, `Unexpected API call: ${route}`);
      const [expected, data, status = 200] = replies.shift();
      assert.equal(route, expected);
      assert.equal(options.owner, context.repo.owner);
      assert.equal(options.repo, context.repo.repo);
      assert.equal(options.request.timeout, 30000);
      if (route === routes.ref) assert.equal(options.ref, REF.slice(5));
      if ([routes.run, routes.jobs, routes.cancel].includes(route)) assert.equal(options.run_id, 123);
      if (route === routes.jobs) assert.deepEqual([options.attempt_number, options.per_page], [1, 100]);
      if (data instanceof Error) throw data;
      return { status, data };
    } };
    const actualContext = { ...context, ...item.context };
    const options = {
      github, context: actualContext, core: { notice: (message) => notices.push(message), info: () => {} },
      sarifFile: file, category: CATEGORY, analysisOutcome: "success", runAttempt: 1, checkout: directory,
      sleep: async (milliseconds) => {
        delays.push(milliseconds);
        if (milliseconds === 60000 && item.signal !== false) throw cancelled;
      }, ...item.options
    };
    let resultId;
    const execute = async (args) => {
      resultId = await publish({ ...args, sleep: options.sleep });
      return resultId;
    };
    const invoke = async () => {
      if (!script) return execute(options);
      Object.assign(process.env, {
        SARIF_FILE: file, SARIF_CATEGORY: options.category, ANALYSIS_OUTCOME: options.analysisOutcome,
        GITHUB_RUN_ATTEMPT: String(options.runAttempt), GITHUB_WORKSPACE: directory
      });
      const requireForScript = (name) => {
        if (name === "node:path") return path;
        assert.equal(name, path.resolve("build_scripts", "codeql_merge_group.cjs"));
        return execute;
      };
      await new AsyncFunction("require", "github", "context", "core", script)(
        requireForScript, github, actualContext, options.core
      );
    };
    if (item.error) {
      await assert.rejects(invoke, item.error instanceof Error ? (error) => error === item.error : item.error);
      assert.equal(resultId, undefined);
    } else {
      await invoke();
      assert.equal(resultId, ID);
      assert.deepEqual(delays, [5000]);
      assert.equal(notices.length, 0);
    }
    assert.equal(replies.length, 0, "All expected API calls must be consumed");
    if (item.delays) assert.deepEqual(delays, item.delays);
    for (const { route, options } of calls.filter((call) => call.route === routes.upload)) {
      assert.equal(route, routes.upload);
      assert.equal(options.commit_sha, actualContext.sha);
      assert.equal(options.ref, REF);
      assert.equal(options.tool_name, "CodeQL");
      assert.equal(options.checkout_uri, pathToFileURL(directory).href);
      assert.equal(gunzipSync(Buffer.from(options.sarif, "base64")).toString("utf8"), prepared);
    }
    if (item.error === cancelled && calls.at(-1)?.route === routes.cancel) {
      assert.equal(notices.length, 1);
      assert.deepEqual(delays.at(-1), 60000);
    }
  });
}
