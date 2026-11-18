// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const RETRYABLE = new Set([
  "deployment_failed", "deployment_content_failed", "deployment_perms_error",
  "deployment_cancelled", "deployment_lost"
]);

module.exports = async function docsDeployment({ github, context, core, mode, runAttempt }) {
  const options = { ...context.repo, request: { timeout: 30000 } };
  const statusRoute = "GET /repos/{owner}/{repo}/pages/deployments/{pages_deployment_id}";
  const deploymentStatus = async (runId, attempt, exactAttempt = false) => {
    const jobs = [];
    for await (const { data } of github.paginate.iterator(
      "GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
      { ...options, run_id: runId, filter: "all", per_page: 100 }
    )) {
      for (const job of data.filter(job => job.name === "Deploy")) {
        if (!Number.isSafeInteger(job.run_attempt) || job.run_attempt < 1) {
          throw new Error("Cannot identify a completed Pages deployment job; refusing to guess its ID");
        }
        if (exactAttempt ? job.run_attempt === attempt : job.run_attempt <= attempt) jobs.push(job);
      }
    }
    let job;
    for (let candidate = attempt; candidate >= (exactAttempt ? attempt : 1); candidate--) {
      const matches = jobs.filter(item => item.run_attempt === candidate);
      if (matches.length !== 1 || matches[0].status !== "completed") {
        throw new Error("Cannot identify a completed Pages deployment job; refusing to guess its ID");
      }
      const steps = matches[0].steps?.filter(step => step.name === "Deploy to GitHub Pages");
      if ((matches[0].conclusion === "skipped" && matches[0].steps?.length === 0) ||
        (steps?.length === 1 && steps[0].conclusion === "skipped")) {
        continue;
      }
      if (steps?.length !== 1 || !steps[0].started_at) {
        throw new Error("Cannot identify a completed Pages deployment job; refusing to guess its ID");
      }
      job = matches[0];
      break;
    }
    if (!job) {
      if (exactAttempt) {
        throw new Error("Cannot identify a completed Pages deployment job; refusing to guess its ID");
      }
      return null;
    }
    // deploy-pages does not expose the ID as an output. Its retained job log
    // records the exact ID it uses, including its status_url/buildVersion fallbacks.
    const { data: log } = await github.request("GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs", {
      ...options, job_id: job.id
    });
    const text = typeof log === "string" ? log : Buffer.from(log).toString("utf8");
    const records = [...text.matchAll(
      /^\S+ Created deployment for ([a-f0-9]{40}), ID: ([A-Za-z0-9._-]+)\r?$/gm
    )];
    if (records.length !== 1 || records[0][1] !== context.sha ||
      ["undefined", "null"].includes(records[0][2])) {
      throw new Error("Cannot identify the Pages deployment ID from its action log; refusing to guess");
    }
    const deploymentId = records[0][2];
    const { data } = await github.request(statusRoute, {
      ...options, pages_deployment_id: deploymentId
    });
    return { deploymentId, deploymentRun: runId, deploymentAttempt: job.run_attempt, status: data.status };
  };

  if (!Number.isSafeInteger(runAttempt) || runAttempt < 1) throw new Error("Invalid workflow attempt");
  if (mode === "diagnose") {
    const deployment = await deploymentStatus(context.runId, runAttempt, true);
    await core.summary.addHeading("Pages deployment failed")
      .addCodeBlock(JSON.stringify({
        run: context.runId, attempt: runAttempt, sha: context.sha, ...deployment
      }, null, 2), "json")
      .addRaw("\nThe action has a hard ten-minute timeout and requests cancellation on timeout. " +
        "A cancellation request is not proof of a terminal deployment state. " +
        "Do not retry an active, empty, or unknown status. See the Pages recovery instructions " +
        "in doc/contributing/10_release_process.md.\n")
      .write();
    core.warning(`Pages status for deployment ${deployment.deploymentId}: ` +
      `${JSON.stringify(deployment.status)}. No deployment was retried.`);
    return;
  }
  if (mode !== "preflight") throw new Error(`Unknown Pages helper mode: ${mode}`);
  if (context.ref !== "refs/heads/main" || !["push", "workflow_dispatch"].includes(context.eventName)) {
    throw new Error("Pages publication requires a main push or manual main run");
  }
  const { data: current } = await github.request("GET /repos/{owner}/{repo}/actions/runs/{run_id}", {
    ...options, run_id: context.runId
  });
  if (
    current.id !== context.runId || current.head_sha !== context.sha ||
    current.run_attempt !== runAttempt || current.path !== ".github/workflows/docs.yml" ||
    current.head_branch !== "main" || current.event !== context.eventName ||
    !Number.isSafeInteger(current.run_number) || !Number.isSafeInteger(current.workflow_id)
  ) throw new Error("Pages workflow run identity or attempt does not match");

  // A new manual run can compose a different release tree at the same main SHA.
  const runsRoute = "GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs";
  const runsOptions = {
    ...options, workflow_id: current.workflow_id, branch: "main", per_page: 100
  };
  const runs = github.paginate.iterator(runsRoute, runsOptions);
  let found = false;
  for await (const { data } of runs) {
    if (data.some(run =>
      ["push", "workflow_dispatch"].includes(run.event) && run.run_number > current.run_number
    )) throw new Error("A newer production docs run exists. Recover that run instead of publishing this artifact.");
    if (data.some(run => run.id === context.runId)) {
      found = true;
      break;
    }
  }
  if (!found) throw new Error("Cannot verify production run ordering; refusing deployment");
  const previousRuns = [];
  // A preflight-only run is not a deployment. Check older same-SHA runs even on
  // retries, but only pass over attempts whose jobs explicitly confirm a skip.
  for await (const { data } of github.paginate.iterator(runsRoute, { ...runsOptions, head_sha: context.sha })) {
    for (const run of data.filter(run =>
      run.id !== context.runId && ["push", "workflow_dispatch"].includes(run.event)
    )) {
      if (!Number.isSafeInteger(run.run_number) || !Number.isSafeInteger(run.run_attempt) ||
        run.run_attempt < 1) throw new Error("Cannot verify production run ordering; refusing deployment");
      if (run.run_number >= current.run_number) {
        throw new Error("A newer production docs run exists. Recover that run instead of publishing this artifact.");
      }
      previousRuns.push(run);
    }
  }
  previousRuns.sort((a, b) => b.run_number - a.run_number);
  if (runAttempt > 1) previousRuns.unshift({ ...current, run_attempt: runAttempt - 1 });
  for (const previous of previousRuns) {
    const deployment = await deploymentStatus(previous.id, previous.run_attempt);
    if (!deployment) continue;
    const { status } = deployment;
    if (!RETRYABLE.has(status) && !(runAttempt === 1 && status === "succeed")) {
      throw new Error(`Refusing Pages retry with status ${JSON.stringify(status)}. ` +
        "Require a confirmed terminal state; unknown or active states cannot be retried, " +
        "and a successful deployment does not need a failed-job retry.");
    }
    break;
  }
  core.info(`Pages preflight passed for run ${context.runId}, attempt ${runAttempt}, SHA ${context.sha}`);
};
