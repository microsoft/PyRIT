# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Pure evaluation of typed text criteria."""

import re

from pyrit.common.text_matching import ExactTextMatching
from pyrit.models.score.text_matcher import Contains, Equals, Regex, TextMatcher


def match_text(*, matcher: TextMatcher, text: str) -> bool:
    """
    Evaluate a matcher without changing the evidence.

    Returns:
        bool: Whether the candidate matches.

    Raises:
        TypeError: If the matcher is unsupported.
    """
    if isinstance(matcher, Contains):
        return ExactTextMatching(
            case_sensitive=matcher.case_sensitive, ignore_whitespace=matcher.ignore_whitespace
        ).is_match(target=matcher.value, text=text)
    candidate = text.strip() if matcher.ignore_whitespace else text
    if isinstance(matcher, Regex):
        return re.search(matcher.value, candidate, flags=0 if matcher.case_sensitive else re.IGNORECASE) is not None
    if isinstance(matcher, Equals):
        expected = matcher.value.strip() if matcher.ignore_whitespace else matcher.value
        return candidate == expected if matcher.case_sensitive else candidate.lower() == expected.lower()
    raise TypeError(f"Unsupported text matcher: {type(matcher).__name__}")
