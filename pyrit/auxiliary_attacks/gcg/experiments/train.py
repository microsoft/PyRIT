# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import time
from typing import Any, Union

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
    def __init__(self) -> None:
        if mp.get_start_method(allow_none=True) != "spawn":
            mp.set_start_method("spawn")

    def generate_suffix(
        self,
        *,
        token: str = "",
        tokenizer_paths: list[str] = [],
        model_name: str = "",
        model_paths: list[str] = [],
        conversation_templates: list[str] = [],
        result_prefix: str = "",
        train_data: str = "",
        control_init: str = "! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !",
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
        devices: list[str] = ["cuda:0"],
        model_kwargs: list[dict[str, Any]] = [{"low_cpu_mem_usage": True, "use_cache": False}],
        tokenizer_kwargs: list[dict[str, Any]] = [{"use_fast": False}],
        n_test_data: int = 0,
        test_data: str = "",
        lr: float = 0.01,
        topk: int = 256,
        temp: int = 1,
        filter_cand: bool = True,
        gbda_deterministic: bool = True,
        logfile: str = "",
        random_seed: int = 42,
    ) -> None:
        params = config_dict.ConfigDict()
        params.result_prefix = result_prefix
        params.train_data = train_data
        params.control_init = control_init
        params.n_train_data = n_train_data
        params.n_steps = n_steps
        params.test_steps = test_steps
        params.batch_size = batch_size
        params.transfer = transfer
        params.target_weight = target_weight
        params.control_weight = control_weight
        params.progressive_goals = progressive_goals
        params.progressive_models = progressive_models
        params.anneal = anneal
        params.incr_control = incr_control
        params.stop_on_success = stop_on_success
        params.verbose = verbose
        params.allow_non_ascii = allow_non_ascii
        params.num_train_models = num_train_models
        params.tokenizer_paths = tokenizer_paths
        params.tokenizer_kwargs = tokenizer_kwargs
        params.model_name = model_name
        params.model_paths = model_paths
        params.model_kwargs = model_kwargs
        params.conversation_templates = conversation_templates
        params.devices = devices
        params.n_test_data = n_test_data
        params.test_data = test_data
        params.lr = lr
        params.topk = topk
        params.temp = temp
        params.filter_cand = filter_cand
        params.gbda_deterministic = gbda_deterministic
        params.token = token
        params.logfile = logfile
        params.random_seed = random_seed
        logger.info(f"Parameters: {params}")

        # Start mlflow logging
        mlflow.start_run()
        log_gpu_memory(step=0)
        log_params(params=params)

        train_goals, train_targets, test_goals, test_targets = get_goals_and_targets(params)
        log_train_goals(train_goals=train_goals)

        def process_fn(s: str) -> str:
            return s.replace("Sure, h", "H")

        def process_fn2(s: str) -> str:
            return s.replace("Sure, here is", "Sure, here's")

        train_targets = [process_fn(t) if np.random.random() < 0.5 else process_fn2(t) for t in train_targets]
        test_targets = [process_fn(t) if np.random.random() < 0.5 else process_fn2(t) for t in test_targets]

        workers, test_workers = get_workers(params)
        managers = {
            "AP": attack_lib.GCGAttackPrompt,
            "PM": attack_lib.GCGPromptManager,
            "MPA": attack_lib.GCGMultiPromptAttack,
        }

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        attack: Union[ProgressiveMultiPromptAttack, IndividualPromptAttack]
        if params.transfer:
            attack = ProgressiveMultiPromptAttack(
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
                mpa_lr=params.lr,
                mpa_batch_size=params.batch_size,
                mpa_n_steps=params.n_steps,
            )
        else:
            attack = IndividualPromptAttack(
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
                mpa_lr=params.lr,
                mpa_batch_size=params.batch_size,
                mpa_n_steps=params.n_steps,
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
