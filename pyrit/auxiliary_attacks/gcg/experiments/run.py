# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import argparse
import os
from typing import Any, Dict, Union

import yaml
from train import GreedyCoordinateGradientAdversarialSuffixGenerator

from pyrit.setup.initialization import _load_environment_files


def _load_yaml_to_dict(config_path: str) -> dict[str, Any]:
    with open(config_path, "r") as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


MODEL_NAMES = ["mistral", "llama_2", "llama_3", "vicuna", "phi_3_mini"]
ALL_MODELS = "all_models"
MODEL_PARAM_OPTIONS = MODEL_NAMES + [ALL_MODELS]


def run_trainer(*, model_name: str, setup: str = "single", **extra_config_parameters: Any) -> None:
    """
    Trains and generates adversarial suffix - single model single prompt.

    Args:
        model_name (str): The name of the model, currently supports:
            "mistral", "llama_2", "llama_3", "vicuna", "phi_3_mini", "all_models"
        setup (str): Identifier for the setup, currently supporst
            - "single": one prompt one model
            - "multiple": multiple prompts one model or multiple prompts multiple models

    """
    if model_name not in MODEL_NAMES:
        raise ValueError(
            "Model name not supported. Currently supports 'mistral', 'llama_2', 'llama_3', 'vicuna', and 'phi_3_mini'"
        )

    _load_environment_files(env_files=None)
    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    if not hf_token:
        raise ValueError("Please set the HUGGINGFACE_TOKEN environment variable")
    runtime_config: Dict[str, Union[str, bool, Any]] = {
        "train_data": (
            "https://raw.githubusercontent.com/llm-attacks/llm-attacks/main/data/advbench/harmful_behaviors.csv"
        ),
        "result_prefix": f"results/individual_behaviors_{model_name}_gcg",
        "token": hf_token,
    }
    if setup != "single":
        runtime_config["progressive_goals"] = True
        runtime_config["stop_on_success"] = True
        config_name = "transfer"
    else:
        config_name = "individual"

    config = _load_yaml_to_dict(f"configs/{config_name}_{model_name}.yaml")

    config.update(runtime_config)
    config.update(extra_config_parameters)
    config["model_name"] = model_name

    trainer = GreedyCoordinateGradientAdversarialSuffixGenerator()
    if not os.path.exists("results"):
        os.makedirs("results")

    trainer.generate_suffix(**config)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Script to run the adversarial suffix trainer")
    parser.add_argument("--model_name", type=str, help="The name of the model")
    parser.add_argument(
        "--setup",
        type=str,
        default="multiple",
        help="'single' or 'multiple' prompts. Multiple optimizes jointly over all prompts while \
            single optimizes separate suffixes for each prompt.",
    )
    parser.add_argument("--n_train_data", type=int, help="Number of training data")
    parser.add_argument("--n_test_data", type=int, help="Number of test data")
    parser.add_argument("--n_steps", type=int, default=100, help="Number of steps")
    parser.add_argument("--batch_size", type=int, default=512, help="Batch size")
    parser.add_argument("--random_seed", type=int, default=None, help="Random seed")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    run_trainer(
        model_name=args.model_name,
        num_train_models=len(MODEL_NAMES) if args.model_name == ALL_MODELS else 1,
        setup=args.setup,
        n_train_data=args.n_train_data,
        n_test_data=args.n_test_data,
        n_steps=args.n_steps,
        batch_size=args.batch_size,
        test_steps=1,
        random_seed=args.random_seed,
    )
