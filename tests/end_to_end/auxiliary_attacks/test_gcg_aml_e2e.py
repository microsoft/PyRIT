# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""End-to-end test for the GCG Azure ML pipeline.

Executes `doc/code/auxiliary_attacks/1_gcg_azure_ml.py` directly as a Python
script (the jupytext percent-format `# %%` markers are plain comments, so the
file is valid Python). After the notebook submits the AML job, this test
polls until the job reaches a terminal state and asserts success.

Running the notebook itself keeps the submission logic in one place: the
tutorial people read is the same code we test. Anything the user can do
manually with the notebook, this test verifies works end-to-end.

Skipped unless `RUN_ALL_TESTS=true`. Per-test skip also applies when the
required Azure ML or HuggingFace credentials are missing, since this submits
real (paid) compute. On test failure or interruption, the submitted job is
cancelled so it does not continue burning compute.

Required environment variables when `RUN_ALL_TESTS=true`:
  - AZURE_ML_SUBSCRIPTION_ID
  - AZURE_ML_RESOURCE_GROUP
  - AZURE_ML_WORKSPACE_NAME
  - HUGGINGFACE_TOKEN  (must have access to meta-llama/Llama-2-7b-chat-hf)

Optional:
  - GCG_E2E_MAX_WAIT_SECONDS  (defaults to 5400 -- 90 minutes)
"""

import contextlib
import os
import runpy
import time
from pathlib import Path

import pytest

# Skip the entire module unless RUN_ALL_TESTS=true; this test submits real
# paid Azure ML compute so it should never run in default CI.
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ALL_TESTS", "").lower() != "true",
    reason="RUN_ALL_TESTS is not set to true",
)

# Heavy imports deferred until skip check passes
pytest.importorskip("azure.ai.ml", reason="azure-ai-ml not installed")
pytest.importorskip("azure.identity", reason="azure-identity not installed")

from pyrit.common.path import HOME_PATH  # noqa: E402
from pyrit.setup.initialization import _load_environment_files  # noqa: E402

_REQUIRED_ENV_VARS = (
    "AZURE_ML_SUBSCRIPTION_ID",
    "AZURE_ML_RESOURCE_GROUP",
    "AZURE_ML_WORKSPACE_NAME",
    "HUGGINGFACE_TOKEN",
)
_NOTEBOOK_PATH = Path(HOME_PATH) / "doc" / "code" / "auxiliary_attacks" / "1_gcg_azure_ml.py"
_DEFAULT_MAX_WAIT_SECONDS = 5400  # 90 minutes
_POLL_INTERVAL_SECONDS = 30
_TERMINAL_STATES = {"Completed", "Failed", "Canceled", "CancelRequested"}


@pytest.mark.timeout(_DEFAULT_MAX_WAIT_SECONDS + 600)
def test_gcg_aml_notebook_runs_to_completion() -> None:
    """Execute the AML notebook end-to-end and verify the submitted job completes.

    The notebook is the single source of truth for how a GCG job is submitted
    to Azure ML. This test loads it via runpy, extracts the submitted job +
    MLClient from its namespace, then polls until the job reaches a terminal
    state and asserts ``Completed``.
    """
    _load_environment_files(env_files=None, silent=True)
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing required env vars for GCG AML e2e test: {', '.join(missing)}")

    max_wait = int(os.environ.get("GCG_E2E_MAX_WAIT_SECONDS", _DEFAULT_MAX_WAIT_SECONDS))

    notebook_globals = runpy.run_path(str(_NOTEBOOK_PATH), run_name="__main__")
    submitted_job = notebook_globals["returned_job"]
    ml_client = notebook_globals["ml_client"]
    job_name = submitted_job.name

    final_status: str | None = None
    status: str | None = None
    try:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            status = ml_client.jobs.get(job_name).status
            if status in _TERMINAL_STATES:
                final_status = status
                break
            time.sleep(_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail(
                f"GCG job '{job_name}' did not reach a terminal state within "
                f"{max_wait}s (last status: {status!r}). Studio URL: {submitted_job.studio_url}"
            )

        assert final_status == "Completed", (
            f"GCG job '{job_name}' finished with status {final_status!r}, expected 'Completed'. "
            f"Studio URL: {submitted_job.studio_url}"
        )
    finally:
        # Always try to cancel a non-terminal job so we never leak paid compute
        # (e.g., if pytest is interrupted or the assertion fires before a
        # terminal state is reached).
        if final_status is None or final_status not in _TERMINAL_STATES:
            with contextlib.suppress(Exception):
                ml_client.jobs.begin_cancel(job_name)
