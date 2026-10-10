# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

from pyrit.converter.text_selection_strategy import WordSelectionStrategy
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.models import ComponentIdentifier


class EmojiConverter(WordLevelConverter):
    """
    Converts English text to randomly chosen circle or square character emojis.

    Inspired by https://github.com/BASI-LABS/parseltongue/blob/main/src/utils.ts
    """

    #: Dictionary mapping letters to their corresponding emojis.
    emoji_dict = {
        "a": ["🅐", "🅰️", "🄰"],
        "b": ["🅑", "🅱️", "🄱"],
        "c": ["🅒", "🅲", "🄲"],
        "d": ["🅓", "🅳", "🄳"],
        "e": ["🅔", "🅴", "🄴"],
        "f": ["🅕", "🅵", "🄵"],
        "g": ["🅖", "🅶", "🄶"],
        "h": ["🅗", "🅷", "🄷"],
        "i": ["🅘", "🅸", "🄸"],
        "j": ["🅙", "🅹", "🄹"],
        "k": ["🅚", "🅺", "🄺"],
        "l": ["🅛", "🅻", "🄻"],
        "m": ["🅜", "🅼", "🄼"],
        "n": ["🅝", "🅽", "🄽"],
        "o": ["🅞", "🅾️", "🄾"],
        "p": ["🅟", "🅿️", "🄿"],
        "q": ["🅠", "🆀", "🅀"],
        "r": ["🅡", "🆁", "🅁"],
        "s": ["🅢", "🆂", "🅂"],
        "t": ["🅣", "🆃", "🅃"],
        "u": ["🅤", "🆄", "🅄"],
        "v": ["🅥", "🆅", "🅅"],
        "w": ["🅦", "🆆", "🅆"],
        "x": ["🅧", "🆇", "🅇"],
        "y": ["🅨", "🆈", "🅈"],
        "z": ["🅩", "🆉", "🅉"],
    }

    def __init__(
        self,
        *,
        seed: int | None = None,
        word_selection_strategy: WordSelectionStrategy | None = None,
        word_split_separator: str | None = " ",
        **kwargs: Any,
    ) -> None:
        """
        Initialize the converter.

        Args:
            seed (int | None): Optional seed for reproducible output. Defaults to None.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words to convert.
                If None, all words will be converted.
            word_split_separator (str | None): Separator used to split words in the input text.
            **kwargs: Forwarded to ``WordLevelConverter`` for cooperative multiple inheritance.
        """
        super().__init__(
            word_selection_strategy=word_selection_strategy,
            word_split_separator=word_split_separator,
            **kwargs,
        )
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        base_identifier = super()._build_identifier()
        return self._create_identifier(params={**base_identifier.params, "seed": self._seed})

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        word = word.lower()
        rng = self._get_random_generator(stream="emoji-substitutions")
        result = []
        for char in word:
            if char in EmojiConverter.emoji_dict:
                result.append(rng.choice(EmojiConverter.emoji_dict[char]))
            else:
                result.append(char)
        return "".join(result)
