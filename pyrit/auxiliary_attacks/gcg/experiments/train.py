# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import time
from typing import Any, Optional, Union

import mlflow
import numpy as np
import torch.multiprocessing as mp
from ml_collections import config_dict

import pyrit.auxiliary_attacks.gcg.attack.gcg.gcg_attack as attack_lib
from pyrit.auxiliary_attacks.gcg.attack.base.attack_manager import (
    IndividualPromptAttack,
    ProgressiveMultiPromptAttack,
    get_goals_and_targets,
    get_workers,
)
from pyrit.auxiliary_attacks.gcg.experiments.log import (
    log_gpu_memory,
    log_params,
    log_train_goals,
)

logger = logging.getLogger(__name__)


class GreedyCoordinateGradientAdversarialSuffixGenerator:
    """Generates adversarial suffixes using the Greedy Coordinate Gradient (GCG) algorithm."""

    _DEFAULT_CONTROL_INIT: str = "! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !"

    def __init__(self) -> None:
        if mp.get_start_method(allow_none=True) != "spawn":
            mp.set_start_method("spawn")

    def generate_suffix(
        self,
        *,
        token: str = "",
        tokenizer_paths: Optional[list[str]] = None,
        model_name: str = "",
        model_paths: Optional[list[str]] = None,
        conversation_templates: Optional[list[str]] = None,
        result_prefix: str = "",
        train_data: str = "",
        control_init: str = _DEFAULT_CONTROL_INIT,
        n_train_data: int = 50,
        n_steps: int = 500,
        test_steps: int = 50,
        batch_size: int = 512,
        transfer: bool = False,
        target_weight: float = 1.0,
        control_weight: float = 0.0,
        progressive_goals: bool = False,
        progressive_models: bool = False,
        anneal: bool = False,
        incr_control: bool = False,
        stop_on_success: bool = False,
        verbose: bool = True,
        allow_non_ascii: bool = False,
        num_train_models: int = 1,
        devices: Optional[list[str]] = None,
        model_kwargs: Optional[list[dict[str, Any]]] = None,
        tokenizer_kwargs: Optional[list[dict[str, Any]]] = None,
        n_test_data: int = 0,
        test_data: str = "",
        learning_rate: float = 0.01,
        topk: int = 256,
        temp: int = 1,
        filter_cand: bool = True,
        gbda_deterministic: bool = True,
        logfile: str = "",
        random_seed: int = 42,
    ) -> None:
        """
        Generate an adversarial suffix using the GCG algorithm.

        Args:
            token (str): HuggingFace authentication token.
            tokenizer_paths (Optional[list[str]]): Paths to tokenizer models.
            model_name (str): Name identifier for the model.
            model_paths (Optional[list[str]]): Paths to model weights.
            conversation_templates (Optional[list[str]]): Conversation template names.
            result_prefix (str): Prefix for result file paths.
            train_data (str): URL or path to training data CSV.
            control_init (str): Initial control string for optimization.
            n_train_data (int): Number of training examples. Defaults to 50.
            n_steps (int): Number of optimization steps. Defaults to 500.
            test_steps (int): Steps between test evaluations. Defaults to 50.
            batch_size (int): Batch size for candidate generation. Defaults to 512.
            transfer (bool): Whether to use transfer attack mode. Defaults to False.
            target_weight (float): Weight for target loss. Defaults to 1.0.
            control_weight (float): Weight for control loss. Defaults to 0.0.
            progressive_goals (bool): Whether to progressively add goals. Defaults to False.
            progressive_models (bool): Whether to progressively add models. Defaults to False.
            anneal (bool): Whether to use simulated annealing. Defaults to False.
            incr_control (bool): Whether to incrementally increase control weight. Defaults to False.
            stop_on_success (bool): Whether to stop on first success. Defaults to False.
            verbose (bool): Whether to print verbose output. Defaults to True.
            allow_non_ascii (bool): Whether to allow non-ASCII tokens. Defaults to False.
            num_train_models (int): Number of models to use for training. Defaults to 1.
            devices (Optional[list[str]]): CUDA devices to use.
            model_kwargs (Optional[list[dict[str, Any]]]): Additional kwargs per model.
            tokenizer_kwargs (Optional[list[dict[str, Any]]]): Additional kwargs per tokenizer.
            n_test_data (int): Number of test examples. Defaults to 0.
            test_data (str): URL or path to test data CSV. Defaults to "".
            learning_rate (float): Learning rate. Defaults to 0.01.
            topk (int): Number of top candidates to consider. Defaults to 256.
            temp (int): Temperature for sampling. Defaults to 1.
            filter_cand (bool): Whether to filter invalid candidates. Defaults to True.
            gbda_deterministic (bool): Whether to use deterministic mode. Defaults to True.
            logfile (str): Path to log file. Defaults to "".
            random_seed (int): Random seed for reproducibility. Defaults to 42.
        """
        if tokenizer_paths is None:
            tokenizer_paths = []
        if model_paths is None:
            model_paths = []
        if conversation_templates is None:
            conversation_templates = []
        if devices is None:
            devices = ["cuda:0"]
        if model_kwargs is None:
            model_kwargs = [{"low_cpu_mem_usage": True, "use_cache": False}]
        if tokenizer_kwargs is None:
            tokenizer_kwargs = [{"use_fast": False}]

        params = self._build_params(
            token=token,
            tokenizer_paths=tokenizer_paths,
            model_name=model_name,
            model_paths=model_paths,
            conversation_templates=conversation_templates,
            result_prefix=result_prefix,
            train_data=train_data,
            control_init=control_init,
            n_train_data=n_train_data,
            n_steps=n_steps,
            test_steps=test_steps,
            batch_size=batch_size,
            transfer=transfer,
            target_weight=target_weight,
            control_weight=control_weight,
            progressive_goals=progressive_goals,
            progressive_models=progressive_models,
            anneal=anneal,
            incr_control=incr_control,
            stop_on_success=stop_on_success,
            verbose=verbose,
            allow_non_ascii=allow_non_ascii,
            num_train_models=num_train_models,
            devices=devices,
            model_kwargs=model_kwargs,
            tokenizer_kwargs=tokenizer_kwargs,
            n_test_data=n_test_data,
            test_data=test_data,
            learning_rate=learning_rate,
            topk=topk,
            temp=temp,
            filter_cand=filter_cand,
            gbda_deterministic=gbda_deterministic,
            logfile=logfile,
            random_seed=random_seed,
        )
        logger.info(f"Parameters: {params}")

        # Start mlflow logging
        mlflow.start_run()
        log_gpu_memory(step=0)
        log_params(params=params)

        train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
        log_train_goals(train_goals=train_goals)

        train_targets, test_targets = self._apply_target_augmentation(
            train_targets=train_targets,
            test_targets=test_targets,
        )

        workers, test_workers = get_workers(params)
        managers = {
            "AP": attack_lib.GCGAttackPrompt,
            "PM": attack_lib.GCGPromptManager,
            "MPA": attack_lib.GCGMultiPromptAttack,
        }

        attack = self._create_attack(
            params=params,
            managers=managers,
            train_goals=train_goals,
            train_targets=train_targets,
            test_goals=test_goals,
            test_targets=test_targets,
            workers=workers,
            test_workers=test_workers,
        )
        attack.run(
            n_steps=params.n_steps,
            batch_size=params.batch_size,
            topk=params.topk,
            temp=params.temp,
            target_weight=params.target_weight,
            control_weight=params.control_weight,
            test_steps=getattr(params, "test_steps", 1),
            anneal=params.anneal,
            incr_control=params.incr_control,
            stop_on_success=params.stop_on_success,
            verbose=params.verbose,
            filter_cand=params.filter_cand,
            allow_non_ascii=params.allow_non_ascii,
        )

        for worker in workers + test_workers:
            worker.stop()

    @staticmethod
    def _build_params(**kwargs: Any) -> config_dict.ConfigDict:
        """
        Build a ConfigDict from keyword arguments.

        Returns:
            config_dict.ConfigDict: The populated configuration object.
        """
        params = config_dict.ConfigDict()
        for key, value in kwargs.items():
            setattr(params, key, value)
        return params

    @staticmethod
    def _apply_target_augmentation(
        *,
        train_targets: list[str],
        test_targets: list[str],
    ) -> tuple[list[str], list[str]]:
        """
        Apply random augmentation to target strings.

        Randomly replaces "Sure, h" with "H" or "Sure, here is" with "Sure, here's"
        for each target string. This adds diversity to the optimization targets.

        Args:
            train_targets (list[str]): Training target strings.
            test_targets (list[str]): Test target strings.

        Returns:
            tuple[list[str], list[str]]: Augmented training and test targets.
        """

        def process_fn(s: str) -> str:
            return s.replace("Sure, h", "H")

        def process_fn2(s: str) -> str:
            return s.replace("Sure, here is", "Sure, here's")

        train_targets = [process_fn(t) if np.random.random() < 0.5 else process_fn2(t) for t in train_targets]
        test_targets = [process_fn(t) if np.random.random() < 0.5 else process_fn2(t) for t in test_targets]
        return train_targets, test_targets

    @staticmethod
    def _create_attack(
        *,
        params: config_dict.ConfigDict,
        managers: dict[str, Any],
        train_goals: list[str],
        train_targets: list[str],
        test_goals: list[str],
        test_targets: list[str],
        workers: list[Any],
        test_workers: list[Any],
    ) -> Union[ProgressiveMultiPromptAttack, IndividualPromptAttack]:
        """
        Create the appropriate attack object based on configuration.

        Args:
            params (config_dict.ConfigDict): Training configuration.
            managers (dict[str, Any]): Dictionary mapping manager keys to GCG classes.
            train_goals (list[str]): Training goal strings.
            train_targets (list[str]): Training target strings.
            test_goals (list[str]): Test goal strings.
            test_targets (list[str]): Test target strings.
            workers (list[Any]): Training model workers.
            test_workers (list[Any]): Test model workers.

        Returns:
            Union[ProgressiveMultiPromptAttack, IndividualPromptAttack]: The configured attack.
        """
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        if params.transfer:
            return ProgressiveMultiPromptAttack(
                train_goals,
                train_targets,
                workers,
                progressive_models=params.progressive_models,
                progressive_goals=params.progressive_goals,
                control_init=params.control_init,
                logfile=f"{params.result_prefix}_{timestamp}.json",
                managers=managers,
                test_goals=test_goals,
                test_targets=test_targets,
                test_workers=test_workers,
                mpa_deterministic=params.gbda_deterministic,
                mpa_lr=params.learning_rate,
                mpa_batch_size=params.batch_size,
                mpa_n_steps=params.n_steps,
            )
        return IndividualPromptAttack(
            train_goals,
            train_targets,
            workers,
            control_init=params.control_init,
            logfile=f"{params.result_prefix}_{timestamp}.json",
            managers=managers,
            test_goals=getattr(params, "test_goals", []),
            test_targets=getattr(params, "test_targets", []),
            test_workers=test_workers,
            mpa_deterministic=params.gbda_deterministic,
            mpa_lr=params.learning_rate,
            mpa_batch_size=params.batch_size,
            mpa_n_steps=params.n_steps,
        )
