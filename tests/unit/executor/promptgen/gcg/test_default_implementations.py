# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ``pyrit.executor.promptgen.gcg.default_implementations``.

These tests verify byte-identical parity between the four default
implementations and the legacy GCG attack code paths they reproduce:

- ``StandardGCGSampling`` vs ``GCGPromptManager.sample_control``
- ``CrossEntropyLoss`` vs the weighted sum of ``AttackPrompt.target_loss``
  and ``AttackPrompt.control_loss`` applied inside
  ``GCGMultiPromptAttack.step``
- ``LengthPreservingFilter`` vs ``MultiPromptAttack.get_filtered_cands``
- ``LiteralStringInit`` vs the literal-string ``control_init`` assignment
  inside ``AttackPrompt.__init__``

Mocking patterns follow the conventions established in
``tests/unit/executor/promptgen/gcg/test_gcg_core.py`` (``object.__new__``
to skip the real ``__init__``, ``MagicMock`` tokenizers).
"""

from unittest.mock import MagicMock

import pytest

torch = pytest.importorskip("torch", reason="GCG default implementations require torch")

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
gcg_attack_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack",
    reason="GCG optional dependencies not installed",
)

import pyrit.executor.promptgen.gcg as gcg_pkg  # noqa: E402
from pyrit.executor.promptgen.gcg import (  # noqa: E402
    CrossEntropyLoss,
    LengthPreservingFilter,
    LiteralStringInit,
    StandardGCGSampling,
)
from pyrit.executor.promptgen.gcg import default_implementations as defaults_module  # noqa: E402
from pyrit.executor.promptgen.gcg.config import GCGAlgorithmConfig  # noqa: E402

AttackPrompt = attack_manager_mod.AttackPrompt
MultiPromptAttack = attack_manager_mod.MultiPromptAttack
GCGPromptManager = gcg_attack_mod.GCGPromptManager


DEFAULT_NAMES = (
    "CrossEntropyLoss",
    "LengthPreservingFilter",
    "LiteralStringInit",
    "StandardGCGSampling",
)


class TestPackageReExports:
    """Verify the four default classes are re-exported from the package root."""

    @pytest.mark.parametrize("name", DEFAULT_NAMES)
    def test_default_is_reexported_with_identity(self, name: str) -> None:
        package_attr = getattr(gcg_pkg, name)
        module_attr = getattr(defaults_module, name)
        assert package_attr is module_attr, (
            f"{name} re-exported from pyrit.executor.promptgen.gcg must be the same "
            f"object as pyrit.executor.promptgen.gcg.default_implementations.{name}"
        )

    @pytest.mark.parametrize("name", DEFAULT_NAMES)
    def test_default_in_package_dunder_all(self, name: str) -> None:
        assert name in gcg_pkg.__all__


class TestStandardGCGSampling:
    """Parity: ``StandardGCGSampling`` vs ``GCGPromptManager.sample_control``."""

    def _make_legacy_prompt_manager(
        self,
        *,
        control_tokens: torch.Tensor,
        non_ascii_tokens: torch.Tensor,
    ) -> GCGPromptManager:
        # Mirrors the construction pattern used by TestSampleControl in
        # test_gcg_core.py: skip __init__ and seed just the attributes that
        # sample_control reads.
        prompt_manager = object.__new__(GCGPromptManager)
        prompt_manager._nonascii_toks = non_ascii_tokens
        prompt_manager._prompts = [MagicMock()]
        prompt_manager._prompts[0].control_toks = control_tokens.clone()
        return prompt_manager

    def test_sample_candidates_matches_legacy_with_ascii_only(self) -> None:
        """Legacy reference: ``GCGPromptManager.sample_control(grad, batch_size,
        topk=top_k, temp=1.0, allow_non_ascii=False)`` in
        ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py``.
        """
        n_control_tokens = 5
        vocab_size = 50
        batch_size = 4
        top_k = 8

        torch.manual_seed(2026)
        gradient_template = torch.randn(n_control_tokens, vocab_size)
        control_tokens = torch.randint(0, vocab_size, (n_control_tokens,))
        non_ascii_tokens = torch.tensor([2, 7, 13])

        # Legacy path
        prompt_manager = self._make_legacy_prompt_manager(
            control_tokens=control_tokens, non_ascii_tokens=non_ascii_tokens
        )
        torch.manual_seed(12345)
        legacy_out = prompt_manager.sample_control(
            gradient_template.clone(),
            batch_size,
            topk=top_k,
            temp=1.0,
            allow_non_ascii=False,
        )

        # Default path
        default = StandardGCGSampling()
        torch.manual_seed(12345)
        default_out = default.sample_candidates(
            gradient=gradient_template.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=batch_size,
            top_k=top_k,
            temperature=1.0,
            allow_non_ascii=False,
            non_ascii_tokens=non_ascii_tokens,
        )

        assert torch.equal(default_out, legacy_out)

    def test_sample_candidates_matches_legacy_with_non_ascii_allowed(self) -> None:
        """Legacy reference: same as above but with ``allow_non_ascii=True``
        (the no-mask branch where the gradient is not mutated).
        """
        n_control_tokens = 6
        vocab_size = 40
        batch_size = 5
        top_k = 10

        torch.manual_seed(2027)
        gradient_template = torch.randn(n_control_tokens, vocab_size)
        control_tokens = torch.randint(0, vocab_size, (n_control_tokens,))
        non_ascii_tokens = torch.tensor([1, 4])

        prompt_manager = self._make_legacy_prompt_manager(
            control_tokens=control_tokens, non_ascii_tokens=non_ascii_tokens
        )
        torch.manual_seed(54321)
        legacy_out = prompt_manager.sample_control(
            gradient_template.clone(),
            batch_size,
            topk=top_k,
            temp=1.0,
            allow_non_ascii=True,
        )

        default = StandardGCGSampling()
        torch.manual_seed(54321)
        default_out = default.sample_candidates(
            gradient=gradient_template.clone(),
            control_tokens=control_tokens.clone(),
            batch_size=batch_size,
            top_k=top_k,
            temperature=1.0,
            allow_non_ascii=True,
            non_ascii_tokens=non_ascii_tokens,
        )

        assert torch.equal(default_out, legacy_out)


class TestCrossEntropyLoss:
    """Parity: ``CrossEntropyLoss`` vs ``AttackPrompt.target_loss`` +
    ``AttackPrompt.control_loss``.
    """

    def _make_legacy_prompt(
        self,
        *,
        target_slice: slice,
        control_slice: slice,
    ) -> AttackPrompt:
        # Mirrors TestTargetAndControlLoss in test_gcg_core.py: skip
        # __init__ and seed only the slice attributes that the loss methods
        # consult.
        prompt = object.__new__(AttackPrompt)
        prompt._target_slice = target_slice
        prompt._control_slice = control_slice
        return prompt

    def test_compute_loss_matches_legacy_weighted_sum(self) -> None:
        """Legacy reference:
        ``target_weight * AttackPrompt.target_loss(logits, ids).mean(dim=-1)``
        ``+ control_weight * AttackPrompt.control_loss(logits, ids).mean(dim=-1)``,
        per ``GCGMultiPromptAttack.step`` in
        ``pyrit/executor/promptgen/gcg/attack/gcg/gcg_attack.py``.
        """
        batch_size = 4
        seq_len = 10
        vocab_size = 30
        target_slice = slice(5, 8)
        control_slice = slice(2, 5)
        target_weight = 1.0
        control_weight = 0.1

        torch.manual_seed(99)
        logits = torch.randn(batch_size, seq_len, vocab_size)
        token_ids = torch.randint(0, vocab_size, (batch_size, seq_len))

        prompt = self._make_legacy_prompt(target_slice=target_slice, control_slice=control_slice)
        legacy_target = prompt.target_loss(logits, token_ids).mean(dim=-1)
        legacy_control = prompt.control_loss(logits, token_ids).mean(dim=-1)
        legacy_total = target_weight * legacy_target + control_weight * legacy_control

        default = CrossEntropyLoss(target_weight=target_weight, control_weight=control_weight)
        default_total = default.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=target_slice,
            control_slice=control_slice,
        )

        assert torch.equal(default_total, legacy_total)

    def test_compute_loss_target_only_matches_legacy_target_loss(self) -> None:
        """With ``control_weight=0`` the legacy ``step`` skips the control
        term (``if control_weight != 0:`` guard at line 211). The default
        must produce the same per-candidate value as
        ``target_weight * target_loss(...).mean(dim=-1)`` alone.
        """
        target_slice = slice(4, 7)
        control_slice = slice(1, 4)

        torch.manual_seed(7)
        logits = torch.randn(3, 9, 25)
        token_ids = torch.randint(0, 25, (3, 9))

        prompt = self._make_legacy_prompt(target_slice=target_slice, control_slice=control_slice)
        legacy_total = 1.0 * prompt.target_loss(logits, token_ids).mean(dim=-1)

        default = CrossEntropyLoss(target_weight=1.0, control_weight=0.0)
        default_total = default.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=target_slice,
            control_slice=control_slice,
        )

        assert torch.equal(default_total, legacy_total)

    def test_compute_loss_control_only_matches_legacy_control_loss(self) -> None:
        """With ``target_weight=0`` the default must produce the same value
        as ``control_weight * control_loss(...).mean(dim=-1)`` alone.
        """
        target_slice = slice(4, 7)
        control_slice = slice(1, 4)

        torch.manual_seed(13)
        logits = torch.randn(3, 9, 25)
        token_ids = torch.randint(0, 25, (3, 9))

        prompt = self._make_legacy_prompt(target_slice=target_slice, control_slice=control_slice)
        legacy_total = 0.5 * prompt.control_loss(logits, token_ids).mean(dim=-1)

        default = CrossEntropyLoss(target_weight=0.0, control_weight=0.5)
        default_total = default.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=target_slice,
            control_slice=control_slice,
        )

        assert torch.equal(default_total, legacy_total)

    def test_init_rejects_both_weights_zero(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            CrossEntropyLoss(target_weight=0.0, control_weight=0.0)

    def test_init_rejects_negative_target_weight(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            CrossEntropyLoss(target_weight=-0.5, control_weight=1.0)

    def test_init_rejects_negative_control_weight(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            CrossEntropyLoss(target_weight=1.0, control_weight=-0.5)

    def test_compute_loss_returns_batch_sized_tensor(self) -> None:
        batch_size = 4
        logits = torch.randn(batch_size, 10, 20)
        token_ids = torch.randint(0, 20, (batch_size, 10))

        default = CrossEntropyLoss(target_weight=1.0, control_weight=0.1)
        out = default.compute_loss(
            logits=logits,
            token_ids=token_ids,
            target_slice=slice(5, 8),
            control_slice=slice(2, 5),
        )

        assert out.shape == (batch_size,)


def _make_filter_tokenizer() -> MagicMock:
    """Build a fresh, deterministic, stateless mock tokenizer for filter tests.

    Behavior:
    - ``decode(tensor)`` -> ``"x" * int(tensor[0].item())`` — string length
      is keyed off the first token id, so each row maps to a distinct
      predictable string.
    - ``tokenizer(text, ...).input_ids`` has length ``len(text)`` — so the
      retokenized length check is fully predictable from the decoded
      string.
    - ``tokenizer("!").input_ids[0] == 0`` — provides the clamp
      replacement id.
    - ``vocab_size == 100``.
    """
    tokenizer = MagicMock()
    tokenizer.vocab_size = 100

    def decode_fn(ids, **_kwargs):
        return "x" * int(ids[0].item())

    tokenizer.decode.side_effect = decode_fn

    def call_tokenizer(text, **_kwargs):
        result = MagicMock()
        if text == "!":
            result.input_ids = [0]
        else:
            result.input_ids = list(range(len(text)))
        return result

    tokenizer.side_effect = call_tokenizer
    return tokenizer


class TestLengthPreservingFilter:
    """Parity: ``LengthPreservingFilter`` vs
    ``MultiPromptAttack.get_filtered_cands``.
    """

    def _make_legacy_attack(self, *, tokenizer: MagicMock) -> MultiPromptAttack:
        # Mirrors TestGetFilteredCands in test_gcg_core.py: skip __init__
        # and only attach the workers list that get_filtered_cands reads.
        attack = object.__new__(MultiPromptAttack)
        worker = MagicMock()
        worker.tokenizer = tokenizer
        attack.workers = [worker]
        return attack

    def test_filter_candidates_matches_legacy_filtered(self) -> None:
        """Legacy reference:
        ``MultiPromptAttack.get_filtered_cands(0, control_cand,
        filter_cand=True, curr_control=...)`` in
        ``pyrit/executor/promptgen/gcg/attack/base/attack_manager.py``.

        With the helper tokenizer:
        - Row 0 ``[3, 0, 1]`` -> decode ``"xxx"`` (len 3); retok len 3 ==
          control_length 3 -> KEEP.
        - Row 1 ``[5, 0, 0]`` -> decode ``"xxxxx"`` (len 5); retok len 5
          != 3 -> DROP.
        - Row 2 ``[2, 0, 1]`` -> decode ``"xx"`` (len 2); retok len 2 !=
          3 -> DROP.
        Pad-with-last gives ``["xxx", "xxx", "xxx"]``.
        """
        candidate_template = torch.tensor([[3, 0, 1], [5, 0, 0], [2, 0, 1]])

        legacy_attack = self._make_legacy_attack(tokenizer=_make_filter_tokenizer())
        legacy_out = legacy_attack.get_filtered_cands(
            0, candidate_template.clone(), filter_cand=True, curr_control="never_matches"
        )

        default = LengthPreservingFilter(filter=True)
        default_out = default.filter_candidates(
            candidate_tokens=candidate_template.clone(),
            tokenizer=_make_filter_tokenizer(),
            current_control="never_matches",
        )

        assert default_out == legacy_out
        assert legacy_out == ["xxx", "xxx", "xxx"]

    def test_filter_candidates_matches_legacy_unfiltered(self) -> None:
        """Legacy reference: ``get_filtered_cands(0, control_cand,
        filter_cand=False)``. Every row is decoded and returned unchanged.
        """
        candidate_template = torch.tensor([[3, 0, 1], [5, 0, 0], [2, 0, 1]])

        legacy_attack = self._make_legacy_attack(tokenizer=_make_filter_tokenizer())
        legacy_out = legacy_attack.get_filtered_cands(0, candidate_template.clone(), filter_cand=False)

        default = LengthPreservingFilter(filter=False)
        default_out = default.filter_candidates(
            candidate_tokens=candidate_template.clone(),
            tokenizer=_make_filter_tokenizer(),
            current_control="ignored_when_filter_false",
        )

        assert default_out == legacy_out
        assert legacy_out == ["xxx", "xxxxx", "xx"]

    def test_filter_candidates_clamps_out_of_vocab_tokens(self) -> None:
        """Both code paths apply the legacy vocab-clamp in-place: tokens
        above ``vocab_size`` are replaced by the id of ``"!"`` before any
        decoding happens.
        """
        candidate_template = torch.tensor([[150, 0, 1], [3, 0, 1]])  # 150 > vocab_size=100

        legacy_input = candidate_template.clone()
        legacy_attack = self._make_legacy_attack(tokenizer=_make_filter_tokenizer())
        legacy_attack.get_filtered_cands(0, legacy_input, filter_cand=False)

        default_input = candidate_template.clone()
        default = LengthPreservingFilter(filter=False)
        default.filter_candidates(
            candidate_tokens=default_input,
            tokenizer=_make_filter_tokenizer(),
            current_control="",
        )

        assert torch.equal(default_input, legacy_input)
        assert default_input[0, 0].item() == 0


class TestLiteralStringInit:
    """Parity: ``LiteralStringInit`` vs the literal-string ``control_init``
    assignment inside ``AttackPrompt.__init__`` (``self.control =
    control_init``).
    """

    def test_make_initial_suffix_returns_default_control_init(self) -> None:
        """Legacy reference: ``GCGAlgorithmConfig.control_init`` (default
        ``_DEFAULT_CONTROL_INIT``) is assigned to ``self.control`` in
        ``AttackPrompt.__init__``.
        """
        default_suffix = GCGAlgorithmConfig().control_init
        initializer = LiteralStringInit(suffix=default_suffix)
        assert initializer.make_initial_suffix(tokenizer=MagicMock()) == default_suffix

    def test_make_initial_suffix_ignores_tokenizer(self) -> None:
        suffix = "custom suffix string"
        initializer = LiteralStringInit(suffix=suffix)
        assert initializer.make_initial_suffix(tokenizer=None) == suffix

    def test_init_rejects_empty_suffix(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            LiteralStringInit(suffix="")
