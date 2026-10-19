// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.
// @ts-check

const { readFile } = require("node:fs/promises");
const { setTimeout: delay } = require("node:timers/promises");
const { gzipSync } = require("node:zlib");
const { pathToFileURL } = require("node:url");

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** @param {unknown} value @returns {Record<string, unknown>} */
function object(value) {
  if (!isObject(value)) throw new Error("Invalid GitHub API or SARIF object");
  return value;
}

/**
 * @typedef {{repo: {owner: string, repo: string}, eventName: string, ref: string, sha: string,
 * runId: number, payload: {action?: string, merge_group?: {base_ref?: string, head_ref?: string, head_sha?: string}}}} Context
 * @typedef {{request: (route: string, options: Record<string, unknown>) =>
 * Promise<{status: number, data: unknown}>}} GitHub
 * @param {{github: GitHub, context: Context, core: {notice: (message: string) => void, info: (message: string) => void},
 * sarifFile: string, category: string, analysisOutcome: string, runAttempt: number,
 * checkout: string, sleep?: (milliseconds: number) => Promise<void>}} options
 */
module.exports = async function publish({
  github, context, core, sarifFile, category, analysisOutcome, runAttempt, checkout, sleep = delay
}) {
  const group = context.payload.merge_group;
  const queuePrefix = `refs/heads/gh-readonly-queue/${group?.base_ref?.slice(11)}/`;
  if (analysisOutcome !== "success") throw new Error("Publishing requires successful CodeQL analysis");
  if (
    context.eventName !== "merge_group" || context.payload.action !== "checks_requested" ||
    !group?.base_ref?.startsWith("refs/heads/") || !context.ref.startsWith(queuePrefix) || context.ref === queuePrefix ||
    group.head_ref !== context.ref || group.head_sha !== context.sha || !/^[0-9a-f]{40}$/.test(context.sha) ||
    !Number.isSafeInteger(context.runId) || context.runId <= 0 || !Number.isSafeInteger(runAttempt) || runAttempt <= 0
  ) {
    throw new Error("The merge-group context does not match the tested queue ref, SHA and run");
  }
  const sarif = await readFile(sarifFile);
  const document = object(JSON.parse(sarif.toString("utf8")));
  if (
    document.version !== "2.1.0" || !Array.isArray(document.runs) || !document.runs.length ||
    document.runs.some((value) => {
      const run = object(value);
      return object(object(run.tool).driver).name !== "CodeQL" ||
        object(run.automationDetails).id !== `${category}/`;
    })
  ) {
    throw new Error("Expected prepared CodeQL SARIF matching the matrix language category");
  }
  const missingRef = `ref '${context.ref}' not found in this repository`;
  const runOptions = { run_id: context.runId };

  /** @param {string} route @param {Record<string, unknown>} [options] @param {number} [status] */
  async function request(route, options = {}, status = 200) {
    const response = await github.request(route, {
      ...context.repo, ...options,
      headers: { "X-GitHub-Api-Version": "2022-11-28" },
      request: { timeout: 30000 }
    });
    if (response.status !== status) throw new Error(`Unexpected HTTP ${response.status} for ${route}`);
    return response.data;
  }

  async function currentSha() {
    const refs = await request("GET /repos/{owner}/{repo}/git/matching-refs/{ref}", { ref: context.ref.slice(5) });
    if (!Array.isArray(refs) || refs.some((value) => typeof object(value).ref !== "string")) {
      throw new Error("Invalid matching-refs response");
    }
    const matches = refs.map(object).filter((value) => value.ref === context.ref);
    if (!matches.length) return null;
    if (matches.length !== 1) throw new Error("Matching-refs returned multiple exact queue refs");
    const target = object(matches[0].object);
    if (target.type !== "commit" || typeof target.sha !== "string" || !/^[0-9a-f]{40}$/.test(target.sha)) {
      throw new Error("The queue ref does not point to a valid commit");
    }
    return target.sha;
  }

  async function verifyRun() {
    const run = object(await request("GET /repos/{owner}/{repo}/actions/runs/{run_id}", runOptions));
    const expected = {
      id: context.runId, run_attempt: runAttempt, event: "merge_group", head_sha: context.sha,
      head_branch: context.ref.slice(11), path: ".github/workflows/codeql.yml"
    };
    if (Object.entries(expected).some(([key, value]) => run[key] !== value)) {
      throw new Error("Refusing to cancel a different workflow run or attempt");
    }
    if (run.status === "completed" && run.conclusion === "cancelled") throw new Error("CodeQL run is already cancelled");
    if (run.status !== "in_progress" || run.conclusion !== null) {
      throw new Error("Refusing to cancel a workflow run that is not in progress");
    }
  }

  async function ensureLive() {
    const sha = await currentSha();
    if (sha === context.sha) return;
    await verifyRun();
    const result = object(await request(
      "GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt_number}/jobs",
      { ...runOptions, attempt_number: runAttempt, per_page: 100 }
    ));
    if (!Array.isArray(result.jobs) || !result.jobs.length || result.total_count !== result.jobs.length) {
      throw new Error("Cannot verify all CodeQL matrix jobs before cancellation");
    }
    for (const value of result.jobs) {
      const job = object(value);
      if (!Array.isArray(job.steps)) throw new Error("Invalid workflow job steps");
      const conclusions = [job.conclusion, ...job.steps.map((step) => object(step).conclusion)];
      if (conclusions.some((value) =>
        value != null && value !== "success" && value !== "skipped" && value !== "cancelled"
      )) {
        throw new Error("A CodeQL matrix job or step failed; refusing to replace failure with cancellation");
      }
    }
    core.notice(`Verified obsolete merge candidate ${context.ref}: ${sha === null ? "absent" : `now at ${sha}`}`);
    try {
      await request("POST /repos/{owner}/{repo}/actions/runs/{run_id}/cancel", runOptions, 202);
    } catch (error) {
      if (isObject(error) && error.status === 409) await verifyRun();
      throw error;
    }
    // Never finish green while waiting for the runner's cancellation signal.
    await sleep(60000);
    throw new Error("Cancellation accepted, but the runner did not stop within 60 seconds");
  }

  await ensureLive();
  let accepted;
  try {
    accepted = object(await request("POST /repos/{owner}/{repo}/code-scanning/sarifs", {
      commit_sha: context.sha,
      ref: context.ref,
      sarif: gzipSync(sarif).toString("base64"),
      checkout_uri: pathToFileURL(checkout).href,
      tool_name: "CodeQL"
    }, 202));
  } catch (error) {
    if (
      isObject(error) && (error.status === 400 || error.status === 404) && isObject(error.response) &&
      isObject(error.response.data) && error.response.data.message === missingRef &&
      (error.response.data.errors == null ||
        (Array.isArray(error.response.data.errors) && !error.response.data.errors.length))
    ) {
      await ensureLive();
    }
    throw error;
  }
  const sarifId = accepted.id;
  if (typeof sarifId !== "string" || !sarifId) throw new Error("SARIF upload accepted without a valid ID");
  for (const milliseconds of [5000, 10000, 20000, 40000, 80000]) {
    await sleep(milliseconds);
    const result = object(await request(
      "GET /repos/{owner}/{repo}/code-scanning/sarifs/{sarif_id}", { sarif_id: sarifId }
    ));
    if (result.processing_status === "complete") {
      await ensureLive();
      core.info(`CodeQL SARIF ${sarifId} processed for ${context.ref} at ${context.sha}`);
      return sarifId;
    }
    if (result.processing_status === "failed") {
      if (Array.isArray(result.errors) && result.errors.length === 1 && result.errors[0] === missingRef) {
        await ensureLive();
      }
      throw new Error(`SARIF processing failed: ${JSON.stringify(result.errors)}`);
    }
    if (result.processing_status !== "pending") {
      throw new Error(`Invalid SARIF processing status: ${result.processing_status}`);
    }
    await ensureLive();
  }
  throw new Error("SARIF processing did not complete within five status checks");
};
