# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keyword selection and masking for the PUZZLED jailbreak technique
(Ahn & Lee, "PUZZLED: Jailbreaking LLMs through Word-Based Puzzles", arXiv:2508.01306).

The masker chooses the most sensitive words in a prompt, replaces each with an indexed
``[WORD1]`` placeholder, and records a deterministic clue (length and part of speech) for
every masked word. Part-of-speech tagging uses spaCy when its ``en_core_web_sm`` model is
installed and degrades to a length-based heuristic when it is not, mirroring the graceful
fallback already used in ``pyrit/executor/benchmark/fairness_bias.py``. Run
``python -m spacy download en_core_web_sm`` to enable the paper's part-of-speech-aware
selection; without it, keywords are chosen by length alone.
"""

import logging
import threading
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import regex

from pyrit.converter.puzzled._text import _letter_units

logger = logging.getLogger(__name__)

# Scan complete tokens before rejecting numbers, underscores, joiners, or leading marks.
# Rejecting a whole run prevents a mark from exposing a maskable prefix of an invalid token.
_WORD_PATTERN = regex.compile(r"[\p{L}\p{M}\p{N}_\u200c\u200d]+")
_MASKABLE_WORD_PATTERN = regex.compile(r"\p{L}[\p{L}\p{M}]*")


def _letter_count(word: str) -> int:
    """
    Return the number of letter units used by the puzzle builders.

    Combining marks stay attached to their letters, so pointed "שָׁלוֹם" has four units
    despite being written with seven code points. Canonically equivalent spellings use
    the same units.

    Args:
        word (str): A word yielded by ``_iter_words``.

    Returns:
        int: The count of letters with their attached combining marks.
    """
    return len(_letter_units(word))


@dataclass(frozen=True)
class _Word:
    """A whole word of the prompt and the span it occupies."""

    text: str
    start: int
    end: int


def _word_key(word: str) -> str:
    """
    Return the case-insensitive key that identifies a word.

    Selection, deduplication, ordering and replacement all compare words through this key,
    so two spellings are either the same masked word everywhere or different words
    everywhere. For ASCII it equals ``str.lower``.

    Case folding alone is not stable across normalization forms: composed "café" and
    decomposed "cafe\u0301" fold to different strings, which would make them two words and let
    the alphabetical tie-break in ``_rank_candidates`` choose differently for the same prompt.
    Normalize before folding to put marks in canonical order, and again afterward because
    folding can produce decomposed text. This also keeps Greek case variants equivalent.

    Args:
        word (str): A word from the prompt or from the caller.

    Returns:
        str: The case-folded word in composed form.
    """
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", word).casefold())


def _iter_words(prompt: str) -> Iterator[_Word]:
    """
    Yield the maskable words of ``prompt``.

    Scan maximal runs of letters, combining marks, numbers, underscores, and joiners.
    Keep only runs that start with a letter and contain only letters and attached marks.
    This accepts pointed scripts and NFD accents without exposing fragments of tokens
    such as "h4ck3r", "abc²", or a marked word followed by a digit or underscore.
    Words containing joiners remain unmaskable. Scanning and validation use the same
    Unicode properties.

    Args:
        prompt (str): The prompt to scan.

    Yields:
        _Word: Each whole word, in order of appearance.
    """
    for match in _WORD_PATTERN.finditer(prompt):
        word = match.group()
        if not _MASKABLE_WORD_PATTERN.fullmatch(word):
            continue
        yield _Word(text=word, start=match.start(), end=match.end())


# Part-of-speech label used when spaCy is unavailable or the word was caller-supplied.
_GENERIC_POS = "word"

# spaCy coarse tags we treat as maskable, mapped to the clue label we expose.
_MASKABLE_POS = {"NOUN": "noun", "PROPN": "noun", "VERB": "verb"}

# Harm-related words the PUZZLED paper masks first, so the puzzle hides the part of the
# instruction that carries the harmful intent. These are the paper's "essential" list
# (Ahn & Lee, arXiv:2508.01306, Table 4), which it gives as examples rather than an
# exhaustive set; callers can extend or override via ``essential_words``.
_ESSENTIAL_HARM_WORDS = frozenset(
    {
        "exploit",
        "malware",
        "firearm",
        "hacking",
        "suicide",
        "propaganda",
        "cyberbullying",
        "extortion",
        "hate",
        "misinformation",
        "hijack",
        "manipulation",
        "ransomware",
        "sabotage",
        "terrorism",
        "stalk",
        "smuggle",
        "harassment",
        "phishing",
        "abuse",
    }
)

# The paper's "recommended" list (Table 4): words that are not harmful alone but carry
# auxiliary context (methods, targets, resources). Masked after the essential list when
# more masks are still needed.
_SUPPLEMENTARY_WORDS = frozenset(
    {
        "identity",
        "encryption",
        "financial",
        "insider",
        "passport",
        "passwords",
        "private",
        "psychological",
        "software",
        "tactics",
        "targets",
        "reputation",
        "redirects",
        "device",
        "accessing",
        "credit",
        "database",
        "voting",
        "medical",
        "witness",
    }
)

# Module-level spaCy pipeline, loaded once on first use.
_nlp = None
_nlp_loaded = False
_nlp_lock = threading.Lock()


@dataclass(frozen=True)
class MaskedWord:
    """A single masked keyword and the metadata needed to build its puzzle clue."""

    text: str
    placeholder: str
    pos: str

    @property
    def clue(self) -> str:
        """The deterministic clue for this word, e.g. ``"9-letter noun"``."""
        return f"{_letter_count(self.text)}-letter {self.pos}"


@dataclass(frozen=True)
class MaskResult:
    """The outcome of masking a prompt."""

    masked_prompt: str
    masked_words: list[MaskedWord]


def mask_count_for_length(token_count: int) -> int:
    """
    Return how many words to mask for a prompt of ``token_count`` whitespace tokens.

    The thresholds are the paper's mapping from instruction length to masked word count
    (Ahn & Lee, arXiv:2508.01306, Table 3): 1-10 tokens mask 3 words, 11-15 mask 4,
    16-20 mask 5, and 21 or more mask 6.

    Args:
        token_count (int): Number of whitespace-separated tokens in the prompt.

    Returns:
        int: The target number of words to mask.
    """
    if token_count <= 10:
        return 3
    if token_count <= 15:
        return 4
    if token_count <= 20:
        return 5
    return 6


def _get_nlp() -> Any:
    """
    Load and cache the spaCy pipeline.

    Returns:
        Any: The loaded spaCy ``Language`` pipeline, or ``None`` if spaCy or its
            ``en_core_web_sm`` model is unavailable.
    """
    global _nlp, _nlp_loaded
    if _nlp_loaded:
        return _nlp

    with _nlp_lock:
        if _nlp_loaded:
            return _nlp
        try:
            import spacy  # type: ignore[ty:unresolved-import]

            _nlp = spacy.load("en_core_web_sm")
        except (ImportError, OSError):
            logger.info("spaCy model 'en_core_web_sm' unavailable; using length-based keyword selection instead.")
            _nlp = None
        _nlp_loaded = True
        return _nlp


def _pos_lookup(prompt: str) -> dict[str, str]:
    """
    Map each noun/verb in the prompt (by ``_word_key``) to its clue label.

    The prompt is normalized before tagging because the tagger is sensitive to the spelling
    it receives: composed "café" and decomposed "cafe\u0301" are given different parts of
    speech, which would make the same prompt select a different word. Words are accepted with
    ``_MASKABLE_WORD_PATTERN``, the same test ``_iter_words`` uses, so a word carrying a
    combining mark keeps its tag; ``token.is_alpha`` is false for all of them.

    Returns an empty mapping when spaCy is unavailable.

    Args:
        prompt (str): The prompt to tag.

    Returns:
        dict[str, str]: Word key to part-of-speech label ("noun" or "verb").
    """
    nlp = _get_nlp()
    if nlp is None:
        return {}
    lookup: dict[str, str] = {}
    for token in nlp(unicodedata.normalize("NFC", prompt)):
        if _MASKABLE_WORD_PATTERN.fullmatch(token.text) and token.pos_ in _MASKABLE_POS:
            # Keep the first tag seen for a given surface form.
            lookup.setdefault(_word_key(token.text), _MASKABLE_POS[token.pos_])
    return lookup


def _rank_candidates(
    prompt: str,
    pos_lookup: dict[str, str],
    essential_words: list[str] | None,
) -> list[str]:
    """
    Order candidate words for masking by priority.

    Priority follows the PUZZLED paper: caller-supplied essential words first, then the
    built-in essential harm words (``_ESSENTIAL_HARM_WORDS``), then the paper's recommended
    contextual words (``_SUPPLEMENTARY_WORDS``), then nouns/verbs (when spaCy is available),
    then any remaining words. Within each tier, longer words rank higher because they carry
    more of the instruction's meaning and make harder puzzles. Ties break alphabetically for
    determinism.

    Args:
        prompt (str): The prompt being masked.
        pos_lookup (dict[str, str]): Noun/verb tags from ``_pos_lookup``.
        essential_words (list[str] | None): Sensitive words to prefer, if any.

    Returns:
        list[str]: Distinct candidate words (as they appear in the prompt) in priority order.
    """
    tokens = [word.text for word in _iter_words(prompt)]
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        key = _word_key(token)
        if key not in seen:
            seen.add(key)
            unique.append(token)

    essential_keys = {_word_key(w) for w in (essential_words or [])}

    def tier(word: str) -> int:
        key = _word_key(word)
        if key in essential_keys:
            return 0
        if key in _ESSENTIAL_HARM_WORDS:
            return 1
        if key in _SUPPLEMENTARY_WORDS:
            return 2
        if key in pos_lookup:
            return 3
        return 4

    return sorted(unique, key=lambda w: (tier(w), -_letter_count(w), _word_key(w)))


def mask_prompt(
    prompt: str,
    *,
    num_to_mask: int | None = None,
    essential_words: list[str] | None = None,
) -> MaskResult:
    """
    Replace the most sensitive words in ``prompt`` with indexed placeholders.

    Words are chosen by ``_rank_candidates``, then the placeholders are numbered by
    the order the chosen words appear in the prompt, so ``[WORD1]`` is always the leftmost
    masked word. Every occurrence of each chosen word is masked, matched case-insensitively.

    Args:
        prompt (str): The instruction to mask.
        num_to_mask (int | None): How many words to mask. Defaults to the length-based rule.
        essential_words (list[str] | None): Sensitive words to prefer when selecting.

    Returns:
        MaskResult: The masked prompt and the ordered list of masked words.

    Raises:
        ValueError: If ``prompt`` contains no maskable words.
    """
    token_count = len(prompt.split())
    target = num_to_mask if num_to_mask is not None else mask_count_for_length(token_count)

    pos_lookup = _pos_lookup(prompt)
    ranked = _rank_candidates(prompt, pos_lookup, essential_words)
    if not ranked:
        raise ValueError("The prompt has no maskable words.")

    chosen = ranked[: max(0, target)]
    if not chosen:
        # Nothing to mask (e.g. num_to_mask == 0); return the prompt unchanged.
        return MaskResult(masked_prompt=prompt, masked_words=[])

    # Number placeholders by where each chosen word first appears (case-insensitively), so
    # [WORD1] is the leftmost masked word. Candidates come from the same scan, so every
    # chosen word has an entry here.
    first_index: dict[str, int] = {}
    for word in _iter_words(prompt):
        first_index.setdefault(_word_key(word.text), word.start)
    ordered = sorted(chosen, key=lambda w: first_index[_word_key(w)])

    masked_words: list[MaskedWord] = []
    placeholders: dict[str, str] = {}
    for index, word in enumerate(ordered, start=1):
        placeholder = f"[WORD{index}]"
        pos = pos_lookup.get(_word_key(word), _GENERIC_POS)
        masked_words.append(MaskedWord(text=word, placeholder=placeholder, pos=pos))
        placeholders[_word_key(word)] = placeholder

    # Replace the word spans of the same scan, looked up by the same key. Every occurrence of
    # a chosen word is masked whatever its casing, and every replaced span has a placeholder.
    # Building the result in one pass means an inserted placeholder can never be matched again.
    pieces: list[str] = []
    last_end = 0
    for word in _iter_words(prompt):
        placeholder = placeholders.get(_word_key(word.text))
        if placeholder is None:
            continue
        pieces.append(prompt[last_end : word.start])
        pieces.append(placeholder)
        last_end = word.end
    pieces.append(prompt[last_end:])

    return MaskResult(masked_prompt="".join(pieces), masked_words=masked_words)
