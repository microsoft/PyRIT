# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
import threading
import types
import unicodedata
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


@_no_spacy
def test_mask_prompt_selects_words_with_non_ascii_letters():
    # "Schädlinge" carries the intent; it must be masked rather than left in cleartext while
    # the mask falls on shorter ASCII words around it.
    result = mask_prompt("Wie kann ich eine Schwachstelle für Schädlinge ausnutzen", num_to_mask=3)
    assert [w.text for w in result.masked_words] == ["Schwachstelle", "Schädlinge", "ausnutzen"]
    assert result.masked_prompt == "Wie kann ich eine [WORD1] für [WORD2] [WORD3]"
    assert result.masked_words[1].clue == "10-letter word"


@_no_spacy
def test_mask_prompt_prefers_non_ascii_content_words_over_ascii_articles():
    result = mask_prompt("Bir güvenlik açığını nasıl istismar ederim", num_to_mask=3)
    assert [w.text for w in result.masked_words] == ["güvenlik", "açığını", "istismar"]
    assert result.masked_prompt == "Bir [WORD1] [WORD2] nasıl [WORD3] ederim"


@_no_spacy
@pytest.mark.parametrize(
    "prompt",
    [
        "как взломать систему",
        "πώς να παραβιάσω ένα σύστημα",
        "làm thế nào để hack hệ thống",
        # Hebrew and Arabic as usually written, without vowel points: plain letters.
        "שלום עולם",
        "مرحبا بالعالم",
    ],
)
def test_mask_prompt_masks_prompts_written_entirely_in_non_ascii_letters(prompt):
    result = mask_prompt(prompt, num_to_mask=2)
    assert len(result.masked_words) == 2
    for word in result.masked_words:
        assert word.text not in result.masked_prompt


@_no_spacy
@pytest.mark.parametrize(
    "prompt",
    [
        # Devanagari vowel signs are combining marks, which \w does not cover.
        "सिस्टम हैक कैसे करें",
        # Hebrew niqqud and Arabic harakat are combining marks too.
        "שָׁלוֹם עוֹלָם",
        "مَرْحَبًا بِالْعَالَم",
        # NFD-decomposed accents: base letters followed by combining marks. Built at runtime so
        # an editor that normalizes the file to NFC cannot silently change the test.
        unicodedata.normalize("NFD", "café crème"),
    ],
)
def test_mask_prompt_masks_whole_words_written_with_combining_marks(prompt):
    # Combining marks belong to the whole word rather than acting as token boundaries.
    result = mask_prompt(prompt)
    assert result.masked_words
    for word in result.masked_words:
        assert word.text not in result.masked_prompt


@_no_spacy
@pytest.mark.parametrize(
    "prompt, expected, expected_words",
    [
        ("שָׁלוֹם עוֹלָם", "[WORD1] [WORD2]", ["שָׁלוֹם", "עוֹלָם"]),
        ("مَرْحَبًا بِالْعَالَم", "[WORD1] [WORD2]", ["مَرْحَبًا", "بِالْعَالَم"]),
        ("सिस्टम हैक कैसे करें", "[WORD1] हैक [WORD2] [WORD3]", ["सिस्टम", "कैसे", "करें"]),
    ],
)
def test_mask_prompt_masks_pointed_scripts_as_whole_words(prompt, expected, expected_words):
    result = mask_prompt(prompt)
    assert [w.text for w in result.masked_words] == expected_words
    assert result.masked_prompt == expected


@_no_spacy
def test_mask_prompt_masks_nfd_and_nfc_text_the_same_way():
    # The accent of an NFD word is a combining mark, so the word used to be skipped while its
    # NFC spelling was masked. Both spellings are now one word, and the two prompts select the
    # same words and produce the same masking.
    nfd = mask_prompt(unicodedata.normalize("NFD", "café crème exploit"), num_to_mask=2)
    nfc = mask_prompt("café crème exploit", num_to_mask=2)
    assert [w.text for w in nfc.masked_words] == ["crème", "exploit"]
    assert nfc.masked_prompt == "café [WORD1] [WORD2]"
    assert unicodedata.normalize("NFC", nfd.masked_prompt) == nfc.masked_prompt


@_no_spacy
def test_mask_prompt_treats_composed_and_decomposed_spellings_as_one_word():
    # Case folding alone is not stable across normalization forms, so the two spellings of
    # "café" would otherwise be two words with two placeholders.
    prompt = "café " + unicodedata.normalize("NFD", "café") + " crème"
    result = mask_prompt(prompt, num_to_mask=2)
    assert [w.text for w in result.masked_words] == ["café", "crème"]
    assert result.masked_prompt == "[WORD1] [WORD1] [WORD2]"


@_no_spacy
def test_mask_prompt_picks_the_same_words_whatever_the_normalization_form():
    # "système" and "čeština" are both seven letters, so the tie breaks alphabetically. On a
    # code-point key the decomposed spelling sorts differently and the other word wins.
    prompt = "exploit système čeština"
    nfc = mask_prompt(unicodedata.normalize("NFC", prompt), num_to_mask=2)
    nfd = mask_prompt(unicodedata.normalize("NFD", prompt), num_to_mask=2)
    assert [w.text for w in nfc.masked_words] == ["exploit", "système"]
    assert [unicodedata.normalize("NFC", w.text) for w in nfd.masked_words] == ["exploit", "système"]


@_no_spacy
def test_clue_counts_letters_not_combining_marks():
    # "שָׁלוֹם" is a four-letter word written with seven code points; the clue states letters.
    result = mask_prompt("שָׁלוֹם", num_to_mask=1)
    assert len(result.masked_words[0].text) == 7
    assert result.masked_words[0].clue == "4-letter word"


