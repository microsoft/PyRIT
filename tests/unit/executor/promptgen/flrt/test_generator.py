# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for :class:`FLRTGenerator` with a hand-built tokenizer and model."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

torch = pytest.importorskip("torch", reason="FLRT generator tests require torch")

from pyrit.executor.promptgen.flrt import (  # noqa: E402
    FLRTAlgorithmConfig,
    FLRTContext,
    FLRTGenerator,
    FLRTModelConfig,
    FLRTOutputConfig,
)

_MODEL_NAME = "tiny/tiny-model"


class _Encoding:
    def __init__(self, ids: list[int], offsets: list[tuple[int, int]]) -> None:
        self.input_ids = ids
        self._offsets = offsets

    def __getitem__(self, key: str) -> Any:
        if key == "offset_mapping":
            return self._offsets
        if key == "input_ids":
            return self.input_ids
        raise KeyError(key)


class TinyTokenizer:
    """A deterministic whitespace tokenizer implementing the pieces FLRT uses."""

    is_fast = True
    chat_template = "tiny"
    pad_token_id = 0
    eos_token_id = 1
    all_special_ids = [0, 1]

    def __init__(self) -> None:
        self.vocab: dict[str, int] = {"<pad>": 0, "<eos>": 1, "<unk>": 2}
        self.inverse: dict[int, str] = {0: "<pad>", 1: "<eos>", 2: "<unk>"}

    def apply_chat_template(self, messages: list[dict[str, str]], tokenize: bool = False) -> str:
        rendered = ""
        for message in messages:
            if message["role"] == "user":
                rendered += f"<|user|> {message['content']} <|end|>"
            else:
                rendered += f" <|assistant|> {message['content']} <|end|>"
        return rendered

    def _id_for(self, token: str) -> int:
        if token not in self.vocab:
            assigned = len(self.vocab) + 7
            self.vocab[token] = assigned
            self.inverse[assigned] = token
        return self.vocab[token]

    def __call__(self, prompt: str, **kwargs: Any) -> Any:
        ids = []
        offsets = []
        for match in re.finditer(r"\S+", prompt):
            ids.append(self._id_for(match.group(0)))
            offsets.append(match.span())
        return _Encoding(ids, offsets)

    def decode(self, ids: list[int], **kwargs: Any) -> str:
        # Real HF models never propose ids outside the tokenizer vocab; the fake
        # can, so register unknown ids bijectively to keep the round trip exact.
        for i in ids:
            if i not in self.inverse:
                token = f"xtok{i}"
                self.inverse[i] = token
                self.vocab[token] = i
        return " ".join(self.inverse[i] for i in ids)


class TinyModel:
    """A deterministic fake causal LM whose logits depend only on position and token."""

    def __init__(self) -> None:
        self.device = torch.device("cpu")

    def to(self, device: torch.device) -> TinyModel:
        return self

    def eval(self) -> TinyModel:
        return self

    def requires_grad_(self, requires: bool) -> TinyModel:
        return self

    def __call__(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> Any:
        batch, seq_len = input_ids.shape
        width = int(input_ids.max().item()) + 1
        logits = torch.zeros((batch, seq_len, width))
        for b in range(batch):
            for t in range(seq_len):
                generator = torch.Generator().manual_seed(int(input_ids[b, t].item()) * 997 + t)
                logits[b, t] = torch.randn(width, generator=generator)
        return SimpleNamespace(logits=logits)


def _fakes() -> tuple[TinyTokenizer, TinyModel]:
    return TinyTokenizer(), TinyModel()


def _make_generator(*, output_dir: Path | None = None, **algorithm_overrides: Any) -> FLRTGenerator:
    overrides = {"n_steps": 4, "n_candidates": 3, "n_token_proposals": 2, "buffer_size": 2}
    overrides.update(algorithm_overrides)
    output = FLRTOutputConfig(result_prefix=str(output_dir / "flrt") if output_dir else "", verbose=False)
    return FLRTGenerator(
        model=FLRTModelConfig(name=_MODEL_NAME, device="cpu"),
        algorithm=FLRTAlgorithmConfig(**overrides),
        output=output,
    )


def _patched_generator(*, output_dir: Path | None = None, **algorithm_overrides: Any) -> FLRTGenerator:
    generator = _make_generator(output_dir=output_dir, **algorithm_overrides)
    tokenizer, model = _fakes()
    generator._load_model_and_tokenizer = lambda: (tokenizer, model)  # type: ignore[method-assign]
    return generator


class TestFLRTGeneratorInit:
    def test_minimal_init_uses_dataclass_defaults(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME))
        assert gen._algorithm.n_steps == 500
        assert gen._output.verbose is True

    def test_custom_output_config(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME), output=FLRTOutputConfig(verbose=False))
        assert gen._output.verbose is False


class TestBuildIdentifier:
    def test_identifier_exposes_model_and_hyperparams(self) -> None:
        gen = FLRTGenerator(
            model=FLRTModelConfig(name=_MODEL_NAME),
            algorithm=FLRTAlgorithmConfig(n_steps=42, n_candidates=8, fluency_weight=0.9),
        )
        ident = gen._build_identifier()
        assert ident.params["model"] == _MODEL_NAME
        assert ident.params["n_steps"] == 42
        assert ident.params["n_candidates"] == 8
        assert ident.params["fluency_weight"] == 0.9


