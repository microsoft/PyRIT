# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import subprocess as sp
import time
from typing import Any, Optional

import mlflow

logger = logging.getLogger(__name__)

_DEFAULT_PARAM_KEYS: list[str] = [
    "model_name",
    "transfer",
    "n_train_data",
    "n_test_data",
    "n_steps",
    "batch_size",
]


def log_params(
    *,
    params: Any,
    param_keys: Optional[list[str]] = None,
) -> None:
    """
    Log selected parameters to MLflow.

    Args:
        params (Any): A config object with a `to_dict()` method containing all parameters.
        param_keys (Optional[list[str]]): Keys to extract and log. Defaults to standard GCG training keys.
    """
    if param_keys is None:
        param_keys = _DEFAULT_PARAM_KEYS
    mlflow_params = {key: params.to_dict()[key] for key in param_keys}
    mlflow.log_params(mlflow_params)


def log_train_goals(*, train_goals: list[str]) -> None:
    """
    Log training goals as a text artifact to MLflow.

    Args:
        train_goals (list[str]): The list of training goal strings to log.
    """
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    train_goals_str = "\n".join(train_goals)
    mlflow.log_text(train_goals_str, f"train_goals_{timestamp}.txt")


def get_gpu_memory() -> dict[str, int]:
    """
    Query free GPU memory via nvidia-smi.

    Returns:
        dict[str, int]: Mapping of GPU identifiers to free memory in MiB.
    """
    command = "nvidia-smi --query-gpu=memory.free --format=csv"
    memory_free_info = sp.check_output(command.split()).decode("ascii").split("\n")[:-1][1:]
    memory_free_values = {f"gpu{i + 1}_free_memory": int(val.split()[0]) for i, val in enumerate(memory_free_info)}
    memory_free_string = ", ".join(f"{val} MiB" for val in memory_free_values.values())
    logger.info(f"Free GPU memory:\n{memory_free_string}")
    return memory_free_values


def log_gpu_memory(*, step: int, synchronous: bool = False) -> None:
    """
    Log free GPU memory metrics to MLflow.

    Args:
        step (int): The current training step number.
        synchronous (bool): Whether to log synchronously. Defaults to False.
    """
    memory_values = get_gpu_memory()
    for gpu, val in memory_values.items():
        mlflow.log_metric(gpu, val, step=step, synchronous=synchronous)


def log_loss(*, step: int, loss: float, synchronous: bool = False) -> None:
    """
    Log training loss to MLflow.

    Args:
        step (int): The current training step number.
        loss (float): The loss value to log.
        synchronous (bool): Whether to log synchronously. Defaults to False.
    """
    mlflow.log_metric("loss", loss, step=step, synchronous=synchronous)


def log_table_summary(*, losses: list[float], controls: list[str], n_steps: int) -> None:
    """
    Log a summary table of losses and controls to MLflow.

    Args:
        losses (list[float]): Loss values for each step.
        controls (list[str]): Control strings for each step.
        n_steps (int): Total number of steps.
    """
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    mlflow.log_table(
        {
            "step": [i + 1 for i in range(n_steps)],
            "loss": losses,
            "control": controls,
        },
        artifact_file=f"gcg_results_{timestamp}.json",
    )