@_no_spacy
def test_mask_prompt_masks_whole_words_next_to_text_with_combining_marks():
    result = mask_prompt("exploit सिस्टम now", num_to_mask=3)
    assert [w.text for w in result.masked_words] == ["exploit", "सिस्टम", "now"]
    assert result.masked_prompt == "[WORD1] [WORD2] [WORD3]"


@_no_spacy
@pytest.mark.parametrize("joiner", ["\u200c", "\u200d"])
@pytest.mark.parametrize("template", ["अक्{joiner}षर", "école{joiner}ami"])
def test_mask_prompt_rejects_fragments_adjoining_joiners(*, joiner: str, template: str) -> None:
    with pytest.raises(ValueError, match="no maskable words"):
        mask_prompt(template.format(joiner=joiner))


@_no_spacy
@pytest.mark.parametrize("joiner", ["\u200c", "\u200d"])
def test_mask_prompt_preserves_joined_words_beside_maskable_words(joiner: str) -> None:
    joined_word = f"अक्{joiner}षर"
    result = mask_prompt(f"garden {joined_word} now", num_to_mask=3)
    assert [word.text for word in result.masked_words] == ["garden", "now"]
    assert result.masked_prompt == f"[WORD1] {joined_word} [WORD2]"


@_no_spacy
@pytest.mark.parametrize("prompt", ["abc²", "² ¼", "x¼ y³"])
def test_mask_prompt_never_selects_tokens_with_non_decimal_numbers(prompt):
    # Non-decimal numbers still belong to the same token. Reject the whole run rather than
    # exposing any of its letters as candidates.
    with pytest.raises(ValueError, match="no maskable words"):
        mask_prompt(prompt)


@_no_spacy
def test_mask_prompt_masks_words_next_to_tokens_with_non_decimal_numbers():
    result = mask_prompt("reset the abc² password", num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["password"]
    assert result.masked_prompt == "reset the abc² [WORD1]"


@_no_spacy
@pytest.mark.parametrize("form", ["NFC", "NFD"])
@pytest.mark.parametrize(
    "token",
    [
        "café2",
        "café_name",
        "caféabc2",
        "caféabc²",
        "café¼",
        "2café",
        "_café",
        "²café",
        "ab2\u0301café",
        "\u0301café",
        "café\u200dami",
        "café\u200cami",
    ],
)
def test_mask_prompt_rejects_complete_marked_nonword_tokens(*, form: str, token: str) -> None:
    token = unicodedata.normalize(form, token)
    with pytest.raises(ValueError, match="no maskable words"):
        mask_prompt(token)

    result = mask_prompt(f"garden {token} now", num_to_mask=3)
    assert [word.text for word in result.masked_words] == ["garden", "now"]
    assert result.masked_prompt == f"[WORD1] {token} [WORD2]"


@_no_spacy
@pytest.mark.parametrize("form", ["NFC", "NFD"])
@pytest.mark.parametrize("word", ["μαΐου", "αΐδιος"])
def test_mask_prompt_masks_every_canonical_greek_case_variant(*, form: str, word: str) -> None:
    lower = unicodedata.normalize(form, word)
    upper = unicodedata.normalize(form, word.upper())
    result = mask_prompt(f"{lower} {upper}", num_to_mask=1)
    assert len(result.masked_words) == 1
    assert result.masked_prompt == "[WORD1] [WORD1]"

    preferred = mask_prompt(f"garden {upper}", num_to_mask=1, essential_words=[lower])
    assert preferred.masked_prompt == "garden [WORD1]"


_ACUTE = "\N{COMBINING ACUTE ACCENT}"


@_no_spacy
@pytest.mark.parametrize(
    "prompt, expected",
    [
        (f"école école{_ACUTE}", f"[WORD1] école{_ACUTE}"),
        (f"école{_ACUTE} école", f"école{_ACUTE} [WORD1]"),
    ],
)
def test_mask_prompt_does_not_mask_a_skipped_fragment_with_the_same_spelling(prompt, expected):
    # The second "école" carries a trailing combining mark, so it is a different word from the
    # bare "école" and has its own key. Masking one must not mask the other, which would
    # either orphan the accent on a placeholder or swallow one that was never selected.
    result = mask_prompt(prompt, num_to_mask=1)
    assert [w.text for w in result.masked_words] == ["école"]
    assert result.masked_prompt == expected


@_no_spacy
@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("ÉCOLE école", "[WORD1] [WORD1]"),
        # Case-folding maps "ß" to "ss", so these are the same word.
        ("Straße STRASSE", "[WORD1] [WORD1]"),
    ],
)
def test_mask_prompt_masks_every_unicode_case_variant_of_a_chosen_word(prompt, expected):
    result = mask_prompt(prompt, num_to_mask=1)
    assert len(result.masked_words) == 1
    assert result.masked_prompt == expected


@_no_spacy
def test_mask_prompt_keeps_case_variants_that_fold_to_different_words_apart():
    # Turkish dotless "ı" and dotted "i" are different letters, and locale-independent case
    # folding keeps them apart: "KIŞ" folds to "kiş", "kış" to "kış". They are two candidate
    # words, so masking one never looks up a placeholder for the other.
    one = mask_prompt("KIŞ kış", num_to_mask=1)
    assert one.masked_prompt == "[WORD1] kış"
    both = mask_prompt("KIŞ kış", num_to_mask=2)
    assert both.masked_prompt == "[WORD1] [WORD2]"


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
