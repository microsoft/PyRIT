# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Select the program to run from text that holds more than code."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pyrit.models import ComponentIdentifier

if TYPE_CHECKING:
    from collections.abc import Collection

_OPENING_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_COMMENT_START = re.compile(r"^ {0,3}<!--")
_LINE_ENDING = re.compile(r"\r\n|\n|\r")


class ProgramSelectionError(Exception):
    """
    The text does not hold exactly one program to select.

    Nothing runs, so the execution evidence is unavailable and the verdict is undetermined.
    It is never treated as a program that failed.
    """


@runtime_checkable
class ProgramSelector(Protocol):
    """
    Choose the exact program text to run from the text a scorable names.

    A selector is deterministic and never guesses: text with no candidate, or with several,
    raises ``ProgramSelectionError`` instead of choosing one.
    """

    def get_identifier(self) -> ComponentIdentifier:
        """Return the nonsecret selection rules."""
        ...

    def select_program(self, *, text: str) -> str:
        """
        Return the program to run, verbatim from ``text``.

        Raises:
            ProgramSelectionError: If ``text`` does not hold exactly one program.
        """
        ...


class FencedCodeBlockSelector:
    """
    Select the one fenced code block whose info string names an accepted language.

    Fences follow Markdown: three or more backticks or tildes, indented at most three spaces,
    closed by a fence of the same character that is at least as long. The fence's indentation is
    removed from each line with tabs counted to four-column stops, as Markdown does. The language is
    the first word of the info string, compared without case. Prose, untagged blocks, indented code,
    fence-like lines inside an HTML comment, and blocks in other languages are never run. Text with
    no accepted block, more than one, an empty one, or any unclosed fence has no program to select.
    """

    def __init__(self, *, languages: Collection[str]) -> None:
        """
        Initialize with the language tags whose blocks may be selected.

        Args:
            languages (Collection[str]): Info string tags such as ``python`` or ``py``.

        Raises:
            ValueError: If ``languages`` is a single string, no language is given, or a tag is empty
                or contains whitespace or backticks.
        """
        if isinstance(languages, str):
            raise ValueError("languages must be a collection of tags, such as ('python',), not one string.")
        normalized = {language.lower() for language in languages}
        if not normalized or any(not re.fullmatch(r"[^\s`]+", language) for language in normalized):
            raise ValueError("languages must be one or more tags without whitespace or backticks.")
        self._languages = tuple(sorted(normalized))

    def get_identifier(self) -> ComponentIdentifier:
        """
        Return the accepted languages.

        Returns:
            ComponentIdentifier: The selection rules.
        """
        return ComponentIdentifier.of(self, params={"selector_version": 1, "languages": list(self._languages)})

    def select_program(self, *, text: str) -> str:
        """
        Return the content of the only accepted fenced block.

        Args:
            text (str): The text that may hold the program.

        Returns:
            str: The block's lines, each newline-terminated, with the fence's indentation removed.

        Raises:
            ProgramSelectionError: If there is no accepted block, more than one, an empty one, or
                an unclosed fence.
        """
        blocks: list[str] = []
        lines = _LINE_ENDING.split(text)
        index = 0
        while index < len(lines):
            comment = _COMMENT_START.match(lines[index])
            if comment is not None:
                # Markdown reads everything up to the line that closes the comment as HTML, so no fence opens in it.
                index = _skip_comment(lines, index, start=comment.end())
                continue
            opening = _OPENING_FENCE.match(lines[index])
            index += 1
            if opening is None:
                continue
            indent, fence, info = len(opening.group(1)), opening.group(2), opening.group(3)
            if fence[0] == "`" and "`" in info:
                continue
            closing = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
            content: list[str] = []
            while index < len(lines) and closing.match(lines[index]) is None:
                content.append(_remove_indent(lines[index], indent))
                index += 1
            if index == len(lines):
                raise ProgramSelectionError("the text has an unclosed code fence, so its code may be incomplete")
            index += 1
            words = info.split()
            if words and words[0].lower() in self._languages:
                blocks.append("".join(f"{line}\n" for line in content))
        accepted = " or ".join(self._languages)
        if not blocks:
            raise ProgramSelectionError(f"the text holds no {accepted} code block")
        if len(blocks) > 1:
            raise ProgramSelectionError(f"the text holds {len(blocks)} {accepted} code blocks, which is ambiguous")
        if not blocks[0].strip():
            raise ProgramSelectionError(f"the {accepted} code block is empty")
        return blocks[0]


def _skip_comment(lines: list[str], index: int, *, start: int) -> int:
    """
    Return the index of the first line after an HTML comment that opens on ``lines[index]``.

    The comment ends on the first line holding ``-->`` after the opening ``<!--``. A comment that never closes
    runs to the end of the text, as Markdown reads it.

    Returns:
        int: The index of the line after the comment, or ``len(lines)`` if it never closes.
    """
    if "-->" in lines[index][start:]:
        return index + 1
    for later in range(index + 1, len(lines)):
        if "-->" in lines[later]:
            return later + 1
    return len(lines)


def _remove_indent(line: str, width: int) -> str:
    """
    Remove up to ``width`` columns of leading indentation, counting tabs to four-column stops.

    A tab that reaches past ``width`` keeps the columns it does not use as spaces, so the code's own
    indentation is unchanged.

    Returns:
        str: The line without the removed indentation.
    """
    column = 0
    for position, character in enumerate(line):
        if column >= width:
            return line[position:]
        if character == " ":
            column += 1
        elif character == "\t":
            stop = column + 4 - column % 4
            if stop > width:
                return " " * (stop - width) + line[position + 1 :]
            column = stop
        else:
            return line[position:]
    return ""
