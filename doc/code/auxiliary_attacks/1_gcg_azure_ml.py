# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 1. Generating GCG Suffixes Using Azure Machine Learning

# %% [markdown]
# > ⚠️ **Experimental module.** `pyrit.auxiliary_attacks` is experimental: its
# > APIs may change in any release without a deprecation cycle. Importing the
# > package below emits a `pyrit.exceptions.ExperimentalWarning`. Pin pyrit to
# > a specific version if you depend on it. To silence the warning:
# >
# > ```python
# > import warnings
# > from pyrit.exceptions import ExperimentalWarning
# > warnings.filterwarnings("ignore", category=ExperimentalWarning)
# > ```

# %% [markdown]
# This notebook shows how to generate GCG [@zou2023gcg] suffixes using Azure Machine Learning (AML), which consists of three main steps:
# 1. Connect to an Azure Machine Learning (AML) workspace.
# 2. Create AML Environment with the Python dependencies.
# 3. Submit a training job to AML.

# %% [markdown]
# ## Connect to Azure Machine Learning Workspace

# %% [markdown]
# The [workspace](https://docs.microsoft.com/en-us/azure/machine-learning/concept-workspace) is the top-level resource for Azure Machine Learning (AML), providing a centralized place to work with all the artifacts you create when using AML. In this section, we will connect to the workspace in which the job will be run.
#
# To connect to a workspace, we need identifier parameters - a subscription, resource group and workspace name. We will use these details in the `MLClient` from `azure.ai.ml` to get a handle to the required AML workspace. We use the [default Azure authentication](https://docs.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential?view=azure-python) for this tutorial.

# %%
import os

from pyrit.setup.initialization import _load_environment_files

_load_environment_files(env_files=None)

subscription_id = os.environ.get("AZURE_ML_SUBSCRIPTION_ID")
resource_group = os.environ.get("AZURE_ML_RESOURCE_GROUP")
workspace = os.environ.get("AZURE_ML_WORKSPACE_NAME")
print(workspace)

# %% [markdown]
# The Azure ML SDK emits a fair amount of telemetry to stderr that looks
# alarming but is benign: every operation logs an `ActivityCompleted: ...
# HowEnded=Failure` line for any expected `UserError` (such as
# `create_or_update` finding the environment already at the latest version),
# and every preview / experimental class prints a one-line warning. Quiet
# all of it so the rest of the notebook output stays focused on what
# actually matters.

# %%
import logging
import warnings

logging.getLogger("azure.ai.ml").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module=r"azure\.ai\.ml.*")

# %%
from azure.ai.ml import MLClient
from azure.identity import AzureCliCredential

ml_client = MLClient(AzureCliCredential(), subscription_id, resource_group, workspace)

# %% [markdown]
# ## Create AML Environment

# %% [markdown]
# To install the dependencies needed to run GCG, we create an AML environment from a
# [Dockerfile](../../../pyrit/auxiliary_attacks/gcg/src/Dockerfile). The Dockerfile uses
# an NVIDIA CUDA base image with Python 3.11 and installs PyRIT with the `gcg` extra.

# %%
from pathlib import Path

from azure.ai.ml.entities import BuildContext, Environment

from pyrit.common.path import HOME_PATH

# Configure the AML environment — build context is the repo root so the Dockerfile
# can COPY pyproject.toml and pyrit/ for pip install -e ".[gcg]"
env_docker_context = Environment(
    build=BuildContext(
        path=Path(HOME_PATH),
        dockerfile_path="pyrit/auxiliary_attacks/gcg/src/Dockerfile",
    ),
    name="pyrit-gcg",
    description="PyRIT GCG environment: CUDA 12.1 + Python 3.11 + pip install -e .[gcg]",
    tags={"Owner": os.environ.get("USER", "unknown")},
)

ml_client.environments.create_or_update(env_docker_context)

# %% [markdown]
# ## Submit Training Job to AML

# %% [markdown]
# Finally, we configure the command to run the GCG algorithm. The entry point is
# [`pyrit.auxiliary_attacks.gcg.experiments.run`](../../../pyrit/auxiliary_attacks/gcg/experiments/run.py),
# invoked as a module so the uploaded code snapshot takes priority over the
# Docker-installed package (Python's `-m` flag puts the cwd at the front of `sys.path`).
#
# The new public API takes a typed ``GCGConfig`` (strategy) and a separate
# ``GCGDataConfig`` (CSV paths/counts). We build both locally with whatever
# overrides we want, serialize each into a JSON file the AML job can read as
# an input, and ship those paths through the job command. Defaults come from
# the dataclasses in ``pyrit.auxiliary_attacks.gcg.config``; goals and targets
# flow into ``GCGGenerator.execute_async`` at runtime, not through the config.
#
# We also have to specify a GPU compute target. In our experience, a GPU instance with
# at least 24GB of vRAM is required (e.g., Standard_NC24ads_A100_v4).
#
# Depending on the compute instance you use, you may encounter "out of memory" errors.
# In this case, we recommend training on a smaller model or lowering ``data.n_train_data``
# or ``algorithm.batch_size``.

