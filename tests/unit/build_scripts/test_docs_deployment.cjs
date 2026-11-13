// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const assert = require("node:assert/strict");
const { test } = require("node:test");
const path = require("node:path");
const check = require(path.resolve(__dirname, "..", "..", "..", "build_scripts", "docs_deployment.cjs"));
const context = {
  repo: { owner: "owner", repo: "repo" }, runId: 123, sha: "a".repeat(40),
  ref: "refs/heads/main", eventName: "push"
};
const current = {
  id: 123, head_sha: context.sha, head_branch: "main", event: "push", run_attempt: 1,
  workflow_id: 7, run_number: 50, path: ".github/workflows/docs.yml"
};
const runRoute = "GET /repos/{owner}/{repo}/actions/runs/{run_id}";
const listRoute = "GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs";
const statusRoute = "GET /repos/{owner}/{repo}/pages/deployments/{pages_deployment_id}";

function fixture({
  attempt = 1, run = {}, pages, previousRuns = [[current]], status = "deployment_cancelled", errorRoute, errorStatus
} = {}) {
  const calls = [];
  const messages = [];
  const summary = {
    addHeading(value) { messages.push(value); return this; },
    addCodeBlock(value) { messages.push(value); return this; },
    addRaw(value) { messages.push(value); return this; },
    async write() { messages.push("written"); }
  };
  const github = {
    async request(route, options) {
      calls.push({ route, options });
      assert.equal(options.owner, context.repo.owner);
      assert.equal(options.repo, context.repo.repo);
      assert.equal(options.request.timeout, 30000);
      if (route === errorRoute) throw Object.assign(new Error("API unavailable"), { status: errorStatus });
      if (route === runRoute) {
        assert.equal(options.run_id, context.runId);
        return { data: { ...current, run_attempt: attempt, ...run } };
      }
      assert.equal(route, statusRoute); // No API writes, retries or deployment creation.
      assert.equal(options.pages_deployment_id, context.sha);
      return { data: { status } };
    },
    paginate: {
      async *iterator(route, options) {
        calls.push({ route, options });
        assert.equal(route, listRoute);
        assert.equal(options.branch, "main");
        assert.equal(options.workflow_id, 7);
        if (route === errorRoute) throw new Error("API unavailable");
        if (options.head_sha) {
          assert.equal(options.head_sha, context.sha);
          for (const data of previousRuns) yield { data };
        } else {
          for (const data of pages ?? [[current]]) yield { data };
        }
      }
    }
  };
  return {
    calls, messages,
    options: { github, context, core: { summary, info() {}, warning(message) { messages.push(message); } },
      runAttempt: attempt, mode: "preflight" }
  };
}

test("first publication uses run history, not an ambiguous Pages response", async () => {
  const f = fixture({ errorRoute: statusRoute });
  await check(f.options);
  assert.deepEqual(f.calls.map(call => call.route), [runRoute, listRoute, listRoute]);
});

test("a new run may publish an updated release tree after a successful same-SHA deployment", async () => {
  const f = fixture({ status: "succeed", previousRuns: [[{ ...current, id: 122 }]] });
  await check(f.options);
  assert.equal(f.calls.at(-1).route, statusRoute);
});

for (const status of ["syncing_files", ""]) {
  test(`a new run cannot bypass an active or unknown same-SHA deployment: ${JSON.stringify(status)}`, async () => {
    const f = fixture({ status, previousRuns: [[{ ...current, id: 122 }]] });
    await assert.rejects(check(f.options), /Refusing Pages retry/);
  });
}

test("same-SHA history paginates past non-production runs", async () => {
  const f = fixture({ status: "", previousRuns: [
    [{ ...current, id: 120, event: "pull_request" }],
    [{ ...current, id: 122 }]
  ] });
  await assert.rejects(check(f.options), /Refusing Pages retry/);
});

test("a missing Pages deployment cannot authorize a failed-job retry", async () => {
  const f = fixture({ attempt: 2, errorRoute: statusRoute, errorStatus: 404 });
  await assert.rejects(check(f.options), /API unavailable/);
});

