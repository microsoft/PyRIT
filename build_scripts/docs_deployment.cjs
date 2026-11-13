// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

const RETRYABLE = new Set([
  "deployment_failed", "deployment_content_failed", "deployment_perms_error",
  "deployment_cancelled", "deployment_lost"
]);

module.exports = async function docsDeployment({ github, context, core, mode, runAttempt }) {
  const options = { ...context.repo, request: { timeout: 30000 } };
  const statusRoute = "GET /repos/{owner}/{repo}/pages/deployments/{pages_deployment_id}";
  const deploymentStatus = async () => {
    const { data } = await github.request(statusRoute, {
      ...options, pages_deployment_id: context.sha
    });
    return data.status;
  };

  if (mode === "diagnose") {
    const status = await deploymentStatus();
    await core.summary.addHeading("Pages deployment failed")
      .addCodeBlock(JSON.stringify({
        run: context.runId, attempt: runAttempt, sha: context.sha, status
      }, null, 2), "json")
      .addRaw("\nThe action has a hard ten-minute timeout and requests cancellation on timeout. " +
        "A cancellation request is not proof of a terminal deployment state. " +
        "Do not retry an active, empty, or unknown status. See the Pages recovery instructions " +
        "in doc/contributing/10_release_process.md.\n")
      .write();
    core.warning(`Pages status for ${context.sha}: ${JSON.stringify(status)}. No deployment was retried.`);
    return;
  }
  if (mode !== "preflight") throw new Error(`Unknown Pages helper mode: ${mode}`);
  if (!Number.isSafeInteger(runAttempt) || runAttempt < 1) throw new Error("Invalid workflow attempt");
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
  let recovery = runAttempt > 1;
  if (!recovery) {
    // The Pages status API can return an empty status even for nonexistent IDs.
    // Use workflow history, not that response, to identify first publications.
    for await (const { data } of github.paginate.iterator(runsRoute, { ...runsOptions, head_sha: context.sha })) {
      if (data.some(run =>
        run.id !== context.runId && ["push", "workflow_dispatch"].includes(run.event)
      )) {
        recovery = true;
        break;
      }
    }
  }
  if (recovery) {
    const status = await deploymentStatus();
    if (!RETRYABLE.has(status) && !(runAttempt === 1 && status === "succeed")) {
      throw new Error(`Refusing Pages retry with status ${JSON.stringify(status)}. ` +
        "Require a confirmed terminal state; unknown or active states cannot be retried, " +
        "and a successful deployment does not need a failed-job retry.");
    }
  }
  core.info(`Pages preflight passed for run ${context.runId}, attempt ${runAttempt}, SHA ${context.sha}`);
};
