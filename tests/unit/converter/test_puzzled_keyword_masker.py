# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from pyrit.converter.puzzled import keyword_masker
from pyrit.converter.puzzled.keyword_masker import (
    MaskedWord,
    mask_count_for_length,
    mask_prompt,
)

# Force the length-based heuristic (no spaCy) for the masking tests below.
_no_spacy = patch.object(keyword_masker, "_get_nlp", lambda: None)


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


@_no_spacy
def test_mask_prompt_prioritizes_built_in_essential_harm_words():
    # "malware" is in the essential harm list, so it is masked ahead of longer plain words.
    result = mask_prompt("please distribute malware everywhere", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["malware"]


@_no_spacy
def test_mask_prompt_prefers_supplementary_words_over_plain():
    # "financial" is in the supplementary list; plain words rank below it.
    result = mask_prompt("prepare the financial summary quickly", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["financial"]


@_no_spacy
def test_mask_prompt_ranks_essential_above_supplementary():
    # Essential harm word beats a supplementary word even when the supplementary one is longer.
    result = mask_prompt("use phishing against financial institutions", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["phishing"]


# --- masking ---------------------------------------------------------------


@_no_spacy
def test_mask_prompt_replaces_every_occurrence_of_a_chosen_word():
    # A repeated sensitive word must be masked at every occurrence, not just the first.
    result = mask_prompt("hack the system then hack it again", num_to_mask=1, essential_words=["hack"])
    assert result.masked_prompt == "[WORD1] the system then [WORD1] it again"
    assert "hack" not in result.masked_prompt


@_no_spacy
def test_mask_prompt_masks_all_casings_of_a_chosen_word():
    # Different casings of the same sensitive word must all be masked, not just the exact form.
    result = mask_prompt("Hack the box then hack it and HACK again", num_to_mask=1, essential_words=["hack"])
    assert result.masked_prompt == "[WORD1] the box then [WORD1] it and [WORD1] again"
    assert "hack" not in result.masked_prompt.lower()


@_no_spacy
def test_mask_prompt_replaces_chosen_words_in_left_to_right_order():
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


@_no_spacy
def test_mask_prompt_falls_back_to_generic_pos_without_spacy():
    result = mask_prompt("disable the alarm", num_to_mask=1, essential_words=["alarm"])
    assert result.masked_words[0].pos == "word"


def test_mask_prompt_uses_spacy_pos_when_available():
    fake = _FakeNLP([_FakeToken("hack", "VERB"), _FakeToken("vault", "NOUN")])
    with patch.object(keyword_masker, "_get_nlp", lambda: fake):
        result = mask_prompt(
            "hack the vault",
            num_to_mask=2,
            essential_words=["hack", "vault"],
        )
    pos_by_word = {w.text: w.pos for w in result.masked_words}
    assert pos_by_word == {"hack": "verb", "vault": "noun"}


def test_mask_prompt_prefers_pos_tagged_words_over_plain_words():
    # spaCy tags "steal" and "documents"; without an essential list they should still
    # be chosen ahead of the untagged filler words.
    fake = _FakeNLP([_FakeToken("steal", "VERB"), _FakeToken("documents", "NOUN")])
    with patch.object(keyword_masker, "_get_nlp", lambda: fake):
        result = mask_prompt("please steal the documents now", num_to_mask=2)
    assert {w.text for w in result.masked_words} == {"steal", "documents"}


@_no_spacy
def test_mask_prompt_prefers_longer_words_without_hints():
    result = mask_prompt("cat elephant dog", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["elephant"]


@_no_spacy
def test_mask_prompt_defaults_to_length_rule():
    # Five tokens -> rule says mask 3.
    result = mask_prompt("alpha beta gamma delta epsilon")
    assert len(result.masked_words) == 3


@_no_spacy
def test_mask_prompt_caps_at_available_words():
    result = mask_prompt("one two", num_to_mask=5)
    assert len(result.masked_words) == 2


@_no_spacy
def test_mask_prompt_zero_masks_nothing():
    result = mask_prompt("hack the vault", num_to_mask=0)
    assert result.masked_words == []
    assert result.masked_prompt == "hack the vault"


@_no_spacy
@pytest.mark.parametrize("prompt", ["123 !!! 456", "h4ck3r t00l"])
def test_mask_prompt_raises_when_no_words(prompt):
    # Letters welded into an alphanumeric blob are not standalone words, so they are never
    # selected as candidates that masking would then fail to find.
    with pytest.raises(ValueError, match="no maskable words"):
        mask_prompt(prompt)


@_no_spacy
def test_mask_prompt_ignores_letters_inside_alphanumeric_tokens():
    result = mask_prompt("reset the h4ck3r password now", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["password"]
    assert result.masked_prompt == "reset the h4ck3r [WORD1] now"


# --- spaCy loader ----------------------------------------------------------


def test_get_nlp_returns_none_when_model_missing():
    fake_spacy = types.ModuleType("spacy")

    def _raise(_name):
        raise OSError("model not installed")

    fake_spacy.load = _raise  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"spacy": fake_spacy}):
        assert keyword_masker._get_nlp() is None
        # Second call uses the cached result rather than importing again.
        assert keyword_masker._get_nlp() is None


def test_get_nlp_caches_loaded_pipeline():
    sentinel = object()
    fake_spacy = types.ModuleType("spacy")
    fake_spacy.load = lambda _name: sentinel  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"spacy": fake_spacy}):
        assert keyword_masker._get_nlp() is sentinel

        # Even if loading would now fail, the cached pipeline is returned.
        def _raise(_name):
            raise OSError("should not be called")

        fake_spacy.load = _raise  # type: ignore[attr-defined]
        assert keyword_masker._get_nlp() is sentinel


def test_get_nlp_waits_for_concurrent_initialization():
    sentinel = object()
    load_started = threading.Event()
    release_load = threading.Event()
    load_count = 0
    fake_spacy = types.ModuleType("spacy")

    def _load(_name: str) -> object:
        nonlocal load_count
        load_count += 1
        load_started.set()
        release_load.wait(timeout=5)
        return sentinel

    fake_spacy.load = _load  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"spacy": fake_spacy}), ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(keyword_masker._get_nlp)
        assert load_started.wait(timeout=5)
        second = executor.submit(keyword_masker._get_nlp)
        release_load.set()

        assert first.result(timeout=5) is sentinel
        assert second.result(timeout=5) is sentinel
    assert load_count == 1
