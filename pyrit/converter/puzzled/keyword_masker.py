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
import re
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# The one tokenizer used for both candidate selection and placeholder ordering. Requiring
# word boundaries keeps candidates to runs of letters that can actually be matched again
# when masking, so a token embedded in an alphanumeric blob (the "h" of "h4ck3r") is never
# selected and then silently dropped.
_WORD_PATTERN = re.compile(r"\b[A-Za-z]+\b")

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
        return f"{len(self.text)}-letter {self.pos}"


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
    Map each alphabetic noun/verb (lowercased) in the prompt to its clue label.

    Returns an empty mapping when spaCy is unavailable.

    Args:
        prompt (str): The prompt to tag.

    Returns:
        dict[str, str]: Lowercased word to part-of-speech label ("noun" or "verb").
    """
    nlp = _get_nlp()
    if nlp is None:
        return {}
    lookup: dict[str, str] = {}
    for token in nlp(prompt):
        if token.is_alpha and token.pos_ in _MASKABLE_POS:
            # Keep the first tag seen for a given surface form.
            lookup.setdefault(token.text.lower(), _MASKABLE_POS[token.pos_])
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
    tokens = _WORD_PATTERN.findall(prompt)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        key = token.lower()
        if key not in seen:
            seen.add(key)
            unique.append(token)

    essential_lower = {w.lower() for w in (essential_words or [])}

    def tier(word: str) -> int:
        key = word.lower()
        if key in essential_lower:
            return 0
        if key in _ESSENTIAL_HARM_WORDS:
            return 1
        if key in _SUPPLEMENTARY_WORDS:
            return 2
        if key in pos_lookup:
            return 3
        return 4

    return sorted(unique, key=lambda w: (tier(w), -len(w), w.lower()))


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
    # [WORD1] is the leftmost masked word. Candidates come from the same word-boundary scan,
    # so every chosen word has an entry here.
    first_index: dict[str, int] = {}
    for match in _WORD_PATTERN.finditer(prompt):
        first_index.setdefault(match.group().lower(), match.start())
    ordered = sorted(chosen, key=lambda w: first_index[w.lower()])

    masked_words: list[MaskedWord] = []
    placeholders: dict[str, str] = {}
    for index, word in enumerate(ordered, start=1):
        placeholder = f"[WORD{index}]"
        pos = pos_lookup.get(word.lower(), _GENERIC_POS)
        masked_words.append(MaskedWord(text=word, placeholder=placeholder, pos=pos))
        placeholders[word.lower()] = placeholder

    # Replace every occurrence of every chosen word in one case-insensitive pass, so a word
    # that recurs or appears in different casing is never left in cleartext, and a placeholder
    # already inserted cannot be re-matched while masking the next word.
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in ordered) + r")\b", re.IGNORECASE)
    masked_prompt = pattern.sub(lambda m: placeholders[m.group(0).lower()], prompt)

    return MaskResult(masked_prompt=masked_prompt, masked_words=masked_words)
