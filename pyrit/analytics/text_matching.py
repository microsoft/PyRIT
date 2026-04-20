# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Text matching strategies for PyRIT.

This module provides various text matching algorithms including exact substring matching
and n-gram based approximate matching through a unified TextMatching interface.
"""

from typing import Protocol


class TextMatching(Protocol):
    """
    Protocol for text matching strategies.

    Classes implementing this protocol must provide an is_match method that
    checks if a target string matches text according to some strategy.
    """

    def is_match(self, *, target: str, text: str) -> bool:
        """
        Check if target matches text according to the strategy.

        Args:
            target (str): The string to search for.
            text (str): The text to search in.

        Returns:
            bool: True if target matches text according to the strategy, False otherwise.
        """
        ...


class ExactTextMatching(TextMatching):
    """
    Exact substring matching strategy.

    Checks if the target string is present in the text as a substring.
    """

    def __init__(self, *, case_sensitive: bool = False, ignore_whitespace: bool = True) -> None:
        """
        Initialize the exact text matching strategy.

        Args:
            case_sensitive (bool): Whether to perform case-sensitive matching. Defaults to False.
            ignore_whitespace (bool): Whether to ignore whitespace. Defaults to True.
        """
        self._case_sensitive = case_sensitive
        self._ignore_whitespace = ignore_whitespace

    def is_match(self, *, target: str, text: str) -> bool:
        """
        Check if target string is present in text.

        Args:
            target (str): The substring to search for.
            text (str): The text to search in.

        Returns:
            bool: True if target is found in text, False otherwise.
        """
        if not text:
            return False
        if self._ignore_whitespace:
            target = target.strip()
            text = text.strip()
        if self._case_sensitive:
            return target in text
        return target.lower() in text.lower()


class ApproximateTextMatching(TextMatching):
    """
    Approximate text matching using n-gram overlap.

    This strategy computes the proportion of character n-grams from the target
    that are present in the text. Useful for detecting partial matches, encoded
    content, or text with variations.
    """

    def __init__(self, *, threshold: float = 0.5, n: int = 3, case_sensitive: bool = False) -> None:
        """
        Initialize the approximate text matching strategy.

        Args:
            threshold (float): The minimum n-gram overlap score (0.0 to 1.0) required for a match.
                Defaults to 0.5 (50% overlap).
            n (int): The length of character n-grams to use. Defaults to 3.
            case_sensitive (bool): Whether to perform case-sensitive matching. Defaults to False.
        """
        self._threshold = threshold
        self._n = n
        self._case_sensitive = case_sensitive

    def is_match(self, *, target: str, text: str) -> bool:
        """
        Check if target approximately matches text using n-gram overlap.

        Args:
            target (str): The string to search for.
            text (str): The text to search in.

        Returns:
            bool: True if n-gram overlap score exceeds threshold, False otherwise.
        """
        score = self._calculate_ngram_overlap(target=target, text=text)
        return score >= self._threshold

    def _calculate_ngram_overlap(self, *, target: str, text: str) -> float:
        """
        Calculate the n-gram overlap score between target and text.

        Args:
            target (str): The target string to match.
            text (str): The text to search in.

        Returns:
            float: A score between 0.0 and 1.0 indicating the proportion of target n-grams
                found in the text.
        """
        if not text:
            return 0.0
        if len(target) < self._n:
            return 0.0  # Confidence is too low for short targets

        target_str = target if self._case_sensitive else target.lower()
        text_str = text if self._case_sensitive else text.lower()

        # Generate all n-grams from target
        target_ngrams = {target_str[i : i + self._n] for i in range(len(target_str) - (self._n - 1))}

        # Safety check: if no n-grams were generated, return 0.0
        if not target_ngrams:
            return 0.0

        # Count how many target n-grams are found in text
        matching_ngrams = sum(int(ngram in text_str) for ngram in target_ngrams)

        # Calculate proportion of matching n-grams
        return matching_ngrams / len(target_ngrams)

    def get_overlap_score(self, *, target: str, text: str) -> float:
        """
        Get the n-gram overlap score without threshold comparison.

        Useful for getting detailed scoring information.

        Args:
            target (str): The target string to match.
            text (str): The text to search in.

        Returns:
            float: The n-gram overlap score between 0.0 and 1.0.
        """
        return self._calculate_ngram_overlap(target=target, text=text)
