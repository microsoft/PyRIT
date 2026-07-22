# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keyword selection and masking for the PUZZLED jailbreak technique
(Ahn & Lee, "PUZZLED: Jailbreaking LLMs through Word-Based Puzzles", arXiv:2508.01306).

The masker chooses the most sensitive words in a prompt, replaces each with an indexed
``[WORD1]`` placeholder, and records a deterministic clue (length and part of speech) for
every masked word. Part-of-speech tagging uses spaCy when its ``en_core_web_sm`` model is
installed and degrades to a length-based heuristic when it is not, mirroring the graceful
fallback already used in ``pyrit/executor/benchmark/fairness_bias.py``.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Part-of-speech label used when spaCy is unavailable or the word was caller-supplied.
_GENERIC_POS = "word"

# spaCy coarse tags we treat as maskable, mapped to the clue label we expose.
_MASKABLE_POS = {"NOUN": "noun", "PROPN": "noun", "VERB": "verb"}

# Harm-related words the PUZZLED paper masks first, so the puzzle hides the part of the
# instruction that carries the harmful intent. Seeded from the paper's Table 4 examples
# (Ahn & Lee, arXiv:2508.01306); callers can extend or override via ``essential_words``.
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

# Contextual "amplifier" words masked after the essential list when more masks are needed.
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

    The thresholds follow the PUZZLED paper: longer instructions hide more words.

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
    _nlp_loaded = True
    try:
        import spacy  # type: ignore[ty:unresolved-import]

        _nlp = spacy.load("en_core_web_sm")
    except Exception:
        logger.info("spaCy model 'en_core_web_sm' unavailable; using length-based keyword selection instead.")
        _nlp = None
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
    built-in essential harm words (``_ESSENTIAL_HARM_WORDS``), then the supplementary
    amplifier words (``_SUPPLEMENTARY_WORDS``), then nouns/verbs (when spaCy is available),
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
    tokens = re.findall(r"[A-Za-z]+", prompt)
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
    masked word. Only the first occurrence of each chosen word is masked.

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

    # Number placeholders by where each chosen word first appears, so [WORD1] is the leftmost
    # masked word. Every occurrence of a chosen word is then replaced, not just the first, so
    # no sensitive word is left in cleartext.
    first_index: dict[str, int] = {}
    for word in chosen:
        match = re.search(rf"\b{re.escape(word)}\b", prompt)
        if match:
            first_index[word] = match.start()
    ordered = sorted(first_index, key=lambda w: first_index[w])

    masked_words: list[MaskedWord] = []
    masked_prompt = prompt
    for index, word in enumerate(ordered, start=1):
        placeholder = f"[WORD{index}]"
        pos = pos_lookup.get(word.lower(), _GENERIC_POS)
        masked_words.append(MaskedWord(text=word, placeholder=placeholder, pos=pos))
        masked_prompt = re.sub(rf"\b{re.escape(word)}\b", placeholder, masked_prompt)

    return MaskResult(masked_prompt=masked_prompt, masked_words=masked_words)
