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
const jobsRoute = "GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs";
const logsRoute = "GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs";
const deploymentId = "pages-deployment-987";
const deploymentJob = {
  id: 987, name: "Deploy", run_attempt: 1, status: "completed",
  steps: [{ name: "Deploy to GitHub Pages", started_at: "2026-11-13T10:00:00Z", conclusion: "failure" }]
};
const deploymentLog = `2026-11-13T10:00:00.0000000Z Created deployment for ${context.sha}, ID: ${deploymentId}\n`;

function fixture({
  attempt = 1, run = {}, pages, previousRuns = [[current]], status = "deployment_cancelled", errorRoute, errorStatus,
  jobPages = [[deploymentJob]], log = deploymentLog, logs
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
      if (route === logsRoute) {
        if (logs) {
          assert.ok(Object.hasOwn(logs, options.job_id));
          return { data: logs[options.job_id] };
        }
        assert.equal(options.job_id, deploymentJob.id);
        return { data: log };
      }
      assert.equal(route, statusRoute); // No API writes, retries or deployment creation.
      assert.equal(options.pages_deployment_id, deploymentId);
      return { data: { status } };
    },
    paginate: {
      async *iterator(route, options) {
        calls.push({ route, options });
        if (route === errorRoute) throw new Error("API unavailable");
        if (route === jobsRoute) {
          assert.equal(options.filter, "all");
          assert.equal(options.per_page, 100);
          for (const data of jobPages) yield { data };
          return;
        }
        assert.equal(route, listRoute);
        assert.equal(options.branch, "main");
        assert.equal(options.workflow_id, 7);
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
  assert.equal(f.calls.find(call => call.route === jobsRoute).options.run_id, 122);
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
    assert.deepEqual(f.calls.map(call => call.route), [runRoute, listRoute, jobsRoute, logsRoute, statusRoute]);
    assert.equal(f.calls.find(call => call.route === jobsRoute).options.run_id, context.runId);
  });
}