class TestValidateContext:
    def test_empty_goals_raises(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME))
        with pytest.raises(ValueError, match="goals must be non-empty"):
            gen._validate_context(context=FLRTContext(goals=[], targets=["t"]))

    def test_empty_targets_raises(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME))
        with pytest.raises(ValueError, match="targets must be non-empty"):
            gen._validate_context(context=FLRTContext(goals=["g"], targets=[]))

    def test_length_mismatch_raises(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME))
        with pytest.raises(ValueError, match="goals/targets length mismatch"):
            gen._validate_context(context=FLRTContext(goals=["g1", "g2"], targets=["t1"]))


class TestEncodeAttack:
    def test_locates_exact_token_spans(self) -> None:
        gen = _patched_generator()
        tokenizer, model = _fakes()
        context = FLRTContext(goals=["aa bb"], targets=["tt"])
        context.tokenizer = tokenizer
        context.template_prefix, context.template_mid, context.template_suffix = gen._probe_template(
            tokenizer=tokenizer
        )

        encoded = gen._encode_attack(context=context, attack="cc dd", goal_index=0)

        assert encoded is not None
        # Tokens: <|user|> aa bb cc dd <|end|> <|assistant|> tt <|end|>
        pieces = [tokenizer.inverse[i] for i in encoded.ids]
        assert pieces == ["<|user|>", "aa", "bb", "cc", "dd", "<|end|>", "<|assistant|>", "tt", "<|end|>"]
        assert (encoded.attack_start, encoded.attack_end) == (3, 5)
        assert encoded.target_positions == [7]
        assert (encoded.user_start, encoded.user_end) == (1, 5)

    def test_empty_attack_points_inside_user_content(self) -> None:
        gen = _patched_generator()
        tokenizer, _ = _fakes()
        context = FLRTContext(goals=["aa"], targets=["tt"])
        context.tokenizer = tokenizer
        context.template_prefix, context.template_mid, context.template_suffix = gen._probe_template(
            tokenizer=tokenizer
        )

        encoded = gen._encode_attack(context=context, attack="", goal_index=0)

        assert encoded is not None
        assert encoded.attack_start == encoded.attack_end
        # Insertion happens before the <|end|> scaffold token.
        assert tokenizer.inverse[encoded.ids[encoded.attack_start]] == "<|end|>"

    def test_special_token_in_attack_is_illegal(self) -> None:
        gen = _patched_generator()
        tokenizer, _ = _fakes()
        context = FLRTContext(goals=["aa"], targets=["tt"])
        context.tokenizer = tokenizer
        context.template_prefix, context.template_mid, context.template_suffix = gen._probe_template(
            tokenizer=tokenizer
        )

        assert gen._encode_attack(context=context, attack="cc <eos>", goal_index=0) is None


class TestTokenProposals:
    def test_proposals_have_one_row_per_position_plus_end(self) -> None:
        gen = _patched_generator()
        tokenizer, model = _fakes()
        context = FLRTContext(goals=["aa"], targets=["tt"])
        context.tokenizer = tokenizer
        context.model = model
        context.torch_rng = torch.Generator().manual_seed(0)
        context.template_prefix, context.template_mid, context.template_suffix = gen._probe_template(
            tokenizer=tokenizer
        )

        encoded = gen._encode_attack(context=context, attack="cc dd", goal_index=0)
        assert encoded is not None
        proposals = gen._token_proposals(context=context, encoded=encoded)

        assert len(proposals) == 3  # two attack positions plus the end position
        assert all(len(row) == gen._algorithm.n_token_proposals for row in proposals)

    def test_proposals_never_contain_special_tokens(self) -> None:
        gen = _patched_generator()
        tokenizer, model = _fakes()
        context = FLRTContext(goals=["aa"], targets=["tt"])
        context.tokenizer = tokenizer
        context.model = model
        context.torch_rng = torch.Generator().manual_seed(0)
        context.template_prefix, context.template_mid, context.template_suffix = gen._probe_template(
            tokenizer=tokenizer
        )

        encoded = gen._encode_attack(context=context, attack="cc", goal_index=0)
        assert encoded is not None
        proposals = gen._token_proposals(context=context, encoded=encoded)

        flat = [token for row in proposals for token in row]
        assert not any(token in tokenizer.all_special_ids for token in flat)