# %%
import tempfile

from pyrit.auxiliary_attacks.gcg import (
    GCGAlgorithmConfig,
    GCGConfig,
    GCGDataConfig,
    GCGModelConfig,
    GCGOutputConfig,
)

config = GCGConfig(
    models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
    algorithm=GCGAlgorithmConfig(n_steps=5, batch_size=64, test_steps=1),
    output=GCGOutputConfig(result_prefix="gcg_suffix"),
)
data_config = GCGDataConfig(
    train_data=("https://raw.githubusercontent.com/llm-attacks/llm-attacks/main/data/advbench/harmful_behaviors.csv"),
    n_train_data=5,
    n_test_data=0,
)

# Write the configs into a tempdir so AML can mount them as separate job inputs.
config_dir = Path(tempfile.mkdtemp(prefix="gcg-aml-config-"))
config_path = config_dir / "config.json"
data_path = config_dir / "data.json"
config.to_json_file(config_path)
data_config.to_json_file(data_path)

# %%
from azure.ai.ml import Input, Output, command

job = command(
    code=Path(HOME_PATH),
    command=(
        "python -m pyrit.auxiliary_attacks.gcg.experiments.run"
        " --config ${{inputs.config}}"
        " --data ${{inputs.data}}"
        " --output-dir ${{outputs.results}}"
    ),
    inputs={
        "config": Input(type="uri_file", path=str(config_path)),
        "data": Input(type="uri_file", path=str(data_path)),
    },
    outputs={"results": Output(type="uri_folder")},
    environment=f"{env_docker_context.name}:{env_docker_context.version}",
    environment_variables={"HUGGINGFACE_TOKEN": os.environ["HUGGINGFACE_TOKEN"]},
    compute="gcg-gpu-a100",
    display_name="gcg_suffix_generation",
    description="Generate adversarial suffixes using GCG on Llama-2.",
    tags={"Owner": os.environ.get("USER", "unknown")},
)

# %%
returned_job = ml_client.create_or_update(job)
print(f"Job: {returned_job.name}")
print(f"Status: {returned_job.status}")
print(f"Studio URL: {returned_job.studio_url}")

# %% [markdown]
# ## Wait for the Job to Complete and Inspect the Generated Suffix
#
# The next cell polls the job until it reaches a terminal state (~20-30
# minutes for the small 5-step baseline above), then downloads the named
# `results` output and prints the final suffix. The runner writes its
# result file as `<result_prefix>_<timestamp>.json` (with `result_prefix`
# coming from the `GCGConfig` we built above, plus the AML output mount
# prepended by `--output-dir`). For our config, that resolves to
# `gcg_suffix_<timestamp>.json` under
# `<download_dir>/named-outputs/results/` once we download. The
# `controls` array in that file contains one entry per training step, and
# the last entry is the final adversarial suffix that, appended to the user
# prompt, was optimized to elicit the target response.

# %%
import json
import tempfile
import time
from pathlib import Path

_TERMINAL_STATES = {"Completed", "Failed", "Canceled", "CancelRequested"}

last_status = None
while True:
    current_status = ml_client.jobs.get(returned_job.name).status
    if current_status != last_status:
        print(f"Job status: {current_status}", flush=True)
        last_status = current_status
    if current_status in _TERMINAL_STATES:
        break
    time.sleep(60)

assert current_status == "Completed", f"Job did not complete successfully: {current_status}"

download_dir = Path(tempfile.mkdtemp(prefix="gcg-aml-"))
ml_client.jobs.download(name=returned_job.name, download_path=str(download_dir), all=True)

result_files = list(download_dir.rglob("gcg_suffix_*.json"))
if not result_files:
    print(f"No GCG result file found under {download_dir}. Files captured:")
    for p in sorted(download_dir.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(download_dir)}")
    raise FileNotFoundError("Result JSON not in downloaded artifacts")

result_file = result_files[0]
with open(result_file) as f:
    log = json.load(f)

final_suffix = log["controls"][-1] if log["controls"] else None
final_loss = log["losses"][-1] if log["losses"] else None

print(f"Result file: {result_file.name}")
print(f"Steps run: {len(log['controls'])}")
print(f"Final loss: {final_loss}")
print(f"Generated suffix: {final_suffix!r}")