for (const status of ["", undefined, null, "syncing_files", "deployment_in_progress",
  "deployment_queued", "deployment_attempt_error", "unknown_status", "not_found", "succeed", "unexpected"]) {
  test(`retry refuses ambiguous, active or successful status ${JSON.stringify(status)}`, async () => {
    const f = fixture({ attempt: 2 });
    const request = f.options.github.request;
    f.options.github.request = async (route, options) => {
      if (route === statusRoute) return { data: { status } };
      return request(route, options);
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

for (const errorRoute of [runRoute, listRoute, jobsRoute, logsRoute, statusRoute]) {
  test(`API failure at ${errorRoute} stops retry`, async () => {
    const f = fixture({ attempt: 2, errorRoute });
    await assert.rejects(check(f.options), /API unavailable/);
  });
}

test("diagnostics preserve empty service status and never retry", async () => {
  const f = fixture({ status: "" });
  await check({ ...f.options, mode: "diagnose" });
  assert.deepEqual(f.calls.map(call => call.route), [jobsRoute, logsRoute, statusRoute]);
  const data = JSON.parse(f.messages[1]);
  assert.deepEqual(data, {
    run: context.runId, attempt: 1, sha: context.sha, deploymentId,
    deploymentRun: context.runId, deploymentAttempt: 1, status: ""
  });
  assert.ok(f.messages.includes("written"));
  assert.match(f.messages.at(-1), /No deployment was retried/);
});

test("diagnostic API failure stays visible", async () => {
  const f = fixture({ errorRoute: statusRoute });
  await assert.rejects(check({ ...f.options, mode: "diagnose" }), /API unavailable/);
});

test("recovery selects the newest same-SHA production run across all pages", async () => {
  const f = fixture({ previousRuns: [
    [{ ...current, id: 121, run_number: 48 }],
    [{ ...current, id: 122, run_number: 49 }]
  ] });
  await check(f.options);
  assert.equal(f.calls.find(call => call.route === jobsRoute).options.run_id, 122);
});

test("retry uses the latest actual deployment, not current or preflight-only attempts", async () => {
  const f = fixture({ attempt: 3, jobPages: [
    [{ ...deploymentJob, id: 989, run_attempt: 3, status: "in_progress" }],
    [{ ...deploymentJob, id: 988, run_attempt: 2,
      steps: [{ name: "Deploy to GitHub Pages", started_at: null, conclusion: "skipped" }] }],
    [deploymentJob]
  ] });
  await check(f.options);
  assert.equal(f.calls.find(call => call.route === logsRoute).options.job_id, deploymentJob.id);
});

test("new runs check the latest deployment attempt of the previous same-SHA run", async () => {
  const f = fixture({
    previousRuns: [[{ ...current, id: 122, run_attempt: 3 }]],
    jobPages: [[{ ...deploymentJob, id: 986 }], [{ ...deploymentJob, run_attempt: 3 }]]
  });
  await check(f.options);
  assert.equal(f.calls.find(call => call.route === jobsRoute).options.run_id, 122);
  assert.equal(f.calls.find(call => call.route === logsRoute).options.job_id, deploymentJob.id);
});

test("a missing latest deployment ID cannot fall back to an older terminal deployment", async () => {
  const f = fixture({
    attempt: 3,
    jobPages: [[deploymentJob], [{ ...deploymentJob, id: 988, run_attempt: 2 }]],
    logs: { 987: deploymentLog, 988: "" }
  });
  await assert.rejects(check(f.options), /Cannot identify the Pages deployment ID/);
  assert.equal(f.calls.find(call => call.route === logsRoute).options.job_id, 988);
  assert.equal(f.calls.some(call => call.route === statusRoute), false);
});

test("diagnostics cannot fall back to a different attempt's deployment", async () => {
  const f = fixture({ attempt: 2 });
  await assert.rejects(check({ ...f.options, mode: "diagnose" }), /Cannot identify a completed/);
  assert.equal(f.calls.some(call => call.route === statusRoute), false);
});

for (const jobPages of [
  [[]],
  [[{ ...deploymentJob, status: "in_progress" }]],
  [[{ ...deploymentJob, steps: [] }]],
  [[{ ...deploymentJob, run_attempt: undefined }]],
  [[deploymentJob, { ...deploymentJob, id: 988 }]],
  [[deploymentJob, { ...deploymentJob, id: 988, run_attempt: 2, status: "in_progress" }]]
]) {
  test(`missing or ambiguous deployment jobs fail closed: ${JSON.stringify(jobPages)}`, async () => {
    const f = fixture({ attempt: 3, jobPages });
    await assert.rejects(check(f.options), /Cannot identify a completed/);
    assert.equal(f.calls.some(call => call.route === statusRoute), false);
  });
}

for (const log of [
  "",
  deploymentLog.replace(context.sha, "b".repeat(40)),
  deploymentLog.replace(deploymentId, "undefined"),
  deploymentLog.replace(deploymentId, "null"),
  deploymentLog + deploymentLog,
  deploymentLog.replace(deploymentId, "unrecognized/id")
]) {
  test(`missing or ambiguous deployment IDs never fall back to SHA: ${JSON.stringify(log)}`, async () => {
    const f = fixture({ attempt: 2, log });
    await assert.rejects(check(f.options), /Cannot identify the Pages deployment ID/);
    assert.equal(f.calls.some(call => call.route === statusRoute), false);
  });
}

test("binary job logs and CRLF retain the actual non-SHA deployment ID", async () => {
  const log = `2026-11-13T10:00:00.0000000Z Starting deployment\n${deploymentLog}`;
  const f = fixture({ attempt: 2, log: Buffer.from(log.replaceAll("\n", "\r\n")) });
  await check(f.options);
  assert.equal(f.calls.at(-1).options.pages_deployment_id, deploymentId);
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
