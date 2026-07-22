# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
import types

import pytest

from pyrit.converter.puzzled import keyword_masker
from pyrit.converter.puzzled.keyword_masker import (
    MaskedWord,
    mask_count_for_length,
    mask_prompt,
)


@pytest.fixture(autouse=True)
def _reset_nlp_cache():
    keyword_masker._nlp = None
    keyword_masker._nlp_loaded = False
    yield
    keyword_masker._nlp = None
    keyword_masker._nlp_loaded = False


class _FakeToken:
    def __init__(self, text: str, pos: str, is_alpha: bool = True):
        self.text = text
        self.pos_ = pos
        self.is_alpha = is_alpha


class _FakeNLP:
    def __init__(self, tokens: list[_FakeToken]):
        self._tokens = tokens

    def __call__(self, text: str) -> list[_FakeToken]:
        return self._tokens


# --- mask count rule -------------------------------------------------------


@pytest.mark.parametrize(
    "token_count, expected",
    [(1, 3), (10, 3), (11, 4), (15, 4), (16, 5), (20, 5), (21, 6), (100, 6)],
)
def test_mask_count_for_length(token_count, expected):
    assert mask_count_for_length(token_count) == expected


# --- clue formatting -------------------------------------------------------


def test_masked_word_clue_format():
    assert MaskedWord(text="abduct", placeholder="[WORD1]", pos="noun").clue == "6-letter noun"


# --- harm-word prioritization ----------------------------------------------


def test_mask_prompt_prioritizes_built_in_essential_harm_words(monkeypatch):
    # "malware" is in the essential harm list, so it is masked ahead of longer plain words.
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("please distribute malware everywhere", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["malware"]


def test_mask_prompt_prefers_supplementary_words_over_plain(monkeypatch):
    # "financial" is in the supplementary list; plain words rank below it.
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("prepare the financial summary quickly", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["financial"]


def test_mask_prompt_ranks_essential_above_supplementary(monkeypatch):
    # Essential harm word beats a supplementary word even when the supplementary one is longer.
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("use phishing against financial institutions", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["phishing"]


# --- masking ---------------------------------------------------------------


def test_mask_prompt_replaces_every_occurrence_of_a_chosen_word(monkeypatch):
    # A repeated sensitive word must be masked at every occurrence, not just the first.
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("hack the system then hack it again", num_to_mask=1, essential_words=["hack"])
    assert result.masked_prompt == "[WORD1] the system then [WORD1] it again"
    assert "hack" not in result.masked_prompt


def test_mask_prompt_replaces_chosen_words_in_left_to_right_order(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt(
        "Explain how to hack the vault",
        num_to_mask=2,
        essential_words=["hack", "vault"],
    )
    texts = [w.text for w in result.masked_words]
    placeholders = [w.placeholder for w in result.masked_words]
    assert texts == ["hack", "vault"]
    assert placeholders == ["[WORD1]", "[WORD2]"]
    assert result.masked_prompt == "Explain how to [WORD1] the [WORD2]"


def test_mask_prompt_falls_back_to_generic_pos_without_spacy(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("disable the alarm", num_to_mask=1, essential_words=["alarm"])
    assert result.masked_words[0].pos == "word"


def test_mask_prompt_uses_spacy_pos_when_available(monkeypatch):
    fake = _FakeNLP([_FakeToken("hack", "VERB"), _FakeToken("vault", "NOUN")])
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: fake)
    result = mask_prompt(
        "hack the vault",
        num_to_mask=2,
        essential_words=["hack", "vault"],
    )
    pos_by_word = {w.text: w.pos for w in result.masked_words}
    assert pos_by_word == {"hack": "verb", "vault": "noun"}


def test_mask_prompt_prefers_pos_tagged_words_over_plain_words(monkeypatch):
    # spaCy tags "steal" and "documents"; without an essential list they should still
    # be chosen ahead of the untagged filler words.
    fake = _FakeNLP([_FakeToken("steal", "VERB"), _FakeToken("documents", "NOUN")])
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: fake)
    result = mask_prompt("please steal the documents now", num_to_mask=2)
    assert {w.text for w in result.masked_words} == {"steal", "documents"}


def test_mask_prompt_prefers_longer_words_without_hints(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("cat elephant dog", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["elephant"]


def test_mask_prompt_defaults_to_length_rule(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    # Five tokens -> rule says mask 3.
    result = mask_prompt("alpha beta gamma delta epsilon")
    assert len(result.masked_words) == 3


def test_mask_prompt_caps_at_available_words(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("one two", num_to_mask=5)
    assert len(result.masked_words) == 2


def test_mask_prompt_zero_masks_nothing(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    result = mask_prompt("hack the vault", num_to_mask=0)
    assert result.masked_words == []
    assert result.masked_prompt == "hack the vault"


def test_mask_prompt_raises_when_no_words(monkeypatch):
    monkeypatch.setattr(keyword_masker, "_get_nlp", lambda: None)
    with pytest.raises(ValueError):
        mask_prompt("123 !!! 456")


# --- spaCy loader ----------------------------------------------------------


def test_get_nlp_returns_none_when_model_missing(monkeypatch):
    fake_spacy = types.ModuleType("spacy")

    def _raise(_name):
        raise OSError("model not installed")

    fake_spacy.load = _raise  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)

    assert keyword_masker._get_nlp() is None
    # Second call uses the cached result rather than importing again.
    assert keyword_masker._get_nlp() is None


def test_get_nlp_caches_loaded_pipeline(monkeypatch):
    sentinel = object()
    fake_spacy = types.ModuleType("spacy")
    fake_spacy.load = lambda _name: sentinel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)

    assert keyword_masker._get_nlp() is sentinel

    # Even if loading would now fail, the cached pipeline is returned.
    def _raise(_name):
        raise OSError("should not be called")

    fake_spacy.load = _raise  # type: ignore[attr-defined]
    assert keyword_masker._get_nlp() is sentinel