for (const status of [
  "deployment_failed", "deployment_cancelled", "deployment_content_failed",
  "deployment_perms_error", "deployment_lost"
]) {
  test(`confirmed terminal ${status} permits a manual failed-job retry`, async () => {
    const f = fixture({ attempt: 2, status });
    await check(f.options);
    assert.deepEqual(f.calls.map(call => call.route), [runRoute, listRoute, statusRoute]);
  });
}

for (const status of ["", undefined, null, "syncing_files", "deployment_in_progress",
  "deployment_queued", "deployment_attempt_error", "unknown_status", "not_found", "succeed", "unexpected"]) {
  test(`retry refuses ambiguous, active or successful status ${JSON.stringify(status)}`, async () => {
    const f = fixture({ attempt: 2 });
    f.options.github.request = async (route, options) => {
      if (route === statusRoute) return { data: { status } };
      return { data: { ...current, run_attempt: 2 } };
    };
    await assert.rejects(check(f.options), /Refusing Pages retry/);
  });
}

for (const event of ["push", "workflow_dispatch"]) {
  test(`a newer ${event} run blocks even an identical main SHA`, async () => {
    const f = fixture({ pages: [[{ ...current, id: 124, run_number: 51, event }, current]] });
    await assert.rejects(check(f.options), /newer production docs run/);
    assert.equal(f.calls.some(call => call.route === statusRoute), false);
  });
}

test("ordering paginates past non-production runs", async () => {
  const f = fixture({ pages: [
    [{ ...current, id: 124, run_number: 51, event: "pull_request" }],
    [current]
  ] });
  await check(f.options);
});

test("missing current run fails closed", async () => {
  const f = fixture({ pages: [[]] });
  await assert.rejects(check(f.options), /Cannot verify production run ordering/);
});

for (const run of [
  { head_sha: "b".repeat(40) }, { id: 124 }, { run_attempt: 2 }, { path: "other.yml" },
  { head_branch: "other" }, { event: "pull_request" }, { run_number: null }, { workflow_id: null }
]) {
  test(`mismatched run identity fails closed: ${JSON.stringify(run)}`, async () => {
    const f = fixture({ run });
    await assert.rejects(check(f.options), /identity or attempt does not match/);
  });
}

for (const errorRoute of [runRoute, listRoute, statusRoute]) {
  test(`API failure at ${errorRoute} stops retry`, async () => {
    const f = fixture({ attempt: 2, errorRoute });
    await assert.rejects(check(f.options), /API unavailable/);
  });
}

test("diagnostics preserve empty service status and never retry", async () => {
  const f = fixture({ status: "" });
  await check({ ...f.options, mode: "diagnose" });
  assert.deepEqual(f.calls.map(call => call.route), [statusRoute]);
  const data = JSON.parse(f.messages[1]);
  assert.deepEqual(data, { run: context.runId, attempt: 1, sha: context.sha, status: "" });
  assert.ok(f.messages.includes("written"));
  assert.match(f.messages.at(-1), /No deployment was retried/);
});

test("diagnostic API failure stays visible", async () => {
  const f = fixture({ errorRoute: statusRoute });
  await assert.rejects(check({ ...f.options, mode: "diagnose" }), /API unavailable/);
});

for (const changes of [{ ref: "refs/heads/release" }, { eventName: "pull_request" }]) {
  test(`non-production context is rejected: ${JSON.stringify(changes)}`, async () => {
    const f = fixture();
    await assert.rejects(check({ ...f.options, context: { ...context, ...changes } }), /requires a main/);
    assert.deepEqual(f.calls, []);
  });
}

for (const runAttempt of [0, NaN, 1.5]) {
  test(`invalid attempt ${runAttempt} is rejected`, async () => {
    const f = fixture();
    await assert.rejects(check({ ...f.options, runAttempt }), /Invalid workflow attempt/);
  });
}
