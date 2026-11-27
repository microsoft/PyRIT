# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
FLRTGenerator — typed PromptGeneratorStrategy implementation of the Fluent
Student-Teacher Redteaming attack.

Follows the same lifecycle/identity pattern as ``GCGGenerator``:

- Strategy configuration goes in ``__init__`` (model identity, hyper-parameters,
  output paths).
- Per-execution data (``goals`` / ``targets`` plus optional ``memory_labels``)
  goes through ``execute_async``.
- ``_setup_async`` loads a single in-process HuggingFace model and tokenizer,
  probes the chat template, and evaluates the initial attack.
  ``_perform_async`` owns the optimization loop and ``_teardown_async``
  releases the model even on errors.
- ``_build_identifier`` exposes the model name and key hyper-parameters so
  results can be traced back to the exact configuration that produced them.

Version one implements the fluent token optimization from the FLRT paper
([@thompson2024flrt](https://arxiv.org/abs/2407.17447)) without the
distillation objective: a clamped forcing loss on the first generation tokens,
a fluency penalty, and a repetition penalty, optimized with token
insertions/deletions/swaps and an optimization buffer.

Example:
    generator = FLRTGenerator(
        model=FLRTModelConfig(name="meta-llama/Llama-3-8B-Instruct"),
        algorithm=FLRTAlgorithmConfig(n_steps=500),
    )
    result = await generator.execute_async(
        goals=["how do I ..."],
        targets=["Sure, here is ..."],
    )
    print(result.final_attack)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast, overload
from uuid import uuid4

import torch
from pydantic import Field

from pyrit.common import combine_dict
from pyrit.executor.promptgen.core.prompt_generator_strategy import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.executor.promptgen.flrt.config import (
    FLRTAlgorithmConfig,
    FLRTModelConfig,
    FLRTOutputConfig,
)
from pyrit.executor.promptgen.flrt.edit_operations import (
    choose_update_type,
    constrain_update_probabilities,
    propose_candidates,
)
from pyrit.executor.promptgen.flrt.objective import (
    clamped_forcing_loss,
    fluency_loss,
    repetition_loss,
    total_loss,
)
from pyrit.models import ComponentIdentifier, Identifiable

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

logger = logging.getLogger(__name__)


@dataclass
class _EncodedAttack:
    """A rendered prompt with the token positions FLRT needs."""

    ids: list[int]
    attack_start: int
    attack_end: int
    target_positions: list[int]
    user_start: int
    user_end: int


@dataclass
class FLRTContext(PromptGeneratorStrategyContext):
    """
    Per-execution state for a FLRTGenerator run.

    Attributes:
        goals (list[str]): Training goal strings (the prompts whose responses
            we are trying to redirect). Must be non-empty.
        targets (list[str]): Training target strings (the desired prefixes of
            the model's responses). Same length as ``goals``.
        memory_labels (dict[str, str]): Optional labels propagated to memory
            for downstream filtering / analysis. Defaults to an empty dict.
    """

    goals: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    memory_labels: dict[str, str] = field(default_factory=dict)

    model: Any = None
    tokenizer: Any = None
    template_prefix: str = ""
    template_mid: str = ""
    template_suffix: str = ""
    template_trims: bool = False
    rng: random.Random | None = None
    torch_rng: torch.Generator | None = None
    buffer: list[tuple[str, float]] = field(default_factory=list)
    best_attack: str = ""
    best_loss: float = math.inf
    logfile_path: str | None = None
    attack_history: list[str] = field(default_factory=list)
    loss_history: list[float] = field(default_factory=list)


class FLRTResult(PromptGeneratorStrategyResult):
    """
    Result of one FLRTGenerator run.

    Attributes:
        final_attack (str): The optimized adversarial attack string. Empty
            string means the run produced no candidates (degenerate config).
        final_loss (float): Loss of the best attack seen. ``float('nan')`` if
            no losses were recorded.
        step_count (int): Number of optimization steps actually executed.
        loss_history (list[float]): Best loss seen after each step.
        attack_history (list[str]): Best attack seen after each step.
        log_path (str | None): Filesystem path of the JSON log written during
            the run, or ``None`` if logging was disabled.
        memory_labels (dict[str, str]): Echo of the labels passed in via the
            context, kept on the result for traceability.
    """

    final_attack: str = ""
    final_loss: float = float("nan")
    step_count: int = 0
    loss_history: list[float] = Field(default_factory=list)
    attack_history: list[str] = Field(default_factory=list)
    log_path: str | None = None
    memory_labels: dict[str, str] = Field(default_factory=dict)


class FLRTGenerator(
    PromptGeneratorStrategy[FLRTContext, FLRTResult],
    Identifiable,
):
    """
    Fluent Student-Teacher Redteaming adversarial prompt generator.

    Optimizes an attack string that, when appended to ``goals``, elicits
    ``targets`` from the configured HuggingFace model while keeping the
    attack's perplexity low and its token repetition penalized, so the result
    reads closer to natural language than GCG suffixes. See the FLRT paper
    ([@thompson2024flrt](https://arxiv.org/abs/2407.17447)) for the algorithm.
    """

    def __init__(
        self,
        *,
        model: FLRTModelConfig,
        algorithm: FLRTAlgorithmConfig | None = None,
        output: FLRTOutputConfig | None = None,
        hf_token: str | None = None,
    ) -> None:
        """
        Initialize the FLRT generator.

        Args:
            model (FLRTModelConfig): The victim model the attack is optimized
                against. FLRT runs a single model in process.
            algorithm (FLRTAlgorithmConfig | None): Optimization
                hyper-parameters. ``None`` uses dataclass defaults.
            output (FLRTOutputConfig | None): Log/result file locations.
                ``None`` uses dataclass defaults.
            hf_token (str | None): HuggingFace authentication token. Defaults
                to ``None`` (the model loader falls back to whatever the
                environment provides).
        """
        super().__init__(logger=logger, context_type=FLRTContext)
        self._model_config = model
        self._algorithm = algorithm or FLRTAlgorithmConfig()
        self._output = output or FLRTOutputConfig()
        self._hf_token = hf_token

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build a behavioral identifier exposing model identity and hyperparameters.

        Returns:
            ComponentIdentifier: The generator's behavioral identifier.
        """
        return ComponentIdentifier.of(
            self,
            params={
                "model": self._model_config.name,
                "n_steps": self._algorithm.n_steps,
                "n_candidates": self._algorithm.n_candidates,
                "n_token_proposals": self._algorithm.n_token_proposals,
                "buffer_size": self._algorithm.buffer_size,
                "fluency_weight": self._algorithm.fluency_weight,
                "repetition_weight": self._algorithm.repetition_weight,
            },
        )

    def _validate_context(self, *, context: FLRTContext) -> None:
        """
        Validate goals and targets.

        Raises:
            ValueError: If goals or targets are empty or have mismatched length.
        """
        if not context.goals:
            raise ValueError("FLRTContext.goals must be non-empty.")
        if not context.targets:
            raise ValueError("FLRTContext.targets must be non-empty.")
        if len(context.goals) != len(context.targets):
            raise ValueError(f"goals/targets length mismatch: {len(context.goals)} vs {len(context.targets)}")

    async def _setup_async(self, *, context: FLRTContext) -> None:
        """
        Load the model and tokenizer, probe the chat template, and score the initial attack.

        Raises:
            ValueError: If the initial attack does not tokenize cleanly for every goal.
        """
        context.memory_labels = combine_dict({}, context.memory_labels)
        context.tokenizer, context.model = await asyncio.to_thread(self._load_model_and_tokenizer)
        context.template_prefix, context.template_mid, context.template_suffix = self._probe_template(
            tokenizer=context.tokenizer
        )
        context.template_trims = self._detect_template_trim(tokenizer=context.tokenizer)
        for goal, target in zip(context.goals, context.targets, strict=True):
            self._validate_template_roundtrip(tokenizer=context.tokenizer, goal=goal, target=target, context=context)

        context.rng = random.Random(self._algorithm.random_seed)
        context.torch_rng = torch.Generator(device=context.model.device)
        context.torch_rng.manual_seed(self._algorithm.random_seed)

        initial_losses, _ = await asyncio.to_thread(
            self._evaluate_attacks, context=context, attacks=[self._algorithm.initial_attack]
        )
        initial_loss = initial_losses[0]
        if math.isinf(initial_loss):
            raise ValueError("The initial attack does not tokenize cleanly in the chat template.")
        context.buffer = [(self._algorithm.initial_attack, initial_loss)]
        context.best_attack = self._algorithm.initial_attack
        context.best_loss = initial_loss
        context.logfile_path = self._build_logfile_path()

    async def _perform_async(self, *, context: FLRTContext) -> FLRTResult:
        """
        Run the FLRT optimization loop.

        Returns:
            FLRTResult: The completed optimization result.
        """
        start_time = time.time()
        for step in range(self._algorithm.n_steps):
            if self._algorithm.max_runtime_seconds and time.time() - start_time > self._algorithm.max_runtime_seconds:
                self._logger.info(f"Stopping after {step} steps: runtime limit reached.")
                break
            await asyncio.to_thread(self._step, context=context, step=step)
            await asyncio.to_thread(self._write_log, context=context)

        return await asyncio.to_thread(
            self._read_result, logfile_path=context.logfile_path, memory_labels=context.memory_labels
        )

    async def _teardown_async(self, *, context: FLRTContext) -> None:
        """Release the model. Runs even when _perform_async raises."""
        context.model = None
        context.tokenizer = None
        context.buffer = []
        if torch.cuda.is_available():
            await asyncio.to_thread(torch.cuda.empty_cache)

    def _step(self, *, context: FLRTContext, step: int) -> None:
        """
        Run one optimization step: mutate the buffer's best attack and retain the top candidates.

        Raises:
            ValueError: If no mutation can produce legal candidates.
        """
        assert context.rng is not None
        algorithm = self._algorithm
        probabilities = algorithm.early_update_probs if step < algorithm.schedule_step else algorithm.late_update_probs

        attack, parent_loss = context.buffer[0]
        goal_index = context.rng.randrange(len(context.goals))
        encoded = self._encode_attack(context=context, attack=attack, goal_index=goal_index)
        if encoded is None:
            raise ValueError(f"The current best attack no longer tokenizes cleanly for goal {goal_index}.")

        n_tokens = encoded.attack_end - encoded.attack_start
        constrained = constrain_update_probabilities(
            probabilities=probabilities,
            n_tokens=n_tokens,
            min_tokens=algorithm.min_tokens,
            max_tokens=algorithm.max_tokens,
        )
        update_type = choose_update_type(rng=context.rng, probabilities=constrained)

        if update_type == "delete":
            proposals: list[list[int]] = []
        else:
            proposals = self._token_proposals(context=context, encoded=encoded)

        attack_ids = encoded.ids[encoded.attack_start : encoded.attack_end]
        mutated = propose_candidates(
            token_ids=attack_ids,
            update_type=update_type,
            n_candidates=algorithm.n_candidates,
            token_proposals=proposals,
            rng=context.rng,
        )
        candidates = [self._decode_ids(context=context, ids=ids) for ids in mutated]

        losses, breakdowns = self._evaluate_attacks(context=context, attacks=candidates)
        finite = [(candidate, loss) for candidate, loss in zip(candidates, losses, strict=True) if math.isfinite(loss)]
        pool = [*context.buffer[1:], *finite]
        pool.sort(key=lambda entry: entry[1])
        if not pool:
            # Every candidate was illegal this step; keep the parent alive.
            pool = [(attack, parent_loss)]
        context.buffer = pool[: algorithm.buffer_size]

        best_index = min(range(len(losses)), key=lambda i: losses[i])
        if losses[best_index] < context.best_loss:
            context.best_attack = candidates[best_index]
            context.best_loss = losses[best_index]
        context.attack_history.append(context.best_attack)
        context.loss_history.append(context.best_loss)

        if self._output.verbose:
            forcing, fluency, repetition = breakdowns[best_index]
            self._logger.info(
                f"step {step} ({update_type}): loss={losses[best_index]:.3f} forcing={forcing:.3f} "
                f"fluency={fluency:.3f} perplexity={math.exp(min(fluency, 20.0)):.1f} "
                f"repetition={repetition:.3f} best={context.best_loss:.3f}"
            )

    def _token_proposals(self, *, context: FLRTContext, encoded: _EncodedAttack) -> list[list[int]]:
        """
        Sample token proposals from the victim model's distribution without replacement.

        Returns:
            list[list[int]]: ``len(attack) + 1`` rows of ``n_token_proposals``
                token ids. Row ``i`` proposes tokens for attack position ``i``;
                the final row proposes tokens to append.
        """
        assert context.torch_rng is not None
        input_ids = torch.tensor([encoded.ids], device=context.model.device)
        with torch.no_grad():
            logits = context.model(input_ids=input_ids).logits[0]
        rows = torch.softmax(logits[encoded.attack_start - 1 : encoded.attack_end].float(), dim=-1)

        special_ids = list(context.tokenizer.all_special_ids)
        if special_ids:
            rows[:, special_ids] = 0.0

        proposals = []
        for row in rows:
            nonzero = int((row > 0).sum().item())
            k = self._algorithm.n_token_proposals
            if nonzero >= k:
                sampled = torch.multinomial(row, k, replacement=False, generator=context.torch_rng)
            else:
                sampled = torch.multinomial(row, k, replacement=True, generator=context.torch_rng)
            proposals.append([int(i) for i in sampled.tolist()])
        return proposals

    def _evaluate_attacks(
        self, *, context: FLRTContext, attacks: list[str]
    ) -> tuple[list[float], list[tuple[float, float, float]]]:
        """
        Score candidate attacks with the combined FLRT loss, averaged over goals.

        Returns:
            tuple[list[float], list[tuple[float, float, float]]]: Per-candidate
                losses and ``(forcing, fluency, repetition)`` breakdowns.
        """
        entries: list[tuple[int, _EncodedAttack]] = []
        losses = [math.inf] * len(attacks)
        breakdowns = [(math.inf, math.inf, math.inf)] * len(attacks)
        for candidate_index, attack in enumerate(attacks):
            for goal_index in range(len(context.goals)):
                encoded = self._encode_attack(context=context, attack=attack, goal_index=goal_index)
                if encoded is not None:
                    entries.append((candidate_index, encoded))

        if not entries:
            return losses, breakdowns

        max_len = max(len(entry.ids) for _, entry in entries)
        pad_id = context.tokenizer.pad_token_id
        if pad_id is None:
            pad_id = context.tokenizer.eos_token_id
        input_ids = torch.full((len(entries), max_len), pad_id, dtype=torch.long)
        attention_mask = torch.zeros((len(entries), max_len), dtype=torch.long)
        for row, (_, entry) in enumerate(entries):
            input_ids[row, : len(entry.ids)] = torch.tensor(entry.ids)
            attention_mask[row, : len(entry.ids)] = 1
        input_ids = input_ids.to(context.model.device)
        attention_mask = attention_mask.to(context.model.device)

        with torch.no_grad():
            logits = context.model(input_ids=input_ids, attention_mask=attention_mask).logits.float()

        n_goals = len(context.goals)
        sums = torch.zeros(len(attacks))
        counts = [0] * len(attacks)
        term_sums = [[0.0, 0.0, 0.0] for _ in attacks]
        for row, (candidate_index, entry) in enumerate(entries):
            ids = torch.tensor(entry.ids, device=logits.device)
            seq_len = len(entry.ids)
            log_probs = torch.log_softmax(logits[row, :seq_len], dim=-1)

            positions = torch.arange(1, seq_len, device=logits.device)
            fluency_positions = positions[positions < entry.target_positions[0]]
            fluency = fluency_loss(log_probs=log_probs, token_ids=ids, positions=fluency_positions)

            target_positions = torch.tensor(entry.target_positions, device=logits.device)
            forcing = clamped_forcing_loss(
                log_probs=log_probs,
                token_ids=ids,
                target_positions=target_positions,
                floor=-math.log(self._algorithm.forcing_probability_threshold),
            )

            user_ids = ids[entry.user_start : entry.user_end]
            repetition = repetition_loss(token_ids=user_ids, exponent=self._algorithm.repetition_exponent)

            loss = total_loss(
                forcing=forcing,
                fluency=fluency,
                repetition=repetition,
                fluency_weight=self._algorithm.fluency_weight,
                repetition_weight=self._algorithm.repetition_weight,
            )
            sums[candidate_index] += loss.cpu()
            counts[candidate_index] += 1
            term_sums[candidate_index][0] += float(forcing)
            term_sums[candidate_index][1] += float(fluency)
            term_sums[candidate_index][2] += float(repetition)

        for candidate_index in range(len(attacks)):
            if counts[candidate_index] != n_goals:
                # A candidate that fails to tokenize cleanly for any goal is illegal.
                continue
            losses[candidate_index] = float(sums[candidate_index]) / n_goals
            forcing_mean, fluency_mean, repetition_mean = (term / n_goals for term in term_sums[candidate_index])
            breakdowns[candidate_index] = (forcing_mean, fluency_mean, repetition_mean)
        return losses, breakdowns

    def _encode_attack(self, *, context: FLRTContext, attack: str, goal_index: int) -> _EncodedAttack | None:
        """
        Render and tokenize one prompt, locating the token spans FLRT needs.

        Returns:
            _EncodedAttack | None: The encoded prompt, or ``None`` if the
                attack tokens merge across the user-content boundary or contain
                special tokens.
        """
        # A padded attack would be silently trimmed by templates that strip
        # message content, so the scored prompt must never differ from the one
        # the real template renders.
        if context.template_trims and attack != attack.strip():
            return None

        goal = context.goals[goal_index]
        target = context.targets[goal_index]
        # An empty attack contributes no trailing space, because templates that
        # trim message content (like Llama 3's) would otherwise eat it.
        content = f"{goal} {attack}" if attack else goal
        prompt = context.template_prefix + content + context.template_mid + target + context.template_suffix
        encoding = context.tokenizer(prompt, add_special_tokens=False, return_offsets_mapping=True)
        ids = cast("list[int]", encoding.input_ids)
        offsets = cast("list[tuple[int, int]]", encoding["offset_mapping"])

        user_start_char = len(context.template_prefix)
        # For an empty attack the insertion point sits right after the goal tokens.
        attack_start_char = user_start_char + len(goal) + (1 if attack else 0)
        attack_end_char = attack_start_char + len(attack)
        user_end_char = user_start_char + len(content)
        target_start_char = user_end_char + len(context.template_mid)
        target_end_char = target_start_char + len(target)

        target_indices = self._inside_token_indices(
            prompt=prompt, offsets=offsets, start=target_start_char, end=target_end_char
        )
        user_indices = self._inside_token_indices(
            prompt=prompt, offsets=offsets, start=user_start_char, end=user_end_char
        )
        if not target_indices or not user_indices:
            return None

        if attack:
            attack_indices = self._inside_token_indices(
                prompt=prompt, offsets=offsets, start=attack_start_char, end=attack_end_char
            )
            if not attack_indices:
                return None
            attack_start, attack_end = attack_indices[0], attack_indices[-1] + 1
        else:
            insert_at = next((i for i, (start, _) in enumerate(offsets) if start >= attack_start_char), len(offsets))
            attack_start = attack_end = insert_at

        if attack_start == 0 or attack_end >= len(ids) or target_indices[-1] >= len(ids):
            return None
        attack_span_ids = ids[attack_start:attack_end]
        if any(token_id in context.tokenizer.all_special_ids for token_id in attack_span_ids):
            return None

        return _EncodedAttack(
            ids=ids,
            attack_start=attack_start,
            attack_end=attack_end,
            target_positions=target_indices,
            user_start=user_indices[0],
            user_end=user_indices[-1] + 1,
        )

    @staticmethod
    def _inside_token_indices(*, prompt: str, offsets: list[tuple[int, int]], start: int, end: int) -> list[int]:
        """
        Token indices fully inside a character span, modulo surrounding whitespace.

        A token that drags non-whitespace content across the span boundary makes
        the span unlocatable and yields an empty list.

        Returns:
            list[int]: Indices of tokens inside the span.
        """
        indices = []
        for i, (token_start, token_end) in enumerate(offsets):
            if token_start >= token_end or token_start >= end or token_end <= start:
                continue
            overlap = prompt[max(start, token_start) : min(end, token_end)]
            outside = prompt[token_start:start] + prompt[end:token_end]
            if outside.strip() and overlap.strip():
                return []
            if not overlap.strip():
                continue
            indices.append(i)
        return indices

    def _decode_ids(self, *, context: FLRTContext, ids: list[int]) -> str:
        """
        Decode mutated attack ids back to a string, dropping a leading separator space.

        Returns:
            str: The decoded attack text.
        """
        text = cast("str", context.tokenizer.decode(ids, skip_special_tokens=False, clean_up_tokenization_spaces=False))
        return text.removeprefix(" ")

    def _probe_template(self, *, tokenizer: PreTrainedTokenizerBase) -> tuple[str, str, str]:
        """
        Split the chat template scaffold around the user and assistant content.

        Returns:
            tuple[str, str, str]: ``(prefix, mid, suffix)`` such that a full
                prompt is ``prefix + user_content + mid + target + suffix``.

        Raises:
            ValueError: If the template does not render each message exactly once.
        """
        user_marker = f"pyritflrtuser{uuid4().hex}"
        target_marker = f"pyritflrttarget{uuid4().hex}"
        messages = [
            {"role": "user", "content": user_marker},
            {"role": "assistant", "content": target_marker},
        ]
        probe = tokenizer.apply_chat_template(messages, tokenize=False)
        if not isinstance(probe, str) or probe.count(user_marker) != 1 or probe.count(target_marker) != 1:
            raise ValueError(
                "Cannot safely locate message content in the chat template. The template must render each "
                "message once and preserve its content, apart from surrounding whitespace."
            )
        prefix, rest = probe.split(user_marker)
        mid, suffix = rest.split(target_marker)
        return prefix, mid, suffix

    @staticmethod
    def _detect_template_trim(*, tokenizer: PreTrainedTokenizerBase) -> bool:
        """
        Probe whether the chat template trims whitespace from message content.

        Returns:
            bool: True if a marker padded with a space and a newline on both
                sides does not survive the template verbatim.
        """
        marker = f"pyritflrttrim{uuid4().hex}"
        padded = " \n" + marker + "\n "
        rendered = tokenizer.apply_chat_template(
            [
                {"role": "user", "content": padded},
                {"role": "assistant", "content": "x"},
            ],
            tokenize=False,
        )
        return not isinstance(rendered, str) or padded not in rendered

    def _validate_template_roundtrip(
        self, *, tokenizer: PreTrainedTokenizerBase, goal: str, target: str, context: FLRTContext
    ) -> None:
        """
        Verify the probed scaffold reproduces the real rendered conversation.

        Raises:
            ValueError: If the template transforms message content or does not
                tokenize into at least one target token.
        """
        initial = self._algorithm.initial_attack
        content = f"{goal} {initial}" if initial else goal
        expected = context.template_prefix + content + context.template_mid + target + context.template_suffix
        messages = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": target},
        ]
        rendered = tokenizer.apply_chat_template(messages, tokenize=False)
        if rendered != expected:
            raise ValueError(
                "The chat template transforms message content, so FLRT cannot track token positions. "
                "Use a chat-tuned model whose template preserves message content."
            )

    def _load_model_and_tokenizer(self) -> tuple[Any, Any]:
        """
        Load the victim tokenizer and model in process.

        Returns:
            tuple[Any, Any]: The loaded tokenizer and evaluation-mode model on the
                configured device.

        Raises:
            ValueError: If the tokenizer is not fast or has no chat template.
        """
        from transformers.models.auto.modeling_auto import AutoModelForCausalLM
        from transformers.models.auto.tokenization_auto import AutoTokenizer

        config = self._model_config
        tokenizer: Any = AutoTokenizer.from_pretrained(config.name, token=self._hf_token, **config.tokenizer_kwargs)
        if not tokenizer.is_fast:
            raise ValueError("FLRT requires a fast tokenizer (use_fast=True) for character-to-token alignment.")
        if tokenizer.chat_template is None:
            raise ValueError(
                f"Tokenizer {config.name!r} has no chat_template configured. FLRT uses "
                "tokenizer.apply_chat_template() to render prompts; without a chat template the attack "
                "cannot be set up. Pick a chat-tuned model or set tokenizer.chat_template explicitly."
            )
        self._logger.info(f"Loading model {config.name} on {config.device}...")
        model_kwargs = {"dtype": torch.float16, **config.model_kwargs}
        model: Any = AutoModelForCausalLM.from_pretrained(
            config.name, token=self._hf_token, trust_remote_code=False, **model_kwargs
        )
        model = model.to(torch.device(config.device)).eval()
        model.requires_grad_(False)
        return tokenizer, model

    def _build_logfile_path(self) -> str:
        if self._output.logfile:
            return self._output.logfile
        # Second-resolution timestamps collide for concurrent runs sharing a
        # prefix; both would then read/modify/write the same JSON log.
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        stem = f"{timestamp}_{uuid.uuid4().hex[:8]}"
        if self._output.result_prefix:
            stem = f"{self._output.result_prefix}_{stem}"
        return f"{stem}.json"

    def _write_log(self, *, context: FLRTContext) -> None:
        """Write the per-step history to the JSON log, mirroring the GCG log shape."""
        if context.logfile_path is None:
            return
        with open(context.logfile_path, "w") as f:
            json.dump(
                {
                    "losses": context.loss_history,
                    "controls": context.attack_history,
                    "params": {
                        "model": self._model_config.name,
                        "n_steps": self._algorithm.n_steps,
                        "n_candidates": self._algorithm.n_candidates,
                        "n_token_proposals": self._algorithm.n_token_proposals,
                        "fluency_weight": self._algorithm.fluency_weight,
                        "repetition_weight": self._algorithm.repetition_weight,
                    },
                },
                f,
                indent=2,
            )

    @staticmethod
    def _read_result(*, logfile_path: str | None, memory_labels: dict[str, str]) -> FLRTResult:
        """
        Pull final values out of the JSON log written during the run.

        Returns:
            FLRTResult: The values parsed from the log, or an empty result if absent.
        """
        if logfile_path is None:
            return FLRTResult(memory_labels=dict(memory_labels), log_path=None)
        try:
            with open(logfile_path) as f:
                log = json.load(f)
        except FileNotFoundError:
            logger.warning(f"FLRT logfile not found at {logfile_path}; returning empty result.")
            return FLRTResult(memory_labels=dict(memory_labels), log_path=None)

        attacks = log.get("controls", []) or []
        losses = log.get("losses", []) or []
        best_index = min(range(len(losses)), key=lambda i: losses[i]) if losses else None
        return FLRTResult(
            final_attack=attacks[best_index] if best_index is not None else "",
            final_loss=losses[best_index] if best_index is not None else float("nan"),
            step_count=len(attacks),
            loss_history=list(losses),
            attack_history=list(attacks),
            log_path=logfile_path,
            memory_labels=dict(memory_labels),
        )

    @overload
    async def execute_async(
        self,
        *,
        goals: list[str],
        targets: list[str],
        memory_labels: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> FLRTResult: ...

    @overload
    async def execute_async(self, **kwargs: Any) -> FLRTResult: ...

    async def execute_async(self, **kwargs: Any) -> FLRTResult:
        """
        Run the FLRT attack with the given goal/target lists.

        Args:
            goals (list[str]): Training goal strings. Required.
            targets (list[str]): Training target prefixes. Required, same
                length as ``goals``.
            memory_labels (dict[str, str] | None): Optional labels echoed onto
                the result.
            **kwargs: Forwarded to the base ``Strategy.execute_async``.

        Returns:
            FLRTResult: The optimization result.
        """
        kwargs.setdefault("memory_labels", {})
        return await super().execute_async(**kwargs)


__all__ = [
    "FLRTContext",
    "FLRTGenerator",
    "FLRTResult",
]