class TestExecuteAsyncLifecycle:
    async def test_run_produces_consistent_result(self, tmp_path: Path) -> None:
        gen = _patched_generator(output_dir=tmp_path)
        result = await gen.execute_async(goals=["aa bb"], targets=["tt uu"], memory_labels={"k": "v"})

        assert result.step_count == 4
        assert len(result.loss_history) == 4
        assert len(result.attack_history) == 4
        assert result.final_loss == min(result.loss_history)
        assert result.attack_history[result.loss_history.index(result.final_loss)] == result.final_attack
        assert result.memory_labels == {"k": "v"}
        assert result.log_path is not None
        with open(result.log_path) as f:
            log = json.load(f)
        assert len(log["controls"]) == 4
        assert len(log["losses"]) == 4

    async def test_deterministic_given_same_seed(self, tmp_path: Path) -> None:
        gen_a = _patched_generator(output_dir=tmp_path)
        gen_b = _patched_generator(output_dir=tmp_path)
        result_a = await gen_a.execute_async(goals=["aa"], targets=["tt"])
        result_b = await gen_b.execute_async(goals=["aa"], targets=["tt"])
        assert result_a.loss_history == result_b.loss_history
        assert result_a.attack_history == result_b.attack_history

    async def test_empty_initial_attack_grows(self, tmp_path: Path) -> None:
        gen = _patched_generator(output_dir=tmp_path, initial_attack="", min_tokens=0)
        result = await gen.execute_async(goals=["aa"], targets=["tt"])
        assert result.step_count == 4
        assert result.final_loss < float("inf")

    async def test_multi_goal_averages_losses(self, tmp_path: Path) -> None:
        gen = _patched_generator(output_dir=tmp_path)
        result = await gen.execute_async(goals=["aa", "bb"], targets=["tt", "uu"])
        assert result.step_count == 4
        assert result.final_loss < float("inf")

    async def test_illegal_initial_attack_raises(self, tmp_path: Path) -> None:
        gen = _patched_generator(output_dir=tmp_path, initial_attack="cc <eos>")
        with pytest.raises(Exception, match="does not tokenize cleanly"):
            await gen.execute_async(goals=["aa"], targets=["tt"])

    async def test_teardown_releases_model_and_tokenizer(self, tmp_path: Path) -> None:
        gen = _patched_generator(output_dir=tmp_path)
        captured: dict[str, Any] = {}

        original_setup = gen._setup_async

        async def capture_setup(*, context: FLRTContext) -> None:
            await original_setup(context=context)
            captured["context"] = context

        with patch.object(gen, "_setup_async", capture_setup):
            await gen.execute_async(goals=["aa"], targets=["tt"])

        assert captured["context"].model is None
        assert captured["context"].tokenizer is None


class TestReadResult:
    def test_reads_best_attack_and_loss(self, tmp_path: Path) -> None:
        log_path = tmp_path / "result.json"
        log_path.write_text(json.dumps({"controls": ["a", "b", "c"], "losses": [1.0, 0.5, 0.7]}))
        result = FLRTGenerator._read_result(logfile_path=str(log_path), memory_labels={"k": "v"})
        assert result.final_attack == "b"
        assert result.final_loss == 0.5
        assert result.step_count == 3
        assert result.loss_history == [1.0, 0.5, 0.7]
        assert result.attack_history == ["a", "b", "c"]
        assert result.memory_labels == {"k": "v"}
        assert result.log_path == str(log_path)

    def test_missing_file_returns_empty_result(self, tmp_path: Path) -> None:
        result = FLRTGenerator._read_result(logfile_path=str(tmp_path / "missing.json"), memory_labels={})
        assert result.final_attack == ""
        assert result.step_count == 0
        assert result.log_path is None


class TestBuildLogfilePath:
    def test_same_prefix_yields_distinct_paths(self, tmp_path: Path) -> None:
        gen = _make_generator(output_dir=tmp_path)
        assert gen._build_logfile_path() != gen._build_logfile_path()

    def test_path_is_prefix_timestamp_and_short_id(self, tmp_path: Path) -> None:
        path = Path(_make_generator(output_dir=tmp_path)._build_logfile_path())
        assert path.parent == tmp_path
        assert re.fullmatch(r"flrt_\d{8}-\d{6}_[0-9a-f]{8}\.json", path.name), path.name

    def test_empty_prefix_does_not_lead_with_underscore(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME), output=FLRTOutputConfig())
        path = Path(gen._build_logfile_path())
        assert re.fullmatch(r"\d{8}-\d{6}_[0-9a-f]{8}\.json", path.name), path.name

    def test_explicit_logfile_is_returned_unchanged(self) -> None:
        gen = FLRTGenerator(model=FLRTModelConfig(name=_MODEL_NAME), output=FLRTOutputConfig(logfile="fixed.json"))
        assert gen._build_logfile_path() == "fixed.json"


class TestProbeTemplate:
    def test_probe_splits_scaffold(self) -> None:
        gen = _patched_generator()
        tokenizer, _ = _fakes()
        prefix, mid, suffix = gen._probe_template(tokenizer=tokenizer)
        assert prefix == "<|user|> "
        assert mid == " <|end|> <|assistant|> "
        assert suffix == " <|end|>"

    def test_template_without_markers_raises(self) -> None:
        class BrokenTokenizer(TinyTokenizer):
            def apply_chat_template(self, messages: list[dict[str, str]], tokenize: bool = False) -> str:
                return "no content here"

        gen = _patched_generator()
        with pytest.raises(ValueError, match="Cannot safely locate message content"):
            gen._probe_template(tokenizer=BrokenTokenizer())
